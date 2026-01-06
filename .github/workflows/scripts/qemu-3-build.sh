#!/usr/bin/env bash

######################################################################
# Build and install truenas_pyos in the VM
######################################################################

set -eu

echo "Building and installing truenas_pyos..."

# Load VM info
source /tmp/vm-info.sh

# Wait for cloud-init to finish
echo "Waiting for cloud-init to complete..."
ssh debian@$VM_IP "cloud-init status --wait" || true

# Install rsync in VM first
echo "Installing rsync in VM..."
ssh debian@$VM_IP "sudo apt-get update && sudo apt-get install -y rsync"

# Copy source code to VM
echo "Copying source code to VM..."
ssh debian@$VM_IP "mkdir -p ~/truenas_pyos"
rsync -az --exclude='.git' --exclude='debian/.debhelper' \
  --exclude='build' --exclude='*.egg-info' --exclude='*.o' \
  "$GITHUB_WORKSPACE/" debian@$VM_IP:~/truenas_pyos/

# Install dependencies and build
echo "Installing dependencies in VM..."
ssh debian@$VM_IP 'bash -s' <<'REMOTE_SCRIPT'
set -eu

cd ~/truenas_pyos

# Update package lists
sudo apt-get update

# Install build dependencies
sudo apt-get install -y \
  build-essential \
  devscripts \
  debhelper \
  dh-python \
  python3-dev \
  python3-all-dev \
  python3-pip \
  python3-setuptools \
  python3-pytest \
  pybuild-plugin-pyproject

# Build and install truenas_pyos
echo "Building truenas_pyos..."
dpkg-buildpackage -us -uc -b
sudo dpkg -i ../python3-truenas-pyos_*.deb

echo "Build and installation complete!"
REMOTE_SCRIPT

echo "truenas_pyos installed successfully in VM"
