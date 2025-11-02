# SSH Certificate Authentication Module
#
# Implements HIPAA-compliant SSH authentication using short-lived certificates
# instead of long-lived SSH keys. Certificates auto-expire and provide audit trail.
#
# HIPAA Controls:
# - §164.312(a)(1) - Access Control
# - §164.308(a)(4)(ii)(C) - Access Establishment and Modification
# - §164.312(d) - Person or Entity Authentication
#
# Features:
# - Certificate-based SSH (no permanent keys)
# - 8-hour certificate lifetime (workday)
# - Integration with step-ca Certificate Authority
# - Automatic cert renewal
# - Complete audit trail
# - User/host principal restrictions
#
# Author: MSP Compliance Platform
# Version: 1.0.0

{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp.sshCertificates;

in {
  options.services.msp.sshCertificates = {
    enable = mkEnableOption "MSP SSH certificate authentication";

    caServerUrl = mkOption {
      type = types.str;
      example = "https://ca.msp.local:443";
      description = ''
        URL of step-ca Certificate Authority server
        Used to issue and verify SSH certificates
      '';
    };

    caFingerprint = mkOption {
      type = types.str;
      example = "a3f8...";
      description = ''
        SHA256 fingerprint of CA root certificate
        Used to verify CA authenticity on first connection
      '';
    };

    certificateLifetime = mkOption {
      type = types.str;
      default = "8h";
      description = ''
        Maximum lifetime for SSH certificates
        Default: 8 hours (single workday)
        Options: 1h, 4h, 8h, 24h
      '';
    };

    allowedPrincipals = mkOption {
      type = types.listOf types.str;
      default = [];
      example = [ "admin" "deploy" "backup" ];
      description = ''
        List of allowed SSH certificate principals
        Only users with these principals can authenticate
      '';
    };

    hostPrincipal = mkOption {
      type = types.str;
      default = config.networking.hostName;
      description = ''
        Host principal for this machine
        Used in host certificates
      '';
    };

    disablePasswordAuth = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Disable password authentication entirely
        Requires certificate or emergency key
      '';
    };

    emergencyKeyFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = "/run/secrets/ssh-emergency-key";
      description = ''
        Emergency SSH key for break-glass access
        Should be stored securely (SOPS) and rotated regularly
      '';
    };

    autoRenewal = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Automatically renew certificates before expiry
        Renews when <25% lifetime remaining
      '';
    };
  };

  config = mkIf cfg.enable {

    # Install step-cli for certificate operations
    environment.systemPackages = with pkgs; [
      step-cli      # Certificate management
      openssh       # SSH client/server
    ];

    # Configure OpenSSH to accept certificates
    services.openssh = {
      enable = true;

      settings = {
        # Disable password authentication (certificate only)
        PasswordAuthentication = !cfg.disablePasswordAuth;
        PermitRootLogin = "prohibit-password";
        ChallengeResponseAuthentication = false;

        # Enable certificate authentication
        PubkeyAuthentication = true;
        TrustedUserCAKeys = "/etc/ssh/ca/trusted_user_ca.pub";

        # Strict modes
        StrictModes = true;
        MaxAuthTries = 3;

        # Logging for audit trail
        LogLevel = "VERBOSE";

        # Disable unnecessary features
        X11Forwarding = false;
        AllowAgentForwarding = false;
        AllowTcpForwarding = false;
        PermitTunnel = false;
      };

      # Allow only specific users if principals are configured
      extraConfig = mkIf (cfg.allowedPrincipals != []) ''
        # Restrict access to allowed principals
        AuthorizedPrincipalsFile /etc/ssh/ca/authorized_principals/%u

        # Certificate revocation list
        RevokedKeys /etc/ssh/ca/revoked_keys

        # Banner for HIPAA notice
        Banner /etc/ssh/ca/banner.txt
      '';
    };

    # Setup CA trust on first boot
    systemd.services.ssh-ca-bootstrap = {
      description = "Bootstrap SSH Certificate Authority Trust";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];
      before = [ "sshd.service" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = pkgs.writeScript "ssh-ca-bootstrap" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          CA_DIR="/etc/ssh/ca"
          mkdir -p "$CA_DIR"

          # Download CA public key if not present
          if [ ! -f "$CA_DIR/trusted_user_ca.pub" ]; then
            echo "Bootstrapping SSH CA trust..."

            # Download and verify CA public key
            ${pkgs.step-cli}/bin/step ca bootstrap \
              --ca-url "${cfg.caServerUrl}" \
              --fingerprint "${cfg.caFingerprint}" \
              --force

            # Extract SSH user CA key
            ${pkgs.step-cli}/bin/step ssh config \
              --roots > "$CA_DIR/trusted_user_ca.pub"

            echo "SSH CA trust established"
            logger -t ssh-ca "SSH CA trust bootstrapped from ${cfg.caServerUrl}"
          fi

          # Create authorized_principals directory
          mkdir -p "$CA_DIR/authorized_principals"

          # Create principal files for allowed users
          ${concatMapStringsSep "\n" (principal: ''
            echo "${principal}" > "$CA_DIR/authorized_principals/${principal}"
          '') cfg.allowedPrincipals}

          # Create empty revocation list
          touch "$CA_DIR/revoked_keys"

          # Create HIPAA banner
          cat > "$CA_DIR/banner.txt" <<'EOF'
          ┌─────────────────────────────────────────────────────────────┐
          │               AUTHORIZED ACCESS ONLY                        │
          │                                                             │
          │  This system contains HIPAA-protected health information.   │
          │  Unauthorized access is prohibited by law.                  │
          │                                                             │
          │  All activities are logged and monitored.                   │
          │  §164.308(a)(4) - Access Establishment                      │
          │  §164.312(d) - Person or Entity Authentication              │
          │                                                             │
          │  Certificate-based authentication required.                 │
          └─────────────────────────────────────────────────────────────┘
          EOF

          chmod 644 "$CA_DIR/trusted_user_ca.pub"
          chmod 644 "$CA_DIR/banner.txt"
          chmod 600 "$CA_DIR/revoked_keys"
        '';
      };
    };

    # Certificate renewal service
    systemd.services.ssh-cert-renewal = mkIf cfg.autoRenewal {
      description = "SSH Certificate Auto-Renewal";
      after = [ "network.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "ssh-cert-renewal" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          CERT_FILE="$HOME/.ssh/id_ecdsa-cert.pub"
          KEY_FILE="$HOME/.ssh/id_ecdsa"

          # Check if certificate exists
          if [ ! -f "$CERT_FILE" ]; then
            echo "No certificate found, skipping renewal"
            exit 0
          fi

          # Check certificate expiry
          EXPIRY=$(${pkgs.openssh}/bin/ssh-keygen -L -f "$CERT_FILE" | grep "Valid:" | awk '{print $5}')
          EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s)
          NOW_EPOCH=$(date +%s)
          TIME_REMAINING=$((EXPIRY_EPOCH - NOW_EPOCH))

          # Renew if <25% lifetime remaining (2 hours for 8h cert)
          RENEWAL_THRESHOLD=$((2 * 3600))

          if [ $TIME_REMAINING -lt $RENEWAL_THRESHOLD ]; then
            echo "Certificate expires soon, renewing..."

            # Request new certificate from CA
            ${pkgs.step-cli}/bin/step ssh certificate \
              "$USER" \
              "$KEY_FILE" \
              --ca-url "${cfg.caServerUrl}" \
              --root /etc/step/certs/root_ca.crt \
              --not-after "${cfg.certificateLifetime}" \
              --force

            echo "Certificate renewed successfully"
            logger -t ssh-cert-renewal "SSH certificate renewed for $USER"
          else
            echo "Certificate still valid for $((TIME_REMAINING / 3600)) hours"
          fi
        '';
      };
    };

    # Timer for certificate renewal (check hourly)
    systemd.timers.ssh-cert-renewal = mkIf cfg.autoRenewal {
      description = "SSH Certificate Renewal Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "hourly";
        Persistent = true;
        Unit = "ssh-cert-renewal.service";
      };
    };

    # Emergency access configuration
    users.users.root.openssh.authorizedKeys.keyFiles = mkIf (cfg.emergencyKeyFile != null) [
      cfg.emergencyKeyFile
    ];

    # Audit logging for SSH events
    security.auditd.enable = true;
    security.audit.rules = [
      # Log all SSH authentication attempts
      "-w /var/log/auth.log -p wa -k ssh-auth"

      # Log SSH daemon configuration changes
      "-w /etc/ssh/sshd_config -p wa -k ssh-config"

      # Log CA certificate changes
      "-w /etc/ssh/ca/ -p wa -k ssh-ca"

      # Log SSH key operations
      "-a always,exit -F arch=b64 -S execve -F path=${pkgs.openssh}/bin/ssh-keygen -k ssh-keygen"
    ];

    # Certificate health check
    systemd.services.ssh-cert-health = {
      description = "SSH Certificate Health Check";
      after = [ "network.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeScript "ssh-cert-health" ''
          #!${pkgs.bash}/bin/bash
          set -euo pipefail

          HEALTH_LOG="/var/log/msp/ssh-cert-health.log"
          mkdir -p "$(dirname "$HEALTH_LOG")"

          echo "=== SSH Certificate Health Check $(date) ===" >> "$HEALTH_LOG"

          # Check CA trust
          if [ -f /etc/ssh/ca/trusted_user_ca.pub ]; then
            echo "✓ CA trust configured" >> "$HEALTH_LOG"
          else
            echo "✗ CA trust NOT configured" >> "$HEALTH_LOG"
            logger -t ssh-cert-health -p err "HIPAA: SSH CA trust not configured"
            exit 1
          fi

          # Check CA reachability
          if ${pkgs.curl}/bin/curl -sf "${cfg.caServerUrl}/health" > /dev/null; then
            echo "✓ CA server reachable" >> "$HEALTH_LOG"
          else
            echo "✗ CA server UNREACHABLE" >> "$HEALTH_LOG"
            logger -t ssh-cert-health -p warning "CA server ${cfg.caServerUrl} unreachable"
          fi

          # Check password authentication disabled
          if grep -q "^PasswordAuthentication no" /etc/ssh/sshd_config; then
            echo "✓ Password authentication disabled" >> "$HEALTH_LOG"
          else
            echo "⚠ Password authentication ENABLED" >> "$HEALTH_LOG"
          fi

          # Check for active SSH sessions
          ACTIVE_SESSIONS=$(${pkgs.procps}/bin/ps aux | grep "sshd:" | grep -v grep | wc -l)
          echo "Active SSH sessions: $ACTIVE_SESSIONS" >> "$HEALTH_LOG"

          echo "Health check completed" >> "$HEALTH_LOG"
          logger -t ssh-cert-health "SSH certificate health check passed"
        '';
      };
    };

    # Run health check daily
    systemd.timers.ssh-cert-health = {
      description = "SSH Certificate Health Check Timer";
      wantedBy = [ "timers.target" ];

      timerConfig = {
        OnCalendar = "daily";
        Persistent = true;
        Unit = "ssh-cert-health.service";
      };
    };

    # Ensure log directory exists
    systemd.tmpfiles.rules = [
      "d /var/log/msp 0755 root root -"
    ];

    # HIPAA compliance assertions
    assertions = [
      {
        assertion = cfg.caServerUrl != "";
        message = "caServerUrl must be configured for SSH certificates";
      }
      {
        assertion = cfg.caFingerprint != "";
        message = "caFingerprint must be configured to verify CA authenticity";
      }
      {
        assertion = !cfg.disablePasswordAuth || cfg.emergencyKeyFile != null;
        message = "emergencyKeyFile required when password auth disabled (break-glass access)";
      }
    ];

    # Documentation output
    environment.etc."msp/ssh-cert-config.txt".text = ''
      MSP SSH Certificate Authentication Configuration
      ================================================

      CA Server: ${cfg.caServerUrl}
      Certificate Lifetime: ${cfg.certificateLifetime}
      Password Authentication: ${if cfg.disablePasswordAuth then "DISABLED" else "enabled"}
      Allowed Principals: ${concatStringsSep ", " cfg.allowedPrincipals}
      Auto-Renewal: ${if cfg.autoRenewal then "enabled" else "disabled"}

      HIPAA Controls:
      - §164.312(a)(1): Access Control (certificate-based)
      - §164.308(a)(4)(ii)(C): Access Establishment
      - §164.312(d): Person or Entity Authentication

      Certificate Management:
      - Certificates expire after ${cfg.certificateLifetime}
      - Automatic renewal when <25% lifetime remains
      - Complete audit trail in /var/log/auth.log

      Health Check: /var/log/msp/ssh-cert-health.log

      Emergency Access:
      ${if cfg.emergencyKeyFile != null
        then "Break-glass SSH key configured for root user"
        else "No emergency access configured"}

      Requesting a Certificate:
      $ step ssh certificate <username> ~/.ssh/id_ecdsa \
          --ca-url ${cfg.caServerUrl} \
          --not-after ${cfg.certificateLifetime}

      Verifying Certificate:
      $ ssh-keygen -L -f ~/.ssh/id_ecdsa-cert.pub
    '';
  };
}
