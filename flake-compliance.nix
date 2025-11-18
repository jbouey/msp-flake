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
          ];
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
