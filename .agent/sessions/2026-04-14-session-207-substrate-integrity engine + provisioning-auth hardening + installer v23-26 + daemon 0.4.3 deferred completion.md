# Session 207 — Substrate Integrity + Auth Hardening + Installer v23-v26

**Date:** 2026-04-14 → 2026-04-15
**Previous Session:** 206
**Themes:** substrate-integrity-engine, provisioning-auth, installer-iso, daemon-completion-gate, hardware-compat

---

## TL;DR

Eight-hour session. Started chasing a t740 install failure, walked
it through 4 ISO iterations (v23 → v24 → v25 → v26) tracing each
failure mode to root cause. Mid-session pivoted to the bigger ask:
why the platform doesn't tell us when a customer's appliance is
silently broken. Built the **Substrate Integrity Engine** — 11
named invariants asserted every 60s, opens/auto-resolves rows in
a `substrate_violations` table, surfaces in a customer-facing
admin panel. Adversarial audit at the end uncovered the actual
mesh-not-joining root cause (auth_failure_count split-brain on
api_keys); shipped triggers + assertions + a legacy-recovery
script for daemons too old to auto-rekey.

---

## What landed (commits)

**Backend hardening**
- `5ea3504` migrations 204/205 — renumbered stray 103/111, DROP POLICY IF EXISTS guards
- `ec344eb` sites.py:3050 `$8::timestamptz` cast — unblocks t740 checkin 500s
- `fb6c91f` migration 206 — `legacy_uuid` backfill + `install_sessions` TTL cleanup
- `c949fc1` migration 207 + `assertions.py` engine + 8 invariants + main.py wiring
- `d9b6132` migration 208 — row-guard bypass for admin DB user (kills per-tx-flag footgun)
- `e87721c` `/admin/substrate-violations` + `/admin/substrate-installation-sla` + `AdminSubstrateHealth.tsx`
- `001f776` migration 209 (api_keys triggers) + 3 more substrate invariants + recovery script
- `9ba0a7e` migration 209 column-name fix (real `admin_audit_log` schema)
- `859f9ce` `fleet_cli` URL DNS validation + `health_monitor` install-loop alert

**Daemon hardening**
- `46eea2b` daemon 0.4.3 — deferred completion gate. Handler writes
  `pending-update.json` marker; processor skips auto-complete on
  `status="update_pending"`; new `CompletePendingUpdate` posts at
  next startup after 90s decision window. 4 unit tests. Binary
  published to `/var/www/updates/appliance-daemon-0.4.3` sha
  `39c89588dd3cfdd15661480002ed0e988350ea000ef7a19b6af14468c4feb32d`.

**Installer ISO**
- `75aeeeb` v23 — sysfs_read tee-log + first-boot hostname tolerance (failed in test)
- `9bb8b35` v24 — sysfs_read returns 0; `/proc/sys/kernel/hostname`; motd tolerant (failed at install reboot)
- `a6390c5` v25 — installer copies `\EFI\systemd\systemd-bootx64.efi → \EFI\BOOT\BOOTX64.EFI` post-dd, fixes UEFI fallback for HP thin clients
- `19a70d4` v26 — `supported_hardware.yaml` + pre-flight gate; halts on uncertified DMI product. Published `/var/www/updates/osiriscare-installer-v26-19a70d4.iso` sha `d3641806b975a7c20eb7873c619e5aae8186253ac19bfc55c9504b8b0392ebb6`.

---

## Substrate Integrity Engine (new architecture)

Runs every 60s. Each tick evaluates 11 invariants against prod;
opens/auto-resolves rows in `substrate_violations` (open-row
dedup on invariant_name + site_id, partial unique index).

Invariants live:
- legacy_uuid_populated · install_loop · offline_appliance_over_1h
- agent_version_lag · fleet_order_url_resolvable
- discovered_devices_freshness · install_session_ttl · mesh_ring_size
- online_implies_installed_system · every_online_appliance_has_active_api_key
- auth_failure_lockout

Adding a new invariant is ~10 lines (one CheckFn returning
`List[Violation]` + one entry in `ALL_ASSERTIONS`).

Customer/auditor/sales view at `/admin/substrate-health`.

---

## Adversarial audit findings (end of session)

| # | Severity | Status |
|---|---|---|
| F1 | Sev-1 | already shipped in v0.3.84+; legacy boxes need recovery script |
| F2 | Sev-1 | **shipped** mig 209 audit trigger |
| F3 | Sev-2 | **deferred** — kill site-level key id=7 only after legacy boxes upgraded |
| F4 | Sev-1 | **shipped** mig 209 one-active trigger |
| F5 | Sev-2 | **shipped** `auth_failure_lockout` invariant |
| F6 | Sev-2 | cascades from F1+F4 fixes |
| F7 | Sev-3 | already in engine (`discovered_devices_freshness`) |
| F8 | Sev-2 | **shipped** `online_implies_installed_system` invariant |

---

## Verified runtime state at session end

- Live runtime SHA: `e87721c` — substrate engine running
- Migrations 204-208 applied; 209 pending CI of `9ba0a7e`
- Canary .241 t640 on daemon 0.4.2 (binary 0.4.3 published, no fleet order yet)
- T740 .228 stuck on live USB (currently APIPA 169.254.61.199 — DHCP failed)
- 1D:0F:E5 t640 powered off
- 7C:D3 t640 on 192.168.0.x (different subnet)
- 5 USB drives potentially being flashed with v26 by operator

---

## Files Changed (all)

| File | Change |
|------|--------|
| mcp-server/central-command/backend/migrations/204_drift_check_exceptions.sql | renumbered from 103 |
| mcp-server/central-command/backend/migrations/205_healing_sla.sql | renumbered from 111 + DROP POLICY guards |
| mcp-server/central-command/backend/migrations/206_reconcile_legacy_uuid_and_cleanup_install_sessions.sql | new |
| mcp-server/central-command/backend/migrations/207_substrate_violations.sql | new |
| mcp-server/central-command/backend/migrations/208_row_guard_admin_user_bypass.sql | new |
| mcp-server/central-command/backend/migrations/209_api_keys_invariants.sql | new |
| mcp-server/central-command/backend/assertions.py | new — 11 invariants + engine |
| mcp-server/central-command/backend/health_monitor.py | + `_check_install_loops` |
| mcp-server/central-command/backend/fleet_cli.py | + DNS validation on update_daemon |
| mcp-server/central-command/backend/sites.py | $8 ambiguous-param fix |
| mcp-server/central-command/backend/routes.py | + 2 substrate endpoints |
| mcp-server/central-command/backend/scripts/recover_legacy_appliance.sh | new — legacy daemon recovery bridge |
| mcp-server/central-command/frontend/src/pages/AdminSubstrateHealth.tsx | new — customer/auditor panel |
| mcp-server/central-command/frontend/src/App.tsx | + route |
| mcp-server/central-command/frontend/src/client/DevicesAtRisk.tsx | NixOS→System label |
| mcp-server/central-command/frontend/src/pages/FleetUpdates.tsx | NixOS→System label |
| mcp-server/central-command/frontend/src/types/index.ts | NixOS→System label |
| mcp-server/main.py | wire `assertions_loop` into supervised tasks |
| iso/appliance-image.nix | v23/v24/v25/v26 fixes; HW-compat gate |
| iso/appliance-disk-image.nix | brief installAsRemovable detour (reverted) |
| iso/supported_hardware.yaml | new — t640+t740 entries |
| appliance/internal/orders/processor.go | deferred-completion sentinel + ctxKeyOrderID |
| appliance/internal/orders/pending_update.go | new — marker file API |
| appliance/internal/orders/pending_update_test.go | new — 7 cases |
| appliance/internal/daemon/daemon.go | wire CompletePendingUpdate at startup |
| docs/m1-full-migration-plan.md | M1 phase plan (Phase 1 only this session) |

---

## Next Session

1. Verify `9ba0a7e` CI lands + mig 209 applies; api_keys triggers active.
2. Hit `/admin/substrate-health` — confirm UI renders + violations open.
3. Operator: run `recover_legacy_appliance.sh` for 1D:0F:E5 + 7C:D3 once on LAN.
4. Push daemon 0.4.3 to canary via fleet order using `api.osiriscare.net` URL.
5. F3 sunset of site-level key id=7 once legacy boxes are upgraded.
6. M1 reader migration phases 2-5 (#151) — plan doc already in repo.
7. T740 install completion verification (currently APIPA — needs DHCP fix or static IP).
