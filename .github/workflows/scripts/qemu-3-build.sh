#!/usr/bin/env bash

######################################################################
# Build and install truenas_pyos in the VM
######################################################################

set -eu

: "${KERNEL_TRAIN:?set by qemu-test.yml}"
: "${KERNEL_RELEASE:?set by qemu-test.yml}"

echo "Building and installing truenas_pyos..."

# Load VM info
source /tmp/vm-info.sh

# Wait for cloud-init to finish
echo "Waiting for cloud-init to complete..."
ssh debian@$VM_IP "cloud-init status --wait" || true

# Install rsync in VM first
echo "Installing rsync in VM..."
ssh debian@$VM_IP "sudo apt-get update && sudo apt-get install -y rsync"

# Check if we have cached ZFS packages
if [ "$ZFS_CACHE_HIT" = "true" ] && [ -d "/tmp/zfs-debs" ]; then
  echo "Found cached OpenZFS packages, copying to VM..."
  ssh debian@$VM_IP "mkdir -p /tmp/zfs-debs"
  rsync -az /tmp/zfs-debs/ debian@$VM_IP:/tmp/zfs-debs/
  CACHED_ZFS="true"
else
  echo "No cached OpenZFS packages found, will build from source"
  CACHED_ZFS="false"
fi

# Copy source code to VM
echo "Copying source code to VM..."
ssh debian@$VM_IP "mkdir -p ~/truenas_pyos"
rsync -az --exclude='.git' --exclude='debian/.debhelper' \
  --exclude='src/.libs' --exclude='*.o' --exclude='*.lo' --exclude='build/' \
  "$GITHUB_WORKSPACE/" debian@$VM_IP:~/truenas_pyos/

# Install dependencies
echo "Installing dependencies in VM..."
ssh debian@$VM_IP \
  "KERNEL_TRAIN='$KERNEL_TRAIN' KERNEL_RELEASE='$KERNEL_RELEASE' bash -s" <<'REMOTE_DEPS'
set -eu
: "${KERNEL_TRAIN:?}"
: "${KERNEL_RELEASE:?}"

export DEBIAN_FRONTEND="noninteractive"

sudo apt-get update

sudo apt-get install -y \
  ca-certificates \
  curl \
  jq \
  build-essential \
  devscripts \
  debhelper \
  dh-autoreconf \
  dh-python \
  autoconf \
  automake \
  libtool \
  pkg-config \
  uuid-dev \
  libssl-dev \
  libaio-dev \
  libblkid-dev \
  libelf-dev \
  libpam0g-dev \
  libtirpc-dev \
  libudev-dev \
  lsb-release \
  po-debconf \
  zlib1g-dev \
  python3-dev \
  python3-all-dev \
  python3-cffi \
  python3-setuptools \
  python3-sphinx \
  python3-pytest \
  python3-mypy \
  pybuild-plugin-pyproject \
  dkms \
  git \
  gdb

# TrueNAS production kernel, consumed the same way the truenas/zfs CI does:
# download the debs published by the truenas/linux CI as the rolling
# <TRAIN>-nightly GitHub release and verify them against SHA256SUMS. The
# image and headers boot the VM and back the ZFS kmod build; unlike
# truenas/zfs we also install the release's UAPI headers package
# (linux-truenas-production-libc-dev, which Provides/Conflicts the stock
# linux-libc-dev), so the C extension compiles the full statmount()/statx
# interface against UAPI headers matching the running kernel.
URL="https://github.com/truenas/linux/releases/download/${KERNEL_TRAIN}-nightly"
mkdir -p /tmp/tn-kernel
cd /tmp/tn-kernel
if ! curl --fail -LSs -O "$URL/manifest.json"; then
  echo "ERROR: no ${KERNEL_TRAIN}-nightly kernel release published at $URL yet"
  exit 1
fi
curl --fail -LSs -O "$URL/SHA256SUMS"
RELEASE=$(jq -r '.release' manifest.json)
echo "Kernel release: $RELEASE" \
  "($(jq -r '.branch' manifest.json) @ $(jq -r '.commit' manifest.json))"

# The workflow keyed the ZFS package cache on the release it saw; fail loudly
# if the nightly release rolled between that fetch and this one.
if [ "$RELEASE" != "$KERNEL_RELEASE" ]; then
  echo "ERROR: nightly release rolled mid-run: expected $KERNEL_RELEASE, got $RELEASE"
  exit 1
fi

DEBS=""
for deb in $(jq -r '.debs[]' manifest.json); do
  case "$deb" in
    linux-image-*-dbg_*)
      ;;
    linux-image-*|linux-headers-*|linux-*libc-dev_*)
      echo "Downloading $deb"
      curl --fail -LSs -O "$URL/$deb"
      DEBS="$DEBS ./$deb"
      ;;
  esac
done
sha256sum -c --ignore-missing SHA256SUMS

sudo -E apt-get install -y $DEBS

# Remove the distribution kernels so the reboot below can only pick the
# TrueNAS kernel. Let apt remove the running kernel without aborting; the
# TrueNAS kernel packages carry version-free names
# (linux-{image,headers}-truenas-production-amd64), so tell them apart from
# the distribution kernels by name.
echo 'linux-base linux-base/removing-running-kernel boolean false' | \
  sudo debconf-set-selections
STOCK=$(dpkg-query -W -f '${Package}\n' 'linux-image-*' 'linux-headers-*' | \
  grep -v -- truenas || true)
if [ -n "$STOCK" ]; then
  sudo -E apt-get purge -y $STOCK
fi
sudo update-grub

# The TrueNAS kernel must now be the one and only installed kernel, and the
# ZFS kmod build must resolve to its development headers.
test -e "/boot/vmlinuz-$RELEASE"
test "$(ls /boot/vmlinuz-* | wc -l)" -eq 1
test -f "$(readlink -f "/lib/modules/$RELEASE/build")/Module.symvers"
rm -rf /tmp/tn-kernel
REMOTE_DEPS

# Reboot VM to boot into the newly installed kernel
echo "Rebooting VM to load new kernel..."
ssh debian@$VM_IP 'sudo poweroff' &

# Wait for VM to shut down
echo "Waiting for VM to shut down..."
for i in {1..60}; do
  if sudo virsh list --all | grep "$VM_NAME" | grep -q "shut off"; then
    echo "VM has shut down"
    break
  fi
  echo "Waiting for shutdown... ($i/60)"
  sleep 2
done

# Verify it's actually shut off
if ! sudo virsh list --all | grep "$VM_NAME" | grep -q "shut off"; then
  echo "VM did not shut down gracefully, forcing shutdown..."
  sudo virsh destroy "$VM_NAME" || true
  sleep 3
fi

# Start the VM
echo "Starting VM with new kernel..."
sudo virsh start "$VM_NAME"

# Give it time to start booting
sleep 5

# Wait for VM to be accessible via SSH again
echo "Waiting for VM to come back up..."
for i in {1..60}; do
  if ssh -o ConnectTimeout=2 debian@$VM_IP "echo 'VM ready'" 2>/dev/null; then
    echo "VM is accessible via SSH"
    break
  fi
  echo "Waiting for VM... ($i/60)"
  sleep 5
done

# Verify VM is accessible and booted the TrueNAS production kernel — fail
# loudly here rather than silently testing on the cloud image's stock kernel
# (where the statmount sb_source / uid-gid-map fields are not populated).
echo "Verifying new kernel is running..."
booted=$(ssh debian@$VM_IP "uname -r")
echo "Booted kernel: $booted"
if [ "$booted" != "$KERNEL_RELEASE" ]; then
  echo "ERROR: expected TrueNAS kernel $KERNEL_RELEASE, got '$booted'"
  exit 1
fi

# Now build ZFS and truenas_pyos
echo "Building OpenZFS and truenas_pyos..."
ssh debian@$VM_IP bash -s "$CACHED_ZFS" <<'REMOTE_SCRIPT'
CACHED_ZFS="$1"
set -eu

# Install or build OpenZFS
if [ -d "/tmp/zfs-debs" ] && [ "$(ls -A /tmp/zfs-debs/*.deb 2>/dev/null)" ]; then
  echo "Using cached OpenZFS packages..."
  sudo apt-get -y install $(find /tmp/zfs-debs -name '*.deb' | grep -Ev 'dkms|dracut')
  echo "Updating module dependencies..."
  sudo depmod -a
else
  echo "Building OpenZFS from source..."
  cd /tmp
  git clone --depth 1 --branch truenas/zfs-2.4-release https://github.com/truenas/zfs.git
  cd zfs
  ./autogen.sh
  ./configure --prefix=/usr --enable-debuginfo
  make -j$(nproc) native-deb-kmod native-deb-utils

  echo "Saving built packages for caching..."
  mkdir -p /tmp/zfs-debs
  find /tmp -maxdepth 1 -name '*.deb' | grep -Ev 'dkms|dracut' | while read deb; do
    cp "$deb" /tmp/zfs-debs/
  done

  sudo apt-get -y install $(find /tmp -maxdepth 1 -name '*.deb' | grep -Ev 'dkms|dracut')
  echo "Updating module dependencies..."
  sudo depmod -a
fi

# Build and install truenas_pyos
echo "Building truenas_pyos..."
cd ~/truenas_pyos
dpkg-buildpackage -us -uc -b
sudo dpkg -i ../python3-truenas-pyos_*.deb

echo "Build and installation complete!"
REMOTE_SCRIPT

# Copy ZFS packages back from VM for caching (if we built them)
# Do this BEFORE powering off the VM
if [ "$CACHED_ZFS" = "false" ]; then
  echo "Copying built OpenZFS packages from VM for caching..."
  mkdir -p /tmp/zfs-debs
  rsync -az debian@$VM_IP:/tmp/zfs-debs/ /tmp/zfs-debs/ || echo "Note: No packages to cache"
fi

# Clean cloud-init and poweroff VM
echo "Cleaning cloud-init and powering off VM..."
ssh debian@$VM_IP 'sudo cloud-init clean --logs && sync && sleep 2 && sudo poweroff' &

echo "Build complete, VM shutting down for final restart"
