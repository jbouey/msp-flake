#!/bin/bash
#===============================================================================
# OsirisCare Linux Sensor - Installation Script
#===============================================================================
# Usage: curl -sSL https://appliance:8443/sensor/install.sh | bash -s -- \
#            --sensor-id <id> --api-key <key> --appliance-url <url>
#===============================================================================

set -euo pipefail

# Defaults
INSTALL_DIR="/opt/osiriscare"
CONFIG_DIR="/etc/osiriscare"
LOG_DIR="/var/log"
STATE_DIR="/var/lib/osiriscare"
SERVICE_NAME="osiriscare-sensor"

# Parse arguments
SENSOR_ID=""
API_KEY=""
APPLIANCE_URL=""

usage() {
    cat <<EOF
OsirisCare Linux Sensor Installer

Usage: $0 [options]

Options:
    --sensor-id <id>       Unique sensor identifier (required)
    --api-key <key>        API key for appliance authentication (required)
    --appliance-url <url>  Appliance API URL (required)
    --help                 Show this help message

Example:
    $0 --sensor-id srv-001 --api-key abc123 --appliance-url https://192.168.88.246:8443

EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sensor-id)
            SENSOR_ID="$2"
            shift 2
            ;;
        --api-key)
            API_KEY="$2"
            shift 2
            ;;
        --appliance-url)
            APPLIANCE_URL="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        *)
            echo "Unknown option: $1"
            usage
            ;;
    esac
done

# Validate required arguments
if [[ -z "$SENSOR_ID" || -z "$API_KEY" || -z "$APPLIANCE_URL" ]]; then
    echo "Error: Missing required arguments"
    usage
fi

# Check for root privileges
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root"
    exit 1
fi

echo "=================================================="
echo "OsirisCare Linux Sensor Installer"
echo "=================================================="
echo "Sensor ID: $SENSOR_ID"
echo "Appliance: $APPLIANCE_URL"
echo ""

# Create directories
echo "[1/5] Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$STATE_DIR"

# Download sensor script (or copy from stdin if piped)
echo "[2/5] Installing sensor script..."
SENSOR_URL="${APPLIANCE_URL}/sensor/osiriscare-sensor.sh"

if curl -sSL --insecure -o "$INSTALL_DIR/osiriscare-sensor.sh" "$SENSOR_URL" 2>/dev/null; then
    echo "    Downloaded from appliance"
else
    echo "    Warning: Could not download from appliance, using embedded version"
    # Fallback: the script should be included in the install bundle
    cat > "$INSTALL_DIR/osiriscare-sensor.sh" << 'SENSOR_SCRIPT'
#!/bin/bash
echo "Error: Sensor script not properly installed. Please re-run installation."
exit 1
SENSOR_SCRIPT
fi

chmod 755 "$INSTALL_DIR/osiriscare-sensor.sh"

# Create configuration
echo "[3/5] Creating configuration..."
cat > "$CONFIG_DIR/sensor.env" << EOF
# OsirisCare Sensor Configuration
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

SENSOR_ID="${SENSOR_ID}"
APPLIANCE_URL="${APPLIANCE_URL}"
API_KEY="${API_KEY}"

# Optional settings
CHECK_INTERVAL=10
DEBUG=0
EOF

chmod 600 "$CONFIG_DIR/sensor.env"

# Create systemd service
echo "[4/5] Installing systemd service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=OsirisCare Linux Sensor
Documentation=https://docs.osiriscare.net/sensor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${INSTALL_DIR}/osiriscare-sensor.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# Security hardening
NoNewPrivileges=false
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${LOG_DIR} ${STATE_DIR}
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
echo "[5/5] Starting sensor service..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1
systemctl start "${SERVICE_NAME}"

# Verify service started
sleep 2
if systemctl is-active "${SERVICE_NAME}" >/dev/null 2>&1; then
    echo ""
    echo "=================================================="
    echo "Installation Complete!"
    echo "=================================================="
    echo "Sensor ID:    $SENSOR_ID"
    echo "Service:      $SERVICE_NAME"
    echo "Status:       $(systemctl is-active ${SERVICE_NAME})"
    echo "Log file:     ${LOG_DIR}/osiriscare-sensor.log"
    echo ""
    echo "Commands:"
    echo "  View logs:    journalctl -u ${SERVICE_NAME} -f"
    echo "  Stop:         systemctl stop ${SERVICE_NAME}"
    echo "  Restart:      systemctl restart ${SERVICE_NAME}"
    echo "  Uninstall:    curl -sSL ${APPLIANCE_URL}/sensor/uninstall.sh | bash"
    echo ""
else
    echo ""
    echo "Warning: Service may not have started correctly."
    echo "Check: journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
fi
