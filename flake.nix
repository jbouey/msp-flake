{
  description = "Infra-Watcher container (Fluent Bit + Python tailer)";

  inputs.nixpkgs.url   = "github:NixOS/nixpkgs/nixos-24.05";
  inputs.flake-utils.url = "github:numtide/flake-utils";
  inputs.nix2container.url = "github:nlewo/nix2container";

  outputs = { self, nixpkgs, flake-utils, nix2container }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python311;
        infra-watcher = import ./flake/pkgs/infrawatcher { inherit pkgs python; };
        container-img = import ./container { inherit pkgs infra-watcher nix2container; };
      in {
        packages.default = infra-watcher;
        packages.container = container-img;
        apps.default = flake-utils.lib.mkApp { drv = container-img; };
      });
}