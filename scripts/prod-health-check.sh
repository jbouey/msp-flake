#!/bin/bash
# =============================================================================
# Production Health Check Script
# =============================================================================
# Verifies production readiness items identified in PRODUCTION_READINESS_AUDIT.md
# Run periodically (daily recommended) to catch regressions.
#
# Usage:
#   ./scripts/prod-health-check.sh           # Full check
#   ./scripts/prod-health-check.sh --quick   # Quick check (local only)
#   ./scripts/prod-health-check.sh --ci      # CI mode (exit 1 on warnings)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configuration
VPS_HOST="178.156.162.116"
APPLIANCE_IP="192.168.88.246"
API_DOMAIN="api.osiriscare.net"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

QUICK_MODE=false
CI_MODE=false
ERRORS=0
WARNINGS=0

# Parse arguments
for arg in "$@"; do
    case $arg in
        --quick) QUICK_MODE=true ;;
        --ci) CI_MODE=true ;;
    esac
done

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}           Production Health Check                              ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# =============================================================================
# Helper Functions
# =============================================================================

check_pass() {
    echo -e "${GREEN}  ✓ $1${NC}"
}

check_fail() {
    echo -e "${RED}  ✗ $1${NC}"
    ERRORS=$((ERRORS + 1))
}

check_warn() {
    echo -e "${YELLOW}  ⚠ $1${NC}"
    WARNINGS=$((WARNINGS + 1))
}

ssh_check() {
    local host=$1
    local cmd=$2
    local timeout=${3:-10}
    timeout "$timeout" ssh -o ConnectTimeout=5 -o BatchMode=yes "root@$host" "$cmd" 2>/dev/null
}

# =============================================================================
# 1. API Health Check
# =============================================================================
echo -e "${YELLOW}[1/7] API Health Check${NC}"

if curl -sf "https://${API_DOMAIN}/health" > /dev/null 2>&1; then
    check_pass "API responding at https://${API_DOMAIN}/health"
else
    check_fail "API not responding at https://${API_DOMAIN}/health"
fi

# Check security headers
HEADERS=$(curl -sI "https://${API_DOMAIN}/health" 2>/dev/null)
if echo "$HEADERS" | grep -q "strict-transport-security"; then
    check_pass "HSTS header present"
else
    check_fail "HSTS header missing"
fi
echo ""

# =============================================================================
# 2. TLS Certificate Check
# =============================================================================
echo -e "${YELLOW}[2/7] TLS Certificate Check${NC}"

CERT_END=$(echo | openssl s_client -connect "${API_DOMAIN}:443" -servername "${API_DOMAIN}" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
if [ -n "$CERT_END" ]; then
    CERT_EPOCH=$(date -j -f "%b %d %H:%M:%S %Y %Z" "$CERT_END" +%s 2>/dev/null || date -d "$CERT_END" +%s 2>/dev/null)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (CERT_EPOCH - NOW_EPOCH) / 86400 ))

    if [ "$DAYS_LEFT" -gt 30 ]; then
        check_pass "TLS cert valid for $DAYS_LEFT days (expires: $CERT_END)"
    elif [ "$DAYS_LEFT" -gt 14 ]; then
        check_warn "TLS cert expires in $DAYS_LEFT days (renew soon)"
    else
        check_fail "TLS cert expires in $DAYS_LEFT days (URGENT)"
    fi
else
    check_fail "Could not check TLS certificate"
fi
echo ""

# =============================================================================
# 3. Local Code Checks
# =============================================================================
echo -e "${YELLOW}[3/7] Local Code Checks${NC}"

# Proto sync check
if [ -f "$ROOT_DIR/proto/compliance.proto" ] && [ -f "$ROOT_DIR/agent/proto/compliance.proto" ]; then
    if diff -q "$ROOT_DIR/proto/compliance.proto" "$ROOT_DIR/agent/proto/compliance.proto" > /dev/null 2>&1; then
        check_pass "Proto files in sync"
    else
        check_fail "Proto files out of sync"
    fi
else
    check_warn "Proto files not found"
fi

# Check for hardcoded secrets patterns
SECRET_HITS=0
if [ -d "$ROOT_DIR/mcp-server" ]; then
    SECRET_HITS=$(grep -r "sk-[a-zA-Z0-9]\{20,\}" "$ROOT_DIR/mcp-server" --include="*.py" 2>/dev/null | grep -vc "test\|demo\|example" || true)
    SECRET_HITS=${SECRET_HITS:-0}
fi
if [ "$SECRET_HITS" -eq 0 ] 2>/dev/null; then
    check_pass "No hardcoded API keys in mcp-server"
else
    check_fail "Found $SECRET_HITS potential hardcoded secrets"
fi
echo ""

if $QUICK_MODE; then
    echo -e "${BLUE}Quick mode - skipping remote checks${NC}"
    echo ""
else

# =============================================================================
# 4. VPS Checks
# =============================================================================
echo -e "${YELLOW}[4/7] VPS Checks (${VPS_HOST})${NC}"

if ssh_check "$VPS_HOST" "echo ok" 5 > /dev/null 2>&1; then
    check_pass "VPS SSH accessible"

    # Check Docker containers
    CONTAINERS=$(ssh_check "$VPS_HOST" "docker ps --format '{{.Names}}:{{.Status}}' | grep -E 'healthy|Up'" 10 || echo "")
    if echo "$CONTAINERS" | grep -q "mcp-postgres"; then
        check_pass "PostgreSQL container running"
    else
        check_fail "PostgreSQL container not healthy"
    fi

    if echo "$CONTAINERS" | grep -q "central-command\|mcp-server"; then
        check_pass "API container running"
    else
        check_fail "API container not running"
    fi

    # Check signing key permissions
    KEY_PERMS=$(ssh_check "$VPS_HOST" "stat -c '%a' /opt/mcp-server/secrets/signing.key 2>/dev/null" 5 || echo "unknown")
    if [ "$KEY_PERMS" = "600" ]; then
        check_pass "Signing key permissions correct (600)"
    elif [ "$KEY_PERMS" = "644" ]; then
        check_fail "Signing key world-readable (644) - SECURITY ISSUE"
    else
        check_warn "Could not verify signing key permissions"
    fi

    # Check NTP
    NTP_STATUS=$(ssh_check "$VPS_HOST" "timedatectl | grep 'synchronized: yes'" 5 || echo "")
    if [ -n "$NTP_STATUS" ]; then
        check_pass "VPS NTP synchronized"
    else
        check_warn "VPS NTP not synchronized"
    fi
else
    check_warn "VPS SSH not accessible"
fi
echo ""

# =============================================================================
# 5. Appliance Checks
# =============================================================================
echo -e "${YELLOW}[5/7] Appliance Checks (${APPLIANCE_IP})${NC}"

if ssh_check "$APPLIANCE_IP" "echo ok" 5 > /dev/null 2>&1; then
    check_pass "Appliance SSH accessible"

    # Check compliance-agent service
    AGENT_STATUS=$(ssh_check "$APPLIANCE_IP" "systemctl is-active compliance-agent" 5 || echo "unknown")
    if [ "$AGENT_STATUS" = "active" ]; then
        check_pass "compliance-agent service active"
    else
        check_fail "compliance-agent service not active ($AGENT_STATUS)"
    fi

    # Check config exists
    if ssh_check "$APPLIANCE_IP" "test -f /var/lib/msp/config.yaml && echo ok" 5 | grep -q "ok"; then
        check_pass "Config file exists"
    else
        check_fail "Config file missing"
    fi

    # Check signing key permissions
    KEY_PERMS=$(ssh_check "$APPLIANCE_IP" "stat -c '%a' /var/lib/msp/signing.key 2>/dev/null" 5 || echo "unknown")
    if [ "$KEY_PERMS" = "600" ]; then
        check_pass "Appliance signing key permissions correct (600)"
    else
        check_warn "Could not verify appliance signing key permissions"
    fi

    # Check NTP
    NTP_STATUS=$(ssh_check "$APPLIANCE_IP" "timedatectl | grep 'synchronized: yes'" 5 || echo "")
    if [ -n "$NTP_STATUS" ]; then
        check_pass "Appliance NTP synchronized"
    else
        check_warn "Appliance NTP not synchronized"
    fi

    # Check DNS resolution
    DNS_OK=$(ssh_check "$APPLIANCE_IP" "nslookup ${API_DOMAIN} 2>&1 | grep -q '178.156.162.116' && echo ok" 5 || echo "")
    if [ "$DNS_OK" = "ok" ]; then
        check_pass "Appliance can resolve ${API_DOMAIN}"
    else
        check_warn "Appliance DNS resolution issue"
    fi
else
    check_warn "Appliance SSH not accessible"
fi
echo ""

# =============================================================================
# 6. Database Checks
# =============================================================================
echo -e "${YELLOW}[6/7] Database Checks${NC}"

# PostgreSQL via API (checks DB connectivity indirectly)
if curl -sf "https://${API_DOMAIN}/api/health/db" > /dev/null 2>&1; then
    check_pass "Database health endpoint OK"
elif curl -sf "https://${API_DOMAIN}/health" > /dev/null 2>&1; then
    check_pass "API healthy (implies DB working)"
else
    check_warn "Could not verify database health"
fi
echo ""

# =============================================================================
# 7. Service Connectivity
# =============================================================================
echo -e "${YELLOW}[7/7] Service Connectivity${NC}"

# Check MinIO
if ssh_check "$VPS_HOST" "docker exec mcp-minio mc ready local 2>/dev/null && echo ok" 10 | grep -q "ok"; then
    check_pass "MinIO storage accessible"
else
    check_warn "Could not verify MinIO status"
fi

# Check Redis
if ssh_check "$VPS_HOST" "docker exec msp-redis redis-cli ping 2>/dev/null" 5 | grep -q "PONG"; then
    check_pass "Redis responding"
else
    check_warn "Could not verify Redis status"
fi
echo ""

fi  # end of remote checks

# =============================================================================
# Summary
# =============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}                         Summary                                ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed!${NC}"
    EXIT_CODE=0
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}⚠ Passed with $WARNINGS warning(s)${NC}"
    if $CI_MODE; then
        EXIT_CODE=1
    else
        EXIT_CODE=0
    fi
else
    echo -e "${RED}✗ Failed with $ERRORS error(s) and $WARNINGS warning(s)${NC}"
    EXIT_CODE=1
fi

echo ""
echo "Errors:   $ERRORS"
echo "Warnings: $WARNINGS"
echo ""
echo "Full audit: docs/PRODUCTION_READINESS_AUDIT.md"

exit $EXIT_CODE
