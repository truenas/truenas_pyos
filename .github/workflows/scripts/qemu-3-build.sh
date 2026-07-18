#!/usr/bin/env bash

######################################################################
# Build and install truenas_pyos in the VM
######################################################################

set -eu

: "${KERNEL_APT_SNAPSHOT:?set by qemu-test.yml}"
: "${KERNEL_DEB_VERSION:?set by qemu-test.yml}"

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
  "KERNEL_APT_SNAPSHOT='$KERNEL_APT_SNAPSHOT' KERNEL_DEB_VERSION='$KERNEL_DEB_VERSION' bash -s" <<'REMOTE_DEPS'
set -eu
: "${KERNEL_APT_SNAPSHOT:?}"
: "${KERNEL_DEB_VERSION:?}"

# Pin the VM kernel to the last 6.18 that trixie-backports shipped: TrueNAS
# runs 6.18 and OpenZFS 2.4 is undefined on a 7.x kernel, and 6.18 is what
# populates the statmount sb_source / uid-gid-map fields the fs tests need.
# Live trixie-backports now carries only 7.0.x, so pull 6.18 from a pinned
# snapshot.debian.org view — its Release file has expired, so
# [check-valid-until=no] accepts it; package integrity still comes from the
# archive signatures.
#
# The cloud image already ships live trixie-backports in its default
# debian.sources; two sources for one release make apt take the newest across
# both (how live 7.0.x could beat the pin). Strip that suite so the snapshot is
# the only trixie-backports source, and fail loudly if any other file has it.
sudo sed -i 's/ trixie-backports\b//g' /etc/apt/sources.list.d/debian.sources
echo "deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/${KERNEL_APT_SNAPSHOT}/ trixie-backports main" \
  | sudo tee /etc/apt/sources.list.d/backports.list
stray=$(grep -rl 'trixie-backports' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null \
  | grep -v '/backports.list$' || true)
if [ -n "$stray" ]; then
  echo "ERROR: live trixie-backports still configured in: $stray"
  exit 1
fi
sudo apt-get update

sudo apt-get install -y \
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
  python3-pydantic \
  python3-mypy \
  pybuild-plugin-pyproject \
  dkms \
  git \
  gdb

# The pinned 6.18 kernel image + matching build headers (for the ZFS kmod).
# GRUB boots the highest version on the reboot below. Install by EXACT version
# — not `-t trixie-backports`, which selects by release name — and assert dpkg
# agrees, so a drifted snapshot fails here rather than as a later ZFS or
# statmount surprise.
sudo apt-get install -y \
  "linux-image-amd64=$KERNEL_DEB_VERSION" \
  "linux-headers-amd64=$KERNEL_DEB_VERSION"
got=$(dpkg-query -W -f='${Version}' linux-image-amd64)
if [ "$got" != "$KERNEL_DEB_VERSION" ]; then
  echo "ERROR: pinned kernel $KERNEL_DEB_VERSION but apt installed '$got'"
  exit 1
fi
echo "Installed pinned kernel $got"

# Matching 6.18 UAPI headers from the same (now sole) trixie-backports source,
# so the C extension compiles the full statmount()/statx interface. `-t` avoids
# hardcoding the linux-libc-dev version string; the snapshot is the only
# backports source, so it resolves to the pinned release.
sudo apt-get install -y -t trixie-backports linux-libc-dev
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

# Verify VM is accessible and booted the pinned 6.18 kernel — fail loudly here
# rather than silently testing on the cloud image's 6.12 kernel (where the
# statmount sb_source / uid-gid-map fields are not populated).
echo "Verifying new kernel is running..."
booted=$(ssh debian@$VM_IP "uname -r")
echo "Booted kernel: $booted"
case "$booted" in
  6.18.*) : ;;
  *) echo "ERROR: expected a 6.18 kernel, got '$booted'"; exit 1 ;;
esac

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
