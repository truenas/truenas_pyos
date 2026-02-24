#!/usr/bin/env bash

######################################################################
# Run pytest tests in the VM
######################################################################

set -eu

echo "Running pytest tests..."

# Load VM info
source /tmp/vm-info.sh

# Run tests in VM as root
ssh debian@$VM_IP 'sudo bash -s' <<'REMOTE_SCRIPT'
set -eu

echo "=========================================="
echo "Loading ZFS kernel modules"
echo "=========================================="

# Load ZFS kernel module (after reboot, modules should load cleanly)
echo "Loading ZFS kernel module..."
sudo modprobe zfs || {
  echo "ERROR: Failed to load ZFS kernel module"
  sudo dmesg | tail -20
  exit 1
}

# Verify module is loaded
if ! lsmod | grep zfs; then
  echo "ERROR: ZFS module not loaded"
  exit 1
fi

echo "ZFS kernel module loaded successfully"
lsmod | grep zfs

cd /home/debian/truenas_pyos

# Diagnose ZFS availability exactly as conftest.py sees it
echo "=== ZFS Python diagnostic ==="
sudo sh -c "python3 - <<'PYEOF'
import subprocess, os, shutil
print('euid:', os.geteuid())
print('zfs path:', shutil.which('zfs'))
print('zpool path:', shutil.which('zpool'))
try:
    r = subprocess.run(['zfs', 'version'], capture_output=True, timeout=5)
    print('zfs version rc:', r.returncode)
    print('zfs version stdout:', r.stdout.decode().strip())
    print('zfs version stderr:', r.stderr.decode().strip())
except Exception as e:
    print('zfs version exception:', type(e).__name__, e)
PYEOF
"
echo "=== end ZFS diagnostic ==="

# Install debug symbols for better crash reports
echo ""
echo "Installing debug symbols..."
sudo apt-get install -y python3-dbg gdb systemd-coredump 2>&1 | grep -v "^\(Reading\|Building\|Extracting\)" || true

# Configure core dumps - bypass systemd-coredump and dump directly to files
echo ""
echo "Configuring core dumps..."

# Create directory for core dumps
sudo mkdir -p /tmp/cores
sudo chmod 777 /tmp/cores

# Disable systemd-coredump and dump directly to files
sudo systemctl mask systemd-coredump.socket 2>&1 || true
echo '/tmp/cores/core.%e.%p.%t' | sudo tee /proc/sys/kernel/core_pattern

# Enable unlimited core dumps for current shell and sudo
ulimit -c unlimited

echo "Core dump configuration:"
cat /proc/sys/kernel/core_pattern
echo "ulimit -c: $(ulimit -c)"
echo ""
echo "Core dump directory:"
ls -la /tmp/cores/

echo ""
echo "=========================================="
echo "Running pytest tests"
echo "=========================================="

# Verify ulimit is set before running tests
echo "Current ulimit -c: $(ulimit -c)"
echo "Verifying core dumps will work with sudo:"
sudo sh -c "ulimit -c unlimited && ulimit -c"

echo ""
echo "Now running full test suite..."
sudo sh -c "ulimit -c unlimited && cd /home/debian/truenas_pyos && python3 -m pytest tests/ -v --tb=short" 2>&1 | tee /home/debian/test-output.txt
TEST_EXIT_CODE=${PIPESTATUS[0]}

# Check if there was a core dump
echo ""
echo "=========================================="
echo "Checking for core dumps..."
echo "=========================================="
ls -lh /tmp/cores/
echo ""

if ls /tmp/cores/core.* 2>/dev/null; then
    echo "Core dumps found!"
    echo ""
    for core in /tmp/cores/core.*; do
        echo "=========================================="
        echo "Analyzing $core"
        echo "=========================================="
        exe_name=$(echo "$core" | sed 's|.*core\.\([^.]*\)\..*|\1|')
        exe_path="/usr/bin/$exe_name"

        echo "Executable: $exe_path"
        echo "Core file: $core"

        echo "Extracting full backtrace..."
        echo "----------------------------------------"
        sudo gdb -batch \
            -ex "set pagination off" \
            -ex "thread apply all bt full" \
            -ex "quit" \
            "$exe_path" "$core" 2>&1 | tee -a ~/test-output.txt
        echo "----------------------------------------"
        echo ""
    done
    echo "=========================================="
else
    echo "No core dumps found in /tmp/cores/"
fi

echo $TEST_EXIT_CODE > ~/test-exitcode.txt

echo "=========================================="
echo "Test run complete (exit code: $TEST_EXIT_CODE)"
echo "=========================================="

exit $TEST_EXIT_CODE
REMOTE_SCRIPT

TEST_RESULT=$?

scp debian@$VM_IP:~/test-output.txt /tmp/ || true
scp debian@$VM_IP:~/test-exitcode.txt /tmp/ || true

if [ $TEST_RESULT -ne 0 ]; then
    echo "Tests failed with exit code $TEST_RESULT"
    exit $TEST_RESULT
fi

echo "All tests passed!"
