# flake/pkgs/infra-watcher-fixed.nix
{ pkgs }:

let
  py = pkgs.python3;
  pyEnv = py.withPackages (ps: with ps; [ fastapi uvicorn requests ]);
in
pkgs.runCommand "infra-watcher-fixed-0.1.2" {
  # only to make the binary runnable inside nix-shell/containers
  buildInputs = [ pyEnv ];
} ''
  set -eux
  mkdir -p "$out/bin"
  cp ${./tailer.py} "$out/bin/infra-tailer"
  chmod +x "$out/bin/infra-tailer"
  test -x "$out/bin/infra-tailer"
''
