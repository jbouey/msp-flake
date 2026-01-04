#!/bin/bash
# Package compliance-agent for remote updates
#
# Usage: ./package-agent.sh [version]
# Output: compliance_agent-<version>.tar.gz
#
# The tarball contains the compliance_agent Python package
# that can be extracted to an overlay directory on appliances.

set -e

VERSION="${1:-$(date +%Y%m%d-%H%M%S)}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$(dirname "$SCRIPT_DIR")/src"
BUILD_DIR="/tmp/agent-package-build"
OUTPUT_FILE="compliance_agent-${VERSION}.tar.gz"

echo "=== Packaging Compliance Agent v${VERSION} ==="

# Clean build directory
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/compliance_agent"

# Copy source files
echo "Copying source files..."
cp -r "$SRC_DIR/compliance_agent/"* "$BUILD_DIR/compliance_agent/"

# Create version file
echo "$VERSION" > "$BUILD_DIR/compliance_agent/VERSION"

# Remove __pycache__ directories
find "$BUILD_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true

# Create tarball
echo "Creating tarball..."
cd "$BUILD_DIR"
tar -czf "$SCRIPT_DIR/$OUTPUT_FILE" compliance_agent

# Cleanup
rm -rf "$BUILD_DIR"

echo ""
echo "=== Package created: $SCRIPT_DIR/$OUTPUT_FILE ==="
echo ""
echo "To upload to Central Command:"
echo "  scp $SCRIPT_DIR/$OUTPUT_FILE root@178.156.162.116:/opt/mcp-server/agent-packages/"
echo ""
echo "Package URL will be:"
echo "  https://api.osiriscare.net/agent-packages/$OUTPUT_FILE"
echo ""
echo "To deploy via order:"
echo '  curl -X POST "https://api.osiriscare.net/api/sites/{site}/appliances/{appliance}/orders" \'
echo '    -H "Content-Type: application/json" \'
echo "    -d '{\"order_type\":\"update_agent\",\"parameters\":{\"package_url\":\"https://api.osiriscare.net/agent-packages/$OUTPUT_FILE\",\"version\":\"$VERSION\"}}'"
