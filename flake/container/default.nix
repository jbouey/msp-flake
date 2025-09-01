# flake/container/default.nix - Fixed version
{ pkgs, infra-watcher, nix2container }:

let
  # Create a minimal base environment with shell
  baseEnv = pkgs.buildEnv {
    name = "container-base";
    paths = [ 
      pkgs.bashInteractive  # Full bash with proper shell features
      pkgs.coreutils       # ls, cat, etc.
      pkgs.findutils       # find, xargs
      pkgs.gnugrep         # grep
      pkgs.curl            # For MCP communication
    ];
    pathsToLink = [ "/bin" "/etc" "/lib" "/share" ];
  };

  # Stable layer for dependencies (rarely changes)
  stableLayer = nix2container.buildLayer {
    deps = [ 
      pkgs.fluent-bit 
      baseEnv
    ];
  };
  
in nix2container.buildImage {
  name = "registry.example.com/infra-watcher";
  tag = "0.1";
  
  # Use layered approach
  layers = [ stableLayer ];
  
  # Copy your application and config
  copyToRoot = [
    infra-watcher
    # Assets with proper directory structure
    (pkgs.runCommand "fluent-bit-assets" {} ''
      mkdir -p $out/assets
      cp ${./../assets}/fluent-bit.conf $out/assets/
      # Ensure executable permissions
      chmod 644 $out/assets/fluent-bit.conf
    '')
  ];

  config = {
    # Use full path to bash
    cmd = [ "${pkgs.bashInteractive}/bin/bash" "-c" 
           "infra-tailer & fluent-bit -c /assets/fluent-bit.conf" ];
    
    exposedPorts = { 
      "8080/tcp" = {}; 
      "2020/tcp" = {};  # Fluent Bit metrics
    };
    
    env = [
      "PATH=${pkgs.lib.makeBinPath [ infra-watcher pkgs.fluent-bit baseEnv ]}"
      "MCP_URL=http://localhost:8000"
      "LOG_LEVEL=INFO"
    ];
    
    workingDir = "/";
  };
}