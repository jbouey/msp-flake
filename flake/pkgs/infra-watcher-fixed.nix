{ pkgs }:

let
  pyEnv = pkgs.python3.withPackages (ps: with ps; [ fastapi uvicorn requests ]);
in
pkgs.runCommand "infra-watcher-fixed-0.1.2" { } ''
  set -eux
  mkdir -p "$out/bin" "$out/libexec"

  # ship the script
  cp ${./tailer.py} "$out/libexec/tailer.py"

  # wrapper that runs it with the Nix Python
  cat > "$out/bin/infra-tailer" <<EOF
#!${pkgs.bash}/bin/bash
exec ${pyEnv}/bin/python3 $out/libexec/tailer.py
EOF
  chmod +x "$out/bin/infra-tailer"

  # sanity check
  test -x "$out/bin/infra-tailer"
''
