#!/bin/bash
# Zero-friction agent deploy to any SSH-accessible host
#
# Usage: ./deploy.sh <user@host> <appliance_addr>
# Example: ./deploy.sh jrelly@192.168.88.50 192.168.88.241:50051
#          ./deploy.sh root@10.0.0.5 10.0.0.1:50051

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <user@host> <appliance_addr>"
    echo "  user@host       SSH target (e.g. jrelly@192.168.88.50)"
    echo "  appliance_addr  Appliance gRPC address (e.g. 192.168.88.241:50051)"
    exit 1
fi

TARGET="$1"
APPLIANCE_ADDR="$2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "==> Detecting remote OS..."
REMOTE_OS=$(ssh -o ConnectTimeout=10 "$TARGET" 'uname -s')
REMOTE_ARCH=$(ssh -o ConnectTimeout=10 "$TARGET" 'uname -m')

echo "    OS: ${REMOTE_OS}, Arch: ${REMOTE_ARCH}"

case "${REMOTE_OS}" in
    Darwin)
        BINARY="${AGENT_DIR}/bin/osiris-agent-darwin-amd64"
        if [ "$REMOTE_ARCH" = "arm64" ]; then
            BINARY="${AGENT_DIR}/bin/osiris-agent-darwin-arm64"
        fi
        INSTALL_DIR="/Library/OsirisCare"
        DATA_DIR="/Library/Application Support/OsirisCare"
        LOG_DIR="/Library/Logs/OsirisCare"
        PLIST_SRC="${SCRIPT_DIR}/macos/com.osiriscare.agent.plist"
        ;;
    Linux)
        BINARY="${AGENT_DIR}/bin/osiris-agent-linux"
        INSTALL_DIR="/opt/osiriscare"
        DATA_DIR="/var/lib/osiriscare"
        UNIT_SRC="${SCRIPT_DIR}/linux/osiriscare-agent.service"
        ;;
    *)
        echo "Error: Unsupported OS '${REMOTE_OS}'. Use Windows GPO for Windows hosts."
        exit 1
        ;;
esac

if [ ! -f "$BINARY" ]; then
    echo "Error: Binary not found at ${BINARY}"
    echo "Run 'make build-darwin' or 'make build-linux' first."
    exit 1
fi

echo "==> Uploading binary ($(du -h "$BINARY" | awk '{print $1}'))..."
scp -o ConnectTimeout=10 "$BINARY" "${TARGET}:/tmp/osiris-agent"

echo "==> Installing..."
case "${REMOTE_OS}" in
    Darwin)
        ssh "$TARGET" "sudo bash -s" <<INSTALL_MAC
set -e
mkdir -p "${INSTALL_DIR}" "${LOG_DIR}" "${DATA_DIR}"
mv /tmp/osiris-agent "${INSTALL_DIR}/osiris-agent"
chmod 755 "${INSTALL_DIR}/osiris-agent"
chown root:wheel "${INSTALL_DIR}/osiris-agent"

# Write config
cat > "${INSTALL_DIR}/config.json" <<EOCFG
{"appliance_addr":"${APPLIANCE_ADDR}","check_interval":300,"data_dir":"${DATA_DIR}"}
EOCFG
chmod 644 "${INSTALL_DIR}/config.json"

# Version check
"${INSTALL_DIR}/osiris-agent" --version || true
echo "Binary installed."
INSTALL_MAC

        echo "==> Installing launchd plist..."
        scp "$PLIST_SRC" "${TARGET}:/tmp/com.osiriscare.agent.plist"
        ssh "$TARGET" "sudo bash -s" <<LAUNCH_MAC
launchctl unload /Library/LaunchDaemons/com.osiriscare.agent.plist 2>/dev/null || true
mv /tmp/com.osiriscare.agent.plist /Library/LaunchDaemons/com.osiriscare.agent.plist
chmod 644 /Library/LaunchDaemons/com.osiriscare.agent.plist
chown root:wheel /Library/LaunchDaemons/com.osiriscare.agent.plist
launchctl load /Library/LaunchDaemons/com.osiriscare.agent.plist
echo "Agent started via launchd."
LAUNCH_MAC
        ;;

    Linux)
        ssh "$TARGET" "sudo bash -s" <<INSTALL_LINUX
set -e
mkdir -p "${INSTALL_DIR}" "${DATA_DIR}"
mv /tmp/osiris-agent "${INSTALL_DIR}/osiris-agent"
chmod 755 "${INSTALL_DIR}/osiris-agent"

# Write config
cat > "${INSTALL_DIR}/config.json" <<EOCFG
{"appliance_addr":"${APPLIANCE_ADDR}","check_interval":300,"data_dir":"${DATA_DIR}"}
EOCFG

# Version check
"${INSTALL_DIR}/osiris-agent" --version || true
echo "Binary installed."
INSTALL_LINUX

        echo "==> Installing systemd unit..."
        scp "$UNIT_SRC" "${TARGET}:/tmp/osiriscare-agent.service"
        ssh "$TARGET" "sudo bash -s" <<LAUNCH_LINUX
mv /tmp/osiriscare-agent.service /etc/systemd/system/osiriscare-agent.service
systemctl daemon-reload
systemctl enable osiriscare-agent
systemctl restart osiriscare-agent
systemctl status osiriscare-agent --no-pager -l
echo "Agent started via systemd."
LAUNCH_LINUX
        ;;
esac

echo ""
echo "==> Deploy complete!"
echo "    Target:    ${TARGET}"
echo "    Appliance: ${APPLIANCE_ADDR}"
echo "    Agent will register via gRPC within 30 seconds."
