{
  description = "Infra-Watcher container (Fluent Bit + Python tailer)";
  
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";
    flake-utils.url = "github:numtide/flake-utils";
    nix2container.url = "github:nlewo/nix2container";
    nix2container.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, flake-utils, nix2container }:
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
        
        # Container image derivation
        container-img = import ./flake/container/default.nix {
          inherit pkgs infra-watcher;
          nix2container = n2cPkgs;
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
            # Container tools
            podman
            skopeo
          ];
          
          shellHook = ''
            echo "🔍 Infra-Watcher Development Environment"
            echo "Python: $(python --version)"
            echo "Fluent Bit: $(fluent-bit --version | head -1)"
            echo ""
            echo "Available commands:"
            echo "  nix build .#container    # Build container image"
            echo "  nix run .#container      # Run container"
            echo "  nix develop              # Enter dev shell"
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
        
        # Apps for easy execution
        apps = {
          default = flake-utils.lib.mkApp { drv = container-img; };
          container = flake-utils.lib.mkApp { drv = container-img; };
          infra-watcher = flake-utils.lib.mkApp { drv = infra-watcher; };
        };
        
        # Development environment
        devShells.default = devShell;
        
        # Formatter for `nix fmt`
        formatter = pkgs.nixpkgs-fmt;
      }
    ) // {
      # Overlay for use in other flakes
      overlays.default = final: prev: {
        infra-watcher = final.callPackage ./flake/pkgs/infra-watcher.nix {
          python = final.python311;
        };
      };
      
      # NixOS module (if applicable)
      nixosModules.infra-watcher = import ./flake/modules/infra-watcher.nix;
    };
}