{ pkgs, python }:

let
  py = python.withPackages (ps: with ps; [ requests fastapi uvicorn httpx ]);
in
pkgs.stdenv.mkDerivation {
  pname = "infra-watcher";
  version = "0.1.1";
  src = ./.;  # tailer.py is here

  buildInputs = [ py ];
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    set -euxo pipefail
    mkdir -p "$out/bin" "$out/etc"

    cp ${./tailer.py} "$out/bin/infra-tailer"
    chmod +x "$out/bin/infra-tailer"
    test -x "$out/bin/infra-tailer"  # hard fail if missing

    # If you ship Fluent Bit config:
    cp ${../assets/fluent-bit.conf} "$out/etc/fluent-bit.conf"

    cat > "$out/bin/infra-entry" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if command -v fluent-bit >/dev/null 2>&1 && [ -f /etc/fluent-bit/fluent-bit.conf ]; then
  fluent-bit -c /etc/fluent-bit/fluent-bit.conf &
fi
exec infra-tailer
EOF
    chmod +x "$out/bin/infra-entry"
  '';
}
