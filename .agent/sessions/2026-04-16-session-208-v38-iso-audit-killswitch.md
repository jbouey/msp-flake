# Session 208 cont. — v38 ISO: kernel-audit kill-switch + halt telemetry + compat-match relax

**Date:** 2026-04-16
**Agent:** v0.4.6  |  **ISO:** v38 (this session)  |  **Schema:** v2.0

## Context

Picking up after the Session 208 OTS audit + SEO cluster + round-table that
produced the `provisioning_stalled` invariant. The t740 at the home lab
(192.168.0.104, MAC `84:3A:5B:1F:FF:E4`) was supposed to come up on v37
but wouldn't actually boot the installed system — pings responded, but
port 22 was actively refused (TCP RST), port 80 accepted then hung,
ports 443/2222/9100 timed out. Screen spammed "audit kudewik overflow".
That pointed straight at the Linux kernel audit subsystem.

User call: "if you know its fucked just flash a new iso and let's go
toward fixing." Per the no-lackluster-ISOs rule, v38 batches MUST +
SHOULD items instead of a single-shot fix.

## Root cause

`modules/compliance-agent.nix` set `security.auditd.enable = mkDefault true`.
Combined with the default NixOS execve audit rule, auditd produced events
faster than `kauditd` could drain them on HP t740-class hardware. The
backlog overflowed, the kernel spammed the console with kauditd warnings,
userspace starved (sshd + appliance-daemon never came up functionally).
The live ISO path had already fixed this (`iso/appliance-image.nix:235`
set `security.auditd.enable = lib.mkForce false` + added `audit=0` to the
live kernel cmdline) — but that fix never propagated to the installed
system. Every box built from v25 through v37 inherited the bug silently.

## Changes landed in v38

**MUST (fixes the t740 silent-boot-death):**

1. `iso/appliance-disk-image.nix:278` — added `"audit=0"` to `kernelParams`.
2. `modules/compliance-agent.nix:852` — flipped `security.auditd.enable`
   from `mkDefault true` to `mkDefault false`, with a full comment
   explaining why and how to re-enable safely.
3. `iso/appliance-image.nix:170` — bumped `installer_version = "v38"`
   (Nix attribute used in `/etc/osiriscare-build.json`).
4. `iso/appliance-image.nix:410` — bumped `INSTALLER_VERSION="v38"`
   (bash var used in every `/api/install/report/*` payload).

**SHOULD (closes the silent-halt visibility gap):**

5. `iso/appliance-image.nix:698` — new `post_halt_report()` bash helper.
   Non-blocking. POSTs `{installer_id, installer_version, halt_stage,
   halt_reason, hw_product, bios_vendor, bios_version, log_tail}` to
   `${API_BASE}/api/install/report/halt` with `--connect-timeout 5 -m 10`.
   Mirrors `post_start_report` / `post_complete_report` auth (X-Install-Token).
6. `iso/appliance-image.nix:969` — rewrote `check_hardware_compat()`:
   - Relaxed product-string match. Exact first (preserves existing yaml
     keys), then trim+lowercase normalized match, then substring vs
     `t740 t640 t630 t730` token list. Closes misses on `"HP t740 Thin
     Client "` (trailing space) and `"HP T740 Thin Client"` (case drift).
   - Both halt paths (unknown_product and tested_false) now call
     `post_halt_report` BEFORE `sleep 86400` — Central Command sees
     the halt within seconds instead of decoding "silent zombie install".

**Backend (supports the new halt post):**

7. `mcp-server/central-command/backend/install_reports.py` — new
   `InstallReportHalt` Pydantic model + `@router.post("/report/halt")`
   handler. UPDATE-then-INSERT pattern so a halt that beats `/report/start`
   still records a row. Logs at `logger.warning` so the log shipper
   alerts on it. `COALESCE(:hw_product, product_name)` keeps whatever
   `/report/start` already captured if it ran first.
8. `mcp-server/central-command/backend/migrations/226_install_report_halt.sql`
   — adds `halted_at TIMESTAMPTZ`, `halt_stage VARCHAR(80)`,
   `halt_reason VARCHAR(80)`, `halt_log_tail TEXT` to `install_reports`
   + partial index on `halted_at DESC WHERE halted_at IS NOT NULL`.
9. CSRF exempt path `/api/install/report/` already covers the new endpoint.
   X-Install-Token auth already required via existing `require_install_token`.

## Deferred / next session

- **Promote a new substrate invariant `install_halted_uncertified`**
  that fires off `install_reports.halted_at IS NOT NULL AND halt_reason IN
  ('unknown_product','tested_false')`. Today the signal is visible via
  the admin `/api/install/report` endpoint + `halted_at` column, but
  it's not yet on the Substrate Health panel. Invariant count currently 28
  (including `provisioning_stalled` from earlier today).
- **`install_sessions.halt_reason` / `.hw_product` passthrough.** The
  round-table flagged adding these columns so `_check_provisioning_stalled`
  can read halt context directly. Deferred — `install_reports` is already
  keyed by `installer_id` and the admin UI can JOIN in. Premature.
- **iMac-side reflash.** `/Users/dad/Downloads/osiriscare-appliance-v37.iso`
  stays on disk until the v38 build lands; then the user reflashes the
  USB and retries the t740. No action needed from the appliance side —
  the v38 installed system will just boot with `audit=0` and quiet
  auditd. `release.osiriscare.net` is intentionally unused — per
  Session 207 that DNS record doesn't exist.

## Verification plan

1. Confirm `result/iso/*.iso` lands with new SHA on the VPS (build in
   progress as of this writeup).
2. `sha256sum` the artifact, ship to `/Users/dad/Downloads/osiriscare-appliance-v38.iso`.
3. Reflash USB, boot t740. Expected: installer runs (compat-gate
   passes via exact match `HP t740 Thin Client`), install completes,
   installed system boots with `audit=0` in cmdline, no kauditd spam,
   sshd + appliance-daemon both functional, checkin reaches Central
   Command within ~90s.
4. Verify on VPS: `install_reports.installer_version = 'v38'` for the
   new box. `site_appliances.agent_version = '0.4.6'` after checkin.
5. Confirm no regression: sibling 7C:D3:0A:7C:55:18 continues online
   across the v38 bump (no fleet re-deploy happening — this is install-
   side only, existing boxes unchanged until their next reinstall).

## Incident notes

**VPS build blockage.** First VPS build failed with `undefined:
newDaemonDialer` in `appliance/internal/daemon/phonehome.go`. The
function is defined in `resolver.go` (committed locally). Root cause:
Nix `buildGoModule` with `src = ../appliance` filters source files via
the git index when the tree is dirty. The VPS's copy at `/root/Msp_Flakes`
was rsync'd but never `git add`'d, so `git ls-files` didn't know about
`resolver.go` and Nix excluded it from the build source. Fix: `git add -A`
on the VPS under both `/root/Msp_Flakes` and the nested `/root/Msp_Flakes/appliance`
git repo. Added a rule in CLAUDE.md so we don't hit this again.
