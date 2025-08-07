# flake/container/default.nix - Back to nix2container (optimized)
{ pkgs, infra-watcher, nix2container }:

let
  # Separate stable dependencies layer (rarely changes)
  stableLayer = nix2container.buildLayer {
    deps = [ 
      pkgs.fluent-bit 
      pkgs.coreutils 
      pkgs.bash 
      pkgs.curl  # For health endpoint and MCP communication
    ];
  };
  
  # Application layer (changes frequently during development)
  # This will be rebuilt when your Python code changes, but stable layer won't
  
in nix2container.buildImage {
  name = "registry.example.com/infra-watcher";
  tag = "0.1";
  
  # Use layered approach for better caching and smaller rebuilds
  layers = [ stableLayer ];
  
  # Copy your application and config
  copyToRoot = [
    infra-watcher
    # Copy fluent-bit config to the right place
    (pkgs.runCommand "fluent-bit-assets" {} ''
      mkdir -p $out/assets
      cp ${./../assets}/fluent-bit.conf $out/assets/
    '')
  ];
  
  # Set up the container filesystem
  perms = [
    {
      path = infra-watcher;
      regex = ".*/bin/.*";
      mode = "0755";
    }
  ];

  config = {
    # Your original command structure for the MSP system
    cmd = [ "/bin/sh" "-c" 
           "infra-tailer & fluent-bit -c /assets/fluent-bit.conf" ];
    
    # Health endpoint port (Step 7 from your plan)
    exposedPorts = { 
      "8080/tcp" = {}; 
    };
    
    env = [
      "PATH=${pkgs.lib.makeBinPath [ infra-watcher pkgs.fluent-bit pkgs.coreutils pkgs.bash pkgs.curl ]}"
      # Environment for MCP server communication (Step 9)
      "MCP_URL=http://mcp-server:8000"
      "LOG_LEVEL=INFO"
    ];
    
    # Run as non-root for security (MSP best practice)
    user = "1000:1000";
    workingDir = "/";
  };
}