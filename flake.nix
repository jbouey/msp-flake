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
      # Define the NixOS module separately so it can be used across systems
      logWatcherModule = import ./flake/modules/log-watcher.nix;
    in
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python311;
        
        # Package derivation for the log-watcher
        infra-watcher = import ./flake/pkgs/infra-watcher.nix {
          inherit pkgs python;
        };
        
        # Attr-set that actually exposes `buildImage`
        n2cPkgs = nix2container.packages."${system}";

        smoke = import ./flake/pkgs/smoke.nix { inherit pkgs; };
        
        # Container image derivation using nix2container
        container-img = import ./flake/container/default.nix {
          inherit pkgs infra-watcher;
          nix2container = n2cPkgs.nix2container;
        };
        
        # Development shell with all dependencies
        devShell = pkgs.mkShell {
          buildInputs = with pkgs; [
            python
            fluent-bit
            # Add development tools
            python311Packages.pytest
            python311Packages.black
            python311Packages.mypy
            python311Packages.requests
            python311Packages.fastapi
            python311Packages.uvicorn
            # Container tools
            podman
            skopeo
            # MSP development tools
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
        # Main outputs
        packages = {
          default = infra-watcher;
          infra-watcher = infra-watcher;
          container = container-img;
        };
        
        # Apps for MSP workflow
        apps = {
          default = flake-utils.lib.mkApp { drv = container-img; };
          
          # Load into Docker (most common for MSP deployments)
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
          
          # Load into Podman
          load-to-podman = {
            type = "app";
            program = toString (pkgs.writeShellScript "load-to-podman" ''
              echo "🚀 Loading MSP Log Watcher into Podman..."
              nix run .#container.copyToPodman
              echo "✅ Done! Image: registry.example.com/infra-watcher:0.1"
            '');
          };
          
          # Push to registry for MSP-wide deployment
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
          
          # Local smoke test (Step 11 from your plan)
          test-local = {
            type = "app";
            program = toString (pkgs.writeShellScript "test-local" ''
              echo "🧪 Running local MSP smoke test..."
              echo "1. Building container..."
              nix build .#container
              
              echo "2. Loading into Docker..."
              nix run .#load-to-docker
              
              echo "3. Starting test container..."
              docker run -d --name infra-watcher-test -p 8080:8080 \
                -v /tmp:/var/log:ro \
                -e MCP_URL=http://localhost:8000 \
                registry.example.com/infra-watcher:0.1
              
              echo "4. Testing health endpoint..."
              sleep 3
              if curl -f http://localhost:8080/status; then
                echo "✅ Health endpoint working!"
              else
                echo "❌ Health endpoint failed"
              fi
              
              echo "5. Cleaning up..."
              docker stop infra-watcher-test
              docker rm infra-watcher-test
              
              echo "✅ Smoke test complete!"
            '');
          };
          
          # Test native (non-containerized) for VM testing
          test-native = {
            type = "app";
            program = toString (pkgs.writeShellScript "test-native" ''
              echo "🧪 Running native log watcher test..."
              echo "Set MCP_URL environment variable first:"
              echo "  export MCP_URL=http://192.168.1.100:8000"
              echo ""
              if [ -z "$MCP_URL" ]; then
                echo "Using default: http://localhost:8000"
                export MCP_URL=http://localhost:8000
              fi
              echo "MCP Server: $MCP_URL"
              echo ""
              echo "Starting log watcher..."
              ${infra-watcher}/bin/infra-tailer
            '');
          };
        };
        
        # Development environment
        devShells.default = devShell;
        
        # Formatter for `nix fmt`
        formatter = pkgs.nixpkgs-fmt;
      }
    ) // {
      # Add NixOS module output (outside of eachDefaultSystem)
      nixosModules = {
        log-watcher = logWatcherModule;
        default = logWatcherModule;
      };
      
      # Example NixOS configuration for testing
      nixosConfigurations.test-vm = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          # Base NixOS configuration
          ({ pkgs, ... }: {
            boot.isContainer = true; # For testing in container/VM
            
            # Import the log watcher module
            imports = [ logWatcherModule ];
            
            # Enable and configure the service
            services.msp-log-watcher = {
              enable = true;
              mcpUrl = "http://192.168.1.100:8000"; # Your laptop's IP
              logLevel = "INFO";
            };
            
            # Basic system config for testing
            system.stateVersion = "24.05";
            networking.hostName = "test-log-watcher";
            
            # For testing: create some log files
            systemd.tmpfiles.rules = [
              "f /var/log/test.log 0644 root root -"
              "f /var/log/app.log 0644 root root -"
            ];
          })
        ];
      };
    };
}