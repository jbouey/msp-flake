{ pkgs, compliance-agent }:

pkgs.stdenv.mkDerivation {
  name = "compliance-agent-unit-tests";

  src = ../packages/compliance-agent;

  nativeBuildInputs = with pkgs; [
    python311
    python311Packages.pytest
    python311Packages.pytest-asyncio
    python311Packages.cryptography
  ];

  buildPhase = ''
    echo "Running unit tests..."

    # Phase 2 will add actual tests for:
    # - Ed25519 signature generation and verification
    # - Webhook HMAC computation
    # - SQLite queue operations (enqueue, dequeue, retry)
    # - Order validation (TTL, nonce, signature)
    # - Maintenance window parsing and enforcement

    # For now, just verify Python can import the package
    python3 -c "import sys; sys.path.insert(0, 'src'); from compliance_agent import __version__; print(f'Version: {__version__}')"

    echo "✓ Unit tests placeholder (full implementation in Phase 2)"
  '';

  installPhase = ''
    mkdir -p $out
    echo "success" > $out/result
  '';

  meta = with pkgs.lib; {
    description = "Unit tests for compliance-agent";
  };
}
