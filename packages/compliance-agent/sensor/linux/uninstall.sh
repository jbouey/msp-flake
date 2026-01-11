#!/bin/bash
#===============================================================================
# OsirisCare Linux Sensor - Uninstallation Script
#===============================================================================
# Usage: curl -sSL https://appliance:8443/sensor/uninstall.sh | bash
#        curl -sSL https://appliance:8443/sensor/uninstall.sh | bash -s -- --force
#===============================================================================

set -euo pipefail

INSTALL_DIR="/opt/osiriscare"
CONFIG_DIR="/etc/osiriscare"
STATE_DIR="/var/lib/osiriscare"
SERVICE_NAME="osiriscare-sensor"
LOG_FILE="/var/log/osiriscare-sensor.log"
FORCE=0
REMOVE_LOGS=0

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force|-f)
            FORCE=1
            REMOVE_LOGS=1
            shift
            ;;
        --keep-logs)
            REMOVE_LOGS=0
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# Check for root privileges
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root"
    exit 1
fi

echo "=================================================="
echo "OsirisCare Linux Sensor Uninstaller"
echo "=================================================="

# Confirm uninstallation (skip if --force)
if [[ "$FORCE" != "1" ]]; then
    read -p "This will remove the OsirisCare sensor. Continue? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Uninstallation cancelled."
        exit 0
    fi
fi

echo ""
echo "[1/5] Stopping service..."
if systemctl is-active "${SERVICE_NAME}" >/dev/null 2>&1; then
    systemctl stop "${SERVICE_NAME}"
    echo "    Service stopped"
else
    echo "    Service not running"
fi

echo "[2/5] Disabling service..."
if systemctl is-enabled "${SERVICE_NAME}" >/dev/null 2>&1; then
    systemctl disable "${SERVICE_NAME}" >/dev/null 2>&1
    echo "    Service disabled"
else
    echo "    Service not enabled"
fi

echo "[3/5] Removing systemd service..."
if [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    echo "    Service file removed"
else
    echo "    Service file not found"
fi

echo "[4/5] Removing installation files..."
# Remove sensor script
if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    echo "    Removed $INSTALL_DIR"
fi

# Remove configuration
if [[ -d "$CONFIG_DIR" ]]; then
    rm -rf "$CONFIG_DIR"
    echo "    Removed $CONFIG_DIR"
fi

# Remove state directory
if [[ -d "$STATE_DIR" ]]; then
    rm -rf "$STATE_DIR"
    echo "    Removed $STATE_DIR"
fi

echo "[5/5] Cleaning up logs..."
# Remove log file if --force or user confirms
if [[ "$REMOVE_LOGS" == "1" ]]; then
    if [[ -f "$LOG_FILE" ]]; then
        rm -f "$LOG_FILE"
        echo "    Log file removed"
    fi
elif [[ "$FORCE" != "1" ]]; then
    read -p "Remove log file ($LOG_FILE)? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if [[ -f "$LOG_FILE" ]]; then
            rm -f "$LOG_FILE"
            echo "    Log file removed"
        fi
    else
        echo "    Log file preserved"
    fi
else
    echo "    Log file preserved"
fi

echo ""
echo "=================================================="
echo "Uninstallation Complete!"
echo "=================================================="
echo "The OsirisCare sensor has been removed from this system."
echo ""
