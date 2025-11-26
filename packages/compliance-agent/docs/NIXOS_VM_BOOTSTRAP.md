# NixOS VM Bootstrap for Compliance Agent

The NixOS VM needs SSH access configured before deploying the compliance agent.

## Option 1: Via VirtualBox Console

1. Open VirtualBox on the Mac host (174.178.63.139)
2. Select the NixOS VM (`nixos-24.05.7376.b134951a4c9f-x86_64-linux.ovf`)
3. Click "Show" to open the console window
4. Log in (default NixOS users vary by image - try `root` with empty password)
5. Run these commands:

```bash
# Enable and set root password
sudo passwd root

# Or add SSH key (recommended)
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Add the MCP server's SSH key
cat > /root/.ssh/authorized_keys << 'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDjaF3Yqc7TABafk4RHmtyT6bfjrz2TKTIA+AAYL3S2s root@mcp-server
EOF
chmod 600 /root/.ssh/authorized_keys

# Ensure SSH is enabled (usually is by default)
systemctl enable sshd
systemctl start sshd

# Verify
ip addr show | grep 10.0.3
```

## Option 2: Via Shared Folder (Already Set Up)

A shared folder named `bootstrap` has been added to the VM with the SSH key.
If the VM mounts shared folders automatically:

```bash
# Check if shared folder is mounted
ls /run/host/share/bootstrap/ 2>/dev/null || ls /mnt/bootstrap/ 2>/dev/null

# If mounted, copy the key
mkdir -p /root/.ssh && chmod 700 /root/.ssh
cp /run/host/share/bootstrap/authorized_keys /root/.ssh/
# or
cp /mnt/bootstrap/authorized_keys /root/.ssh/
chmod 600 /root/.ssh/authorized_keys
```

## Option 3: Add Key via NixOS Configuration

Edit `/etc/nixos/configuration.nix`:

```nix
{
  users.users.root.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDjaF3Yqc7TABafk4RHmtyT6bfjrz2TKTIA+AAYL3S2s root@mcp-server"
  ];

  services.openssh = {
    enable = true;
    settings.PermitRootLogin = "prohibit-password";
  };
}
```

Then rebuild:
```bash
nixos-rebuild switch
```

## After Enabling SSH

Once SSH is accessible from the MCP server (10.0.3.4), the compliance agent can be deployed:

```bash
# From MCP server
ssh root@10.0.3.5 hostname

# Expected: nixos
```

## Network Details

- **NixOS VM IP**: 10.0.3.5
- **MCP Server IP**: 10.0.3.4
- **Windows DC**: WinRM at localhost:55985 (from Mac host)
- **NAT Network**: msp-network (10.0.3.0/24)
