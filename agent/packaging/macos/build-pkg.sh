#!/bin/bash
# Build macOS .pkg installer for OsirisCare Agent
#
# Usage: ./build-pkg.sh [version]
# Prerequisites: bin/osiris-agent-darwin must exist (run make build-darwin first)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VERSION="${1:-0.3.0}"

BINARY="${AGENT_DIR}/bin/osiris-agent-darwin"
if [ ! -f "${BINARY}" ]; then
    echo "Error: ${BINARY} not found. Run 'make build-darwin' first."
    exit 1
fi

# Create staging directory
STAGING=$(mktemp -d)
PAYLOAD="${STAGING}/payload"
SCRIPTS="${STAGING}/scripts"

mkdir -p "${PAYLOAD}/Library/OsirisCare"
mkdir -p "${SCRIPTS}"

# Copy binary
cp "${BINARY}" "${PAYLOAD}/Library/OsirisCare/osiris-agent"
chmod 755 "${PAYLOAD}/Library/OsirisCare/osiris-agent"

# Copy launchd plist into payload (postinstall moves it to /Library/LaunchDaemons)
cp "${SCRIPT_DIR}/com.osiriscare.agent.plist" "${PAYLOAD}/Library/OsirisCare/"

# Copy install scripts
cp "${SCRIPT_DIR}/preinstall" "${SCRIPTS}/preinstall"
cp "${SCRIPT_DIR}/postinstall" "${SCRIPTS}/postinstall"
chmod 755 "${SCRIPTS}/preinstall" "${SCRIPTS}/postinstall"

# Build the .pkg
OUTPUT="${AGENT_DIR}/bin/osiris-agent-${VERSION}.pkg"
pkgbuild \
    --root "${PAYLOAD}" \
    --scripts "${SCRIPTS}" \
    --identifier "com.osiriscare.agent" \
    --version "${VERSION}" \
    --install-location "/" \
    "${OUTPUT}"

# Cleanup
rm -rf "${STAGING}"

echo ""
echo "Package built: ${OUTPUT}"
echo "Install with: sudo installer -pkg ${OUTPUT} -target /"
echo "Or distribute via MDM (Jamf, Mosyle, Kandji, etc.)"
