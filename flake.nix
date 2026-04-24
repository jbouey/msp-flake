{
  description = "Infra-Watcher container (Fluent Bit + Python tailer)";

  inputs = {
    # Pinned to exact commit for reproducible builds (was nixos-24.05 branch)
    nixpkgs.url = "github:NixOS/nixpkgs/b134951a4c9f3c995fd7be05f3243f8ecd65d798";
    flake-utils.url = "github:numtide/flake-utils";
    nix2container.url = "github:nlewo/nix2container";
    nix2container.inputs.nixpkgs.follows = "nixpkgs";
    lanzaboote.url = "github:nix-community/lanzaboote/v0.4.1";
    lanzaboote.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, flake-utils, nix2container, lanzaboote }:
    let
      # Keep a single import of the module and reuse it everywhere.
      logWatcherModule = import ./flake/Modules/log-watcher.nix;
      nixosModules.log-watcher = import ./flake/modules/log-watcher.nix;

    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python311;

        # Packages
        log-watcher = infra-watcher-fixed; 
        infra-watcher-fixed = import ./flake/pkgs/infra-watcher-fixed.nix { inherit pkgs; };
        

        # nix2container helpers
        n2cPkgs = nix2container.packages."${system}";
        container-img = import ./flake/container/default.nix {
          inherit pkgs;
          infra-watcher = infra-watcher-fixed;
          nix2container = n2cPkgs.nix2container;
        };

        # Dev shell
        devShell = pkgs.mkShell {
          buildInputs = with pkgs; [
            python
            fluent-bit
            python311Packages.pytest
            python311Packages.black
            python311Packages.mypy
            python311Packages.requests
            python311Packages.fastapi
            python311Packages.uvicorn
            podman
            skopeo
            curl
            jq
          ];
          shellHook = ''
            echo "🔍 Infra-Watcher Development Environment"
            echo "Python: $(python --version)"
            echo "Fluent Bit: $(fluent-bit --version | head -1)"
            echo ""
            echo "MSP Log Watcher Container Build Commands:"
            echo "  nix build .#container              # Build container image"
            echo "  nix run .#load-to-docker           # Load into Docker"
            echo "  nix run .#load-to-podman           # Load into Podman"
            echo "  nix run .#push-to-registry         # Push to your registry"
            echo "  nix develop                        # Enter dev shell"
            echo ""
            echo "Local testing:"
            echo "  nix run .#test-local               # Run smoke test"
            echo "  nix run .#test-native              # Run native (non-container) test"
          '';
        };
      in
      {
        # === per-system outputs ===
        packages = {
          default      = log-watcher; 
          log-watcher  = log-watcher;
          infra-watcher-fixed = infra-watcher-fixed;
          container    = container-img;
        };

        apps = {
          default = flake-utils.lib.mkApp { drv = container-img; };

          load-to-docker = {
            type = "app";
            program = toString (pkgs.writeShellScript "load-to-docker" ''
              echo "🚀 Loading MSP Log Watcher into Docker..."
              nix run .#container.copyToDockerDaemon
              echo "✅ Done! Image: registry.example.com/infra-watcher:0.1"
              echo ""
              echo "Test locally:"
              echo "  docker run -d --name infra-watcher -p 8080:8080 \\"
              echo "    -v /var/log:/var/log:ro \\"
              echo "    -e MCP_URL=http://your-mcp-server:8000 \\"
              echo "    registry.example.com/infra-watcher:0.1"
            '');
          };

          load-to-podman = {
            type = "app";
            program = toString (pkgs.writeShellScript "load-to-podman" ''
              echo "🚀 Loading MSP Log Watcher into Podman..."
              nix run .#container.copyToPodman
              echo "✅ Done! Image: registry.example.com/infra-watcher:0.1"
            '');
          };

          push-to-registry = {
            type = "app";
            program = toString (pkgs.writeShellScript "push-to-registry" ''
              if [ -z "$REGISTRY_URL" ]; then
                echo "❌ Set REGISTRY_URL environment variable first"
                echo "   Example: export REGISTRY_URL=your-registry.com/infra-watcher:0.1"
                exit 1
              fi
              echo "🚀 Pushing to registry: $REGISTRY_URL"
              nix run .#container.copyToRegistry -- "$REGISTRY_URL"
              echo "✅ Done! All MSP offices can now pull: $REGISTRY_URL"
            '');
          };

          # Fixed test-native app
          test-native = {
            type = "app";
            program = toString (pkgs.writeShellScript "test-native" ''
              echo "🧪 Running native log watcher test..."
              if [ -z "$MCP_URL" ]; then
                export MCP_URL="http://localhost:8000"
              fi
              echo "MCP Server: $MCP_URL"
              ${infra-watcher-fixed}/bin/infra-tailer
              echo "✅ Native test complete!"
            '');
          };

          test-local = {
            type = "app";
            program = toString (pkgs.writeShellScript "test-local" ''
              echo "🧪 Running local MSP smoke test..."
              nix build .#container
              nix run .#load-to-docker
              docker run -d --name infra-watcher-test -p 8080:8080 \
                -v /tmp:/var/log:ro \
                -e MCP_URL=http://localhost:8000 \
                registry.example.com/infra-watcher:0.1
              sleep 3
              if curl -f http://localhost:8080/status; then
                echo "✅ Health endpoint working!"
              else
                echo "❌ Health endpoint failed"
              fi
              docker rm -f infra-watcher-test
              echo "✅ Smoke test complete!"
            '');
          };
        };

        devShells.default = devShell;
        formatter = pkgs.nixpkgs-fmt;
      }
    )
    //
    {
      # === cross-system outputs ===
      nixosModules = {
        log-watcher = logWatcherModule;
        default = logWatcherModule;
      };

      # OsirisCare Appliance Disk Image (for permanent installation)
      # This is the "golden configuration" that gets installed.
      # Deployed appliances rebuild with:
      #   nixos-rebuild switch --flake github:jbouey/msp-flake/main#osiriscare-appliance-disk --refresh
      nixosConfigurations.osiriscare-appliance-disk = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          lanzaboote.nixosModules.lanzaboote
          ./iso/appliance-disk-image.nix
        ];
      };

      # Compressed raw disk image with full NixOS closure (dd-based installer)
      # 3 partitions: ESP (512M) + root (auto) + MSP-DATA (2G)
      # Produces osiriscare-system.raw.zst — zero network needed at install time
      packages.x86_64-linux.appliance-raw-image =
        import ./iso/raw-image.nix { inherit nixpkgs lanzaboote; };

      # OsirisCare Installer ISO — offline, writes embedded raw image via dd+zstd
      nixosConfigurations.osiriscare-appliance = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = {
          appliance-raw-image = self.packages.x86_64-linux.appliance-raw-image;
          # Passed through so the ISO can stamp /etc/osiriscare-build.json
          # with the source git revision. self.rev is only set when the
          # working tree is clean; self.dirtyRev when dirty; absent when
          # neither (CI or tarball build).
          builtFrom = {
            git_sha = self.rev or self.dirtyRev or "unknown";
            dirty = self ? dirtyRev;
          };
        };
        modules = [
          "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
          ./iso/appliance-image.nix
        ];
      };

      # Installer ISO for zero-friction deployment
      packages.x86_64-linux.appliance-iso =
        self.nixosConfigurations.osiriscare-appliance.config.system.build.isoImage;

      # Raw disk image for writing to SSD/USB (20GB, GPT, EFI) - DEPRECATED
      # Use appliance-raw-image instead for dd-based offline install
      packages.x86_64-linux.appliance-disk-image =
        let
          pkgs = import nixpkgs { system = "x86_64-linux"; };
          config = (nixpkgs.lib.nixosSystem {
            system = "x86_64-linux";
            modules = [ ./iso/appliance-disk-image.nix ];
          }).config;
        in
        import "${nixpkgs}/nixos/lib/make-disk-image.nix" {
          inherit pkgs config;
          inherit (pkgs) lib;
          format = "raw";
          diskSize = 20 * 1024;  # 20GB
          partitionTableType = "efi";
          bootSize = "512M";  # Larger boot partition for kernels
          additionalSpace = "2G";  # Extra space for updates
          installBootLoader = true;
          copyChannel = false;
          label = "nixos";
          memSize = 2048;  # More memory for the build VM
        };

      # =================================================================
      # v40.6 (2026-04-24, Principal SWE round-table) — QEMU boot-
      # integration test harness.
      #
      # Catches the class of runtime regression that text-only pytest
      # cannot see:
      #   * missing binary inside a present nix derivation (v40.0 inetutils/host)
      #   * Python SyntaxError in embedded heredoc scripts (v40.0 em-dash)
      #   * systemd unit ordering deadlocks (v40.0 Phase 0 Before=[sysinit,multi-user])
      #   * `set -euo pipefail` + command substitution fatal-exit bugs
      #     (v40.3 DNS gate pre-`|| true`)
      #   * any other "file presence assertions pass but the box won't boot" class
      #
      # Currently attr-level only (runs via `nix flake check` on demand).
      # Next-session Task #129 wires this into the CI deploy gate so
      # no ISO ships without a green boot test.
      #
      # Structure: two derivations — `appliance-boot-smoke` proves the
      # testing framework works with our pinned nixpkgs (runs a
      # minimal NixOS); `appliance-boot` imports the real disk-image
      # config and asserts appliance-specific runtime properties.
      # =================================================================
      checks.x86_64-linux = let
        pkgs = import nixpkgs { system = "x86_64-linux"; };
      in {
        # Framework-proof smoke test. Must always be green. If this
        # fails, the testing framework itself is broken in our
        # nixpkgs pin (not an appliance bug).
        appliance-boot-smoke = pkgs.testers.runNixOSTest {
          name = "appliance-boot-smoke";
          nodes.machine = { config, pkgs, lib, ... }: {
            networking.hostName = "test-smoke";
            users.users.root.hashedPassword = "!";
            services.getty.autologinUser = null;
            system.stateVersion = "24.05";
          };
          testScript = ''
            machine.wait_for_unit("multi-user.target", timeout=60)
            machine.succeed("echo framework-up")
          '';
        };

        # Real appliance boot test. Imports the production disk-image
        # module, boots in QEMU, asserts the runtime properties that
        # matter. Over time this becomes a CI deploy gate.
        appliance-boot = pkgs.testers.runNixOSTest {
          name = "appliance-boot";
          nodes.machine = { config, pkgs, lib, ... }: {
            imports = [ ./iso/appliance-disk-image.nix ];

            # VM shim: the disk image declares real disk fileSystems
            # and a boot loader. In test, QEMU provides its own.
            fileSystems = lib.mkForce {
              "/" = {
                device = "/dev/vda";
                fsType = "ext4";
              };
            };
            boot.loader.systemd-boot.enable = lib.mkForce false;
            boot.loader.grub.enable = lib.mkForce false;
            boot.loader.grub.device = lib.mkForce "nodev";
            lanzaboote.enable = lib.mkForce false;
            virtualisation.memorySize = 2048;
            virtualisation.diskSize = 4096;
          };
          # Failure signatures we deliberately test for — exit codes
          # help triage: 1=phase0, 2=sshd, 3=daemon, 4=beacon.
          testScript = ''
            start_all()
            # Phase 0: Break-glass passphrase must exist (tests the
            # v40.1 deadlock fix + v40.3 binary classpath fix).
            machine.wait_for_unit("msp-breakglass-provision.service", timeout=60)
            machine.wait_for_file(
                "/var/lib/msp/.emergency-credentials.enc", timeout=90
            )
            # Multi-user.target reaches (tests that nothing blocks
            # user-space boot — the Phase 0 deadlock class).
            machine.wait_for_unit("multi-user.target", timeout=120)
            # SSH up (tests v40.1 sshd-on-boot rescue posture + the
            # implicit "multi-user reached" assertion).
            machine.wait_for_open_port(22, timeout=30)
            # Beacon up on :8443 (tests v40.3 em-dash fix — Python
            # heredoc parses cleanly).
            machine.wait_for_unit("msp-status-beacon.service", timeout=60)
            machine.wait_for_open_port(8443, timeout=30)
            # Beacon returns JSON (tests the HTTP handler + that
            # beacon.json got written).
            machine.succeed(
                "curl -sf http://127.0.0.1:8443/ "
                "| python3 -c 'import json,sys; "
                "d=json.load(sys.stdin); "
                "assert \"state\" in d, \"beacon missing state field\"'"
            )
          '';
        };
      };

      # Example NixOS host (VM/container) using the module
      nixosConfigurations.log-watcher-test = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        # so we can reference self.packages inside the machine config
        specialArgs = { inherit self; };

        modules = [
          # bring in the service module
          self.nixosModules.log-watcher

          "${nixpkgs}/nixos/modules/virtualisation/qemu-vm.nix"
          
         {
          # VM test environment - password disabled, use SSH keys
          users.users.root.hashedPassword = "!";  # Disabled - use SSH key
          services.getty.autologinUser = null;  # No auto-login for security
          services.openssh.enable = true;
          services.openssh.settings.PermitRootLogin = "prohibit-password";
          virtualisation.forwardPorts = [
        { from = "host"; host.port = 2222; guest.port = 22; }
          ];
          # Add your SSH key for VM access:
          # users.users.root.openssh.authorizedKeys.keys = [ "ssh-ed25519 YOUR_KEY" ];
        }

          
          # host settings
          ({ pkgs, ... }: {
            boot.initrd.enable = true;
            users.users.osirisclinic = {
              isNormalUser = true;
              extraGroups = [ "wheel" "docker" ];
            };
            services.log-watcher = {
              enable = true;
              package = import ./flake/pkgs/infra-watcher-fixed.nix { inherit pkgs; };
              mcpUrl = "http://192.168.1.100:8000";
              logLevel = "INFO";
              schedule = "*:0/5";  # uncomment if your module supports scheduling
            };
                  # Make sure “network-online.target” is actually waited for
            systemd.services."log-watcher".wants = [ "network-online.target" ];
            systemd.services."log-watcher".after  = [ "network-online.target" ];

             # VM knobs (optional)
            
            

      
            networking.hostName = "test-log-watcher";
            system.stateVersion = "24.05";

            systemd.tmpfiles.rules = [
              "f /var/log/test.log 0644 root root -"
              "f /var/log/app.log 0644 root root -"
            ];
          })
        ];
      };
    };
}