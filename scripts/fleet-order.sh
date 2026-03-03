#!/usr/bin/env bash
# fleet-order.sh — convenience wrapper for fleet_cli.py
# Runs on Mac, SSHes to VPS, executes inside mcp-server container.
#
# Usage:
#   ./scripts/fleet-order.sh create nixos_rebuild
#   ./scripts/fleet-order.sh create force_checkin --expires 1
#   ./scripts/fleet-order.sh create update_daemon --param binary_url=https://... --param binary_sha256=abc --param version=0.3.14
#   ./scripts/fleet-order.sh list
#   ./scripts/fleet-order.sh list --status active
#   ./scripts/fleet-order.sh cancel <uuid>

set -euo pipefail

VPS_HOST="${VPS_HOST:-root@178.156.162.116}"
CONTAINER="${FLEET_CONTAINER:-mcp-server}"
CLI_PATH="/app/dashboard_api/fleet_cli.py"

if [ $# -eq 0 ]; then
    echo "Usage: fleet-order.sh <create|list|cancel> [args...]"
    echo ""
    echo "Examples:"
    echo "  fleet-order.sh create nixos_rebuild"
    echo "  fleet-order.sh create force_checkin --expires 1"
    echo "  fleet-order.sh create update_daemon --param binary_url=https://... --param binary_sha256=abc --param version=0.3.14"
    echo "  fleet-order.sh create diagnostic --param command=agent_status"
    echo "  fleet-order.sh list"
    echo "  fleet-order.sh list --status active"
    echo "  fleet-order.sh cancel <uuid>"
    exit 1
fi

# Build the docker exec command with proper quoting
DOCKER_CMD="docker exec ${CONTAINER} python3 ${CLI_PATH}"
for arg in "$@"; do
    # Shell-escape each argument for the remote side
    DOCKER_CMD+=" $(printf '%q' "$arg")"
done

exec ssh -o ConnectTimeout=10 "${VPS_HOST}" "${DOCKER_CMD}"
