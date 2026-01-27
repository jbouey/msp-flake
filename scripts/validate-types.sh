#!/bin/bash
# =============================================================================
# Full-Stack Type Validation Script
# =============================================================================
# Runs type checking across Python backend and TypeScript frontend.
# Exit code 0 = all checks pass, non-zero = errors found.
#
# Usage:
#   ./scripts/validate-types.sh           # Run all checks
#   ./scripts/validate-types.sh --quick   # Quick mode (essential files only)
#   ./scripts/validate-types.sh --fix     # Show fix suggestions
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

QUICK_MODE=false
SHOW_FIX=false
ERRORS_FOUND=0

# Parse arguments
for arg in "$@"; do
    case $arg in
        --quick)
            QUICK_MODE=true
            shift
            ;;
        --fix)
            SHOW_FIX=true
            shift
            ;;
    esac
done

echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}           Full-Stack Type Validation                          ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# =============================================================================
# 1. Python Type Check (compliance-agent)
# =============================================================================
echo -e "${YELLOW}[1/4] Python Type Check (compliance-agent)${NC}"
cd "$ROOT_DIR/packages/compliance-agent"

if [ ! -f "venv/bin/activate" ]; then
    echo -e "${RED}  ✗ venv not found. Run: python -m venv venv && pip install -e .[dev]${NC}"
    ERRORS_FOUND=$((ERRORS_FOUND + 1))
else
    source venv/bin/activate

    if ! command -v mypy &> /dev/null; then
        echo -e "${RED}  ✗ mypy not installed. Run: pip install mypy types-PyYAML${NC}"
        ERRORS_FOUND=$((ERRORS_FOUND + 1))
    else
        if $QUICK_MODE; then
            # Quick mode: only check core type files (strict mode)
            MYPY_FILES="src/compliance_agent/_types.py src/compliance_agent/models.py"
            echo "  Checking core type files only..."
        else
            MYPY_FILES="src/compliance_agent/"
            echo "  Checking all files..."
        fi

        # Run mypy with project config
        if mypy $MYPY_FILES --config-file pyproject.toml --no-error-summary 2>&1 | tee /tmp/mypy_output.txt; then
            echo -e "${GREEN}  ✓ Python type check passed${NC}"
        else
            ERROR_COUNT=$(grep -c "error:" /tmp/mypy_output.txt || echo "0")
            echo -e "${RED}  ✗ Python type check failed ($ERROR_COUNT errors)${NC}"
            if $SHOW_FIX; then
                echo ""
                echo "  First 10 errors:"
                head -20 /tmp/mypy_output.txt | sed 's/^/    /'
            fi
            ERRORS_FOUND=$((ERRORS_FOUND + 1))
        fi
    fi
    deactivate
fi
echo ""

# =============================================================================
# 2. Python Type Check (mcp-server)
# =============================================================================
echo -e "${YELLOW}[2/4] Python Type Check (mcp-server)${NC}"
cd "$ROOT_DIR/mcp-server"

if [ ! -f "venv/bin/activate" ]; then
    echo -e "${RED}  ✗ venv not found${NC}"
    ERRORS_FOUND=$((ERRORS_FOUND + 1))
else
    source venv/bin/activate

    if ! command -v mypy &> /dev/null; then
        echo -e "${YELLOW}  ⚠ mypy not installed (skipping)${NC}"
    else
        if $QUICK_MODE; then
            MYPY_FILES="central-command/backend/models.py database/models.py"
            echo "  Checking essential files only..."
        else
            MYPY_FILES="central-command/backend/ database/"
            echo "  Checking all backend files..."
        fi

        if mypy $MYPY_FILES --ignore-missing-imports 2>&1 | tee /tmp/mypy_mcp_output.txt; then
            echo -e "${GREEN}  ✓ MCP server type check passed${NC}"
        else
            ERROR_COUNT=$(grep -c "error:" /tmp/mypy_mcp_output.txt || echo "0")
            echo -e "${RED}  ✗ MCP server type check failed ($ERROR_COUNT errors)${NC}"
            ERRORS_FOUND=$((ERRORS_FOUND + 1))
        fi
    fi
    deactivate
fi
echo ""

# =============================================================================
# 3. TypeScript Type Check (frontend)
# =============================================================================
echo -e "${YELLOW}[3/4] TypeScript Type Check (frontend)${NC}"
cd "$ROOT_DIR/mcp-server/central-command/frontend"

if [ ! -d "node_modules" ]; then
    echo -e "${RED}  ✗ node_modules not found. Run: npm install${NC}"
    ERRORS_FOUND=$((ERRORS_FOUND + 1))
else
    if [ ! -f "node_modules/.bin/tsc" ]; then
        echo -e "${RED}  ✗ TypeScript not installed. Run: npm install typescript${NC}"
        ERRORS_FOUND=$((ERRORS_FOUND + 1))
    else
        echo "  Running tsc --noEmit..."
        if ./node_modules/.bin/tsc --noEmit 2>&1 | tee /tmp/tsc_output.txt; then
            echo -e "${GREEN}  ✓ TypeScript type check passed${NC}"
        else
            ERROR_COUNT=$(wc -l < /tmp/tsc_output.txt | tr -d ' ')
            echo -e "${RED}  ✗ TypeScript type check failed ($ERROR_COUNT lines of output)${NC}"
            if $SHOW_FIX; then
                head -20 /tmp/tsc_output.txt | sed 's/^/    /'
            fi
            ERRORS_FOUND=$((ERRORS_FOUND + 1))
        fi
    fi
fi
echo ""

# =============================================================================
# 4. Proto File Sync Check
# =============================================================================
echo -e "${YELLOW}[4/4] Proto File Sync Check${NC}"
cd "$ROOT_DIR"

PROTO_CANONICAL="proto/compliance.proto"
PROTO_AGENT="agent/proto/compliance.proto"

if [ ! -f "$PROTO_CANONICAL" ]; then
    echo -e "${RED}  ✗ Canonical proto not found: $PROTO_CANONICAL${NC}"
    ERRORS_FOUND=$((ERRORS_FOUND + 1))
elif [ ! -f "$PROTO_AGENT" ]; then
    echo -e "${RED}  ✗ Agent proto not found: $PROTO_AGENT${NC}"
    ERRORS_FOUND=$((ERRORS_FOUND + 1))
else
    if diff -q "$PROTO_CANONICAL" "$PROTO_AGENT" > /dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Proto files in sync${NC}"
    else
        echo -e "${RED}  ✗ Proto files out of sync${NC}"
        if $SHOW_FIX; then
            echo "    Differences:"
            diff "$PROTO_CANONICAL" "$PROTO_AGENT" | head -20 | sed 's/^/    /'
            echo ""
            echo "    Fix: cp $PROTO_CANONICAL $PROTO_AGENT"
        fi
        ERRORS_FOUND=$((ERRORS_FOUND + 1))
    fi
fi
echo ""

# =============================================================================
# Summary
# =============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
if [ $ERRORS_FOUND -eq 0 ]; then
    echo -e "${GREEN}✓ All type checks passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ $ERRORS_FOUND check(s) failed${NC}"
    echo ""
    echo "Run with --fix to see error details and suggestions."
    exit 1
fi
