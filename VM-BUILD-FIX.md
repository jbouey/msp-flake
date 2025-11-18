# VirtualBox VM Build Fix - Module Import Error

## Error Fixed

```
error: The option `description' does not exist. Definition values:
- In `/Users/jrelly/Documents/msp-flake/flake-compliance.nix':
  "MSP Compliance Appliance - Self-Healing NixOS Agent"
```

## Root Cause

The example configuration (`examples/test-client-wired.nix`) was incorrectly importing the **flake definition file** as a NixOS module:

```nix
imports = [
  ../flake-compliance.nix  # ❌ This is a flake, not a module!
];
```

### Why This Failed

1. **Flake vs Module Confusion:**
   - `flake-compliance.nix` = Flake definition (has `description`, `inputs`, `outputs`)
   - `modules/compliance-agent.nix` = NixOS module (has `options`, `config`)

2. **Wrong Service Namespace:**
   - Used: `services.msp-compliance`
   - Correct: `services.compliance-agent`

3. **Incorrect Option Names:**
   - Used: `egressAllowlist`, `evidenceRetentionDays`, `secretsProvider`
   - Correct: `allowedHosts`, `evidenceRetention`, (no secretsProvider)

## Solution Applied

### 1. Fixed Import Path

```diff
  imports = [
-   ../flake-compliance.nix
+   ../modules/compliance-agent.nix
  ];
```

### 2. Fixed Service Name

```diff
- services.msp-compliance = {
+ services.compliance-agent = {
    enable = true;
```

### 3. Fixed Option Names

```diff
  services.compliance-agent = {
    enable = true;
    siteId = "test-client-001";
    deploymentMode = "direct";
    mcpUrl = "http://MCP_SERVER_IP:8000";

-   egressAllowlist = [ "MCP_SERVER_IP:8000" ];
+   allowedHosts = [ "MCP_SERVER_IP" ];  # Hostnames only, no ports

-   evidenceRetentionDays = 7;
+   evidenceRetention = 50;  # Number of bundles, not days

-   logLevel = "debug";
+   logLevel = "DEBUG";  # Must be uppercase
  };
```

## Module Option Reference

### Required Options
- `siteId` - Unique site identifier (no default)
- `resellerId` - Required only if `deploymentMode = "reseller"`

### Common Options with Defaults
```nix
services.compliance-agent = {
  enable = true;
  siteId = "your-site-id";  # REQUIRED

  # Connection
  mcpUrl = "https://mcp.local";  # Default
  allowedHosts = [ "mcp.local" ];  # Egress allowlist

  # Deployment
  deploymentMode = "reseller";  # or "direct"
  resellerId = null;  # Required if mode = "reseller"

  # Evidence
  evidenceRetention = 200;  # Number of bundles to keep
  pruneRetentionDays = 30;  # Days to keep bundles

  # Logging
  logLevel = "INFO";  # DEBUG/INFO/WARNING/ERROR

  # Maintenance
  maintenanceWindow = "02:00-04:00";  # HH:MM-HH:MM UTC

  # Optional certificates (for mTLS)
  clientCertFile = /path/to/cert;
  clientKeyFile = /path/to/key;
  signingKeyFile = /path/to/signing-key;
};
```

### Options That Don't Exist
```nix
# ❌ These will cause errors:
egressAllowlist = [...];     # Use: allowedHosts
evidenceRetentionDays = 7;   # Use: evidenceRetention
logLevel = "debug";          # Use: "DEBUG" (uppercase)
secretsProvider = "env";     # Doesn't exist (use cert file paths)
clientId = "...";            # Use: siteId
```

## Testing the Fix

### On Your iMac

```bash
cd ~/Documents/msp-flake
git pull origin main

# Clean previous failed attempts
rm -rf deploy/

# Rebuild with fixed configuration
./scripts/deploy-vbox-vms.sh --full test-client-wired
```

### Expected Output

```
[INFO] Packaging flake for transfer...
[INFO] ✓ Flake packaged to: deploy/msp-flakes.tar.gz
[INFO] Building VirtualBox VM for: test-client-wired
[INFO] Building VirtualBox OVA (this may take 10-15 minutes)...

# ... build progress ...

[INFO] ✓ VM 'test-client-wired' started successfully (headless mode)

Connection details:
  SSH: ssh -p 4444 root@localhost
  HTTP: http://localhost:8080
```

### Build Time Expectations

**First build:**
- ~15-20 minutes on modern Mac
- Downloads NixOS packages
- Builds system image
- Creates VirtualBox OVA

**Subsequent builds:**
- ~2-5 minutes (uses Nix cache)

## File Structure Clarity

```
msp-flake/
├── flake-compliance.nix          # ← Flake definition (don't import this)
│   └── description, inputs, outputs
│
├── modules/
│   └── compliance-agent.nix      # ← NixOS module (import this)
│       └── options, config
│
└── examples/
    ├── test-client-wired.nix     # ← Fixed: now imports module
    ├── direct-config.nix
    └── reseller-config.nix
```

## Common Mistakes to Avoid

### ❌ Wrong: Importing Flake as Module
```nix
imports = [ ../flake-compliance.nix ];
# Error: The option 'description' does not exist
```

### ✅ Correct: Importing NixOS Module
```nix
imports = [ ../modules/compliance-agent.nix ];
# Works: Gets options and config
```

### ❌ Wrong: Using Old Service Name
```nix
services.msp-compliance = { ... };
# Error: Option 'services.msp-compliance' does not exist
```

### ✅ Correct: Using Actual Service Name
```nix
services.compliance-agent = { ... };
# Works: Matches module definition
```

## Troubleshooting

### "option does not exist" Errors

**If you see:**
```
error: The option 'services.compliance-agent.OPTION_NAME' does not exist
```

**Check:**
1. Option name spelling (case-sensitive)
2. Option actually exists in module (see `modules/compliance-agent.nix` lines 19-238)
3. Using correct type (string vs list vs int)

**Find valid options:**
```bash
# Show all compliance-agent options
nix eval --impure --expr '
  let
    nixos = import <nixpkgs/nixos> {
      configuration = { imports = [ ./modules/compliance-agent.nix ]; };
    };
  in
  builtins.attrNames nixos.options.services.compliance-agent
'
```

### Build Still Fails

**Get detailed error:**
```bash
# Run build with maximum verbosity
nix-build '<nixpkgs/nixos>' \
  -A config.system.build.virtualBoxOVA \
  -I nixos-config=./examples/test-client-wired.nix \
  --show-trace
```

**Check:**
1. Module syntax: `nix-instantiate --parse modules/compliance-agent.nix`
2. Example syntax: `nix-instantiate --parse examples/test-client-wired.nix`
3. Module assertions (lines 243-256 in compliance-agent.nix)

## Next Steps

1. **Pull the fix:**
   ```bash
   git pull origin main
   ```

2. **Clean previous attempts:**
   ```bash
   rm -rf deploy/
   ```

3. **Rebuild:**
   ```bash
   ./scripts/deploy-vbox-vms.sh --full test-client-wired
   ```

4. **If successful:**
   ```bash
   # Connect to VM
   ssh -p 4444 root@localhost

   # Check compliance agent
   systemctl status compliance-agent
   journalctl -u compliance-agent -f
   ```

## Related Documentation

- Module options: `modules/compliance-agent.nix` (lines 19-238)
- Module assertions: `modules/compliance-agent.nix` (lines 243-256)
- Deployment script: `scripts/deploy-vbox-vms.sh`
- VirtualBox guide: `VIRTUALBOX-DEPLOYMENT.md`

---

**Fixed:** 2025-11-18
**Commit:** f1c5a34
**Status:** ✅ Ready to rebuild
**Expected Result:** VirtualBox VM builds successfully
