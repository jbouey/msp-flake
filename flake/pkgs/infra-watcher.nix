# flake/pkgs/infra-watcher.nix - Corrected version
{ pkgs, python }:

pkgs.stdenv.mkDerivation rec {
  pname = "infra-watcher";
  version = "0.1";
  
  # Point to source directory (create src/ first)
  src = ../../src;
  
  buildInputs = [
    pkgs.fluent-bit
    python
    python.pkgs.requests
    python.pkgs.fastapi
    python.pkgs.uvicorn
    python.pkgs.httpx
  ];
  
  # Simple install - just copy the script
  installPhase = ''
    mkdir -p $out/bin
    
    # Copy your Python script
    if [ -f "$src/tailer.py" ]; then
      cp $src/tailer.py $out/bin/infra-tailer
      chmod +x $out/bin/infra-tailer
    else
      # Create a minimal stub for testing
      cat > $out/bin/infra-tailer << 'EOF'
#!/usr/bin/env python3
import time
print("Minimal infra-tailer running...")
while True:
    print(f"Monitoring... {time.time()}")
    time.sleep(30)
EOF
      chmod +x $out/bin/infra-tailer
    fi
  '';
}