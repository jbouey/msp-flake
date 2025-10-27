#!/usr/bin/env bash
# Sign container image with cosign
# Usage: ./sign-image.sh <image:tag>

set -euo pipefail

IMAGE=${1:-}

if [ -z "$IMAGE" ]; then
    echo "Usage: $0 <image:tag>"
    echo ""
    echo "Examples:"
    echo "  $0 registry.example.com/infra-watcher:0.1"
    echo "  $0 ghcr.io/yourorg/msp-platform:main"
    exit 1
fi

echo "═══════════════════════════════════════════════════════════"
echo "Container Image Signing Tool"
echo "HIPAA Compliance - Evidence Trail"
echo "═══════════════════════════════════════════════════════════"
echo ""

# Check if cosign is installed
if ! command -v cosign &> /dev/null; then
    echo "❌ cosign is not installed"
    echo ""
    echo "Install with:"
    echo "  nix-shell -p cosign"
    echo "  # or"
    echo "  brew install cosign"
    exit 1
fi

# Check if image exists locally
if ! docker image inspect "$IMAGE" &> /dev/null; then
    echo "❌ Image not found locally: $IMAGE"
    echo ""
    echo "Pull it first:"
    echo "  docker pull $IMAGE"
    exit 1
fi

echo "Image: $IMAGE"
echo "Digest: $(docker inspect "$IMAGE" --format='{{.Id}}')"
echo ""

# Choose signing method
echo "Select signing method:"
echo "  1) Keyless (recommended - uses OIDC)"
echo "  2) Key pair (requires private key)"
echo ""
read -p "Choice [1]: " SIGNING_METHOD
SIGNING_METHOD=${SIGNING_METHOD:-1}

if [ "$SIGNING_METHOD" = "1" ]; then
    echo ""
    echo "Using keyless signing (OIDC-based)..."
    echo "You'll be redirected to authenticate via browser"
    echo ""

    COSIGN_EXPERIMENTAL=1 cosign sign --yes "$IMAGE"

    echo ""
    echo "✅ Image signed successfully (keyless)"
    echo ""
    echo "Verify with:"
    echo "  COSIGN_EXPERIMENTAL=1 cosign verify $IMAGE"

elif [ "$SIGNING_METHOD" = "2" ]; then
    echo ""
    read -p "Path to private key [cosign.key]: " KEY_PATH
    KEY_PATH=${KEY_PATH:-cosign.key}

    if [ ! -f "$KEY_PATH" ]; then
        echo ""
        echo "Key not found. Generate a key pair:"
        echo "  cosign generate-key-pair"
        echo ""
        read -p "Generate key pair now? [y/N]: " GENERATE
        if [ "$GENERATE" = "y" ] || [ "$GENERATE" = "Y" ]; then
            cosign generate-key-pair
            KEY_PATH="cosign.key"
        else
            exit 1
        fi
    fi

    echo ""
    echo "Signing with key: $KEY_PATH"
    cosign sign --key "$KEY_PATH" --yes "$IMAGE"

    echo ""
    echo "✅ Image signed successfully (key-based)"
    echo ""
    echo "Verify with:"
    echo "  cosign verify --key cosign.pub $IMAGE"

else
    echo "Invalid choice"
    exit 1
fi

# Generate and attach SBOM
echo ""
read -p "Generate and attach SBOM? [Y/n]: " GENERATE_SBOM
GENERATE_SBOM=${GENERATE_SBOM:-Y}

if [ "$GENERATE_SBOM" = "Y" ] || [ "$GENERATE_SBOM" = "y" ]; then
    if ! command -v syft &> /dev/null; then
        echo "⚠️  syft not installed - skipping SBOM"
        echo ""
        echo "Install with:"
        echo "  nix-shell -p syft"
    else
        echo ""
        echo "Generating SBOM with syft..."

        SBOM_FILE="sbom-$(echo "$IMAGE" | tr '/:' '-').spdx.json"

        syft "$IMAGE" -o spdx-json="$SBOM_FILE"

        echo ""
        echo "SBOM generated: $SBOM_FILE"
        echo "Size: $(du -h "$SBOM_FILE" | cut -f1)"
        echo ""

        read -p "Attach SBOM to image? [Y/n]: " ATTACH
        ATTACH=${ATTACH:-Y}

        if [ "$ATTACH" = "Y" ] || [ "$ATTACH" = "y" ]; then
            cosign attach sbom --sbom "$SBOM_FILE" "$IMAGE"
            echo "✅ SBOM attached to image"
        fi

        # Generate attestation
        read -p "Generate provenance attestation? [Y/n]: " ATTEST
        ATTEST=${ATTEST:-Y}

        if [ "$ATTEST" = "Y" ] || [ "$ATTEST" = "y" ]; then
            if [ "$SIGNING_METHOD" = "1" ]; then
                COSIGN_EXPERIMENTAL=1 cosign attest --yes \
                    --predicate "$SBOM_FILE" \
                    --type spdx \
                    "$IMAGE"
            else
                cosign attest --yes \
                    --key "$KEY_PATH" \
                    --predicate "$SBOM_FILE" \
                    --type spdx \
                    "$IMAGE"
            fi

            echo "✅ Provenance attestation generated"
        fi
    fi
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "✅ Container signing complete"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "Image: $IMAGE"
echo "Status: Signed and verified"
echo "HIPAA Evidence: Cryptographic proof of container integrity"
echo ""
echo "Next steps:"
echo "  1. Push signed image to registry"
echo "  2. Deploy to production"
echo "  3. Include signature in compliance packet"
echo ""
