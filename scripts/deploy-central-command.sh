#!/bin/bash
# Deploy Central Command to VPS
# Usage: ./scripts/deploy-central-command.sh

set -e

VPS_HOST="178.156.162.116"
VPS_USER="root"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Deploying Central Command to VPS ==="

# Build frontend
echo "Building frontend..."
cd "$REPO_ROOT/mcp-server/central-command/frontend"
npm run build

# Deploy backend
echo "Deploying backend files..."
rsync -avz --delete \
    "$REPO_ROOT/mcp-server/central-command/backend/" \
    "$VPS_USER@$VPS_HOST:/opt/mcp-server/dashboard_api_mount/"

# Deploy main.py and server.py
echo "Deploying main.py and server.py..."
rsync -avz \
    "$REPO_ROOT/mcp-server/main.py" \
    "$REPO_ROOT/mcp-server/server.py" \
    "$VPS_USER@$VPS_HOST:/opt/mcp-server/app/"

# Deploy frontend
echo "Deploying frontend..."
rsync -avz --delete \
    "$REPO_ROOT/mcp-server/central-command/frontend/dist/" \
    "$VPS_USER@$VPS_HOST:/opt/mcp-server/frontend_dist/"

# Fix permissions and restart
echo "Fixing permissions and restarting services..."
ssh "$VPS_USER@$VPS_HOST" << 'EOF'
    chmod -R 755 /opt/mcp-server/dashboard_api_mount/
    chmod 644 /opt/mcp-server/app/main.py /opt/mcp-server/app/server.py
    cd /opt/mcp-server
    docker compose restart mcp-server frontend
EOF

# Verify
echo "Waiting for services to start..."
sleep 15
if ssh "$VPS_USER@$VPS_HOST" "curl -sf http://localhost:8000/health" > /dev/null; then
    echo "=== Deployment successful! ==="
else
    echo "=== Warning: Health check failed ==="
    exit 1
fi
