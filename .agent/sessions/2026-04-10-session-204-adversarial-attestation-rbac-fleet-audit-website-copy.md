# Session 204 — Enterprise Hardening + Installer Redesign

**Date:** 2026-04-10 to 2026-04-11 (16+ hours)
**Previous Session:** 203
**Daemon:** v0.3.85 → v0.3.86 → v0.3.87 → v0.3.88
**Commits:** 40+
**Migrations:** 151, 152, 153, 154

---

## Enterprise Installer (NEW — from scratch)

- Raw disk image Nix derivation (`iso/raw-image.nix`) — complete NixOS system as compressed raw image
- 1.0GB compressed (zstd-19), 7.6GB decompressed, 3 partitions (ESP + root + MSP-DATA)
- Installer rewrite (`appliance-image.nix`) — dd-based, zero network, ANSI visual progress
- eMMC support: initrd modules (mmc_block, sdhci_pci, sdhci_acpi), partition naming, drive detection
- Poweroff not reboot (no USB-snatching race)
- No efibootmgr (was hijacking BIOS boot order, preventing reinstalls)
- No dialog (was freezing on T740, blocking zero-friction)
- Config from USB partition (offline provisioning)
- Shared drive detection (`iso/detect-drive.sh`) — single source of truth
- Nix daemon version: single `daemonVersion` variable (was duplicated)
- Spec: `docs/superpowers/specs/2026-04-11-installer-redesign.md`
- Plan: `docs/superpowers/plans/2026-04-11-installer-redesign.md`
- T740 successfully installed via enterprise installer on eMMC — v0.3.88 from first boot

## Security — 5 Trust Boundary Audits

### Cackle-level adversarial audit (Identity, Policy, Execution, Attestation, Access)

**Critical fixes:**
- Evidence submit cross-site injection (auth_site_id enforcement)
- SSO cross-org login (org_id scoping on user lookup)
- Magic link MFA bypass (enforce org MFA on all login paths)
- Shell injection in run_backup_job (regex validation)
- L2 raw script execution blocked (runbook-only enforcement)
- enable_emergency_access added to dangerousOrderTypes
- Healing orders respect disabled_checks (client authority enforced)
- canonical_id + merge_from_ids UnboundLocalError (500 on new registration)

**Infrastructure:**
- Migration 151: 69 DELETE protection triggers on all evidence + audit tables
- Migration 152: FK on client_approvals.acted_by, created_by columns on 3 tables
- Migration 153: Appliance soft-delete (deleted_at + deleted_by)
- Migration 154: Index on discovered_devices(appliance_id, device_status)
- Chain gap detection in verify_chain_integrity
- Timestamp validation on evidence submit (reject backdated/future)
- Fleet order health check: systemd transient timer (survives daemon restart)
- Fleet order immutability trigger after completion
- HIPAA 7-year retention enforced (removed 3-year auto-purge)
- 7 missing _audit_client_action() calls added
- Provision audit logging (3 new audit event types)

## Architecture — WireGuard Lifecycle

- WireGuard off by default (`wantedBy = mkForce []`)
- Emergency access: customer-initiated, time-bounded, systemd timer enforced, fail-secure
- Client portal toggle: POST /api/client/emergency-access/enable + /disable
- Bootstrap auto-teardown on install completion
- wg_access_state reporting in daemon health
- Fleet teardown order sent to existing appliances

## Fleet + Ghost Resolution

- Ghost appliance root-caused: multi-NIC bug (two Intel NICs on same machine)
- Deterministic MAC selection (sorted by interface name)
- all_mac_addresses in checkin for ghost detection
- 3-node mesh live: hash ring 192 entries, targets distributed
- T740 installed on eMMC, v0.3.88, checking in as osiriscare-3
- Evidence flowing from all 3 appliances
- Peer witnessing: 4,469+ attestations

## Provisioning Overhaul

- Provision modal (MAC + client email)
- Self-registration: unclaimed list + claim endpoint
- Deployment pack: config.yaml for offline USB provisioning
- API key single-use rotation on first checkin
- boot_source telemetry (live_usb vs installed_disk)
- Device discovery push notifications
- Welcome email on first checkin
- Provisioning response Ed25519 signing
- Partner API key role scoping (derives from partner_user, not hardcoded admin)

## Autodeploy Hardening

- SHA256 verification on all 3 transfer paths (HTTP, NETLOGON, base64)
- Final SHA256 gate before service install

## Portal Audits (Admin + Client + Partner)

- Admin: 38+ pages — PASS
- Client: MFA bypass fixed, 7 audit gaps closed — PASS
- Partner: API key role scoping, RBAC verified — PASS

## Website Copy

- "Always audit-ready. With proof — not paperwork."
- Fear language, proof positioning, no AI hints

## Mesh Resilience

- Stale device cleanup on subnet change
- Soft-delete for appliances
- display_name auto-generation

## Production State

- Health: OK
- Fleet: 3 appliances (v0.3.88, v0.3.87, v0.3.88)
- Evidence: 234K+ bundles
- Triggers: 69 active
- CI: Green

## Next Session

1. Client onboarding wizard
2. Stripe env vars + price IDs
3. New devices — validate enterprise installer at scale
4. Checkin handler decomposition
5. Integrate detect-drive.sh into Nix configs
