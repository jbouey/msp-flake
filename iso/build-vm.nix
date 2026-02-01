# NixOS VM for Building Appliance ISOs
#
# Deploy this on VirtualBox to have a local ISO build environment.
# Requirements: 4GB RAM, 40GB disk, 2 CPU cores minimum
#
# Installation:
# 1. Download NixOS minimal ISO: https://nixos.org/download.html
# 2. Create VirtualBox VM (Linux, 64-bit, 4GB RAM, 40GB disk)
# 3. Boot from NixOS ISO
# 4. Run: sudo -i
# 5. Partition disk: parted /dev/sda -- mklabel gpt && parted /dev/sda -- mkpart primary 512MiB 100% && parted /dev/sda -- mkpart ESP fat32 1MiB 512MiB && parted /dev/sda -- set 2 esp on
# 6. Format: mkfs.ext4 -L nixos /dev/sda1 && mkfs.fat -F 32 -n boot /dev/sda2
# 7. Mount: mount /dev/disk/by-label/nixos /mnt && mkdir -p /mnt/boot && mount /dev/disk/by-label/boot /mnt/boot
# 8. Generate config: nixos-generate-config --root /mnt
# 9. Copy this file to /mnt/etc/nixos/configuration.nix
# 10. Install: nixos-install
# 11. Reboot and login as root

{ config, pkgs, ... }:

{
  imports = [
    ./hardware-configuration.nix
  ];

  # Boot
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # Networking
  networking.hostName = "nix-builder";
  networking.networkmanager.enable = true;

  # SSH for remote access - use SSH key authentication
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "no";  # Use builder user + sudo
      PasswordAuthentication = false;  # SSH key only
    };
  };

  # Nix settings for building
  nix = {
    settings = {
      experimental-features = [ "nix-command" "flakes" ];
      max-jobs = "auto";
      cores = 0;  # Use all cores
      trusted-users = [ "root" "builder" ];
    };
    # Garbage collection
    gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 14d";
    };
  };

  # Build user - add your SSH key for access
  users.users.builder = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    hashedPassword = "!";  # Disabled - use SSH key authentication
    openssh.authorizedKeys.keys = [
      # Add your SSH public key here for access
      # Example: "ssh-ed25519 AAAA... your-key-comment"
    ];
  };

  # Root password disabled - use builder user + sudo
  users.users.root.hashedPassword = "!";

  # Essential packages for building
  environment.systemPackages = with pkgs; [
    git
    vim
    htop
    tmux
    wget
    curl
    tree
  ];

  # VirtualBox guest additions
  virtualisation.virtualbox.guest.enable = true;
  virtualisation.virtualbox.guest.x11 = false;

  # Performance tuning for builds
  boot.kernel.sysctl = {
    "vm.swappiness" = 10;
  };

  # Shared folder for ISO output (optional)
  # Mount with: VBoxManage sharedfolder add "nix-builder" --name "iso-output" --hostpath "/path/to/iso" --automount
  fileSystems."/mnt/shared" = {
    device = "iso-output";
    fsType = "vboxsf";
    options = [ "rw" "nofail" "uid=1000" "gid=100" ];
  };

  system.stateVersion = "24.05";
}
