{ config, pkgs, ... }:

{
  imports = [
    # Import the compliance-agent module
    # In real deployment: inputs.compliance-appliance.nixosModules.compliance-agent
  ];

  # SOPS-nix for secret management
  sops = {
    defaultSopsFile = ./secrets.yaml;
    age.keyFile = "/var/lib/sops-nix/key.txt";

    secrets = {
      "compliance/client-cert" = {
        owner = "compliance-agent";
        mode = "0600";
      };
      "compliance/client-key" = {
        owner = "compliance-agent";
        mode = "0600";
      };
      "compliance/signing-key" = {
        owner = "compliance-agent";
        mode = "0600";
      };
    };
  };

  services.compliance-agent = {
    enable = true;

    # ========================================================================
    # Site Identification
    # ========================================================================
    siteId = "direct-clinic-xyz";
    hostId = "srv-main";

    # ========================================================================
    # Deployment Mode: DIRECT
    # ========================================================================
    deploymentMode = "direct";
    resellerId = null; # Not used in direct mode

    # ========================================================================
    # MCP Connection
    # ========================================================================
    mcpUrl = "https://mcp.compliance-platform.com";
    allowedHosts = [
      "mcp.compliance-platform.com"
    ];

    # ========================================================================
    # Secrets (SOPS-managed)
    # ========================================================================
    clientCertFile = config.sops.secrets."compliance/client-cert".path;
    clientKeyFile = config.sops.secrets."compliance/client-key".path;
    signingKeyFile = config.sops.secrets."compliance/signing-key".path;

    # No webhook secret in direct mode
    webhookSecretFile = null;

    # ========================================================================
    # Policy
    # ========================================================================
    baselinePath = /etc/nixos/baseline.nix;
    policyVersion = "1.0";

    # ========================================================================
    # Timing
    # ========================================================================
    pollInterval = 60;
    orderTtl = 900;
    maintenanceWindow = "01:00-03:00"; # 1-3 AM UTC

    # ========================================================================
    # Evidence Retention
    # ========================================================================
    evidenceRetention = 200;
    pruneRetentionDays = 90;

    # ========================================================================
    # Clock Sanity
    # ========================================================================
    ntpMaxSkewMs = 5000;

    # ========================================================================
    # Reseller Integrations (DISABLED in direct mode)
    # ========================================================================
    rmmWebhookUrl = null;
    syslogTarget = null;

    # ========================================================================
    # Logging
    # ========================================================================
    logLevel = "INFO";
  };

  # Example baseline
  environment.etc."nixos/baseline.nix".text = ''
    { config, pkgs, ... }:
    {
      # NixOS-HIPAA baseline v1
      # Direct deployment configuration
    }
  '';
}
