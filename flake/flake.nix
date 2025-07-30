{
  description = "NixOS configuration pulling from GitHub flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.05";  # adjust if needed
    mspflake.url = "git+file:///home/osirisclinic/msp-flake"; 
    # later you can swap to "github:jbouey/msp-flake"
  };

  outputs = { self, nixpkgs, mspflake, ... }@inputs:
    let
      system = "x86_64-linux";
    in {
      nixosConfigurations.vm = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          mspflake/modules/base.nix
          mspflake/modules/compliance.nix
          mspflake/modules/monitoring.nix
        ];
      };
    };
}
