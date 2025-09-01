# flake/pkgs/infra-watcher.nix
{ pkgs, python }:

let
  pythonEnv = python.withPackages (ps: with ps; [
    requests
    fastapi
    uvicorn
    httpx
  ]);
in
pkgs.stdenv.mkDerivation rec {
  pname = "infra-watcher";
  version = "0.1";
  
  # Current directory where tailer.py is
  src = ./.;
  
  buildInputs = [
    pkgs.fluent-bit
    pythonEnv
  ];
  
  installPhase = ''
    mkdir -p $out/bin
    
    # Check if tailer.py exists
    if [ -f "tailer.py" ]; then
      echo "Found tailer.py, installing..."
      
      # Create executable wrapper with proper Python environment
      cat > $out/bin/infra-tailer << EOF
#!${pythonEnv}/bin/python3
EOF
      # Append the actual Python code (skip the shebang line)
      tail -n +2 tailer.py >> $out/bin/infra-tailer
      chmod +x $out/bin/infra-tailer
      
      echo "Installed infra-tailer to $out/bin/"
    else
      echo "ERROR: tailer.py not found in source directory"
      echo "Contents of source directory:"
      ls -la
      exit 1
    fi
  '';
}