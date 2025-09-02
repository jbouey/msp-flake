{ pkgs }:
pkgs.runCommand "smoke" { } ''
  mkdir -p $out/bin
  printf '#!/usr/bin/env bash\necho SMOKE OK\n' > $out/bin/hello
  chmod +x $out/bin/hello
''
