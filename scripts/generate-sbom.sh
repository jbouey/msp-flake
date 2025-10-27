#!/usr/bin/env bash
# Generate SBOM for container image or directory
# Usage: ./generate-sbom.sh <target>

set -euo pipefail

TARGET=${1:-}

if [ -z "$TARGET" ]; then
    echo "Usage: $0 <image:tag|directory>"
    echo ""
    echo "Examples:"
    echo "  $0 registry.example.com/infra-watcher:0.1"
    echo "  $0 /path/to/project"
    echo "  $0 ."
    exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo "SBOM Generation Tool"
echo "Software Bill of Materials for HIPAA Compliance"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Check if syft is installed
if ! command -v syft &> /dev/null; then
    echo "❌ syft is not installed"
    echo ""
    echo "Install with:"
    echo "  nix-shell -p syft"
    echo "  # or"
    echo "  curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh"
    exit 1
fi

echo "Target: $TARGET"
echo ""

# Determine if target is an image or directory
if [[ "$TARGET" == *":"* ]] || [[ "$TARGET" == *"@sha256:"* ]]; then
    echo "Target type: Container image"
    SCAN_TYPE="image"
    OUTPUT_PREFIX="sbom-$(echo "$TARGET" | tr '/:@' '-')"
else
    echo "Target type: Directory/Path"
    SCAN_TYPE="dir"
    OUTPUT_PREFIX="sbom-$(basename "$TARGET")"
fi

echo "Output prefix: $OUTPUT_PREFIX"
echo ""

# Select output formats
echo "Select SBOM formats to generate:"
echo "  1) SPDX JSON (recommended for HIPAA)"
echo "  2) CycloneDX JSON"
echo "  3) SPDX XML"
echo "  4) CycloneDX XML"
echo "  5) Syft JSON (internal format)"
echo "  6) All formats"
echo ""
read -p "Choice [1]: " FORMAT_CHOICE
FORMAT_CHOICE=${FORMAT_CHOICE:-1}

FORMATS=()

case "$FORMAT_CHOICE" in
    1) FORMATS+=("spdx-json") ;;
    2) FORMATS+=("cyclonedx-json") ;;
    3) FORMATS+=("spdx-xml") ;;
    4) FORMATS+=("cyclonedx-xml") ;;
    5) FORMATS+=("syft-json") ;;
    6) FORMATS+=("spdx-json" "cyclonedx-json" "spdx-xml" "cyclonedx-xml" "syft-json") ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "Generating SBOM(s)..."
echo ""

# Generate SBOMs
for format in "${FORMATS[@]}"; do
    OUTPUT_FILE="${OUTPUT_PREFIX}.${format}"

    echo "Format: $format"
    echo "Output: $OUTPUT_FILE"

    syft "$TARGET" -o "${format}=${OUTPUT_FILE}" --quiet

    # Get file info
    FILE_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)
    COMPONENT_COUNT=$(jq -r '.components | length' "$OUTPUT_FILE" 2>/dev/null || echo "N/A")

    echo "  ✅ Generated ($FILE_SIZE, $COMPONENT_COUNT components)"
    echo ""
done

echo "═══════════════════════════════════════════════════════════"
echo "SBOM Generation Complete"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Display summary
echo "Generated files:"
for format in "${FORMATS[@]}"; do
    OUTPUT_FILE="${OUTPUT_PREFIX}.${format}"
    if [ -f "$OUTPUT_FILE" ]; then
        ls -lh "$OUTPUT_FILE"
    fi
done

echo ""
echo "SBOM Summary:"

# Try to extract summary from SPDX JSON
if [ -f "${OUTPUT_PREFIX}.spdx-json" ]; then
    echo ""
    echo "Document Name: $(jq -r '.name' "${OUTPUT_PREFIX}.spdx-json")"
    echo "SPDX Version: $(jq -r '.spdxVersion' "${OUTPUT_PREFIX}.spdx-json")"
    echo "Created: $(jq -r '.creationInfo.created' "${OUTPUT_PREFIX}.spdx-json")"
    echo "Total Packages: $(jq -r '.packages | length' "${OUTPUT_PREFIX}.spdx-json")"

    # List top-level packages
    echo ""
    echo "Top packages:"
    jq -r '.packages[:10] | .[] | "  - \(.name) \(.versionInfo)"' "${OUTPUT_PREFIX}.spdx-json"

    # Check for known vulnerabilities (if grype is available)
    if command -v grype &> /dev/null; then
        echo ""
        read -p "Run vulnerability scan with grype? [Y/n]: " RUN_SCAN
        RUN_SCAN=${RUN_SCAN:-Y}

        if [ "$RUN_SCAN" = "Y" ] || [ "$RUN_SCAN" = "y" ]; then
            echo ""
            echo "Running vulnerability scan..."
            VULN_OUTPUT="${OUTPUT_PREFIX}.vulnerabilities.json"

            grype "sbom:${OUTPUT_PREFIX}.spdx-json" -o json > "$VULN_OUTPUT"

            CRITICAL=$(jq '[.matches[] | select(.vulnerability.severity == "Critical")] | length' "$VULN_OUTPUT")
            HIGH=$(jq '[.matches[] | select(.vulnerability.severity == "High")] | length' "$VULN_OUTPUT")
            MEDIUM=$(jq '[.matches[] | select(.vulnerability.severity == "Medium")] | length' "$VULN_OUTPUT")
            LOW=$(jq '[.matches[] | select(.vulnerability.severity == "Low")] | length' "$VULN_OUTPUT")

            echo ""
            echo "Vulnerability Summary:"
            echo "  Critical: $CRITICAL"
            echo "  High: $HIGH"
            echo "  Medium: $MEDIUM"
            echo "  Low: $LOW"

            if [ "$CRITICAL" -gt 0 ] || [ "$HIGH" -gt 0 ]; then
                echo ""
                echo "⚠️  Critical or High vulnerabilities found!"
                echo "Review: $VULN_OUTPUT"
            else
                echo ""
                echo "✅ No critical or high vulnerabilities found"
            fi
        fi
    fi
fi

echo ""
echo "Next steps:"
echo "  1. Review SBOM for compliance"
echo "  2. Attach to container image with cosign"
echo "  3. Include in compliance packet"
echo "  4. Store in evidence repository"
echo ""
echo "Commands:"
if [[ "$SCAN_TYPE" == "image" ]]; then
    echo "  # Attach SBOM to image"
    echo "  cosign attach sbom --sbom ${OUTPUT_PREFIX}.spdx-json $TARGET"
    echo ""
    echo "  # Generate attestation"
    echo "  cosign attest --predicate ${OUTPUT_PREFIX}.spdx-json --type spdx $TARGET"
fi

echo ""
echo "  # Verify SBOM integrity"
echo "  sha256sum ${OUTPUT_PREFIX}.spdx-json"
echo ""
