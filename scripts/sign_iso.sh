#!/usr/bin/env bash
#
# sign_iso.sh — Week 4 of the composed identity stack.
#
# Cosign keyless signing for an OsirisCare installer ISO. Designed
# to run inside GitHub Actions where the OIDC token is available
# automatically — `cosign sign-blob --yes` picks it up and the
# signature ends up in the public Sigstore Rekor transparency log.
#
# Outputs alongside the input ISO:
#   <iso>.sig    — raw cosign signature (base64)
#   <iso>.cert   — Fulcio-issued ephemeral cert pinning the
#                  GitHub Actions identity that signed
#   <iso>.bundle — Sigstore bundle (sig + cert + rekor proof)
#                  for offline verification
#
# Anyone (customer, auditor, hostile party) can later verify with:
#
#   cosign verify-blob <iso> \
#       --bundle <iso>.bundle \
#       --certificate-identity-regexp 'github.com/jbouey/msp-flake' \
#       --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'
#
# That command:
#   - confirms the signature was issued by our GitHub Actions
#   - confirms the cert was issued by Fulcio (Sigstore CA)
#   - confirms the signature is in the Rekor transparency log
#     (so we can't quietly un-sign or backdate)
#
# We do NOT hold any long-lived signing key. The whole pipeline
# is keyless + transparency-logged — minimal supply-chain risk.

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <path-to-iso>" >&2
  exit 2
fi

ISO="$1"

if [ ! -f "$ISO" ]; then
  echo "ISO not found: $ISO" >&2
  exit 2
fi

if ! command -v cosign >/dev/null 2>&1; then
  echo "cosign not installed. Install: https://docs.sigstore.dev/cosign/installation/" >&2
  exit 2
fi

SIG_OUT="${ISO}.sig"
CERT_OUT="${ISO}.cert"
BUNDLE_OUT="${ISO}.bundle"

echo "[sign_iso] Signing $ISO ..."

# --yes        skip the interactive "are you sure" prompt
# --bundle     emit the offline-verifiable bundle (sig + cert +
#              rekor inclusion proof) — preferred over loose .sig +
#              .cert files for downstream verifiers
cosign sign-blob "$ISO" \
  --output-signature "$SIG_OUT" \
  --output-certificate "$CERT_OUT" \
  --bundle "$BUNDLE_OUT" \
  --yes

# Compute and print the SHA256 so the publishing step can stamp it
# into the release record. Idempotent.
ISO_SHA=$(sha256sum "$ISO" | awk '{print $1}')

cat <<EOF
[sign_iso] DONE.
  iso          : $ISO
  sha256       : $ISO_SHA
  signature    : $SIG_OUT
  certificate  : $CERT_OUT
  bundle       : $BUNDLE_OUT

Verify offline:
  cosign verify-blob "$ISO" \\
    --bundle "$BUNDLE_OUT" \\
    --certificate-identity-regexp 'github.com/jbouey/msp-flake' \\
    --certificate-oidc-issuer 'https://token.actions.githubusercontent.com'

Lookup in Rekor:
  rekor-cli search --sha "$ISO_SHA"
  https://search.sigstore.dev/?hash=$ISO_SHA
EOF
