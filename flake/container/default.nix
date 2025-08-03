{ pkgs, infra-watcher, nix2container }:

# nix2container is the *package* that exposes `buildImage`
nix2container.buildImage {
  name = "infra-watcher";
  tag = "0.1";

  # Copy your application, config, and dependencies to the container
  copyToRoot = [
    infra-watcher 
    pkgs.fluent-bit
    pkgs.coreutils
    pkgs.bash
    # Copy the fluent-bit config to /assets in the container
    (pkgs.runCommand "fluent-bit-config" {} ''
      mkdir -p $out/assets
      cp ${./../assets}/fluent-bit.conf $out/assets/
    '')
  ];

  config = {
    # Use 'cmd' instead of 'Cmd' (lowercase for OCI spec)
    cmd = [ "/bin/sh" "-c" 
           "infra-tailer & fluent-bit -c /assets/fluent-bit.conf" ];
    
    # Use 'exposedPorts' instead of 'ExposedPorts' (camelCase for OCI spec)
    exposedPorts = { 
      "8080/tcp" = {}; 
    };
    
    # Set PATH so binaries can be found
    env = [
      "PATH=${pkgs.lib.makeBinPath [ infra-watcher pkgs.fluent-bit pkgs.coreutils pkgs.bash ]}"
    ];
  };
}