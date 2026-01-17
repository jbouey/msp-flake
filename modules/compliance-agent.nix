{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.compliance-agent;

  # Parse maintenance window (HH:MM-HH:MM)
  parseMaintenanceWindow = window:
    let
      parts = builtins.split "-" window;
      start = builtins.elemAt parts 0;
      end = builtins.elemAt parts 2;
    in
    { inherit start end; };

in
{
  options.services.compliance-agent = {
    enable = mkEnableOption "MSP Compliance Agent";

    package = mkOption {
      type = types.package;
      default = pkgs.writeScriptBin "compliance-agent" ''
        #!${pkgs.bash}/bin/bash
        echo "MSP Compliance Agent (placeholder)"
        echo "This is a placeholder service - full agent implementation coming soon"
        sleep infinity
      '';
      defaultText = literalExpression "pkgs.writeScriptBin ...";
      description = "The compliance-agent package to use";
    };

    # ========================================================================
    # MCP Server (Local)
    # ========================================================================

    mcpServer = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Enable local MCP server (recommended for production)";
      };

      port = mkOption {
        type = types.port;
        default = 8000;
        description = "Port for local MCP server API";
      };

      openaiApiKeyFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        example = literalExpression "config.sops.secrets.\"mcp/openai-key\".path";
        description = "Path to OpenAI API key for L2 LLM runbook selection (SOPS-encrypted)";
      };

      openaiModel = mkOption {
        type = types.str;
        default = "gpt-4o";
        example = "gpt-4-turbo";
        description = "OpenAI model for runbook selection";
      };

      runbookDir = mkOption {
        type = types.path;
        default = "/var/lib/mcp-server/runbooks";
        description = "Directory containing YAML runbook definitions";
      };
    };

    # ========================================================================
    # Redis (Local)
    # ========================================================================

    redis = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Enable local Redis for MCP rate limiting and caching";
      };

      port = mkOption {
        type = types.port;
        default = 6379;
        description = "Port for local Redis";
      };
    };

    # ========================================================================
    # MCP Connection
    # ========================================================================

    mcpUrl = mkOption {
      type = types.str;
      default = "http://127.0.0.1:8000";
      example = "https://mcp.example.com";
      description = "MCP base URL for polling orders and pushing evidence (defaults to local)";
    };

    allowedHosts = mkOption {
      type = types.listOf types.str;
      default = [ ];
      example = [ "api.openai.com" "s3.amazonaws.com" ];
      description = "Allowlist for outbound HTTPS connections (for LLM API, WORM upload)";
    };

    # ========================================================================
    # Site Identification
    # ========================================================================

    siteId = mkOption {
      type = types.str;
      example = "clinic-001";
      description = "Unique site identifier (required)";
    };

    hostId = mkOption {
      type = types.str;
      default = config.networking.hostName;
      defaultText = literalExpression "config.networking.hostName";
      description = "Host identifier (defaults to hostname)";
    };

    # ========================================================================
    # Deployment Mode
    # ========================================================================

    deploymentMode = mkOption {
      type = types.enum [ "reseller" "direct" ];
      default = "reseller";
      description = ''
        Deployment mode:
        - reseller: Enable RMM/PSA integrations, white-label branding
        - direct: Disable integrations, default branding
      '';
    };

    resellerId = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "msp-alpha";
      description = "MSP reseller identifier (required if deploymentMode = reseller)";
    };

    # ========================================================================
    # Baseline Policy
    # ========================================================================

    baselinePath = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = literalExpression "/etc/nixos/baseline.nix";
      description = "Path to declarative baseline NixOS configuration (optional)";
    };

    policyVersion = mkOption {
      type = types.str;
      default = "1.0";
      example = "2.1";
      description = "Policy version identifier (for evidence bundles)";
    };

    # ========================================================================
    # Secrets (SOPS/age)
    # ========================================================================

    clientCertFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = literalExpression "config.sops.secrets.\"compliance/client-cert\".path";
      description = "Path to mTLS client certificate (SOPS-encrypted)";
    };

    clientKeyFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = literalExpression "config.sops.secrets.\"compliance/client-key\".path";
      description = "Path to mTLS client key (SOPS-encrypted)";
    };

    signingKeyFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = literalExpression "config.sops.secrets.\"compliance/signing-key\".path";
      description = "Path to Ed25519 signing key for evidence bundles";
    };

    webhookSecretFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = literalExpression "config.sops.secrets.\"compliance/webhook-secret\".path";
      description = "Path to HMAC secret for webhook signatures (reseller mode)";
    };

    # ========================================================================
    # Polling & Timing
    # ========================================================================

    pollInterval = mkOption {
      type = types.int;
      default = 60;
      example = 120;
      description = "Poll MCP every N seconds (±10% jitter applied automatically)";
    };

    orderTtl = mkOption {
      type = types.int;
      default = 900; # 15 minutes
      example = 600;
      description = "Discard orders older than N seconds";
    };

    maintenanceWindow = mkOption {
      type = types.str;
      default = "02:00-04:00";
      example = "01:00-05:00";
      description = "Time window for disruptive actions (HH:MM-HH:MM UTC)";
    };

    allowDisruptiveOutsideWindow = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Allow disruptive actions outside maintenance window.
        If false, actions are deferred and evidence emitted with outcome:"deferred"
      '';
    };

    # ========================================================================
    # Evidence Retention
    # ========================================================================

    evidenceRetention = mkOption {
      type = types.int;
      default = 200;
      example = 500;
      description = "Keep last N evidence bundles on disk (oldest pruned first)";
    };

    pruneRetentionDays = mkOption {
      type = types.int;
      default = 90;
      example = 180;
      description = "Never delete evidence bundles younger than N days";
    };

    # ========================================================================
    # Clock & Time Sync
    # ========================================================================

    ntpMaxSkewMs = mkOption {
      type = types.int;
      default = 5000; # 5 seconds
      example = 10000;
      description = ''
        Maximum allowed NTP offset in milliseconds.
        If exceeded, emit outcome:"alert" and skip disruptive actions until time is sane.
      '';
    };

    # ========================================================================
    # Reseller Integrations (reseller mode only)
    # ========================================================================

    rmmWebhookUrl = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "https://rmm.example.com/webhook";
      description = "RMM/PSA webhook URL (reseller mode only)";
    };

    syslogTarget = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "syslog.example.com:514";
      description = "Syslog target for events (reseller mode only, format: host:port)";
    };

    # ========================================================================
    # Health Checks
    # ========================================================================

    rebuildHealthCheckTimeout = mkOption {
      type = types.int;
      default = 60;
      example = 120;
      description = ''
        Seconds to wait for systemctl is-system-running after nixos-rebuild switch.
        If timeout or not "running", trigger automatic rollback.
      '';
    };

    # ========================================================================
    # Logging
    # ========================================================================

    logLevel = mkOption {
      type = types.enum [ "DEBUG" "INFO" "WARNING" "ERROR" ];
      default = "INFO";
      description = "Agent log level";
    };

    # ========================================================================
    # Auto-Healing
    # ========================================================================

    healingDryRun = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Run healing in dry-run mode (log actions but don't execute).
        Set to false for production to enable actual remediation.
      '';
    };

    # ========================================================================
    # Web UI (Dashboard)
    # ========================================================================

    webUI = {
      enable = mkEnableOption "Compliance dashboard web UI";

      port = mkOption {
        type = types.port;
        default = 8080;
        description = "Port for web UI to listen on";
      };

      bindAddress = mkOption {
        type = types.str;
        default = "127.0.0.1";
        example = "0.0.0.0";
        description = "Address to bind web UI (127.0.0.1 for local only, 0.0.0.0 for all interfaces)";
      };
    };
  };

  config = mkIf cfg.enable {

    # Assertions
    assertions = [
      {
        assertion = cfg.deploymentMode == "direct" || cfg.resellerId != null;
        message = "resellerId must be set when deploymentMode = reseller";
      }
      {
        assertion = cfg.siteId != "";
        message = "siteId is required";
      }
      {
        assertion = builtins.match "[0-9]{2}:[0-9]{2}-[0-9]{2}:[0-9]{2}" cfg.maintenanceWindow != null;
        message = "maintenanceWindow must be in format HH:MM-HH:MM";
      }
      {
        assertion = cfg.mcpServer.enable -> cfg.redis.enable;
        message = "Redis must be enabled when MCP server is enabled";
      }
    ];

    # ========================================================================
    # Redis (local instance for MCP)
    # ========================================================================

    services.redis.servers.mcp = mkIf cfg.redis.enable {
      enable = true;
      port = cfg.redis.port;
      bind = "127.0.0.1";
      settings = {
        maxmemory = "64mb";
        maxmemory-policy = "allkeys-lru";
      };
    };

    # Install the agent package
    environment.systemPackages = [ cfg.package ];

    # State directories for evidence bundles, queue, and MCP server
    systemd.tmpfiles.rules = [
      "d /var/lib/compliance-agent 0700 compliance-agent compliance-agent -"
      "d /var/lib/compliance-agent/evidence 0700 compliance-agent compliance-agent -"
      "d /var/lib/mcp-server 0700 mcp-server mcp-server -"
      "d /var/lib/mcp-server/runbooks 0700 mcp-server mcp-server -"
      "d /var/lib/mcp-server/evidence 0700 mcp-server mcp-server -"
    ];

    # Create compliance-agent user
    users.users.compliance-agent = {
      isSystemUser = true;
      group = "compliance-agent";
      description = "MSP Compliance Agent";
      home = "/var/lib/compliance-agent";
    };

    users.groups.compliance-agent = { };

    # Create mcp-server user
    users.users.mcp-server = mkIf cfg.mcpServer.enable {
      isSystemUser = true;
      group = "mcp-server";
      description = "MCP Server";
      home = "/var/lib/mcp-server";
    };

    users.groups.mcp-server = mkIf cfg.mcpServer.enable { };

    # Main agent service
    systemd.services.compliance-agent = {
      description = "MSP Compliance Agent - Self-Healing & Evidence Generation";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" "systemd-timesyncd.service" ];
      wants = [ "network-online.target" ];

      serviceConfig = {
        Type = "simple";
        User = "compliance-agent";
        Group = "compliance-agent";
        Restart = "always";
        RestartSec = "10s";

        ExecStart = "${cfg.package}/bin/compliance-agent";

        # ====================================================================
        # Systemd Hardening
        # ====================================================================

        # Filesystem protections
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ "/var/lib/compliance-agent" ];

        # Process protections
        NoNewPrivileges = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;

        # Network restrictions
        RestrictAddressFamilies = "AF_INET AF_INET6 AF_UNIX";
        PrivateNetwork = false; # Need network for outbound connections

        # Capabilities (none needed)
        CapabilityBoundingSet = "";
        AmbientCapabilities = "";

        # System calls
        SystemCallFilter = "@system-service";
        SystemCallErrorNumber = "EPERM";

        # Misc protections
        ProtectHostname = true;
        ProtectClock = true;
        ProtectKernelLogs = true;
        RestrictNamespaces = true;
        LockPersonality = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        RemoveIPC = true;

        # State directory (auto-created)
        StateDirectory = "compliance-agent";
        StateDirectoryMode = "0700";

        # Logging
        StandardOutput = "journal";
        StandardError = "journal";
        SyslogIdentifier = "compliance-agent";
      };

      environment = {
        # MCP connection
        MCP_URL = cfg.mcpUrl;
        SITE_ID = cfg.siteId;
        HOST_ID = cfg.hostId;

        # Deployment mode
        DEPLOYMENT_MODE = cfg.deploymentMode;
        RESELLER_ID = if cfg.resellerId != null then cfg.resellerId else "";

        # Policy
        POLICY_VERSION = cfg.policyVersion;
        BASELINE_PATH = if cfg.baselinePath != null then toString cfg.baselinePath else "";

        # Timing
        POLL_INTERVAL = toString cfg.pollInterval;
        ORDER_TTL = toString cfg.orderTtl;
        MAINTENANCE_WINDOW = cfg.maintenanceWindow;
        ALLOW_DISRUPTIVE_OUTSIDE_WINDOW = if cfg.allowDisruptiveOutsideWindow then "true" else "false";

        # Secrets (paths)
        CLIENT_CERT_FILE = if cfg.clientCertFile != null then toString cfg.clientCertFile else "";
        CLIENT_KEY_FILE = if cfg.clientKeyFile != null then toString cfg.clientKeyFile else "";
        SIGNING_KEY_FILE = if cfg.signingKeyFile != null then toString cfg.signingKeyFile else "";
        WEBHOOK_SECRET_FILE = if cfg.webhookSecretFile != null then toString cfg.webhookSecretFile else "";

        # Evidence
        EVIDENCE_RETENTION = toString cfg.evidenceRetention;
        PRUNE_RETENTION_DAYS = toString cfg.pruneRetentionDays;

        # Time sync
        NTP_MAX_SKEW_MS = toString cfg.ntpMaxSkewMs;

        # Reseller integrations
        RMM_WEBHOOK_URL = if cfg.rmmWebhookUrl != null then cfg.rmmWebhookUrl else "";
        SYSLOG_TARGET = if cfg.syslogTarget != null then cfg.syslogTarget else "";

        # Health checks
        REBUILD_HEALTH_CHECK_TIMEOUT = toString cfg.rebuildHealthCheckTimeout;

        # Logging
        LOG_LEVEL = cfg.logLevel;

        # Healing
        HEALING_DRY_RUN = if cfg.healingDryRun then "true" else "false";
      };
    };

    # ========================================================================
    # MCP Server Service (local)
    # ========================================================================

    systemd.services.mcp-server = mkIf cfg.mcpServer.enable {
      description = "MCP Server - Local Control Plane for Compliance Agent";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" "redis-mcp.service" ];
      wants = [ "network-online.target" ];
      requires = [ "redis-mcp.service" ];

      # Python environment for MCP server
      path = with pkgs; [
        (python311.withPackages (ps: with ps; [
          fastapi
          uvicorn
          redis
          aiohttp
          pyyaml
          pydantic
        ]))
      ];

      serviceConfig = {
        Type = "simple";
        User = "mcp-server";
        Group = "mcp-server";
        Restart = "always";
        RestartSec = "5s";

        ExecStart = "${pkgs.python311.withPackages (ps: with ps; [ fastapi uvicorn redis aiohttp pyyaml pydantic ])}/bin/python -m uvicorn mcp_server:app --host 127.0.0.1 --port ${toString cfg.mcpServer.port}";
        WorkingDirectory = "/var/lib/mcp-server";

        # Systemd hardening
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ "/var/lib/mcp-server" ];
        NoNewPrivileges = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = "AF_INET AF_INET6 AF_UNIX";
        CapabilityBoundingSet = "";
        SystemCallFilter = "@system-service";
        SystemCallErrorNumber = "EPERM";
        ProtectHostname = true;
        ProtectClock = true;
        ProtectKernelLogs = true;
        RestrictNamespaces = true;
        LockPersonality = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        RemoveIPC = true;

        StateDirectory = "mcp-server";
        StateDirectoryMode = "0700";

        StandardOutput = "journal";
        StandardError = "journal";
        SyslogIdentifier = "mcp-server";
      };

      environment = {
        REDIS_HOST = "127.0.0.1";
        REDIS_PORT = toString cfg.redis.port;
        REDIS_PASSWORD = "";  # Local Redis, no auth needed
        RUNBOOK_DIR = toString cfg.mcpServer.runbookDir;
        EVIDENCE_DIR = "/var/lib/mcp-server/evidence";
        LOG_LEVEL = cfg.logLevel;
        MCP_API_HOST = "127.0.0.1";
        MCP_API_PORT = toString cfg.mcpServer.port;
        OPENAI_MODEL = cfg.mcpServer.openaiModel;
      };
    };

    # MCP server Python script as a package
    environment.etc."mcp-server/mcp_server.py" = mkIf cfg.mcpServer.enable {
      source = pkgs.writeText "mcp_server.py" (builtins.readFile ../mcp-server/server.py);
      mode = "0644";
    };

    # MCP server runbook YAML files
    environment.etc."mcp-server/runbooks/RB-BACKUP-001.yaml" = mkIf cfg.mcpServer.enable {
      source = ../mcp-server/runbooks/RB-BACKUP-001.yaml;
      mode = "0644";
    };
    environment.etc."mcp-server/runbooks/RB-CERT-001.yaml" = mkIf cfg.mcpServer.enable {
      source = ../mcp-server/runbooks/RB-CERT-001.yaml;
      mode = "0644";
    };
    environment.etc."mcp-server/runbooks/RB-CPU-001.yaml" = mkIf cfg.mcpServer.enable {
      source = ../mcp-server/runbooks/RB-CPU-001.yaml;
      mode = "0644";
    };
    environment.etc."mcp-server/runbooks/RB-DISK-001.yaml" = mkIf cfg.mcpServer.enable {
      source = ../mcp-server/runbooks/RB-DISK-001.yaml;
      mode = "0644";
    };
    environment.etc."mcp-server/runbooks/RB-DRIFT-001.yaml" = mkIf cfg.mcpServer.enable {
      source = ../mcp-server/runbooks/RB-DRIFT-001.yaml;
      mode = "0644";
    };
    environment.etc."mcp-server/runbooks/RB-RESTORE-001.yaml" = mkIf cfg.mcpServer.enable {
      source = ../mcp-server/runbooks/RB-RESTORE-001.yaml;
      mode = "0644";
    };
    environment.etc."mcp-server/runbooks/RB-SERVICE-001.yaml" = mkIf cfg.mcpServer.enable {
      source = ../mcp-server/runbooks/RB-SERVICE-001.yaml;
      mode = "0644";
    };

    # Symlink MCP server files to state directory
    system.activationScripts.mcp-server-setup = mkIf cfg.mcpServer.enable ''
      # Link MCP server script to working directory
      ln -sf /etc/mcp-server/mcp_server.py /var/lib/mcp-server/mcp_server.py 2>/dev/null || true

      # Link runbooks to state directory
      mkdir -p /var/lib/mcp-server/runbooks
      for rb in /etc/mcp-server/runbooks/*.yaml; do
        [ -f "$rb" ] && ln -sf "$rb" /var/lib/mcp-server/runbooks/ 2>/dev/null || true
      done
    '';

    # Web UI service (optional)
    systemd.services.compliance-agent-web = mkIf cfg.webUI.enable {
      description = "MSP Compliance Agent Web Dashboard";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" "compliance-agent.service" ];
      wants = [ "network-online.target" ];

      serviceConfig = {
        Type = "simple";
        User = "compliance-agent";
        Group = "compliance-agent";
        Restart = "always";
        RestartSec = "5s";

        ExecStart = "${cfg.package}/bin/compliance-web --host ${cfg.webUI.bindAddress} --port ${toString cfg.webUI.port} --evidence-dir /var/lib/compliance-agent/evidence --db-path /var/lib/compliance-agent/incidents.db --site-id ${cfg.siteId} --host-id ${cfg.hostId}";

        # Same hardening as main agent
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ "/var/lib/compliance-agent" ];
        NoNewPrivileges = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = "AF_INET AF_INET6 AF_UNIX";
        CapabilityBoundingSet = "";
        SystemCallFilter = "@system-service";
        SystemCallErrorNumber = "EPERM";
        ProtectHostname = true;
        ProtectClock = true;
        ProtectKernelLogs = true;
        RestrictNamespaces = true;
        LockPersonality = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        RemoveIPC = true;

        StandardOutput = "journal";
        StandardError = "journal";
        SyslogIdentifier = "compliance-agent-web";
      };
    };

    # Timer for DNS resolution and nftables refresh
    systemd.timers.compliance-agent-firewall-refresh = {
      description = "Refresh compliance agent egress allowlist";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnBootSec = "5min";
        OnUnitActiveSec = "1h";
        Persistent = true;
      };
    };

    systemd.services.compliance-agent-firewall-refresh = {
      description = "Refresh compliance agent egress allowlist";
      after = [ "network-online.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "refresh-firewall" ''
          set -euo pipefail

          # Resolve MCP FQDNs to IPs
          declare -a IPS
          ${concatMapStringsSep "\n" (host: ''
            if IP=$(${pkgs.dnsutils}/bin/dig +short ${host} A | head -1); then
              if [ -n "$IP" ]; then
                IPS+=("$IP")
              fi
            fi
          '') cfg.allowedHosts}

          # Build nftables set
          RULES="flush set inet filter external_allowed\n"
          for ip in "''${IPS[@]}"; do
            RULES+="add element inet filter external_allowed { $ip }\n"
          done

          # Apply atomically
          echo -e "$RULES" | ${pkgs.nftables}/bin/nft -f -

          echo "Firewall refreshed: ''${#IPS[@]} hosts resolved"
        '';
      };
    };

    # nftables egress allowlist
    networking.nftables = {
      enable = true;
      ruleset = ''
        table inet filter {
          # Set for allowed external IPs (OpenAI, S3, etc - populated by timer)
          set external_allowed {
            type ipv4_addr
            flags dynamic
          }

          chain input {
            type filter hook input priority 0; policy drop;

            # Allow loopback (critical for local MCP server)
            iif lo accept

            # Allow established/related connections
            ct state established,related accept

            # Allow SSH (management)
            tcp dport 22 accept

            ${optionalString cfg.webUI.enable ''
            # Allow web UI port
            tcp dport ${toString cfg.webUI.port} accept
            ''}

            # Drop everything else
            drop
          }

          chain output {
            type filter hook output priority 0; policy drop;

            # Allow loopback (critical for agent <-> MCP <-> Redis communication)
            oif lo accept

            # Allow established/related connections
            ct state established,related accept

            # Allow DNS (for hostname resolution)
            udp dport 53 accept
            tcp dport 53 accept

            # Allow NTP for time sync
            udp dport 123 accept

            # Allow HTTPS to external allowlist (OpenAI API, S3 WORM, etc)
            ip daddr @external_allowed tcp dport 443 accept

            # Allow WinRM to local network (for Windows server management)
            tcp dport 5985 accept
            tcp dport 5986 accept

            # Log and drop everything else
            log prefix "compliance-agent-blocked: " level info drop
          }
        }
      '';
    };

    # Ensure systemd-timesyncd is enabled for NTP
    services.timesyncd.enable = mkDefault true;

    # Enable auditd for logging systemd-journald restarts
    security.auditd.enable = mkDefault true;

    # Daily evidence pruner
    systemd.timers.compliance-agent-prune-evidence = {
      description = "Prune old compliance evidence bundles";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
      };
    };

    systemd.services.compliance-agent-prune-evidence = {
      description = "Prune old compliance evidence bundles";

      serviceConfig = {
        Type = "oneshot";
        User = "compliance-agent";
        Group = "compliance-agent";

        ExecStart = pkgs.writeShellScript "prune-evidence" ''
          set -euo pipefail

          EVIDENCE_DIR="/var/lib/compliance-agent/evidence"
          RETENTION_COUNT=${toString cfg.evidenceRetention}
          RETENTION_DAYS=${toString cfg.pruneRetentionDays}

          # Find all bundle.json files, sorted by mtime (oldest first)
          readarray -t BUNDLES < <(find "$EVIDENCE_DIR" -name "bundle.json" -type f -printf '%T@ %p\n' | sort -n | cut -d' ' -f2-)

          TOTAL=''${#BUNDLES[@]}
          TO_DELETE=$((TOTAL - RETENTION_COUNT))

          if [ "$TO_DELETE" -le 0 ]; then
            echo "No evidence bundles to prune (total: $TOTAL, retention: $RETENTION_COUNT)"
            exit 0
          fi

          # Never delete last successful bundle for each check type
          # (This requires parsing JSON - for now, just enforce time-based retention)

          DELETED=0
          for bundle in "''${BUNDLES[@]:0:$TO_DELETE}"; do
            # Get bundle age in days
            MTIME=$(stat -c %Y "$bundle")
            NOW=$(date +%s)
            AGE_DAYS=$(( (NOW - MTIME) / 86400 ))

            if [ "$AGE_DAYS" -ge "$RETENTION_DAYS" ]; then
              BUNDLE_DIR=$(dirname "$bundle")
              rm -rf "$BUNDLE_DIR"
              DELETED=$((DELETED + 1))
            fi
          done

          echo "Pruned $DELETED evidence bundles (total: $TOTAL, retention: $RETENTION_COUNT)"
        '';
      };
    };
  };
}
