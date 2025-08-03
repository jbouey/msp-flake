{
  description = "Infra-Watcher container (Fluent Bit + Python tailer)";
  inputs.nixpkgs.url        = "github:NixOS/nixpkgs/nixos-24.05";
  inputs.flake-utils.url    = "github:numtide/flake-utils";
  inputs.nix2container.url  = "github:nlewo/nix2container";
  inputs.nix2container.inputs.nixpkgs.follows = "nixpkgs";
  
  outputs = { self, nixpkgs, flake-utils, nix2container }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs   = import nixpkgs { inherit system; };
        python = pkgs.python311;
        # package derivation for the log-watcher
        infra-watcher = import ./flake/pkgs/infra-watcher.nix {
          inherit pkgs python;
        };
        # attr-set that actually exposes `buildImage`
        n2cPkgs = nix2container.packages."${system}";
        # container image derivation
        container-img = import ./flake/container/default.nix {
          inherit pkgs infra-watcher;
          nix2container = n2cPkgs.nix2container;  # Pass the nix2container package, not the package set
        };
      in
      {
        packages.default   = infra-watcher;
        packages.container = container-img;
        apps.default       = flake-utils.lib.mkApp { drv = container-img; };
      });
}