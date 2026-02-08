{ config, pkgs, modulesPath, ... }:

# Test client configuration for wired network deployment
# This triggers the build-vm.yml workflow to build a VirtualBox OVA

{
  imports = [
    ../modules/compliance-agent.nix
    "${modulesPath}/virtualisation/virtualbox-image.nix"
  ];

  # Enable compliance agent with MCP server connection
  services.compliance-agent = {
    enable = true;

    # Site identification
    siteId = "test-client-001";

    # Deployment mode (direct for testing)
    deploymentMode = "direct";

    # MCP server connection
    mcpUrl = "https://MCP_SERVER_IP:8000";  # REPLACE WITH ACTUAL IP

    # Network egress allowlist (hostnames only, no ports)
    allowedHosts = [
      "MCP_SERVER_IP"  # REPLACE WITH ACTUAL IP/HOSTNAME
    ];

    # Evidence retention (number of bundles to keep, not days)
    evidenceRetention = 50;  # Keep 50 most recent evidence bundles

    # Verbose logging for testing (uppercase values)
    logLevel = "DEBUG";
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

  # NixOS release version (required for VM builds)
  system.stateVersion = "24.05";

  # Basic system packages
  environment.systemPackages = with pkgs; [
    curl
    jq
    vim
  ];

  # VirtualBox configuration
  virtualbox = {
    vmName = "test-client-001";
  };

  # Enable guest additions for better VM integration
  virtualisation.virtualbox.guest.enable = true;

  # Set root password for initial login (change after first boot!)
  users.users.root.password = "root";

  # Enable SSH for remote access
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "yes";
      PasswordAuthentication = true;
    };
  };
}
