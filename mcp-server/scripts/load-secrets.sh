#!/bin/bash
# =============================================================================
# OsirisCare 1Password Secret Loader
# =============================================================================
# Loads secrets from 1Password and creates/updates .env file
#
# Prerequisites:
#   - 1Password CLI installed: brew install 1password-cli
#   - Signed in: op signin
#   - Vault "OsirisCare" with items as documented in SECRETS_INVENTORY.md
#
# Usage:
#   ./scripts/load-secrets.sh              # Interactive mode
#   ./scripts/load-secrets.sh --production # Load production secrets
#   ./scripts/load-secrets.sh --export     # Export to stdout (for CI/CD)
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 1Password vault and item names
OP_VAULT="${OP_VAULT:-Central Command}"

# Item names in 1Password (customize to match your vault)
OP_POSTGRES="${OP_POSTGRES:-PostgreSQL}"
OP_REDIS="${OP_REDIS:-Redis}"
OP_MINIO="${OP_MINIO:-MinIO}"
OP_ANTHROPIC="${OP_ANTHROPIC:-Anthropic Key}"
OP_OPENAI="${OP_OPENAI:-OpenAI Key}"
OP_SMTP="${OP_SMTP:-SMTP}"
OP_ADMIN="${OP_ADMIN:-Admin Dashboard}"

log_info() { echo -e "${GREEN}[INFO]${NC} $1" >&2; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1" >&2; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

check_op_cli() {
    if ! command -v op &> /dev/null; then
        log_error "1Password CLI not installed"
        echo "Install with: brew install 1password-cli"
        exit 1
    fi
}

check_op_signin() {
    if ! op account list &> /dev/null; then
        log_error "Not signed in to 1Password"
        echo "Sign in with: op signin"
        exit 1
    fi
    log_info "1Password CLI authenticated"
}

get_secret() {
    local item="$1"
    local field="${2:-password}"

    # Use --reveal to actually get the secret value (required by 1Password CLI)
    local value
    value=$(op item get "$item" --vault "$OP_VAULT" --field "$field" --reveal 2>/dev/null)

    if [[ -n "$value" ]]; then
        echo "$value"
        return
    fi

    # Try common variations for specific field types
    case "$field" in
        credential)
            for try_field in "credential" "api_key" "api key" "key" "password"; do
                value=$(op item get "$item" --vault "$OP_VAULT" --field "$try_field" --reveal 2>/dev/null)
                if [[ -n "$value" ]]; then echo "$value"; return; fi
            done
            ;;
        server)
            for try_field in "server" "host" "hostname" "url"; do
                value=$(op item get "$item" --vault "$OP_VAULT" --field "$try_field" --reveal 2>/dev/null)
                if [[ -n "$value" ]]; then echo "$value"; return; fi
            done
            ;;
    esac

    echo ""
}

get_secret_or_default() {
    local item="$1"
    local field="${2:-password}"
    local default="$3"

    local value
    value=$(get_secret "$item" "$field")

    if [[ -z "$value" ]]; then
        echo "$default"
    else
        echo "$value"
    fi
}

generate_env() {
    log_info "Loading secrets from 1Password vault: ${OP_VAULT}/${OP_ENV}"

    # PostgreSQL
    local pg_user pg_pass pg_db
    pg_user=$(get_secret_or_default "$OP_POSTGRES" "username" "mcp")
    pg_pass=$(get_secret "$OP_POSTGRES" "password")
    pg_db=$(get_secret_or_default "$OP_POSTGRES" "database" "mcp")

    # Redis
    local redis_pass
    redis_pass=$(get_secret "$OP_REDIS" "password")

    # MinIO
    local minio_user minio_pass minio_access minio_secret
    minio_user=$(get_secret_or_default "$OP_MINIO" "username" "minio")
    minio_pass=$(get_secret "$OP_MINIO" "password")
    minio_access=$(get_secret_or_default "$OP_MINIO" "access_key" "$minio_user")
    minio_secret=$(get_secret_or_default "$OP_MINIO" "secret_key" "$minio_pass")

    # LLM APIs
    local anthropic_key openai_key
    anthropic_key=$(get_secret "$OP_ANTHROPIC" "credential")
    openai_key=$(get_secret "$OP_OPENAI" "credential")

    # SMTP
    local smtp_host smtp_user smtp_pass
    smtp_host=$(get_secret_or_default "$OP_SMTP" "server" "mail.privateemail.com")
    smtp_user=$(get_secret "$OP_SMTP" "username")
    smtp_pass=$(get_secret "$OP_SMTP" "password")

    # Admin
    local admin_pass
    admin_pass=$(get_secret "$OP_ADMIN" "password")

    # Generate .env content
    cat << EOF
# OsirisCare Environment - Generated from 1Password
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Vault: ${OP_VAULT}/${OP_ENV}
# DO NOT COMMIT THIS FILE

# Database
POSTGRES_USER=${pg_user}
POSTGRES_PASSWORD=${pg_pass}
POSTGRES_DB=${pg_db}

# Redis
REDIS_PASSWORD=${redis_pass}

# MinIO
MINIO_ROOT_USER=${minio_user}
MINIO_ROOT_PASSWORD=${minio_pass}
MINIO_ACCESS_KEY=${minio_access}
MINIO_SECRET_KEY=${minio_secret}
MINIO_BUCKET=evidence-worm

# LLM APIs
ANTHROPIC_API_KEY=${anthropic_key}
OPENAI_API_KEY=${openai_key}
OPENAI_MODEL=gpt-4o
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022

# SMTP
SMTP_HOST=${smtp_host}
SMTP_PORT=587
SMTP_USER=${smtp_user}
SMTP_PASSWORD=${smtp_pass}
SMTP_FROM=alerts@osiriscare.net
ALERT_EMAIL=administrator@osiriscare.net

# Admin
ADMIN_INITIAL_PASSWORD=${admin_pass}

# Server
LOG_LEVEL=INFO
RATE_LIMIT_REQUESTS=10
RATE_LIMIT_WINDOW=300
ORDER_TTL_SECONDS=900
EOF
}

main() {
    local mode="${1:-interactive}"

    check_op_cli

    case "$mode" in
        --export)
            check_op_signin
            OP_ENV="Production"
            generate_env
            ;;
        --production)
            check_op_signin
            OP_ENV="Production"
            log_info "Loading Production secrets..."
            generate_env > "$ENV_FILE"
            chmod 600 "$ENV_FILE"
            log_info "Secrets written to ${ENV_FILE}"
            log_warn "Remember to restart services: docker compose restart"
            ;;
        --development)
            check_op_signin
            OP_ENV="Development"
            log_info "Loading Development secrets..."
            generate_env > "$ENV_FILE"
            chmod 600 "$ENV_FILE"
            log_info "Secrets written to ${ENV_FILE}"
            ;;
        *)
            echo "OsirisCare 1Password Secret Loader"
            echo ""
            echo "Usage:"
            echo "  $0 --production    Load production secrets to .env"
            echo "  $0 --development   Load development secrets to .env"
            echo "  $0 --export        Export secrets to stdout (for CI/CD)"
            echo ""
            echo "Prerequisites:"
            echo "  1. Install 1Password CLI: brew install 1password-cli"
            echo "  2. Sign in: op signin"
            echo "  3. Create vault '${OP_VAULT}' with items:"
            echo "     - ${OP_VAULT}/Production/PostgreSQL"
            echo "     - ${OP_VAULT}/Production/Redis"
            echo "     - ${OP_VAULT}/Production/MinIO"
            echo "     - ${OP_VAULT}/Production/Anthropic"
            echo "     - ${OP_VAULT}/Production/SMTP"
            echo "     - ${OP_VAULT}/Production/AdminDashboard"
            echo ""
            echo "See docs/security/SECRETS_INVENTORY.md for details"
            ;;
    esac
}

main "$@"
