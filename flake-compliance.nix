{
  description = "MSP Compliance Appliance - Self-Healing NixOS Agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    flake-utils.url = "github:numtide/flake-utils";
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, sops-nix }:
    let
      # Overlay for our packages
      overlay = final: prev: {
        compliance-agent = final.callPackage ./packages/compliance-agent { };
      };

      # Base appliance module (shared by all variants)
      baseApplianceModule = { config, pkgs, lib, ... }: {
        imports = [
          ./modules/compliance-agent.nix
          sops-nix.nixosModules.sops
        ];

        # System identification
        system.stateVersion = "24.05";
        networking.hostName = lib.mkDefault "osiriscare-appliance";

        # Boot configuration (use mkDefault so ISO module can override)
        boot = {
          loader.grub = {
            enable = lib.mkDefault true;
            device = lib.mkDefault "nodev";
            efiSupport = lib.mkDefault true;
            efiInstallAsRemovable = lib.mkDefault true;
          };
          loader.efi.canTouchEfiVariables = lib.mkDefault false;
          loader.timeout = lib.mkDefault 3;
          kernelParams = [ "console=tty1" "quiet" ];
          initrd.availableKernelModules = [
            "ahci" "xhci_pci" "ehci_pci" "usbhid" "usb_storage" "sd_mod"
            "nvme" "sata_nv" "sata_via" "virtio_pci" "virtio_blk" "virtio_net"
          ];
        };

        # No GUI
        services.xserver.enable = false;

        # Networking
        networking = {
          useDHCP = true;
          firewall = {
            enable = true;
            allowedTCPPorts = [ 22 80 ];  # SSH + status page
          };
        };

        # Time sync (critical for compliance)
        services.chrony = {
          enable = true;
          servers = [ "time.nist.gov" "pool.ntp.org" ];
        };
        time.timeZone = "UTC";

        # SSH (use mkDefault so installer profile can override)
        services.openssh = {
          enable = true;
          settings = {
            PasswordAuthentication = lib.mkDefault false;
            PermitRootLogin = lib.mkDefault "prohibit-password";
          };
        };

        # Security
        security.auditd.enable = true;

        # Minimal packages - slimmed for ISO size
        environment.systemPackages = with pkgs; [
          curl iproute2 jq
          python311 python311Packages.pywinrm python311Packages.aiohttp
          python311Packages.cryptography python311Packages.pydantic python311Packages.pyyaml
        ];

        # Reduce image size
        documentation.enable = false;
        documentation.man.enable = false;
        documentation.nixos.enable = false;
        programs.command-not-found.enable = false;

        # Journal
        services.journald.extraConfig = ''
          Storage=persistent
          Compress=yes
          SystemMaxUse=100M
          MaxRetentionSec=7day
        '';

        # Memory optimization
        zramSwap = {
          enable = true;
          algorithm = "zstd";
          memoryPercent = 25;
        };
      };

      # Lean appliance config (connects to central MCP)
      # QUICK FIX: Using embedded phone-home.py instead of placeholder module
      leanApplianceModule = { config, pkgs, lib, ... }: {
        imports = [ baseApplianceModule ];

        # Disable the placeholder compliance-agent module
        services.compliance-agent.enable = lib.mkForce false;

        # SSH keys for testing (will be provisioned per-site in production)
        users.users.root.openssh.authorizedKeys.keys = [
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBv6abzJDSfxWt00y2jtmZiubAiehkiLe/7KBot+6JHH jbouey@osiriscare.net"
        ];
        users.users.msp = {
          isNormalUser = true;
          extraGroups = [ "wheel" ];
          openssh.authorizedKeys.keys = [
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBv6abzJDSfxWt00y2jtmZiubAiehkiLe/7KBot+6JHH jbouey@osiriscare.net"
          ];
        };
        security.sudo.wheelNeedsPassword = false;

        # Phone-home agent script embedded directly
        environment.etc."msp/phone-home.py" = {
          mode = "0755";
          text = builtins.readFile ./iso/phone-home.py;
        };

        # Phone-home systemd service
        systemd.services.osiriscare-agent = {
          description = "OsirisCare Compliance Agent (Phone-Home)";
          wantedBy = [ "multi-user.target" ];
          after = [ "network-online.target" ];
          wants = [ "network-online.target" ];
          serviceConfig = {
            Type = "simple";
            ExecStart = "${pkgs.python311}/bin/python3 /etc/msp/phone-home.py";
            Restart = "always";
            RestartSec = "10s";
          };
        };
      };

    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ overlay ];
        };
      in
      {
        # Main agent package
        packages = {
          default = pkgs.compliance-agent;
          compliance-agent = pkgs.compliance-agent;
        };

        # Development shell
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            python311
            python311Packages.pytest
            python311Packages.pytest-asyncio
            python311Packages.aiohttp
            python311Packages.cryptography
            python311Packages.pydantic
            sops
            age
            nftables
            qemu  # For testing ISO
          ];

          shellHook = ''
            echo "MSP Compliance Appliance Development Environment"
            echo ""
            echo "Build commands:"
            echo "  nix build .#appliance-iso           # Build bootable ISO"
            echo "  nix build .#appliance-iso-installer # Build installer ISO"
            echo ""
            echo "Test commands:"
            echo "  nix run .#test-iso                  # Test ISO in QEMU"
            echo ""
            echo "Provisioning:"
            echo "  python iso/provisioning/generate-config.py --site-id clinic-001 --site-name 'Test Clinic'"
          '';
        };

        # Unit tests
        checks = {
          unit-tests = pkgs.callPackage ./checks/unit-tests.nix { };
        };

        # Formatter
        formatter = pkgs.nixpkgs-fmt;
      }
    ) // {
      # NixOS module (system-independent)
      nixosModules.default = import ./modules/compliance-agent.nix;
      nixosModules.compliance-agent = import ./modules/compliance-agent.nix;

      # ========================================================================
      # Appliance ISO Images (x86_64-linux only)
      # ========================================================================

      # Bootable ISO for direct boot/install
      packages.x86_64-linux.appliance-iso = (nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
          leanApplianceModule
          ({ lib, pkgs, ... }: {
            isoImage = {
              isoName = lib.mkForce "osiriscare-appliance.iso";
              makeEfiBootable = true;
              makeUsbBootable = true;
              squashfsCompression = "gzip";  # Use default gzip - zstd level 19 caused boot failures
            };

            # Auto-login for debugging (mkForce to override installation-device profile)
            services.getty.autologinUser = lib.mkForce "root";

            # Disable nginx in ISO to save space (can enable post-install)
            services.nginx.enable = lib.mkForce false;

            # Additional size reductions for ISO
            fonts.fontconfig.enable = lib.mkDefault false;
            services.udisks2.enable = lib.mkDefault false;
            xdg.icons.enable = lib.mkDefault false;
            xdg.mime.enable = lib.mkDefault false;
            xdg.sounds.enable = lib.mkDefault false;

            # More aggressive trimming for ISO size
            security.auditd.enable = lib.mkForce false;  # Override compliance-agent module
            security.polkit.enable = lib.mkDefault false;
            services.lvm.enable = lib.mkDefault false;
            programs.nano.enable = lib.mkForce false;
            programs.less.lessopen = null;

            # Don't include installer-specific tools we don't need
            system.disableInstallerTools = true;
          })
        ];
      }).config.system.build.isoImage;

      # VirtualBox OVA for testing
      packages.x86_64-linux.appliance-ova = (nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          "${nixpkgs}/nixos/modules/virtualisation/virtualbox-image.nix"
          leanApplianceModule
          {
            virtualbox = {
              baseImageSize = 20 * 1024;  # 20GB
              memorySize = 2048;          # 2GB RAM
            };
          }
        ];
      }).config.system.build.virtualBoxOVA;

      # ========================================================================
      # Build Apps
      # ========================================================================

      apps.x86_64-linux = {
        # Build ISO
        build-iso = {
          type = "app";
          program = toString (nixpkgs.legacyPackages.x86_64-linux.writeShellScript "build-iso" ''
            set -e
            echo "Building MSP Compliance Appliance ISO..."
            echo ""
            nix build .#appliance-iso -o result-iso
            echo ""
            echo "ISO built successfully!"
            ls -lh result-iso/iso/*.iso
            echo ""
            echo "To write to USB drive:"
            echo "  sudo dd if=result-iso/iso/osiriscare-appliance.iso of=/dev/sdX bs=4M status=progress sync"
            echo ""
            echo "To test in QEMU:"
            echo "  qemu-system-x86_64 -m 2G -cdrom result-iso/iso/osiriscare-appliance.iso -boot d -enable-kvm"
          '');
        };

        # Test ISO in QEMU
        test-iso = {
          type = "app";
          program = toString (nixpkgs.legacyPackages.x86_64-linux.writeShellScript "test-iso" ''
            set -e
            ISO_PATH="result-iso/iso/osiriscare-appliance.iso"

            if [ ! -f "$ISO_PATH" ]; then
              echo "ISO not found. Building first..."
              nix build .#appliance-iso -o result-iso
            fi

            echo "Starting QEMU with ISO..."
            echo "  - Port 8080 -> Guest port 80 (status page)"
            echo "  - Port 2222 -> Guest port 22 (SSH)"
            echo ""
            echo "Access status page at: http://localhost:8080"
            echo "SSH: ssh -p 2222 root@localhost"
            echo ""

            ${nixpkgs.legacyPackages.x86_64-linux.qemu}/bin/qemu-system-x86_64 \
              -m 2G \
              -cdrom "$ISO_PATH" \
              -boot d \
              -enable-kvm \
              -net nic \
              -net user,hostfwd=tcp::8080-:80,hostfwd=tcp::2222-:22
          '');
        };

        # Generate site config
        generate-config = {
          type = "app";
          program = toString (nixpkgs.legacyPackages.x86_64-linux.writeShellScript "generate-config" ''
            exec ${nixpkgs.legacyPackages.x86_64-linux.python311}/bin/python3 \
              iso/provisioning/generate-config.py "$@"
          '');
        };
      };

      # ========================================================================
      # NixOS Configurations
      # ========================================================================

      nixosConfigurations = {
        # Lean appliance (connects to central MCP)
        appliance = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [ leanApplianceModule ];
        };

        # Test VM configuration
        appliance-vm = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            "${nixpkgs}/nixos/modules/virtualisation/qemu-vm.nix"
            leanApplianceModule
            {
              virtualisation = {
                memorySize = 2048;
                cores = 2;
                graphics = false;
                forwardPorts = [
                  { from = "host"; host.port = 8080; guest.port = 80; }
                  { from = "host"; host.port = 2222; guest.port = 22; }
                ];
              };
            }
          ];
        };
      };

      # Integration tests
      nixosTests = {
        compliance-agent = import ./nixosTests/compliance-agent.nix {
          inherit nixpkgs;
          inherit (self) nixosModules;
          inherit (self.packages.x86_64-linux) compliance-agent;
        };
      };

      # Overlay for other flakes to use
      overlays.default = overlay;
    };
}
