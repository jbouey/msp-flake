#!/usr/bin/env bash
#
# Deploy MCP Server to VM
# Usage: ./deploy.sh [ssh_port]
#

set -euo pipefail

SSH_PORT="${1:-4445}"
SSH_HOST="root@localhost"

echo "=== Deploying MCP Server to VM ==="
echo "SSH: ${SSH_HOST}:${SSH_PORT}"
echo ""

# Copy runbooks
echo "[1/3] Copying runbooks..."
ssh -p ${SSH_PORT} ${SSH_HOST} "mkdir -p /var/lib/mcp-server/runbooks"
scp -P ${SSH_PORT} runbooks/*.yaml ${SSH_HOST}:/var/lib/mcp-server/runbooks/
echo "✓ Runbooks deployed"

# Copy server code
echo "[2/3] Copying server.py..."
scp -P ${SSH_PORT} server.py ${SSH_HOST}:/var/lib/mcp-server/server.py
echo "✓ Server code deployed"

# Update systemd service and restart
echo "[3/3] Updating systemd service..."
ssh -p ${SSH_PORT} ${SSH_HOST} << 'REMOTE'
# Create systemd service override
mkdir -p /etc/systemd/system/mcp-server.service.d

cat > /etc/systemd/system/mcp-server.service.d/override.conf << 'SERVICE'
[Service]
ExecStart=
ExecStart=/usr/bin/env python3 /var/lib/mcp-server/server.py
WorkingDirectory=/var/lib/mcp-server
SERVICE

# Reload and restart
systemctl daemon-reload
systemctl restart mcp-server
systemctl status mcp-server --no-pager

echo ""
echo "✓ MCP Server deployed and restarted"
REMOTE

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Test endpoints:"
echo "  curl http://localhost:8001/health"
echo "  curl http://localhost:8001/runbooks"
echo ""
echo "View logs:"
echo "  ssh -p ${SSH_PORT} ${SSH_HOST} journalctl -u mcp-server -f"
