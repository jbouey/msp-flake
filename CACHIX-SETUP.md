# Cachix Binary Cache Setup

**Last Updated:** 2025-11-21
**Cache Name:** msp-platform
**Cache URL:** https://msp-platform.cachix.org

---

## Overview

Cachix is configured to provide binary caches for the MSP Platform, reducing build times by downloading pre-built packages instead of compiling locally.

---

## Current Configuration

### Local Machine (/etc/nix/nix.conf)

```ini
build-users-group = nixbld
substituters = https://cache.nixos.org https://msp-platform.cachix.org
trusted-public-keys = cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY= msp-platform.cachix.org-1:r8C1wX/FDivhkF2+HUQEj6P4Z/licqpa20vdKhrT3Mo=
trusted-users = root dad
```

### GitHub Actions

Cachix is configured in `.github/workflows/ci.yml` using the `cachix/cachix-action`:

```yaml
- uses: cachix/cachix-action@v14
  with:
    name: msp-platform
    authToken: '${{ secrets.CACHIX_AUTH_TOKEN }}'
```

---

## Cache Details

| Setting | Value |
|---------|-------|
| Cache Name | `msp-platform` |
| URL | https://msp-platform.cachix.org |
| Public Key | `msp-platform.cachix.org-1:r8C1wX/FDivhkF2+HUQEj6P4Z/licqpa20vdKhrT3Mo=` |
| Storage | Cachix cloud |

---

## Verification

### Check if Cachix is Working

```bash
# Check nix.conf
cat /etc/nix/nix.conf

# Test cache accessibility
curl -s --head https://msp-platform.cachix.org/nix-cache-info

# Build something and watch for cache hits
nix build ./flake-compliance.nix#compliance-agent -v 2>&1 | grep -E "copying path|building|substitut"
```

### Expected Output (Cache Hit)
```
copying path '/nix/store/...' from 'https://msp-platform.cachix.org'...
```

### Expected Output (Cache Miss/Building Locally)
```
building '/nix/store/...-compliance-agent.drv'...
```

---

## Setup Instructions (If Not Configured)

### 1. Install Cachix CLI (Optional)
```bash
nix-env -iA cachix -f https://cachix.org/api/v1/install
# or
nix profile install nixpkgs#cachix
```

### 2. Configure Substituters

Add to `/etc/nix/nix.conf`:
```ini
substituters = https://cache.nixos.org https://msp-platform.cachix.org
trusted-public-keys = cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY= msp-platform.cachix.org-1:r8C1wX/FDivhkF2+HUQEj6P4Z/licqpa20vdKhrT3Mo=
trusted-users = root YOUR_USERNAME
```

### 3. Restart Nix Daemon
```bash
sudo launchctl kickstart -k system/org.nixos.nix-daemon
```

---

## Pushing to Cache (CI Only)

Builds are automatically pushed to Cachix via GitHub Actions when:
- PR is merged to main
- Manual workflow dispatch

The CI workflow uses `CACHIX_AUTH_TOKEN` secret for authentication.

---

## Troubleshooting

### Cache Not Being Used

1. **Check nix.conf is correct:**
   ```bash
   cat /etc/nix/nix.conf | grep substituters
   ```

2. **Verify daemon sees the config:**
   ```bash
   nix show-config | grep substituters
   ```

3. **Restart nix-daemon:**
   ```bash
   sudo launchctl kickstart -k system/org.nixos.nix-daemon
   ```

### "untrusted substituter" Error

Add yourself to trusted-users in `/etc/nix/nix.conf`:
```ini
trusted-users = root YOUR_USERNAME
```

Then restart nix-daemon.

### Cache Shows 404

The specific package may not be in the cache yet. It will be built locally and (if CI is configured) pushed on next CI run.

---

## Flake Note

This repository has TWO flakes:

| File | Purpose | Use With |
|------|---------|----------|
| `flake.nix` | Old log-watcher (legacy) | Don't use for new builds |
| `flake-compliance.nix` | Compliance agent (current) | Use this one |

**Correct usage:**
```bash
# Build compliance agent
nix build ./flake-compliance.nix#compliance-agent

# Run tests
nix build ./flake-compliance.nix#checks.x86_64-darwin.unit-tests
```
