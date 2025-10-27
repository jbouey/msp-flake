# NixOS-HIPAA Baseline v1.0

**Purpose:** Baseline security configuration for HIPAA-compliant healthcare infrastructure monitoring.

## Files

- **hipaa-v1.yaml** - Complete baseline configuration with ~30 security toggles
- **controls-map.csv** - HIPAA Security Rule controls mapped to NixOS options
- **exceptions/** - Per-client baseline exceptions with risk assessment

## Baseline Philosophy

This baseline follows the "metadata-only" approach:
- ✅ **System logs, configurations, and operational metrics** - IN SCOPE
- ❌ **Patient PHI, medical records, or clinical data** - OUT OF SCOPE

Your MSP platform operates as a **Business Associate for operations only**, not for treatment or records.

## Usage

### Apply Baseline to Client Flake

```nix
{ config, pkgs, ... }:

let
  baseline = builtins.fromJSON (builtins.readFile ./baseline/hipaa-v1.yaml);
in {
  # Import baseline settings
  imports = [ ./baseline-implementation.nix ];

  # Override specific settings if needed
  services.msp-baseline = {
    enable = true;
    baselineVersion = "1.0.0";
    enforce = true;
  };
}
```

### Verify Compliance

```bash
# Check baseline enforcement
nix-instantiate --eval -E '(import ./baseline/hipaa-v1.yaml)'

# Verify control mappings
cat baseline/controls-map.csv | column -t -s,

# Review exceptions
ls -la baseline/exceptions/
```

## Exceptions Process

When a client requires an exception to the baseline:

1. Create exception file: `baseline/exceptions/clinic-001.yaml`
2. Include:
   - Rule being excepted
   - Business justification
   - Risk assessment
   - Compensating controls
   - Owner
   - Expiry date (max 90 days)
3. Get approval (documented in git commit)
4. Review monthly

Example exception:

```yaml
client_id: clinic-001
exceptions:
  - rule_id: ssh.disable_password_auth
    current_value: false  # Exception: password auth still enabled
    baseline_value: true
    reason: "Legacy system integration requires password auth until Q1 2026 migration"
    risk: "medium"
    compensating_controls:
      - "Password complexity enforced (14+ chars)"
      - "Account lockout after 3 failed attempts"
      - "IP whitelist restricts SSH access to office network only"
    owner: "security_team@clinic.com"
    approved_by: "CISO John Doe"
    approved_date: "2025-10-24"
    expires: "2026-03-31"
    review_frequency_days: 30
```

## HIPAA Control Coverage

This baseline addresses **52 controls** from HIPAA Security Rule:

- **Administrative Safeguards** (§164.308): 18 controls
- **Physical Safeguards** (§164.310): 8 controls
- **Technical Safeguards** (§164.312): 20 controls
- **Documentation** (§164.316): 6 controls

See `controls-map.csv` for complete mapping.

## Validation

### Automated Checks

```bash
# Run baseline validation
nix build .#baseline-validator

# Generate compliance report
nix run .#compliance-report -- --baseline hipaa-v1
```

### Manual Verification

Key controls to spot-check:

```bash
# Encryption enabled
lsblk | grep crypt
cryptsetup status /dev/mapper/luks-root

# SSH hardening
sshd -T | grep -E "(PasswordAuthentication|PubkeyAuthentication|PermitRootLogin)"

# Audit logging
systemctl status auditd
aureport --summary

# Time sync
timedatectl status

# Firewall enabled
nftables list ruleset
# or
iptables -L -n -v

# Patch status
nixos-version
nix-channel --list
```

## Review Cycle

- **Baseline review:** Every 90 days
- **Exception review:** Every 30 days
- **Control verification:** Continuous (automated via MCP)
- **Major updates:** Annually or when regulations change

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2025-10-24 | Initial baseline with 52 HIPAA controls |

## References

- [HIPAA Security Rule](https://www.hhs.gov/hipaa/for-professionals/security/index.html) - 45 CFR Parts 160, 162, 164
- [NIST 800-66 Rev. 2](https://csrc.nist.gov/publications/detail/sp/800-66/rev-2/final) - HIPAA Security Rule Guidance
- [OCR Audit Protocol](https://www.hhs.gov/hipaa/for-professionals/compliance-enforcement/audit/protocol/index.html)
- [Anduril NixOS STIG](https://ncp.nist.gov/repository) - Defense-grade NixOS hardening

## Support

Questions about baseline implementation:
- Review `controls-map.csv` for NixOS option mappings
- Check CLAUDE.md for architectural guidance
- Reference runbooks in `/runbooks` for automated enforcement
