{ pkgs, python }:

let
  py = python.withPackages (ps: with ps; [ requests fastapi uvicorn httpx ]);
in
pkgs.stdenv.mkDerivation {
  pname = "infra-watcher";
  version = "0.1";
  src = ./.;  # this directory contains tailer.py

  buildInputs = [ py ];

  installPhase = ''
    mkdir -p $out/bin $out/etc
    cp ${./tailer.py} $out/bin/infra-tailer
    chmod +x $out/bin/infra-tailer

    # ship fluent-bit config alongside (container will place it in /etc/fluent-bit/)
    cp ${../assets/fluent-bit.conf} $out/etc/fluent-bit.conf

    # lightweight entrypoint that runs both processes if you want it later
    cat > $out/bin/infra-entry <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
# start fluent-bit in background if present
if command -v fluent-bit >/dev/null 2>&1 && [ -f /etc/fluent-bit/fluent-bit.conf ]; then
  fluent-bit -c /etc/fluent-bit/fluent-bit.conf &
fi
exec infra-tailer
EOF
    chmod +x $out/bin/infra-entry
  '';
}
