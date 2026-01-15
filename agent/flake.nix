{
  description = "OsirisCare Go Agent - Workstation compliance monitoring";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        version = "0.1.0";
        buildTime = builtins.toString builtins.currentTime;

        # Common Go build settings
        commonAttrs = {
          pname = "osiris-agent";
          inherit version;
          src = ./.;

          # Vendor hash - update after first build or use null for initial build
          vendorHash = null;

          ldflags = [
            "-s" "-w"
            "-X main.Version=${version}"
            "-X main.BuildTime=${buildTime}"
          ];

          # Tags for build variants
          tags = [ ];

          # Exclude test files from build
          excludedPackages = [ ];

          meta = with pkgs.lib; {
            description = "OsirisCare Windows workstation compliance agent";
            homepage = "https://github.com/osiriscare/agent";
            license = licenses.proprietary;
            maintainers = [ ];
          };
        };

      in
      {
        packages = {
          # Linux amd64 (for testing)
          osiris-agent-linux-amd64 = pkgs.buildGoModule (commonAttrs // {
            CGO_ENABLED = "0";
            GOOS = "linux";
            GOARCH = "amd64";
          });

          # Windows amd64 - the primary target
          # Note: Cross-compiling Go with CGO for Windows requires special setup
          # For CGO-free build (limited WMI support):
          osiris-agent-windows-amd64 = pkgs.buildGoModule (commonAttrs // {
            CGO_ENABLED = "0";
            GOOS = "windows";
            GOARCH = "amd64";

            postInstall = ''
              mv $out/bin/osiris-agent $out/bin/osiris-agent.exe 2>/dev/null || true
            '';
          });

          # Windows arm64 (for newer Surface devices)
          osiris-agent-windows-arm64 = pkgs.buildGoModule (commonAttrs // {
            CGO_ENABLED = "0";
            GOOS = "windows";
            GOARCH = "arm64";

            postInstall = ''
              mv $out/bin/osiris-agent $out/bin/osiris-agent.exe 2>/dev/null || true
            '';
          });

          # Default is Linux for development
          default = self.packages.${system}.osiris-agent-linux-amd64;
        };

        # Development shell
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            go_1_22
            gopls
            gotools
            go-tools
            protobuf
            protoc-gen-go
            protoc-gen-go-grpc
            sqlite
          ];

          shellHook = ''
            echo "╔══════════════════════════════════════════════════════════╗"
            echo "║       OsirisCare Agent Development Shell                 ║"
            echo "╚══════════════════════════════════════════════════════════╝"
            echo ""
            echo "Build commands:"
            echo "  make build-linux          Build for Linux"
            echo "  make build-windows-nocgo  Build for Windows (no CGO)"
            echo "  make test                 Run tests"
            echo "  make run-dry              Run in dry-run mode"
            echo ""
            echo "Nix build commands:"
            echo "  nix build .#osiris-agent-linux-amd64"
            echo "  nix build .#osiris-agent-windows-amd64"
            echo ""
          '';
        };

        # Checks for CI
        checks = {
          build = self.packages.${system}.osiris-agent-linux-amd64;
        };
      }
    );
}
