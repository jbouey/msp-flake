{ pkgs, python }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "infra-watcher";
  version = "0.1";

  src = ./.;

  buildInputs = [
    pkgs.fluent-bit
    (python.withPackages (ps: [ ps.requests ]))
  ];

  installPhase = ''
    mkdir -p $out/bin
    cp ${./tailer.py} $out/bin/infra-tailer
    chmod +x $out/bin/infra-tailer
    # Fluent Bit binary is already in PATH
  '';
}
