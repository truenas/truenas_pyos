#!/usr/bin/env bash

######################################################################
# Run pytest tests in the VM
######################################################################

set -eu

echo "Running pytest tests..."

# Load VM info
source /tmp/vm-info.sh

# Run tests in VM
ssh debian@$VM_IP 'bash -s' <<'REMOTE_SCRIPT'
set -eu

cd ~/truenas_pyos

echo "=========================================="
echo "Running pytest tests"
echo "=========================================="

# Install debug symbols for better crash reports
echo "Installing debug symbols..."
sudo apt-get install -y python3-dbg gdb systemd-coredump 2>&1 | grep -v "^\(Reading\|Building\|Extracting\)" || true

# Configure core dumps - bypass systemd-coredump and dump directly to files
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

# Run pytest with detailed output
echo "=========================================="
echo "Starting pytest..."
echo "=========================================="

# Set environment for core dumps
export PYTHONFAULTHANDLER=1
export PYTEST_TIMEOUT=300

# Run tests with verbose output
python3 -m pytest tests/ -v --tb=short --color=yes 2>&1 | tee /tmp/pytest-output.log

TEST_EXIT_CODE=${PIPESTATUS[0]}

# Check for core dumps
if ls /tmp/cores/core.* 1> /dev/null 2>&1; then
    echo ""
    echo "=========================================="
    echo "CORE DUMPS DETECTED"
    echo "=========================================="
    ls -lh /tmp/cores/

    # Analyze each core dump
    for core in /tmp/cores/core.*; do
        if [ -f "$core" ]; then
            echo ""
            echo "Analyzing $core..."
            # Extract binary name and PID from core filename (core.BINARY.PID.TIMESTAMP)
            binary_name=$(basename "$core" | cut -d. -f2)

            # Try to find the binary
            binary_path=$(which "$binary_name" 2>/dev/null || echo "")
            if [ -z "$binary_path" ] && [ "$binary_name" = "python3" ]; then
                binary_path=$(which python3)
            fi

            if [ -n "$binary_path" ]; then
                echo "Binary: $binary_path"
                gdb -batch -ex "thread apply all bt" "$binary_path" "$core" 2>&1 || true
            else
                echo "Could not find binary for $binary_name"
                file "$core"
            fi
        fi
    done
fi

echo ""
echo "=========================================="
echo "Test execution completed"
echo "=========================================="
echo "Exit code: $TEST_EXIT_CODE"

# Save test results
mkdir -p ~/test-results
cp /tmp/pytest-output.log ~/test-results/ || true
if ls /tmp/cores/core.* 1> /dev/null 2>&1; then
    cp /tmp/cores/core.* ~/test-results/ 2>/dev/null || true
fi

exit $TEST_EXIT_CODE
REMOTE_SCRIPT

TEST_RESULT=$?

if [ $TEST_RESULT -ne 0 ]; then
    echo "Tests failed with exit code $TEST_RESULT"
    exit $TEST_RESULT
fi

echo "All tests passed!"
