#!/bin/bash
set -euo pipefail

# ============================================================================
# OsirisCare Central Command — Production Deploy Script
# Deploys backend + frontend with validation at every step.
# Usage: ssh root@VPS 'bash /opt/mcp-flake/mcp-server/scripts/deploy.sh'
# ============================================================================

REPO="/opt/msp-flake"
PROD="/opt/mcp-server"
LOG_PREFIX="[deploy]"

log()  { echo "$LOG_PREFIX $1"; }
fail() { echo "$LOG_PREFIX FAILED: $1" >&2; exit 1; }

# ── Step 1: Pull latest code ──────────────────────────────────────────────
log "Pulling latest code..."
cd "$REPO" && git pull origin main || fail "git pull"
COMMIT=$(git rev-parse --short HEAD)
log "At commit $COMMIT"

# ── Step 2: Backend deploy ────────────────────────────────────────────────
log "Deploying backend..."
[ -f "$REPO/mcp-server/main.py" ] || fail "main.py not found in repo"
cp "$REPO/mcp-server/main.py" "$PROD/main.py"
rsync -a --delete "$REPO/mcp-server/central-command/backend/" "$PROD/app/dashboard_api/"
rsync -a --delete "$REPO/mcp-server/central-command/backend/" "$PROD/dashboard_api_mount/"
log "Backend synced (main.py + dashboard_api)"

# ── Step 3: Frontend build ────────────────────────────────────────────────
log "Building frontend..."
cd "$REPO/mcp-server/central-command/frontend"
[ -d "node_modules/.bin" ] || { log "Installing npm deps..."; npm install --silent; }
PATH="./node_modules/.bin:$PATH" tsc || fail "TypeScript compilation"
PATH="./node_modules/.bin:$PATH" vite build || fail "Vite build"
[ -f "dist/index.html" ] || fail "Build produced no index.html"
log "Frontend built"

# ── Step 4: Frontend deploy (both paths for safety) ───────────────────────
log "Deploying frontend..."
cp -r dist/* "$PROD/frontend_dist/" || fail "Copy to frontend_dist"
cp -r dist/* "$PROD/frontend-dist/" 2>/dev/null || true
log "Frontend deployed to $PROD/frontend_dist/"

# ── Step 5: Restart containers ────────────────────────────────────────────
log "Restarting containers..."
cd "$PROD"
docker compose up -d --build mcp-server || fail "mcp-server restart"
docker restart central-command || fail "central-command restart"

# ── Step 6: Health verification ───────────────────────────────────────────
log "Verifying health..."
sleep 5
HEALTH=$(docker exec mcp-server curl -sf http://localhost:8000/health 2>/dev/null || echo "FAIL")
if [ "$HEALTH" = '{"status":"ok"}' ]; then
    log "Backend healthy"
else
    fail "Backend health check failed: $HEALTH"
fi

# Verify frontend serves HTML
FE_CHECK=$(docker exec central-command curl -sf http://localhost:80/ 2>/dev/null | head -1 || echo "FAIL")
if echo "$FE_CHECK" | grep -q 'DOCTYPE'; then
    log "Frontend healthy"
else
    fail "Frontend health check failed"
fi

log "Deploy complete — commit $COMMIT"
