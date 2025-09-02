{ pkgs, python }:

let
  py = python.withPackages (ps: with ps; [ requests fastapi uvicorn httpx ]);
in
pkgs.stdenv.mkDerivation {
  pname = "infra-watcher-fixed";   # new name → new store path
  version = "0.1.2";               # bump as needed
  src = ./.;                       # directory that contains tailer.py

  buildInputs = [ py ];
  dontConfigure = true;
  dontBuild = true;

  installPhase = ''
    set -euxo pipefail
    mkdir -p "$out/bin" "$out/etc"

    cp ${./tailer.py} "$out/bin/infra-tailer"
    chmod +x "$out/bin/infra-tailer"
    test -x "$out/bin/infra-tailer"   # fail hard if missing
  '';
}
