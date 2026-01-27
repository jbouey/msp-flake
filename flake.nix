{
  description = "Infra-Watcher container (Fluent Bit + Python tailer)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    flake-utils.url = "github:numtide/flake-utils";
    nix2container.url = "github:nlewo/nix2container";
    nix2container.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, flake-utils, nix2container }:
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

      # OsirisCare Appliance ISO (live boot)
      nixosConfigurations.osiriscare-appliance = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
          ./iso/appliance-image.nix
        ];
      };

      # OsirisCare Appliance Disk Image (for permanent installation)
      nixosConfigurations.osiriscare-appliance-disk = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          ./iso/appliance-disk-image.nix
        ];
      };

      # Raw disk image for writing to SSD/USB (20GB, GPT, EFI)
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
          # Create ESP partition for EFI boot
          additionalSpace = "1G";  # Extra space for updates
          copyChannel = false;
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
          users.users.root.initialPassword = "root";
          services.getty.autologinUser = "root";
          services.openssh.enable = true;
          virtualisation.forwardPorts = [
        { from = "host"; host.port = 2222; guest.port = 22; }
          ];
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
              package = self.packages.${pkgs.system}.infra-watcher-fixed;
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