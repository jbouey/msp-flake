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
      "compliance/webhook-secret" = {
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
    siteId = "clinic-abc-001";
    hostId = "srv-primary";

    # ========================================================================
    # Deployment Mode: RESELLER
    # ========================================================================
    deploymentMode = "reseller";
    resellerId = "msp-alpha";

    # ========================================================================
    # MCP Connection
    # ========================================================================
    mcpUrl = "https://mcp.msp-alpha.com";
    allowedHosts = [
      "mcp.msp-alpha.com"
      "backup-mcp.msp-alpha.com"
    ];

    # ========================================================================
    # Secrets (SOPS-managed)
    # ========================================================================
    clientCertFile = config.sops.secrets."compliance/client-cert".path;
    clientKeyFile = config.sops.secrets."compliance/client-key".path;
    signingKeyFile = config.sops.secrets."compliance/signing-key".path;
    webhookSecretFile = config.sops.secrets."compliance/webhook-secret".path;

    # ========================================================================
    # Policy
    # ========================================================================
    baselinePath = /etc/nixos/baseline.nix;
    policyVersion = "2.1";

    # ========================================================================
    # Timing
    # ========================================================================
    pollInterval = 60; # Poll every 60 seconds
    orderTtl = 900; # 15-minute TTL
    maintenanceWindow = "02:00-04:00"; # 2-4 AM UTC

    # ========================================================================
    # Evidence Retention
    # ========================================================================
    evidenceRetention = 200;
    pruneRetentionDays = 90;

    # ========================================================================
    # Clock Sanity
    # ========================================================================
    ntpMaxSkewMs = 5000; # 5 seconds

    # ========================================================================
    # Reseller Integrations (enabled in reseller mode)
    # ========================================================================
    rmmWebhookUrl = "https://rmm.msp-alpha.com/api/webhook";
    syslogTarget = "syslog.msp-alpha.com:514";

    # ========================================================================
    # Logging
    # ========================================================================
    logLevel = "INFO";

    # ========================================================================
    # Web UI (Dashboard)
    # ========================================================================
    webUI = {
      enable = true;
      port = 8080;
      bindAddress = "0.0.0.0"; # Accessible from local network
    };
  };

  # Example baseline (referenced by baselinePath above)
  # In real deployment, this would be in /etc/nixos/baseline.nix
  environment.etc."nixos/baseline.nix".text = ''
    { config, pkgs, ... }:
    {
      # NixOS-HIPAA baseline v1
      # Security hardening, service configurations, etc.

      # Example: Ensure critical services are running
      systemd.services.backup-job.enable = true;

      # Example: Firewall baseline
      networking.firewall.enable = true;

      # Example: Encryption enforcement (monitoring only, no auto-enable)
      # LUKS status checked but not auto-configured
    }
  '';
}
