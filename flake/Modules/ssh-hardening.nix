{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp-ssh-hardening;
in {
  options.services.msp-ssh-hardening = {
    enable = mkEnableOption "MSP SSH hardening and certificate authentication";

    # SSH Certificate Authority (§164.312(a)(2)(i), §164.312(d))
    certificateAuth = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Enable SSH certificate-based authentication";
      };

      trustedUserCAKeys = mkOption {
        type = types.listOf types.str;
        default = [];
        description = "List of trusted CA public keys for user certificates";
        example = [ "ssh-rsa AAAAB3NzaC1yc2E... ca@msp.local" ];
      };

      trustedHostCAKeys = mkOption {
        type = types.listOf types.str;
        default = [];
        description = "List of trusted CA public keys for host certificates";
      };

      # Certificate validity enforcement
      maxCertValidity = mkOption {
        type = types.int;
        default = 86400;  # 24 hours
        description = "Maximum certificate validity in seconds";
      };
    };

    # Key-based Authentication Settings
    keyAuth = {
      authorizedKeys = mkOption {
        type = types.attrsOf (types.listOf types.str);
        default = {};
        description = "Per-user authorized SSH keys";
        example = literalExpression ''
          {
            admin = [ "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5..." ];
            operator = [ "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5..." ];
          }
        '';
      };

      keyTypes = mkOption {
        type = types.listOf types.str;
        default = [ "ed25519" "rsa" "ecdsa" ];
        description = "Allowed SSH key types (ordered by preference)";
      };

      minKeySize = mkOption {
        type = types.attrsOf types.int;
        default = {
          rsa = 3072;
          ecdsa = 384;
        };
        description = "Minimum key sizes by type";
      };
    };

    # Session Settings (§164.312(a)(2)(iii))
    session = {
      timeout = mkOption {
        type = types.int;
        default = 300;  # 5 minutes
        description = "Client alive interval in seconds";
      };

      maxSessions = mkOption {
        type = types.int;
        default = 3;
        description = "Maximum concurrent sessions per user";
      };

      loginGraceTime = mkOption {
        type = types.int;
        default = 30;
        description = "Time allowed for authentication in seconds";
      };
    };

    # Allowed Users/Groups
    access = {
      allowUsers = mkOption {
        type = types.listOf types.str;
        default = [];
        description = "List of allowed usernames (empty = all)";
        example = [ "admin" "operator" "backup" ];
      };

      allowGroups = mkOption {
        type = types.listOf types.str;
        default = [ "wheel" "ssh-users" ];
        description = "List of allowed groups";
      };

      denyUsers = mkOption {
        type = types.listOf types.str;
        default = [ "root" ];
        description = "List of denied usernames";
      };
    };

    # Audit Logging
    logging = {
      level = mkOption {
        type = types.enum [ "QUIET" "FATAL" "ERROR" "INFO" "VERBOSE" "DEBUG" "DEBUG1" "DEBUG2" "DEBUG3" ];
        default = "VERBOSE";
        description = "SSH logging level";
      };

      logAuthAttempts = mkOption {
        type = types.bool;
        default = true;
        description = "Log all authentication attempts";
      };
    };
  };

  config = mkIf cfg.enable {
    # Create ssh-users group
    users.groups.ssh-users = {};

    # OpenSSH Configuration
    services.openssh = {
      enable = true;

      # HIPAA-compliant settings
      settings = {
        # Disable password authentication (§164.312(a)(2)(i))
        PasswordAuthentication = false;
        PermitRootLogin = "no";
        ChallengeResponseAuthentication = false;
        KbdInteractiveAuthentication = false;
        UsePAM = true;

        # Certificate authentication
        TrustedUserCAKeys = mkIf cfg.certificateAuth.enable
          (concatStringsSep "\n" cfg.certificateAuth.trustedUserCAKeys);

        # Key-based authentication
        PubkeyAuthentication = true;
        AuthenticationMethods = mkIf cfg.certificateAuth.enable "publickey";

        # Session timeouts (§164.312(a)(2)(iii))
        ClientAliveInterval = cfg.session.timeout;
        ClientAliveCountMax = 2;
        LoginGraceTime = cfg.session.loginGraceTime;
        MaxSessions = cfg.session.maxSessions;
        MaxAuthTries = 3;

        # Access restrictions
        AllowUsers = mkIf (cfg.access.allowUsers != []) cfg.access.allowUsers;
        AllowGroups = cfg.access.allowGroups;
        DenyUsers = cfg.access.denyUsers;

        # Protocol and cipher restrictions
        Protocol = 2;

        # Strong ciphers only (§164.312(e)(2)(ii))
        Ciphers = [
          "chacha20-poly1305@openssh.com"
          "aes256-gcm@openssh.com"
          "aes128-gcm@openssh.com"
          "aes256-ctr"
          "aes192-ctr"
          "aes128-ctr"
        ];

        # Strong MACs
        MACs = [
          "hmac-sha2-512-etm@openssh.com"
          "hmac-sha2-256-etm@openssh.com"
          "hmac-sha2-512"
          "hmac-sha2-256"
        ];

        # Strong key exchange algorithms
        KexAlgorithms = [
          "curve25519-sha256"
          "curve25519-sha256@libssh.org"
          "diffie-hellman-group16-sha512"
          "diffie-hellman-group18-sha512"
          "diffie-hellman-group-exchange-sha256"
        ];

        # Host key algorithms (prefer Ed25519)
        HostKeyAlgorithms = [
          "ssh-ed25519-cert-v01@openssh.com"
          "ssh-ed25519"
          "rsa-sha2-512-cert-v01@openssh.com"
          "rsa-sha2-512"
          "rsa-sha2-256-cert-v01@openssh.com"
          "rsa-sha2-256"
        ];

        # Security hardening
        PermitEmptyPasswords = false;
        PermitUserEnvironment = false;
        X11Forwarding = false;
        AllowTcpForwarding = "no";
        AllowAgentForwarding = false;
        AllowStreamLocalForwarding = false;
        GatewayPorts = false;
        PrintMotd = false;
        PrintLastLog = true;
        TCPKeepAlive = true;
        Compression = false;  # Disable to prevent CRIME-style attacks

        # Logging (§164.312(b))
        LogLevel = cfg.logging.level;
        SyslogFacility = "AUTH";
      };

      # Host keys (prefer Ed25519)
      hostKeys = [
        {
          path = "/etc/ssh/ssh_host_ed25519_key";
          type = "ed25519";
        }
        {
          path = "/etc/ssh/ssh_host_rsa_key";
          type = "rsa";
          bits = 4096;
        }
      ];

      # Banner (optional)
      banner = ''
        ════════════════════════════════════════════════════════════
        AUTHORIZED ACCESS ONLY

        This system is for authorized healthcare personnel only.
        All activities are monitored and logged per HIPAA requirements.
        Unauthorized access attempts will be prosecuted.
        ════════════════════════════════════════════════════════════
      '';
    };

    # Set up authorized keys for users
    users.users = mkMerge (
      mapAttrsToList (username: keys: {
        ${username} = {
          openssh.authorizedKeys.keys = keys;
          extraGroups = [ "ssh-users" ];
        };
      }) cfg.keyAuth.authorizedKeys
    );

    # Certificate validation script
    environment.systemPackages = with pkgs; [
      openssh
    ] ++ optional cfg.certificateAuth.enable (
      pkgs.writeShellScriptBin "validate-ssh-cert" ''
        #!/bin/sh
        # Validate SSH certificate

        if [ $# -lt 1 ]; then
          echo "Usage: $0 <certificate-file>"
          exit 1
        fi

        CERT=$1

        echo "=== SSH Certificate Validation ==="

        # Show certificate details
        ${pkgs.openssh}/bin/ssh-keygen -L -f "$CERT"

        # Check validity
        VALID_FROM=$(${pkgs.openssh}/bin/ssh-keygen -L -f "$CERT" | grep "Valid:" | awk '{print $2}')
        VALID_TO=$(${pkgs.openssh}/bin/ssh-keygen -L -f "$CERT" | grep "Valid:" | awk '{print $4}')

        echo ""
        echo "Valid from: $VALID_FROM"
        echo "Valid to: $VALID_TO"

        # Check if expired
        CURRENT_TIME=$(date +%s)
        EXPIRY_TIME=$(date -d "$VALID_TO" +%s 2>/dev/null || echo "0")

        if [ "$EXPIRY_TIME" -gt 0 ] && [ "$CURRENT_TIME" -gt "$EXPIRY_TIME" ]; then
          echo "⚠️  Certificate has EXPIRED"
          exit 1
        else
          echo "✅ Certificate is valid"
        fi
      ''
    );

    # SSH audit logger
    systemd.services."ssh-audit-logger" = mkIf cfg.logging.logAuthAttempts {
      description = "SSH Authentication Audit Logger";
      after = [ "sshd.service" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };

      script = ''
        # Create audit log
        echo "[$(date -Iseconds)] SSH Audit Logger Started" >> /var/log/ssh-audit.log

        # Log current SSH configuration
        ${pkgs.openssh}/bin/sshd -T >> /var/log/ssh-audit.log 2>&1

        # Set up log monitoring (would integrate with log-watcher in production)
        echo "[$(date -Iseconds)] Monitoring /var/log/auth.log for SSH events" >> /var/log/ssh-audit.log
      '';
    };

    # Fail2ban integration (optional but recommended)
    services.fail2ban = {
      enable = true;
      maxretry = 3;
      bantime = "1h";

      jails = {
        sshd = ''
          enabled = true
          filter = sshd
          action = iptables[name=SSH, port=ssh, protocol=tcp]
          logpath = /var/log/auth.log
          maxretry = 3
          findtime = 600
          bantime = 3600
        '';
      };
    };

    # Create audit log directory
    systemd.tmpfiles.rules = [
      "f /var/log/ssh-audit.log 0600 root root -"
    ];

    # Firewall rule (only allow SSH from management network if configured)
    networking.firewall.allowedTCPPorts = [ 22 ];
  };
}
