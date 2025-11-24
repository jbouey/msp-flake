#!/bin/bash
# VM Status Check Script - Run on the remote Mac (174.178.63.139)
# This checks VirtualBox VMs and optionally starts them

echo "=== VirtualBox VM Status Check ==="
echo "Date: $(date)"
echo ""

# List all VMs
echo "--- All VMs ---"
VBoxManage list vms

echo ""
echo "--- Running VMs ---"
VBoxManage list runningvms

echo ""

# Check if win-test-vm exists and get its status
WIN_VM_NAME="win-test-vm"
if VBoxManage list vms | grep -q "$WIN_VM_NAME"; then
    echo "--- Windows VM ($WIN_VM_NAME) Details ---"
    VBoxManage showvminfo "$WIN_VM_NAME" | grep -E "State:|Name:|Guest OS:|Memory size:|Number of CPUs:"

    # Check if running
    if ! VBoxManage list runningvms | grep -q "$WIN_VM_NAME"; then
        echo ""
        echo "Windows VM is NOT running."

        if [ "$1" = "--start" ]; then
            echo "Starting Windows VM..."
            VBoxManage startvm "$WIN_VM_NAME" --type headless
            echo "Waiting 60 seconds for VM to boot..."
            sleep 60

            # Check WinRM port
            echo "Checking WinRM port (5985)..."
            nc -zv 192.168.56.10 5985 && echo "WinRM is available!" || echo "WinRM not responding yet"
        else
            echo "Run with --start to start the VM: ./check_vms.sh --start"
        fi
    else
        echo "Windows VM is RUNNING."

        # Check WinRM port
        echo ""
        echo "Checking WinRM port (5985)..."
        nc -zv 192.168.56.10 5985 2>&1 && echo "WinRM is available!" || echo "WinRM not responding"
    fi
else
    echo "Windows VM ($WIN_VM_NAME) not found!"
    echo ""
    echo "To create the Windows VM, see docs/WINDOWS_TEST_SETUP.md"
fi

echo ""
echo "--- Network Check ---"
echo "VirtualBox host-only networks:"
VBoxManage list hostonlyifs 2>/dev/null | grep -E "Name:|IPAddress:|NetworkMask:" || echo "No host-only networks found"

echo ""
echo "=== Done ==="
