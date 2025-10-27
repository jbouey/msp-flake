{ config, lib, pkgs, ... }:

with lib;

{
  # MSP Automation Platform - Base Configuration
  # HIPAA-compliant baseline for all managed systems

  imports = [
    ./encryption.nix
    ./ssh-hardening.nix
    ./secrets.nix
    ./timesync.nix
    ./log-watcher.nix
  ];

  options.services.msp-base = {
    enable = mkEnableOption "MSP base configuration with HIPAA compliance";

    clientId = mkOption {
      type = types.str;
      description = "Unique client identifier";
      example = "clinic-001";
    };

    mcpServerUrl = mkOption {
      type = types.str;
      description = "MCP server URL for automated remediation";
      example = "https://mcp.yourmsp.com";
    };

    # Baseline enforcement
    enforceBaseline = mkOption {
      type = types.bool;
      default = true;
      description = "Enforce HIPAA baseline settings";
    };

    baselineVersion = mkOption {
      type = types.str;
      default = "1.0.0";
      description = "HIPAA baseline version";
    };
  };

  config = mkIf config.services.msp-base.enable {
    # Enable all security hardening modules
    services.msp-encryption.enable = mkDefault true;
    services.msp-ssh-hardening.enable = mkDefault true;
    services.msp-secrets.enable = mkDefault true;
    services.msp-timesync.enable = mkDefault true;
    services.log-watcher.enable = mkDefault true;

    # System Hardening (§164.312)

    # Disable unused filesystems (attack surface reduction)
    boot.blacklistedKernelModules = [
      "cramfs"
      "freevxfs"
      "jffs2"
      "hfs"
      "hfsplus"
      "udf"
      # USB storage (disable unless needed)
      "usb-storage"
      "uas"
    ];

    # Kernel hardening parameters
    boot.kernelParams = [
      # Enable kernel page table isolation (Meltdown mitigation)
      "pti=on"

      # Disable kernel address leak
      "kptr_restrict=2"

      # Disable kernel profiling (performance counters)
      "nmi_watchdog=0"

      # Disable IPv6 if not needed
      # "ipv6.disable=1"
    ];

    # Kernel sysctl parameters
    boot.kernel.sysctl = {
      # Network security
      "net.ipv4.tcp_syncookies" = 1;  # SYN flood protection
      "net.ipv4.conf.all.rp_filter" = 1;  # Reverse path filtering
      "net.ipv4.conf.default.rp_filter" = 1;
      "net.ipv4.conf.all.accept_source_route" = 0;  # Disable source routing
      "net.ipv4.conf.default.accept_source_route" = 0;
      "net.ipv4.conf.all.accept_redirects" = 0;  # Disable ICMP redirects
      "net.ipv4.conf.default.accept_redirects" = 0;
      "net.ipv4.conf.all.secure_redirects" = 0;
      "net.ipv4.conf.default.secure_redirects" = 0;
      "net.ipv4.conf.all.send_redirects" = 0;  # Don't send redirects
      "net.ipv4.conf.default.send_redirects" = 0;
      "net.ipv4.icmp_echo_ignore_broadcasts" = 1;  # Ignore broadcast pings
      "net.ipv4.icmp_ignore_bogus_error_responses" = 1;

      # IPv6 security (if IPv6 enabled)
      "net.ipv6.conf.all.accept_redirects" = 0;
      "net.ipv6.conf.default.accept_redirects" = 0;
      "net.ipv6.conf.all.accept_source_route" = 0;
      "net.ipv6.conf.default.accept_source_route" = 0;

      # Kernel hardening
      "kernel.dmesg_restrict" = 1;  # Restrict dmesg to root
      "kernel.kptr_restrict" = 2;  # Hide kernel pointers
      "kernel.yama.ptrace_scope" = 2;  # Restrict ptrace
      "kernel.unprivileged_bpf_disabled" = 1;  # Disable unprivileged BPF
      "kernel.unprivileged_userns_clone" = 0;  # Disable unprivileged user namespaces

      # File system hardening
      "fs.protected_hardlinks" = 1;
      "fs.protected_symlinks" = 1;
      "fs.protected_fifos" = 2;
      "fs.protected_regular" = 2;
      "fs.suid_dumpable" = 0;  # Disable core dumps for SUID programs
    };

    # Disable core dumps (§164.312(a)(2)(iv))
    systemd.coredump.enable = false;
    security.pam.services.su.limits = [
      { domain = "*"; type = "hard"; item = "core"; value = "0"; }
    ];

    # Firewall configuration (§164.312(a)(1))
    networking.firewall = {
      enable = true;
      allowPing = false;  # Don't respond to pings from untrusted networks

      # Log dropped packets (for intrusion detection)
      logRefusedConnections = true;
      logRefusedPackets = false;  # Too noisy
      logRefusedUnicastsOnly = true;

      # Rate limit logging
      logRateLimitInterval = "5sec";
      logRateLimitBurst = 5;
    };

    # Audit logging (§164.312(b))
    security.auditd.enable = mkDefault true;
    security.audit = {
      enable = mkDefault true;
      rules = [
        # Monitor sudo commands
        "-a always,exit -F arch=b64 -S execve -F euid=0 -F auid>=1000 -F auid!=4294967295 -k privileged"

        # Monitor password file changes
        "-w /etc/passwd -p wa -k identity"
        "-w /etc/group -p wa -k identity"
        "-w /etc/shadow -p wa -k identity"

        # Monitor SSH configuration
        "-w /etc/ssh/sshd_config -p wa -k sshd"

        # Monitor kernel module loading
        "-w /sbin/insmod -p x -k modules"
        "-w /sbin/rmmod -p x -k modules"
        "-w /sbin/modprobe -p x -k modules"

        # Monitor system calls
        "-a always,exit -F arch=b64 -S mount -S umount2 -k mount"

        # Monitor file deletions
        "-a always,exit -F arch=b64 -S unlink -S unlinkat -S rename -S renameat -k delete"
      ];
    };

    # PAM configuration (§164.312(a)(2)(i))
    security.pam.services = {
      # Password quality requirements
      passwd.text = mkDefault ''
        password required pam_pwquality.so retry=3 minlen=14 difok=3 ucredit=-1 lcredit=-1 dcredit=-1 ocredit=-1
        password required pam_unix.so use_authtok sha512 shadow remember=12
      '';

      # Account lockout
      login.text = mkDefault ''
        auth required pam_faillock.so preauth silent audit deny=5 unlock_time=900
        auth include system-auth
        auth required pam_faillock.so authfail audit deny=5 unlock_time=900
        account required pam_faillock.so
      '';
    };

    # Automatic updates for security patches (§164.308(a)(5)(ii)(B))
    system.autoUpgrade = {
      enable = mkDefault true;
      allowReboot = false;  # Don't auto-reboot (use runbooks for scheduled reboots)
      dates = "daily";

      # Only apply security updates
      flake = "github:yourmsp/msp-platform";
    };

    # Log retention (§164.316(b)(2)(i))
    services.journald.extraConfig = ''
      SystemMaxUse=1G
      SystemKeepFree=2G
      SystemMaxFileSize=100M
      MaxRetentionSec=63072000  # 2 years (730 days) for HIPAA
      ForwardToSyslog=yes
    '';

    # Package management security
    nix.settings = {
      # Require signatures on substitutes
      require-sigs = true;

      # Trusted public keys (add your binary cache key)
      trusted-public-keys = [
        "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      ];

      # Sandbox all builds
      sandbox = true;

      # Restrict which users can use Nix
      allowed-users = [ "@wheel" ];
    };

    # Environment hardening
    environment.systemPackages = with pkgs; [
      # Security tools
      cryptsetup
      openssh
      fail2ban
      aide  # File integrity monitoring

      # Monitoring tools
      htop
      iotop
      nethogs

      # Audit tools
      auditd

      # Basic utilities
      vim
      git
      curl
      wget
      jq
    ];

    # Set up system identification
    environment.etc."msp-client-id".text = config.services.msp-base.clientId;
    environment.etc."msp-baseline-version".text = config.services.msp-base.baselineVersion;

    # Metadata for compliance reporting
    system.stateVersion = "24.05";  # Don't change after initial install

    # Documentation
    documentation.enable = true;
    documentation.man.enable = true;
    documentation.dev.enable = false;  # Save space
  };
}
