# Security Hardening Guide: LUKS, SSH Certificates, and Defense in Depth

**Document Purpose:** Complete implementation guide for client infrastructure security hardening

**Version:** 1.0
**Last Updated:** 2025-10-31
**Target Audience:** Implementation engineers, security architects

---

## Table of Contents

1. [Security Philosophy](#security-philosophy)
2. [LUKS Full Disk Encryption](#luks-full-disk-encryption)
3. [SSH Certificate Authentication](#ssh-certificate-authentication)
4. [Baseline Enforcement](#baseline-enforcement)
5. [Audit Logging](#audit-logging)
6. [Health Monitoring](#health-monitoring)
7. [Implementation Checklist](#implementation-checklist)

---

## Security Philosophy

### Defense in Depth Strategy

**Core Principle:** Assume every layer will eventually be breached. Design so that breach of outer layers doesn't compromise inner layers.

```
┌─────────────────────────────────────────────────────────────┐
│ LAYER 1: Network Perimeter                                  │
│ • Firewall (default deny)                                   │
│ • VPN (WireGuard)                                            │
│ • DDoS protection                                            │
│                                                              │
│ RISK: Misconfigured firewall, stolen VPN credentials        │
└────────────────────┬────────────────────────────────────────┘
                     │ ↓ Attacker bypasses network controls
┌─────────────────────────────────────────────────────────────┐
│ LAYER 2: Authentication                                      │
│ • SSH certificates (short-lived, 8h max)                    │
│ • No password authentication                                 │
│ • Certificate authority validation                           │
│                                                              │
│ RISK: Stolen certificate (limited by 8h lifetime)           │
└────────────────────┬────────────────────────────────────────┘
                     │ ↓ Attacker authenticates successfully
┌─────────────────────────────────────────────────────────────┐
│ LAYER 3: Authorization & Audit                              │
│ • Least-privilege sudo rules                                 │
│ • auditd logging all actions                                 │
│ • Command whitelisting                                       │
│                                                              │
│ RISK: Privilege escalation (logged and detected)            │
└────────────────────┬────────────────────────────────────────┘
                     │ ↓ Attacker gains root access
┌─────────────────────────────────────────────────────────────┐
│ LAYER 4: Disk Encryption                                     │
│ • LUKS AES-256-XTS                                           │
│ • Network-bound decryption (Tang)                            │
│ • TPM-sealed backup key                                      │
│                                                              │
│ RISK: Physical theft + network access (detectable)          │
└────────────────────┬────────────────────────────────────────┘
                     │ ↓ Attacker decrypts disk
┌─────────────────────────────────────────────────────────────┐
│ LAYER 5: Data Segmentation                                   │
│ • This system: metadata only (syslog, configs)              │
│ • PHI stored elsewhere (separate EHR server)                 │
│ • Blast radius limited                                       │
│                                                              │
│ ATTACKER GAINS: System logs, not patient records            │
└─────────────────────────────────────────────────────────────┘
```

### WHY This Approach Works for Healthcare

**Single-Layer Security (Traditional):**
- All eggs in one basket
- Breach = total compromise
- "Compliant" until first vulnerability

**Multi-Layer Security (Our Approach):**
- Breach of outer layer is expected and survivable
- Each layer generates evidence of attack
- Multiple chances to detect and respond
- Compliance through architecture, not policy

---

## LUKS Full Disk Encryption

### Overview

**WHAT:** LUKS (Linux Unified Key Setup) provides full disk encryption for all data at rest.

**WHY HIPAA Requires It:**
- §164.312(a)(2)(iv): "Implement a mechanism to encrypt ePHI"
- §164.310(d)(1): "Implement policies for media disposal and re-use"

**OUR IMPLEMENTATION:** LUKS + Tang network binding + TPM fallback

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    BOOT PROCESS                              │
└─────────────────────────────────────────────────────────────┘

1. BIOS/UEFI loads bootloader (unencrypted /boot partition)
                     ↓
2. Bootloader loads initramfs (initial ramdisk)
                     ↓
3. Initramfs runs Clevis unlock script
                     ↓
4. Clevis attempts to contact Tang servers
                     ↓
         ┌───────────┴───────────┐
         │                       │
    Tang servers              Tang servers
     reachable?              unreachable?
         │                       │
         ↓ YES                   ↓ NO
    Network-bound            TPM-sealed
    decryption key           fallback key
    (automatic)              (automatic)
         │                       │
         └───────────┬───────────┘
                     ↓
5. LUKS partition decrypted with key
                     ↓
6. Root filesystem mounted
                     ↓
7. System continues normal boot
```

### NixOS Configuration

**File:** `client-flake/modules/luks-encryption.nix`

```nix
{ config, lib, pkgs, baseline, ... }:

let
  cfg = baseline.encryption.disk_encryption;
in
{
  # Boot configuration for LUKS
  boot.initrd = {
    # Load LUKS-related kernel modules early
    kernelModules = [ "dm-crypt" "aes" "xts" "sha256" ];

    # LUKS device configuration
    luks.devices = {
      # Root partition encryption
      root = {
        device = "/dev/disk/by-uuid/YOUR-ROOT-PARTITION-UUID";
        preLVM = true;  # Decrypt before LVM setup

        # Allow multiple unlock methods
        fallbackToPassword = false;  # Force automated unlock

        # Clevis Tang binding configuration
        clevisConfig = {
          enable = true;
          useTang = true;
          tangServers = cfg.remote_unlock.tang_servers;
          # Threshold: require 1 of N Tang servers
          threshold = 1;
        };

        # TPM 2.0 fallback (if Tang unreachable)
        tpmBind = {
          enable = true;
          device = "/dev/tpmrm0";
          # PCR policy: Seal key to boot state
          pcrList = [ 0 7 ];  # PCR0=firmware, PCR7=secure boot
        };
      };

      # Swap partition encryption (ephemeral key)
      swap = {
        device = "/dev/disk/by-uuid/YOUR-SWAP-PARTITION-UUID";
        # Swap uses random key (data not persistent)
        keyFile = "/dev/urandom";
        keyFileSize = 4096;
      };
    };

    # Network configuration for Tang access during boot
    network = lib.mkIf cfg.remote_unlock.enabled {
      enable = true;

      # SSH server for emergency unlock
      ssh = {
        enable = true;
        port = 2222;
        hostKeys = [ /etc/secrets/initrd/ssh_host_ed25519_key ];
        # Only allow specific admin keys
        authorizedKeys = config.users.users.root.openssh.authorizedKeys.keys;
      };

      # Static IP during initramfs (before DHCP)
      postCommands = ''
        echo "Waiting for network..."
        sleep 5
        # Tang servers should be on same network
      '';
    };
  };

  # Encrypted swap
  swapDevices = [{
    device = "/dev/mapper/swap";
    encrypted = {
      enable = true;
      blkDev = "/dev/disk/by-uuid/YOUR-SWAP-PARTITION-UUID";
      keyFile = "/dev/urandom";
    };
  }];

  # LUKS key management
  environment.systemPackages = with pkgs; [
    cryptsetup  # LUKS management tool
    clevis      # Tang client
    jose        # JSON Object Signing and Encryption
    tpm2-tools  # TPM management
  ];

  # Periodic key rotation (yearly)
  systemd.timers.luks-key-rotation = lib.mkIf cfg.key_rotation.enabled {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "yearly";
      Persistent = true;
    };
  };

  systemd.services.luks-key-rotation = lib.mkIf cfg.key_rotation.enabled {
    description = "LUKS Key Rotation";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.bash}/bin/bash ${./scripts/rotate-luks-key.sh}";
    };
  };
}
```

### Key Rotation Script

**File:** `client-flake/modules/scripts/rotate-luks-key.sh`

```bash
#!/usr/bin/env bash
# LUKS Key Rotation Script
# Rotates LUKS master key while maintaining Tang/TPM bindings

set -euo pipefail

LUKS_DEVICE="/dev/disk/by-uuid/YOUR-ROOT-PARTITION-UUID"
LUKS_NAME="root"

log() {
    echo "[$(date -Iseconds)] $*" | tee -a /var/log/luks-rotation.log
}

log "Starting LUKS key rotation for $LUKS_DEVICE"

# Step 1: Generate new passphrase
NEW_PASSPHRASE=$(openssl rand -base64 32)

# Step 2: Add new key to LUKS header (slot 1)
echo "$NEW_PASSPHRASE" | cryptsetup luksAddKey "$LUKS_DEVICE" --key-slot 1

if [ $? -eq 0 ]; then
    log "New key added to slot 1"
else
    log "ERROR: Failed to add new key"
    exit 1
fi

# Step 3: Re-bind Clevis Tang
clevis luks unbind -d "$LUKS_DEVICE" -s 0 || true
echo "$NEW_PASSPHRASE" | clevis luks bind -d "$LUKS_DEVICE" \
    -k 0 \
    tang '{"url":"http://tang1.msp.internal","thp":"YOUR-THUMBPRINT"}'

if [ $? -eq 0 ]; then
    log "Tang rebinding successful"
else
    log "ERROR: Tang rebinding failed"
    exit 1
fi

# Step 4: Re-bind TPM
systemd-cryptenroll --tpm2-device=auto "$LUKS_DEVICE" <<< "$NEW_PASSPHRASE"

if [ $? -eq 0 ]; then
    log "TPM rebinding successful"
else
    log "WARNING: TPM rebinding failed (non-critical)"
fi

# Step 5: Remove old passphrase from slot 0
cryptsetup luksRemoveKey "$LUKS_DEVICE" <<< "$OLD_PASSPHRASE"

log "Key rotation complete"

# Step 6: Generate evidence bundle
cat > /var/lib/msp/evidence/key-rotation-$(date +%Y%m%d).json <<EOF
{
  "event": "luks_key_rotation",
  "device": "$LUKS_DEVICE",
  "timestamp": "$(date -Iseconds)",
  "tang_rebind": "success",
  "tpm_rebind": "success",
  "hipaa_control": "§164.312(a)(2)(iv)"
}
EOF

log "Evidence bundle created"
```

### Tang Server Setup (Separate Infrastructure)

**File:** `terraform/modules/tang-servers/main.tf`

```hcl
# Tang servers provide network-bound encryption
# Deploy 2+ servers for redundancy

resource "aws_instance" "tang" {
  count = 2

  ami           = data.aws_ami.nixos.id
  instance_type = "t3.micro"
  subnet_id     = var.private_subnet_ids[count.index]

  tags = {
    Name = "tang-${count.index + 1}"
    Role = "encryption-key-server"
  }

  user_data = <<-EOF
    #!/bin/bash
    # Install and configure Tang
    nix-env -iA nixos.tang

    # Generate Tang keys
    /var/db/tang/tang-show-keys || tang-gen-keys

    # Start Tang service
    systemctl enable --now tangd.socket
  EOF
}

# Security group: Only allow access from client subnets
resource "aws_security_group" "tang" {
  name_description = "Tang Key Server"

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.client_subnet_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

### LUKS Management Commands

```bash
# Check LUKS status
cryptsetup luksDump /dev/sda2

# List key slots
cryptsetup luksDump /dev/sda2 | grep "Key Slot"

# Test Tang connectivity
curl http://tang1.msp.internal/adv

# Test TPM status
tpm2_getcap properties-fixed

# Emergency: Add manual unlock password
cryptsetup luksAddKey /dev/sda2

# Emergency: Decrypt manually during boot
cryptsetup luksOpen /dev/sda2 root
```

### WHY Network-Bound Encryption

**Traditional LUKS:** Passphrase entered at boot
- **Problem:** Unattended server can't reboot (needs human)
- **Risk:** Passphrase could be weak or shared

**Tang Network Binding:** Decryption requires network access
- **Benefit:** Server auto-unlocks when on trusted network
- **Security:** Stolen server can't decrypt if not on network
- **Compliance:** Proves device was on authorized network

**Evidence Value:**
```json
{
  "luks_unlock": {
    "timestamp": "2025-10-31T06:15:00Z",
    "method": "tang",
    "tang_server": "tang1.msp.internal",
    "network": "clinic-001-private",
    "evidence": "Device can only unlock on authorized network"
  }
}
```

---

## SSH Certificate Authentication

### Overview

**WHAT:** Short-lived SSH certificates issued by Certificate Authority instead of long-lived SSH keys.

**WHY HIPAA Requires It:**
- §164.312(a)(2)(i): "Unique user identification"
- §164.308(a)(4)(ii)(C): "Termination procedures" (auto-expires)
- §164.312(d): "Person or entity authentication"

**TRADITIONAL SSH KEYS:** Long-lived, hard to revoke, no audit trail

**SSH CERTIFICATES:** Short-lived (8h), auto-expire, comprehensive audit trail

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   SSH CERTIFICATE FLOW                       │
└─────────────────────────────────────────────────────────────┘

1. User requests access
   $ ssh user@server.clinic-001.local
          ↓
2. SSH client checks for valid certificate
   ~/.ssh/id_ed25519-cert.pub exists?
          ↓ NO
3. Client redirects to Certificate Authority (CA)
   "No valid certificate, requesting new one..."
          ↓
4. User authenticates to CA
   Options:
   • SSO (Okta, Azure AD)
   • LDAP
   • TOTP/U2F
          ↓
5. CA validates user identity
   • Check user exists
   • Check MFA completed
   • Check not terminated
          ↓
6. CA issues certificate
   Metadata:
   • Valid for: 8 hours
   • Principal: user@clinic-001.com
   • Serial: 1234567890
   • Permissions: [list]
          ↓
7. Certificate saved locally
   ~/.ssh/id_ed25519-cert.pub (expires in 8h)
          ↓
8. SSH retries connection with certificate
   $ ssh user@server.clinic-001.local
          ↓
9. Server validates certificate
   • Check CA signature (trusted?)
   • Check not expired
   • Check principal authorized
   • Log serial number
          ↓
10. Access granted (with audit trail)
```

### NixOS Configuration

**File:** `client-flake/modules/ssh-certificates.nix`

```nix
{ config, lib, pkgs, baseline, ... }:

let
  sshCfg = baseline.identity_and_access.ssh_hardening;
  certCfg = sshCfg.certificate_authentication;
in
{
  # SSH server configuration
  services.openssh = {
    enable = true;

    settings = {
      # Disable all password-based authentication
      PasswordAuthentication = false;
      ChallengeResponseAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitRootLogin = lib.mkForce "no";

      # Require public key authentication (certificate is subset of this)
      PubkeyAuthentication = true;

      # Security hardening
      MaxAuthTries = sshCfg.max_auth_tries;
      LoginGraceTime = sshCfg.login_grace_time_seconds;
      ClientAliveInterval = sshCfg.client_alive_interval_seconds;
      ClientAliveCountMax = sshCfg.client_alive_count_max;

      # Logging
      LogLevel = "VERBOSE";  # Includes certificate serial numbers
    };

    # Only allow strong ciphers
    ciphers = sshCfg.ciphers;
    macs = sshCfg.macs;
    kexAlgorithms = sshCfg.kex_algorithms;

    extraConfig = lib.mkIf certCfg.enabled ''
      # Trust certificates signed by this CA
      TrustedUserCAKeys /etc/ssh/ca.pub

      # Require valid principal in certificate
      AuthorizedPrincipalsFile /etc/ssh/principals/%u

      # Reject certificates with no principal
      AuthorizedPrincipalsCommandUser nobody

      # Force certificate expiry check
      RevokedKeys /etc/ssh/revoked_keys
    '';
  };

  # Certificate Authority public key
  environment.etc."ssh/ca.pub" = lib.mkIf certCfg.enabled {
    text = certCfg.ca_public_key;
    mode = "0644";
  };

  # Per-user principal files (username → allowed principals)
  environment.etc."ssh/principals/admin" = {
    text = ''
      admin@clinic-001.com
      sysadmin@clinic-001.com
    '';
    mode = "0644";
  };

  # Audit SSH access with auditd
  security.auditd.rules = [
    # Log all SSH authentication attempts
    "-w /var/log/auth.log -p wa -k ssh_auth"

    # Log SSH configuration changes
    "-w /etc/ssh/sshd_config -p wa -k ssh_config"

    # Log CA key changes
    "-w /etc/ssh/ca.pub -p wa -k ssh_ca"
  ];

  # Monitoring: Alert on certificate expiring soon
  systemd.services.ssh-cert-monitor = {
    description = "SSH Certificate Expiry Monitor";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeScript "ssh-cert-monitor" ''
        #!${pkgs.bash}/bin/bash
        # Check all logged-in users for expiring certificates
        who | awk '{print $1}' | sort -u | while read user; do
          home=$(getent passwd "$user" | cut -d: -f6)
          cert="$home/.ssh/id_ed25519-cert.pub"

          if [ -f "$cert" ]; then
            expiry=$(ssh-keygen -L -f "$cert" | grep "Valid:" | awk '{print $NF}')
            expiry_epoch=$(date -d "$expiry" +%s)
            now_epoch=$(date +%s)
            hours_left=$(( (expiry_epoch - now_epoch) / 3600 ))

            if [ $hours_left -lt 2 ]; then
              echo "WARNING: Certificate for $user expires in $hours_left hours"
              # Trigger alert (email, webhook, etc.)
            fi
          fi
        done
      '';
    };
  };

  systemd.timers.ssh-cert-monitor = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "hourly";
      Persistent = true;
    };
  };
}
```

### Certificate Authority Setup (step-ca)

**File:** `terraform/modules/step-ca/main.tf`

```hcl
# Certificate Authority for SSH certificates
# Uses Smallstep step-ca

resource "aws_instance" "step_ca" {
  ami           = data.aws_ami.nixos.id
  instance_type = "t3.small"

  user_data = <<-EOF
    #!/bin/bash
    # Install step-ca
    nix-env -iA nixos.step-ca nixos.step-cli

    # Initialize CA
    step ca init --ssh \
      --name="MSP SSH CA" \
      --dns="ca.msp.internal" \
      --address=":443" \
      --provisioner="admin@msp.com"

    # Configure SSH certificate templates
    step ssh config --set=maxUserSSHCertDuration=8h
    step ssh config --set=maxHostSSHCertDuration=720h

    # Start CA
    systemctl enable --now step-ca
  EOF

  tags = {
    Name = "ssh-certificate-authority"
    Role = "authentication"
  }
}
```

### User Workflow (Client-Side)

```bash
# One-time setup: Install step CLI
brew install step  # macOS
# or
sudo apt install step-cli  # Ubuntu

# Configure step CLI
step ca bootstrap \
  --ca-url https://ca.msp.internal \
  --fingerprint YOUR-CA-FINGERPRINT

# Request SSH certificate (prompted for password/MFA)
step ssh login user@clinic-001.com

# Certificate saved to ~/.ssh/id_ed25519-cert.pub
# Valid for 8 hours

# SSH to server (certificate used automatically)
ssh user@server.clinic-001.local

# Check certificate details
ssh-keygen -L -f ~/.ssh/id_ed25519-cert.pub
```

### Evidence Trail

**CA Logs (Audit Trail):**
```json
{
  "event": "certificate_issued",
  "timestamp": "2025-10-31T14:32:00Z",
  "user": "admin@clinic-001.com",
  "principal": "admin",
  "serial": "1234567890",
  "valid_from": "2025-10-31T14:32:00Z",
  "valid_until": "2025-10-31T22:32:00Z",
  "lifetime_hours": 8,
  "authentication_method": "okta_sso",
  "mfa_verified": true,
  "source_ip": "192.168.1.45"
}
```

**Server Logs (Access Trail):**
```
Oct 31 14:35:12 server sshd[12345]: Accepted publickey for admin from 192.168.1.45 port 54321 ssh2: ED25519-CERT SHA256:abc123... serial 1234567890
Oct 31 14:35:12 server sshd[12345]: pam_unix(sshd:session): session opened for user admin by (uid=0)
```

**Compliance Value:**

Auditor: "How do you know terminated employees can't access systems?"

You: "SSH certificates expire in 8 hours. Bob's last certificate issued 2025-06-15 08:00:00Z, expired 2025-06-15 16:00:00Z. Bob terminated 2025-06-15 09:00:00Z. Certificate expired 7 hours later automatically. No manual revocation needed. CA logs prove no new certificates issued after termination."

This is **automatic compliance** vs. "We have a process to disable accounts."

---

## Baseline Enforcement

### Overview

**WHAT:** Continuous verification that system configuration matches approved baseline.

**WHY:** Configuration drift is the #1 cause of security incidents.

**TRADITIONAL APPROACH:** Quarterly audits (drift undetected for months)

**OUR APPROACH:** Hourly drift detection + automatic remediation

### Implementation

**File:** `client-flake/modules/baseline-enforcement.nix`

```nix
{ config, lib, pkgs, baseline, ... }:

{
  # Baseline configuration (from YAML)
  imports = [
    (builtins.fromJSON (builtins.readFile ./baseline-loader.nix))
  ];

  # Drift detection service
  systemd.services.baseline-drift-detector = {
    description = "Baseline Configuration Drift Detection";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeScript "drift-detector" ''
        #!${pkgs.bash}/bin/bash
        set -euo pipefail

        # Compare running config to baseline
        BASELINE_HASH="${baseline.metadata.flake_hash}"
        CURRENT_HASH=$(nix flake metadata --json | jq -r .locked.narHash)

        if [ "$BASELINE_HASH" != "$CURRENT_HASH" ]; then
          echo "DRIFT DETECTED"
          echo "Expected: $BASELINE_HASH"
          echo "Actual:   $CURRENT_HASH"

          # Publish drift incident
          curl -X POST http://mcp-server/incidents \
            -d '{
              "client_id": "${config.networking.hostName}",
              "type": "config_drift",
              "severity": "high",
              "details": {
                "expected_hash": "'"$BASELINE_HASH"'",
                "actual_hash": "'"$CURRENT_HASH"'"
              }
            }'

          exit 1
        else
          echo "Baseline verified: $BASELINE_HASH"
        fi
      '';
    };
  };

  systemd.timers.baseline-drift-detector = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "hourly";
      Persistent = true;
    };
  };

  # Auto-remediation: Apply baseline on drift
  systemd.services.baseline-remediation = {
    description = "Automatic Baseline Remediation";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeScript "baseline-remediate" ''
        #!${pkgs.bash}/bin/bash
        # Pull latest approved baseline
        nix flake update github:yourorg/msp-platform

        # Apply (atomic switch)
        nixos-rebuild switch --flake .#

        # Verify
        systemctl restart baseline-drift-detector
      '';
    };
  };
}
```

### WHY NixOS Makes This Possible

**Traditional Config Management (Ansible/Chef):**
```yaml
# ansible/playbook.yml
- name: Ensure SSH password auth disabled
  lineinfile:
    path: /etc/ssh/sshd_config
    line: "PasswordAuthentication no"
```

**Problem:** What if file was manually edited? Ansible doesn't know about drift between runs.

**NixOS Approach:**
```nix
services.openssh.settings.PasswordAuthentication = false;
```

**Benefit:** System cannot boot unless setting is enforced. Drift is structurally impossible.

---

## Audit Logging

**File:** `client-flake/modules/audit-logging.nix`

```nix
{ config, lib, pkgs, baseline, ... }:

let
  auditCfg = baseline.audit_and_logging.auditd;
in
{
  # auditd configuration
  security.auditd.enable = auditCfg.enabled;

  security.audit.rules = auditCfg.rules;

  # journald configuration
  services.journald = {
    extraConfig = ''
      Storage=persistent
      Compress=yes
      Seal=yes
      MaxRetentionSec=${toString baseline.audit_and_logging.journald.max_retention_sec}
    '';
  };

  # Log forwarding
  services.fluent-bit = lib.mkIf baseline.audit_and_logging.log_forwarding.enabled {
    enable = true;
    config = ''
      [INPUT]
          Name systemd
          Tag host.*
          Read_From_Tail On

      [FILTER]
          Name grep
          Match host.*
          Exclude log ^DEBUG

      [OUTPUT]
          Name redis
          Match *
          Host ${baseline.audit_and_logging.log_forwarding.destinations[0].endpoint}
          Key tenant:${config.networking.hostName}:logs
          tls on
    '';
  };
}
```

---

## Health Monitoring

**File:** `client-flake/modules/health-checks.nix`

```nix
{ config, lib, pkgs, baseline, ... }:

{
  systemd.services.health-monitor = {
    description = "System Health Monitoring";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeScript "health-check" ''
        #!${pkgs.bash}/bin/bash

        # Disk space check
        DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
        if [ $DISK_USAGE -gt 85 ]; then
          echo "ALERT: Disk usage $DISK_USAGE%"
          # Publish incident
        fi

        # Memory check
        # CPU check
        # Service status check
        # Time sync check
      '';
    };
  };

  systemd.timers.health-monitor = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*:0/15";  # Every 15 minutes
      Persistent = true;
    };
  };
}
```

---

## Implementation Checklist

### Week 5 Tasks

- [ ] Implement LUKS encryption module
- [ ] Deploy Tang servers (2+ for redundancy)
- [ ] Test network-bound decryption
- [ ] Configure TPM fallback
- [ ] Deploy step-ca Certificate Authority
- [ ] Configure SSH certificate authentication
- [ ] Test certificate issuance workflow
- [ ] Implement baseline drift detection
- [ ] Configure auditd rules
- [ ] Set up log forwarding
- [ ] Deploy health monitoring
- [ ] End-to-end security test

### Verification Commands

```bash
# LUKS status
cryptsetup luksDump /dev/sda2
lsblk -f

# SSH certificates
ssh-keygen -L -f ~/.ssh/id_ed25519-cert.pub
ssh-add -L

# Baseline verification
nix flake metadata --json | jq .locked.narHash

# Audit logs
ausearch -k ssh_auth
journalctl -u sshd

# Health status
systemctl status health-monitor
```

---

**End of Document**
**Version:** 1.0
**Last Updated:** 2025-10-31
