{ config, lib, pkgs, ... }:

with lib;

let
  cfg = config.services.msp-encryption;
in {
  options.services.msp-encryption = {
    enable = mkEnableOption "MSP LUKS encryption and security hardening";

    # LUKS Configuration (§164.312(a)(2)(iv), §164.310(d)(1))
    luks = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Enable LUKS full-disk encryption";
      };

      devices = mkOption {
        type = types.attrsOf (types.submodule {
          options = {
            device = mkOption {
              type = types.str;
              description = "Block device to encrypt (e.g., /dev/sda2)";
            };

            keyFile = mkOption {
              type = types.nullOr types.path;
              default = null;
              description = "Path to key file (optional, for automated unlock)";
            };

            allowDiscards = mkOption {
              type = types.bool;
              default = false;
              description = "Enable TRIM/discard for SSDs";
            };
          };
        });
        default = {};
        description = "LUKS encrypted devices";
        example = literalExpression ''
          {
            root = {
              device = "/dev/sda2";
              allowDiscards = true;
            };
          }
        '';
      };

      # Encryption algorithm (HIPAA requires strong encryption)
      cryptsetup = {
        cipher = mkOption {
          type = types.str;
          default = "aes-xts-plain64";
          description = "Cipher specification";
        };

        keySize = mkOption {
          type = types.int;
          default = 512;  # AES-256-XTS = 512-bit key
          description = "Key size in bits";
        };

        hashAlgorithm = mkOption {
          type = types.str;
          default = "sha256";
          description = "Hash algorithm for key derivation";
        };
      };
    };

    # Encrypted Swap (§164.312(a)(2)(iv))
    encryptedSwap = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Enable encrypted swap";
      };

      devices = mkOption {
        type = types.listOf types.str;
        default = [ "/dev/disk/by-label/swap" ];
        description = "Swap devices to encrypt";
      };
    };

    # TPM Integration (optional, for hardware-backed encryption)
    tpm = {
      enable = mkOption {
        type = types.bool;
        default = false;
        description = "Enable TPM 2.0 integration for key storage";
      };

      device = mkOption {
        type = types.str;
        default = "/dev/tpmrm0";
        description = "TPM device path";
      };
    };
  };

  config = mkIf cfg.enable {
    # LUKS Configuration
    boot.initrd.luks.devices = mkIf cfg.luks.enable (
      mapAttrs (name: deviceCfg: {
        device = deviceCfg.device;
        keyFile = deviceCfg.keyFile;
        allowDiscards = deviceCfg.allowDiscards;

        # HIPAA-compliant encryption settings
        crypttabExtraOpts = [
          "cipher=${cfg.luks.cryptsetup.cipher}"
          "size=${toString cfg.luks.cryptsetup.keySize}"
          "hash=${cfg.luks.cryptsetup.hashAlgorithm}"
          "no-read-workqueue"
          "no-write-workqueue"
        ];
      }) cfg.luks.devices
    );

    # Encrypted Swap
    swapDevices = mkIf cfg.encryptedSwap.enable (
      map (device: {
        device = device;
        randomEncryption = {
          enable = true;
          cipher = cfg.luks.cryptsetup.cipher;
          source = "/dev/urandom";
        };
      }) cfg.encryptedSwap.devices
    );

    # TPM Integration (if enabled)
    security.tpm2 = mkIf cfg.tpm.enable {
      enable = true;
      pkcs11.enable = true;
      tctiEnvironment.enable = true;
    };

    # Additional encryption-related hardening
    boot.initrd.availableKernelModules = [
      "dm_crypt"
      "dm_mod"
      "aes_x86_64"  # Hardware AES acceleration
      "sha256_ssse3"
    ];

    # Ensure cryptsetup is available
    environment.systemPackages = with pkgs; [
      cryptsetup
    ];

    # Kernel parameters for encryption
    boot.kernelParams = [
      "cryptomgr.notests"  # Disable self-tests for faster boot
    ];

    # Logging for audit trail
    systemd.services."luks-audit-logger" = {
      description = "LUKS Encryption Status Logger";
      after = [ "local-fs.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };

      script = ''
        # Log encryption status for audit trail
        echo "[$(date -Iseconds)] LUKS Encryption Status Check" >> /var/log/encryption-audit.log

        ${pkgs.cryptsetup}/bin/cryptsetup status root >> /var/log/encryption-audit.log 2>&1 || true

        # Check for unencrypted sensitive directories
        for dir in /home /var/lib /etc; do
          if [ -d "$dir" ]; then
            df -h "$dir" | grep -q "dm-" && \
              echo "[$(date -Iseconds)] $dir is on encrypted volume" >> /var/log/encryption-audit.log || \
              echo "[$(date -Iseconds)] WARNING: $dir may not be encrypted" >> /var/log/encryption-audit.log
          fi
        done
      '';
    };

    # Create audit log directory
    systemd.tmpfiles.rules = [
      "f /var/log/encryption-audit.log 0600 root root -"
    ];
  };
}
