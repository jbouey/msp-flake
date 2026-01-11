#!/bin/bash
# =============================================================================
# Run database migrations for MCP Server
# =============================================================================
# Usage: ./scripts/run-migrations.sh [database_url]
#
# If no database_url is provided, uses DATABASE_URL from .env or defaults to local
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MIGRATIONS_DIR="${PROJECT_DIR}/central-command/backend/migrations"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Load .env if exists
if [[ -f "${PROJECT_DIR}/.env" ]]; then
    log_info "Loading .env file..."
    export $(grep -v '^#' "${PROJECT_DIR}/.env" | xargs)
fi

# Get database URL
if [[ -n "${1:-}" ]]; then
    DB_URL="$1"
elif [[ -n "${DATABASE_URL:-}" ]]; then
    DB_URL="$DATABASE_URL"
else
    # Construct from individual vars
    DB_HOST="${POSTGRES_HOST:-localhost}"
    DB_PORT="${POSTGRES_PORT:-5432}"
    DB_USER="${POSTGRES_USER:-mcp}"
    DB_PASS="${POSTGRES_PASSWORD:-}"
    DB_NAME="${POSTGRES_DB:-mcp}"
    DB_URL="postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

log_info "Database: ${DB_URL%%:*}://***@${DB_URL##*@}"

# Check for psql
if ! command -v psql &> /dev/null; then
    log_error "psql not found. Install PostgreSQL client."
    echo "  macOS: brew install postgresql"
    echo "  Linux: apt install postgresql-client"
    exit 1
fi

# Run migrations
log_info "Running migrations from ${MIGRATIONS_DIR}..."

for migration in $(ls -1 "${MIGRATIONS_DIR}"/*.sql | sort); do
    migration_name=$(basename "$migration")
    log_info "Running: ${migration_name}"

    if PGPASSWORD="${POSTGRES_PASSWORD}" psql "${DB_URL}" -f "$migration" 2>&1; then
        log_info "  Success: ${migration_name}"
    else
        log_warn "  Warning: ${migration_name} may have already been applied"
    fi
done

log_info "Migrations complete!"
echo ""
echo "Next steps:"
echo "  1. Restart the MCP server"
echo "  2. Login with:"
echo "     Username: admin"
echo "     Password: (from ADMIN_INITIAL_PASSWORD in .env)"
