# flake/pkgs/infra-watcher.nix - Correct path to tailer.py
{ pkgs, python }:

pkgs.stdenv.mkDerivation rec {
  pname = "infra-watcher";
  version = "0.1";
  
  # Point to current directory where tailer.py actually is
  src = ./.;  # This is flake/pkgs/ directory
  
  buildInputs = [
    pkgs.fluent-bit
    python
    python.pkgs.requests
    python.pkgs.fastapi
    python.pkgs.uvicorn
    python.pkgs.httpx
  ];
  
  # Install phase that handles the Python script properly
  installPhase = ''
    mkdir -p $out/bin
    
    # Copy your Python script (it's in the same directory as this .nix file)
    if [ -f "tailer.py" ]; then
      # Create a wrapper that ensures Python can find dependencies
      cat > $out/bin/infra-tailer << EOF
#!${python}/bin/python3
import sys
sys.path.insert(0, '${python.pkgs.requests}/lib/python3.11/site-packages')
sys.path.insert(0, '${python.pkgs.fastapi}/lib/python3.11/site-packages')
sys.path.insert(0, '${python.pkgs.uvicorn}/lib/python3.11/site-packages')
sys.path.insert(0, '${python.pkgs.httpx}/lib/python3.11/site-packages')
EOF
      cat tailer.py >> $out/bin/infra-tailer
      chmod +x $out/bin/infra-tailer
    else
      echo "ERROR: tailer.py not found in $src"
      exit 1
    fi
  '';
}