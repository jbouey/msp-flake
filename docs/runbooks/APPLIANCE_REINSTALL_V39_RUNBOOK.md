# Appliance Reinstall Runbook — v39 ISO (FIX-2 + FIX-5 + FIX-6)

**Status:** MUST follow in full order. Physical access required.
**Blast radius:** one appliance per pass. Reinstalls wipe `/var/lib/msp` on the target — evidence is already persisted on Central Command, but in-flight bundles staged locally will be lost.

## Why this runbook exists

All 3 appliances in `north-valley-branch-2` are structurally stuck on a 1 GB root partition. `nixos_rebuild` fails within ~120 s on every attempt because the nix store sqlite can no longer commit (ENOSPC surfaces as both `No space left on device` and `database or disk is full` — both patterns are caught by the `appliance_disk_pressure` invariant after FIX-7 on 2026-04-22). Garbage collection cannot reclaim space: GC itself writes to `db.sqlite`, which is full.

The v39 ISO ships:
- FIX-2 — single `boot.loader.systemd-boot` attrset (no duplicate definition).
- FIX-5 — `nixos-rebuild` pinned in `environment.systemPackages` (no `Failed to find executable` regression).
- FIX-6 — root partition headroom `1G → 8G` via `additionalSpace` in `iso/raw-image.nix`. Survives ~8–10 generations of day-to-day drift plus the nix db and eval-cache working set.

First reinstall is the validation gate for FIX-2 + FIX-5 — they cannot be tested on any existing appliance (all disk-stuck).

## Target selection

Reinstall order:

1. **`7C:D3:0A:7C:55:18`** — least historical state. Lowest risk; validates the ISO itself.
2. **`84:3A:5B:91:B6:61`** — mid-history. Run after target 1 passes the canary.
3. **`84:3A:5B:1D:0F:E5`** — the known-bad canary. Last, because failure here would be ambiguous (always had the most pathology).

Do NOT flash more than one appliance in parallel until target 1 completes a successful `nixos_rebuild`.

## Artifact provenance

On VPS `178.156.162.116`:
- Path: `/tmp/iso-v39-result/iso/osiriscare-appliance.iso`
- Size: 2.2 GB
- SHA256: `d53fc019af081dfb5e76391d92339e4c0f69aaaa7bd4d58624216066fad1ea83`
- Built from commit `0cb67dd1` (HEAD of `main` at build time, FIX-2 + FIX-5 + FIX-6 all present).

## Step 1 — Pull the ISO to your workstation

From your local terminal:

```
scp root@178.156.162.116:/tmp/iso-v39-result/iso/osiriscare-appliance.iso ~/osiriscare-appliance-v39.iso
shasum -a 256 ~/osiriscare-appliance-v39.iso
```

Confirm: `d53fc019af081dfb5e76391d92339e4c0f69aaaa7bd4d58624216066fad1ea83`. If it doesn't match, STOP — the artifact was tampered with or partially copied.

## Step 2 — Flash USB

Identify the USB drive:

```
diskutil list   # macOS
lsblk           # Linux
```

**Confirm the target disk twice.** Writing to the wrong disk destroys data. USB drives typically show as `/dev/diskN` on macOS or `/dev/sdX` on Linux. Never flash to `/dev/disk0` or `/dev/sda` — those are almost always the system drive.

macOS:
```
diskutil unmountDisk /dev/diskN
sudo dd if=~/osiriscare-appliance-v39.iso of=/dev/rdiskN bs=4m status=progress
sync
diskutil eject /dev/diskN
```

Linux:
```
sudo umount /dev/sdX* 2>/dev/null || true
sudo dd if=~/osiriscare-appliance-v39.iso of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

Post-flash verification (reads the first 2.2 GB back off the USB and compares):
```
sudo dd if=/dev/rdiskN bs=4m count=$((2200/4)) 2>/dev/null | shasum -a 256
```
The prefix of the reported digest should match the first bytes of the ISO's digest (full match requires reading exactly the ISO length — `ls -l` the ISO for the precise byte count if you want 1:1).

## Step 3 — Pre-flight at the site

For the target appliance, capture its current state BEFORE physical touch so you can confirm reinstall uniqueness later:

```sql
SELECT appliance_id, hostname, agent_version, last_checkin, first_checkin,
       ip_addresses, (last_checkin IS NULL OR last_checkin < NOW() - INTERVAL '5 min') AS stale
  FROM site_appliances
 WHERE appliance_id = 'north-valley-branch-2-7C:D3:0A:7C:55:18';
```

Record `first_checkin` — after reinstall the new `first_checkin` will be later than this, confirming the appliance was genuinely re-imaged.

## Step 4 — Reinstall

1. Physically insert the USB into the target appliance.
2. Power-cycle the appliance. If it doesn't boot from USB automatically, interrupt to the boot menu (F9 / F12 depending on model; for HP t740 this is F9).
3. Select the USB. The ISO auto-runs `nixos-install` — no interactive prompts. Expected elapsed time: **8–15 minutes** depending on closure cache warmth.
4. The appliance powers off automatically at the end of install. **Remove the USB** before the next power-on or the installer will loop (see substrate invariant `install_loop`).
5. Power on. First boot: ~60–90 s to systemd, then the daemon attempts MAC-based re-enrollment against Central Command.

## Step 5 — Watch the checkin

From any machine with DB access (on VPS):

```
ssh root@178.156.162.116
docker exec mcp-postgres psql -U mcp -d mcp -c "
  SELECT appliance_id, hostname, agent_version, first_checkin, last_checkin, ip_addresses
    FROM site_appliances
   WHERE appliance_id = 'north-valley-branch-2-7C:D3:0A:7C:55:18';
"
```

Expected within ~5 min of power-on:
- `first_checkin` is **newer** than the value captured in Step 3 (genuine reinstall).
- `agent_version` = the version baked into v39 (check `git show 0cb67dd1:appliance/internal/daemon/daemon.go | grep -E "Version[[:space:]]*=" `).
- `last_checkin` within the last 60 s.

If the appliance does NOT check in within 10 min, abort and escalate — the substrate invariants `provisioning_stalled` and `install_loop` should fire within 15 min and surface the cause on `/admin/substrate-health`.

## Step 6 — Validate FIX-2 + FIX-5 via canary

On the VPS, inside the `mcp-server` container, fire a `nixos_rebuild` canary at the freshly-reinstalled appliance. See `mcp-server/central-command/backend/scripts/canary_post_reinstall.py` for the template. Example:

```
ssh root@178.156.162.116 "docker cp /path/to/canary_post_reinstall.py mcp-server:/tmp/canary.py && \
  docker exec mcp-server python3 /tmp/canary.py 7C:D3:0A:7C:55:18"
```

Pass/fail reporting is via the admin_order row:

```sql
SELECT order_id, status, completed_at, LEFT(error_message, 200) AS err
  FROM admin_orders
 WHERE order_id LIKE 'post-reinstall-canary-%'
 ORDER BY created_at DESC LIMIT 3;
```

**Pass criteria:** `status = 'completed'` AND `error_message IS NULL` AND the rebuild's `nix eval` + `switch-to-configuration` both succeed.

**Fail criteria:**
- `status = 'failed'` + error contains `database or disk is full` OR `No space left` → FIX-6 didn't land (partition is still 1 GB). Check `additionalSpace` in `iso/raw-image.nix` at the build commit.
- `status = 'failed'` + error contains `attribute 'loader' already defined` → FIX-2 didn't land (duplicate `boot.loader` block).
- `status = 'failed'` + error contains `Failed to find executable nixos-rebuild` → FIX-5 didn't land.
- Any other failure class → capture the full `error_message` and triage independently.

## Step 7 — Verify root partition size

The most important structural confirmation:

```
ssh -o StrictHostKeyChecking=no root@<APPLIANCE_IP> "df -h /nix/store /"
```

(Appliance SSH keys are managed by Central Command; `/etc/msp-recovery-authorized-keys` is empty unless `enable_recovery_shell_24h` fleet order was issued.)

Expected: the `/` filesystem should have roughly `2.0–2.5 GB available` with `~6 GB used` — i.e., 8 GB partition ≈ closure + headroom. If `/` is `~100M available` on a 1 GB partition, FIX-6 did not land.

## Step 8 — Repeat for targets 2 and 3

Only after Step 6 reports `completed`. Otherwise the fault reproduction is in the image, not the appliance, and reinstalling the other two wastes reflash cycles.

## Rollback

If reinstall fails mid-way and the appliance will not boot off disk:
- Re-insert the USB. The ISO has a second install attempt path (`nixos-install` re-runs on each boot if the installed system fails initramfs).
- If that also fails, escalate — likely a hardware fault, not an installer bug. The `supported_hardware.yaml` gate should have caught known-bad models pre-install; anything making it past the gate and still failing is new ground.

## Post-reinstall monitoring

For 24 h after the first reinstall, watch:
- `/admin/substrate-health` — the `appliance_disk_pressure` row for `north-valley-branch-2` should show `match_count = 2` drop to `1` (only the not-yet-reinstalled appliances remain) after the 24 h window rolls past the last disk-pressure error.
- `/api/dashboard/flywheel-intelligence` — no regression on auto-promotion throughput.
- Scheduled weekly `nix.gc` timer — confirmed in `modules/system.nix` (`nix.gc.automatic = true`, `--delete-older-than 14d`, `persistent = true`). Re-verify `systemctl list-timers nix-gc.timer` on the reinstalled appliance.

## Three-list reminder (only if you also patch the ISO build)

If during this reinstall pass you discover FIX-2/5/6 also need fleet-order handler, agent code, or substrate invariant changes, remember the three-list lockstep rules:

- Privileged orders: `fleet_cli.PRIVILEGED_ORDER_TYPES` + `privileged_access_attestation.ALLOWED_EVENTS` + migration 175 `v_privileged_types`.
- Substrate invariants: `ALL_ASSERTIONS` + `_DISPLAY_METADATA` + `substrate_runbooks/<name>.md` (CI gate via `test_substrate_docs_present.py`).
- Flywheel: `promoted_rule_events.event_type` CHECK + `flywheel_state.EVENT_TYPES` + `promoted_rule_lifecycle_transitions` matrix.

Breaking any of those silently = credibility event.

## Change log

- 2026-04-22 — initial — drafted after FIX-7 validation confirmed all 3 fleet appliances are disk-stuck. Reinstall is the only path forward.
