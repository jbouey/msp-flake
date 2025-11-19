{ config, pkgs, modulesPath, ... }:

# MCP Server configuration for VirtualBox deployment
# Central control plane that receives incidents from compliance agents

{
  imports = [
    "${modulesPath}/virtualisation/virtualbox-image.nix"
  ];

  # ============================================================================
  # System Configuration
  # ============================================================================

  networking.hostName = "mcp-server";
  networking.firewall = {
    enable = true;
    allowedTCPPorts = [
      22    # SSH
      8000  # MCP server API
      6379  # Redis (for localhost only)
    ];
  };

  system.stateVersion = "24.05";

  # ============================================================================
  # VirtualBox Configuration
  # ============================================================================

  virtualbox = {
    vmName = "mcp-server";
  };

  virtualisation.virtualbox.guest.enable = true;

  # ============================================================================
  # User Configuration
  # ============================================================================

  users.users.root.password = "root";  # Change after first boot!

  # Create mcp user for running services
  users.users.mcp = {
    isNormalUser = true;
    description = "MCP Service User";
    extraGroups = [ "wheel" ];
    password = "mcp";  # Change after first boot!
  };

  # ============================================================================
  # SSH Configuration
  # ============================================================================

  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "yes";
      PasswordAuthentication = true;
    };
  };

  # ============================================================================
  # Redis Configuration (Event Queue)
  # ============================================================================

  services.redis.servers.mcp = {
    enable = true;
    bind = "127.0.0.1";  # Localhost only for security
    port = 6379;
    requirePass = "mcp-redis-password";  # Change in production!

    # Settings are passed directly to redis.conf
    settings = {
      # Enable AOF persistence for durability
      appendonly = "yes";
      appendfsync = "everysec";

      # Memory limits
      maxmemory = "256mb";
      maxmemory-policy = "allkeys-lru";
    };
  };

  # ============================================================================
  # Python Environment for MCP Server
  # ============================================================================

  environment.systemPackages = with pkgs; [
    # System tools
    curl
    jq
    vim
    htop
    tmux
    git

    # Python with required packages
    (python3.withPackages (ps: with ps; [
      fastapi
      uvicorn
      pydantic
      redis
      aiohttp
      openai
      pyyaml

      # Development tools
      pytest
      black
      mypy
    ]))
  ];

  # ============================================================================
  # MCP Server Service
  # ============================================================================

  systemd.services.mcp-server = {
    description = "MSP MCP Server - Central Control Plane";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" "redis-mcp.service" ];
    requires = [ "redis-mcp.service" ];

    serviceConfig = {
      Type = "simple";
      User = "mcp";
      Group = "users";
      Restart = "always";
      RestartSec = "10s";
      WorkingDirectory = "/var/lib/mcp-server";

      # Placeholder startup - will be replaced with real server
      ExecStart = pkgs.writeScript "mcp-server-placeholder" ''
        #!${pkgs.bash}/bin/bash
        echo "MCP Server starting..."
        echo "This is a placeholder - real MCP server implementation coming next"
        echo ""
        echo "Configuration:"
        echo "  - Redis running on localhost:6379"
        echo "  - API will listen on 0.0.0.0:8000"
        echo "  - Runbook library: /var/lib/mcp-server/runbooks/"
        echo "  - Evidence storage: /var/lib/mcp-server/evidence/"
        echo ""
        echo "Keeping service alive..."
        sleep infinity
      '';

      # Security hardening
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
      ReadWritePaths = [ "/var/lib/mcp-server" ];
    };

    # Environment variables
    environment = {
      REDIS_HOST = "127.0.0.1";
      REDIS_PORT = "6379";
      REDIS_PASSWORD = "mcp-redis-password";
      MCP_API_HOST = "0.0.0.0";
      MCP_API_PORT = "8000";
      LOG_LEVEL = "DEBUG";
    };
  };

  # ============================================================================
  # Directory Structure
  # ============================================================================

  systemd.tmpfiles.rules = [
    "d /var/lib/mcp-server 0755 mcp users -"
    "d /var/lib/mcp-server/runbooks 0755 mcp users -"
    "d /var/lib/mcp-server/evidence 0755 mcp users -"
    "d /var/lib/mcp-server/logs 0755 mcp users -"
  ];

  # ============================================================================
  # Test Services (for demonstration)
  # ============================================================================

  systemd.services.mcp-health-check = {
    description = "MCP Server Health Check";

    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeScript "health-check" ''
        #!${pkgs.bash}/bin/bash
        echo "=== MCP Server Health Check ==="

        # Check Redis
        if ${pkgs.systemd}/bin/systemctl is-active redis-mcp > /dev/null 2>&1; then
          echo "✓ Redis is running"
        else
          echo "✗ Redis is NOT running"
        fi

        # Check MCP Server
        if ${pkgs.systemd}/bin/systemctl is-active mcp-server > /dev/null 2>&1; then
          echo "✓ MCP Server is running"
        else
          echo "✗ MCP Server is NOT running"
        fi

        # Check ports
        echo ""
        echo "Listening ports:"
        ${pkgs.nettools}/bin/netstat -tlnp 2>/dev/null | grep -E ':(22|6379|8000)' || echo "  (ports not yet bound)"

        echo ""
        echo "Health check complete"
      '';
    };
  };

  # Run health check on boot
  systemd.timers.mcp-health-check = {
    description = "MCP Server Health Check Timer";
    wantedBy = [ "timers.target" ];

    timerConfig = {
      OnBootSec = "30s";
      OnUnitActiveSec = "5min";
    };
  };

  # ============================================================================
  # Motd (Message of the Day)
  # ============================================================================

  users.motd = ''
    ╔════════════════════════════════════════════════════════════╗
    ║            MSP MCP Server - Central Control Plane          ║
    ╚════════════════════════════════════════════════════════════╝

    Services:
      • MCP Server:    systemctl status mcp-server
      • Redis Queue:   systemctl status redis-mcp
      • Health Check:  systemctl start mcp-health-check

    Logs:
      • MCP Server:    journalctl -u mcp-server -f
      • Redis:         journalctl -u redis-mcp -f

    Configuration:
      • Runbooks:      /var/lib/mcp-server/runbooks/
      • Evidence:      /var/lib/mcp-server/evidence/
      • Logs:          /var/lib/mcp-server/logs/

    Network:
      • API Endpoint:  http://<this-vm-ip>:8000
      • Redis:         localhost:6379 (internal only)

    Next Steps:
      1. Change passwords: passwd root && passwd mcp
      2. Deploy real MCP server code
      3. Configure LLM API key (OpenAI/Azure)
      4. Add runbooks to /var/lib/mcp-server/runbooks/
      5. Connect compliance agents
  '';
}
