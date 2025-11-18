{ config, pkgs, ... }:

{
  imports = [
    ../flake-compliance.nix
  ];

  # Enable compliance agent with MCP server connection
  services.msp-compliance = {
    enable = true;

    # Connect to your test MCP server
    mcpServer = {
      url = "http://MCP_SERVER_IP:8000";  # REPLACE WITH ACTUAL IP
      apiKey = "test-key-12345";
    };

    # Client identification
    clientId = "test-client-001";
    resellerId = null;  # Direct mode for testing

    # Monitoring configuration
    monitoring = {
      services = [
        "nginx"
        "test-service"  # We'll create this for testing
      ];
      interval = 60;  # Check every 60 seconds
    };

    # Evidence storage
    evidence = {
      localPath = "/var/lib/msp/evidence";
      retention = 7;  # 7 days for testing
    };

    # Network restrictions
    networking = {
      egressAllowlist = [
        "MCP_SERVER_IP:8000"  # REPLACE WITH ACTUAL IP
      ];
      blockInbound = true;
    };

    # Logging
    logging = {
      level = "debug";  # Verbose for testing
      destination = "local";
    };
  };

  # Create a test service that we can crash/restart
  systemd.services.test-service = {
    description = "MSP Test Service (for crash testing)";
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = "10s";
      ExecStart = "${pkgs.bash}/bin/bash -c 'while true; do echo \"Test service running...\"; sleep 5; done'";
    };
  };

  # Enable nginx for testing
  services.nginx = {
    enable = true;
    virtualHosts."localhost" = {
      root = "/var/www";
    };
  };

  # System settings
  networking.hostName = "test-client-001";
  networking.firewall.enable = true;

  # Basic system packages
  environment.systemPackages = with pkgs; [
    curl
    jq
    vim
  ];
}
