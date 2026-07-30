#!/usr/bin/env bash

######################################################################
# Install the prebuilt TrueNAS kernel and OpenZFS release debs in the VM,
# then build and install truenas_pyos against them.
#
# Invoked with the TrueNAS train (master or 26) in the TRAIN environment
# variable.  The kernel image + UAPI headers (truenas/linux) and the
# OpenZFS userland + kmod debs (truenas/zfs) are consumed from the rolling
# <TRAIN>-nightly GitHub releases.  The test suite needs the real TrueNAS
# kernel and a live ZFS pool -- e.g. the ACL tests run against posixacl /
# nfsv4 datasets -- and the OpenZFS modules are prebuilt against that exact
# kernel, so the VM must reboot into it (qemu-3.5-restart.sh) before the
# tests can load zfs.ko.
######################################################################

set -eu

TRAIN="${TRAIN:?TRAIN must be set (master or 26)}"

echo "Installing prebuilt TrueNAS kernel + OpenZFS ($TRAIN train) and building truenas_pyos..."

# Load VM info
source /tmp/vm-info.sh

# Wait for cloud-init to finish
echo "Waiting for cloud-init to complete..."
ssh debian@$VM_IP "cloud-init status --wait" || true

# Install rsync in VM first
echo "Installing rsync in VM..."
ssh debian@$VM_IP "sudo apt-get update && sudo apt-get install -y rsync"

# Copy source code to VM (this brings the .github/workflows/scripts helpers
# the remote script calls, e.g. tn-fetch-debs.sh)
echo "Copying source code to VM..."
ssh debian@$VM_IP "mkdir -p ~/truenas_pyos"
rsync -az --exclude='.git' --exclude='debian/.debhelper' \
  --exclude='src/.libs' --exclude='*.o' --exclude='*.lo' --exclude='build/' \
  "$GITHUB_WORKSPACE/" debian@$VM_IP:~/truenas_pyos/

# Install the kernel, OpenZFS and truenas_pyos inside the VM
echo "Running in-VM install/build..."
ssh debian@$VM_IP bash -s "$TRAIN" <<'REMOTE_SCRIPT'
TRAIN="$1"
set -eu
export DEBIAN_FRONTEND=noninteractive

cd ~/truenas_pyos

# Update package lists
sudo apt-get update

# Packages the VM needs, in three groups:
#  - build the deb: build-essential (compiler + dpkg-buildpackage),
#    debhelper + dh-python + pybuild-plugin-pyproject, python3-all-dev,
#    python3-setuptools.
#  - run the tests: python3-pytest, python3-pydantic (truenas_pyfilter
#    models), python3-mypy (stub checks), gdb (core backtraces).
#  - fetch+verify the releases: curl, jq, ca-certificates.
# The OpenZFS build toolchain (autoconf, libelf-dev, dkms, ...) is gone:
# the kmod + userland are downloaded prebuilt instead of compiled here.
sudo apt-get install -y \
  build-essential \
  debhelper \
  dh-python \
  git \
  pybuild-plugin-pyproject \
  python3-all-dev \
  python3-setuptools \
  python3-pytest \
  python3-pydantic \
  python3-mypy \
  gdb \
  curl \
  jq \
  ca-certificates

# Fetch (and verify) the prebuilt OpenZFS debs and the TrueNAS kernel from
# their rolling <TRAIN>-nightly releases.  Alongside the bootable image we
# pull the kernel's linux-*libc-dev package: the C extension compiles the
# statmount()/statx interface against the kernel's UAPI headers, which must
# match the running kernel (the stock linux-libc-dev does not).
ZFS_MANIFEST="$(.github/workflows/scripts/tn-fetch-debs.sh \
  truenas/zfs "$TRAIN" /tmp/zfs-debs 'openzfs-*')"
KERNEL_MANIFEST="$(.github/workflows/scripts/tn-fetch-debs.sh \
  truenas/linux "$TRAIN" /tmp/tn-kernel 'linux-image-*' 'linux-*libc-dev_*')"

# The OpenZFS kmod is built against one exact kernel.  The kernel and zfs
# nightlies roll independently, so if the kernel has advanced past the one
# zfs was built against, the prebuilt zfs.ko will not load.  Refuse to
# proceed on a mismatch with a clear message rather than failing later at
# modprobe time.
ZFS_KREL="$(jq -r '.kernel_release' "$ZFS_MANIFEST")"
RELEASE="$(jq -r '.release' "$KERNEL_MANIFEST")"
if [ "$ZFS_KREL" != "$RELEASE" ]; then
  echo "FATAL: OpenZFS $TRAIN-nightly debs were built against kernel $ZFS_KREL,"
  echo "but truenas/linux $TRAIN-nightly currently publishes kernel $RELEASE."
  echo "The two rolling nightlies are out of sync; the prebuilt zfs.ko cannot"
  echo "load under the mismatched kernel.  This self-heals once the truenas/zfs"
  echo "nightly rebuilds against $RELEASE."
  exit 1
fi
echo "Kernel release: $RELEASE (matches the OpenZFS build kernel)"

# Install the TrueNAS kernel image and its matching UAPI headers.  Install
# the image first so /lib/modules/$RELEASE exists and the OpenZFS modules
# deb's linux-image dependency resolves; the libc-dev package
# Provides/Conflicts the stock linux-libc-dev, so it replaces it for the
# C-extension build.
echo "Installing TrueNAS kernel image + UAPI headers..."
sudo -E apt-get install -y \
  /tmp/tn-kernel/linux-image-*.deb \
  /tmp/tn-kernel/linux-*libc-dev_*.deb

# Install the OpenZFS userland + kmod debs (the release already excludes
# dkms/dracut).
echo "Installing OpenZFS debs..."
sudo -E apt-get install -y /tmp/zfs-debs/openzfs-*.deb
sudo depmod -a "$RELEASE"

# The prebuilt zfs.ko must have landed under the TrueNAS kernel's modules
# tree, or the post-reboot modprobe will fail.  Fail loudly here instead.
ZFS_KO="$(find "/lib/modules/$RELEASE" -name 'zfs.ko*' -print -quit 2>/dev/null || true)"
if [ -z "$ZFS_KO" ]; then
  echo "FATAL: no zfs.ko under /lib/modules/$RELEASE/ after installing the OpenZFS modules deb"
  echo "Installed zfs.ko paths in /lib/modules:"
  find /lib/modules -name 'zfs.ko*' 2>/dev/null || echo "  (none found)"
  exit 1
fi
echo "Found zfs.ko at: $ZFS_KO"

# Build and install truenas_pyos.  The build is userland-only (it compiles
# against the UAPI headers, not the kernel build tree), so it runs fine
# while the stock kernel is still booted.
echo "Building truenas_pyos..."
dpkg-buildpackage -us -uc -b
echo "Installing truenas_pyos..."
sudo dpkg -i ../python3-truenas-pyos_*.deb

# Now replace the distribution kernel with the TrueNAS kernel so the next
# boot (qemu-3.5-restart.sh) can only use it, and the prebuilt zfs.ko can
# load.
echo "Removing distribution kernels so the TrueNAS kernel is the default..."
# Let apt remove the running (stock) kernel without aborting.
echo 'linux-base linux-base/removing-running-kernel boolean false' | \
  sudo debconf-set-selections
# TrueNAS kernel packages carry version-free names
# (linux-{image,headers}-truenas-production-amd64), so tell them apart from
# the distribution kernels by name.
STOCK=$(dpkg-query -W -f '${Package}\n' 'linux-image-*' 'linux-headers-*' | \
  grep -v -- truenas || true)
if [ -n "$STOCK" ]; then
  sudo -E apt-get purge -y $STOCK
fi
sudo update-grub

# The TrueNAS kernel must now be the one and only installed kernel.
test -e "/boot/vmlinuz-$RELEASE"
test "$(ls /boot/vmlinuz-* | wc -l)" -eq 1
echo "TrueNAS kernel $RELEASE is the only installed kernel."
REMOTE_SCRIPT

# Clean cloud-init and poweroff VM (qemu-3.5-restart.sh brings it back up
# into the TrueNAS kernel)
echo "Cleaning cloud-init and powering off VM..."
ssh debian@$VM_IP 'sudo cloud-init clean --logs && sync && sleep 2 && sudo poweroff' &

echo "Build complete, VM shutting down for restart"
