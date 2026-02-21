# Session 121 - Compliance Pipeline + Systemic Fixes

**Date:** 2026-02-21
**Previous Session:** 120

---

## Summary

Completed the end-to-end compliance evidence pipeline and fixed three systemic issues that caused cascading failures during deployment.

### What Was Done

1. **Evidence pipeline live** - Physical appliance Go daemon (v0.2.0) now submits signed compliance bundles after drift scans. First real bundle: CB-2026-02-21, 7 checks, 6/7 pass (firewall disabled on 192.168.88.250), Ed25519 signed, chain position 128090.

2. **VM appliance 401 fix** - Root cause: Go checkin-receiver validated single static auth token. VM appliance had a different per-site API key. Fix: added per-site key validation from `appliance_provisioning` table. Deployed new checkin-receiver binary to VPS. Both appliances now checking in.

3. **Appliance ID format normalization** - `provisioning.py` created IDs without MAC colons (`843A5B91B661`), but Go/Python checkin used colons (`84:3A:5B:91:B6:61`). Fixed provisioning to use colons. Migrated 35 existing admin_orders rows.

4. **Nix version bump** - Updated Go daemon from 0.1.0 to 0.2.1 in both `appliance-image.nix` and `appliance-disk-image.nix`. Documented rebuild command for deployed appliances.

5. **Compliance packet endpoint working** - Returns real HIPAA compliance data: 56.4% compliance, 15 controls, evidence chain IDs.

### Files Changed

| File | Change |
|------|--------|
| `appliance/internal/evidence/signer.go` | NEW - Ed25519 key management |
| `appliance/internal/evidence/submitter.go` | NEW - Compliance bundle builder + HTTP submit |
| `appliance/internal/evidence/signer_test.go` | NEW - 3 tests |
| `appliance/internal/evidence/submitter_test.go` | NEW - 4 tests |
| `appliance/internal/daemon/daemon.go` | Add evidence submitter init |
| `appliance/internal/daemon/driftscan.go` | Call BuildAndSubmit after scan |
| `appliance/internal/daemon/phonehome.go` | Send agent public key |
| `appliance/internal/daemon/config.go` | SigningKeyPath() helper |
| `appliance/internal/checkin/handler.go` | Per-site API key auth |
| `appliance/internal/checkin/db.go` | ValidateAPIKey() method |
| `appliance/internal/orders/processor.go` | Absolute path for nixos-rebuild |
| `mcp-server/central-command/backend/provisioning.py` | Normalize MAC in appliance_id |
| `mcp-server/central-command/backend/sites.py` | Legacy compat comment |
| `mcp-server/central-command/backend/evidence_chain.py` | Fix compliance_packet import |
| `mcp-server/central-command/backend/compliance_packet.py` | Moved from mcp-server/ |
| `mcp-server/central-command/backend/db_queries.py` | Add check types to CATEGORY_CHECKS |
| `iso/appliance-disk-image.nix` | Version 0.1.0 -> 0.2.1 |
| `iso/appliance-image.nix` | Version 0.1.0 -> 0.2.1 |
| `flake.nix` | Document rebuild command |

### Commits

- `060dc7f` feat: end-to-end compliance pipeline
- `9951dc9` fix: use absolute path for nixos-rebuild
- `8e904c4` feat: per-site API key auth in checkin-receiver

---

## Next Priorities

1. **Kill old daemon on physical appliance** - v0.1.0 from Nix store still holding ports 50051/8090. Run `pkill -f '/nix/store.*appliance-daemon'` from iMac.
2. **Power on remaining Windows VMs** - Only 1 of 4 scanned. Check VirtualBox on iMac for powered-off VMs.
3. **Commit + push systemic fixes** - provisioning.py normalization, nix version bump, documentation
4. **Test nixos-rebuild via order** - Insert rebuild order, verify full chain works (order -> checkin -> rebuild -> new daemon)
5. **WS01 machine trust** - Run `Reset-ComputerMachinePassword` from DC to fix domain auth
6. **Improve compliance score** - Fix the firewall_status drift (enable Windows Firewall on 192.168.88.250)
