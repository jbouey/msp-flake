# Post-Mortem: v40.x Appliance Brick Class (three source-of-truth splits)

**Incident ID:** 2026-04-23-v40-appliance-brick-class
**Severity:** sev2
**Status:** published (action-items-open)
**Author:** Claude Code session (operator: jeff)
**Published:** 2026-04-24
**Duration:** ≈ 8 h (2026-04-23 14:30 UTC → 2026-04-24 02:30 UTC)

## Summary

Three reflashed appliances at `north-valley-branch-2` bricked on first boot across ISOs v40.0, v40.1, v40.2, and v40.3 — the installed system either deadlocked in systemd (Phase 0 ordering), classpath-errored in shell (missing binary in a present derivation), or silently auth-looped (stale api_key + out-of-date sub-component HTTP clients). Recovery required hand-crafted `INSERT INTO api_keys` rescue SQL and three ISO rebuilds.

## Impact

- Fleet at `north-valley-branch-2`: 3 appliances (`84:3A:5B:1D:0F:E5`, `84:3A:5B:91:B6:61`, `7C:D3:0A:7C:55:18`) unavailable simultaneously. `live_status` was falsely "online" due to the phantom-online class; real checkin age was 4–40 h stale.
- Evidence chain paused for the site during the outage (0 `compliance_bundles` rows for ~6 h).
- Break-glass submit never executed for any of the 3 boxes — the chain-of-custody passphrase record for this boot period is missing.
- CI blocked on main for 5 consecutive commits (non-customer-visible but substantial engineering time).

## Timeline (UTC)

- `14:30` — operator starts flashing `84:3A:5B:1D:0F:E5` with v40.0 ISO.
- `14:32` — `install_sessions` row inserted with `first_seen=14:32`, `checkin_count=0`. Installer posts `/report/start` exactly once and stops.
- `14:45` — second appliance (`91:B6:61`) flashed with v40.0. Same single-post pattern.
- `15:39` — operator observes all 3 boxes appear green in dashboard while `last_checkin` is stale (phantom-online class).
- `17:33` — v40.1 ISO built (Phase 0 ordering fix — removes `Before=[sysinit,multi-user]` which was deadlocking user-space boot).
- `17:55` — v40.2 ISO built (embeds operator pubkey for rescue SSH).
- `18:10` — v40.3 ISO built (pkgs.inetutils/bin/host → pkgs.bind.host, because `inetutils-2.5` in the pinned nixpkgs no longer contains `host`; also fixes em-dash in Python `b'...'` bytes literal inside `msp-status-beacon.py`).
- `21:32` — operator flashes all 3 boxes with v40.2. SSH comes up on each, but msp-auto-provision still fails because v40.2 doesn't have the classpath fix yet (that's v40.3).
- `22:07` — VPS disk hits 100% (4 ISO builds in a day overwhelmed the daily 7 d nix-gc). Postgres PANICs. Backend cascades into 30 min of 5xx.
- `22:08` — `nix-collect-garbage --delete-older-than 7d` reclaims 71 GiB. Postgres recovers.
- `22:12` — first `systematic-debugging` skill invocation (finally). Stops the compound-thrash.
- `00:17` — commit `5a635c1e` ships v40.3 ISO + backend B1 fix (`/api/provision/{mac}` mints fresh key + dual-writes `api_keys` + `appliance_provisioning.api_key`). CI green (first green commit in 5 pushes).
- `01:11` — daemon on `.242` auto-rekeys; `api_keys` gets fresh row. But its `UpdateAPIKey` writes only top-level of `config.yaml` while the signature-verify reader and other tooling see the nested block — split config.yaml first surfaces here.
- `01:37` — operator executes rescue SQL: `INSERT INTO api_keys (..., active=true) VALUES (..., hash_of_current_config_yaml_top_level_key, ...)` for `.242` + `.245`. `.245` recovers in 60 s. `.242` is wedged in circuit-breaker silent state.
- `02:00` — operator power-cycles `.242`. Fresh daemon + rescue INSERT's active row = 200 checkin within 78 s.
- `02:21` — operator power-cycles `.246`. First rescue INSERT targets the **nested** `config.api_key` (45 min lost to wrong-hash rescue). Second INSERT targets the **top-level** `api_key` (correct — matches daemon's Go-yaml-unmarshal). Box recovers.
- `02:30` — Principal SWE round-table identifies three structural source-of-truth splits.
- `02:38` – `03:00` — commits `a87dc9a6`, `5a6bd7df`, `67a06836` close all three splits at the code level (Split #1 stage-1 writers removed + stage-2 DROP COLUMN migration 241, Split #2 `yq` filter to `.config`, Split #3 daemon `CredentialProvider` interface in 0.4.9).

## Contributing factors

1. **[architectural]** `appliance_provisioning.api_key` (raw, frozen at registration) vs `api_keys.active` (hashed, rotation-aware) — same logical value, two tables, no sync.
2. **[architectural]** `/api/provision/{mac}` response shipped both a signed `config:` envelope AND legacy top-level flat duplicates; installer `yq -y '.'` wrote both verbatim.
3. **[architectural]** Daemon sub-components (`incident_reporter`, `l2planner.TelemetryReporter`, `logshipper.Shipper`) each captured `cfg.APIKey` by value at construction; rekey updated one but not N copies.
4. **[tooling]** `pkgs.inetutils/bin/host` was valid at nix-eval time but the binary was absent in the derivation — `inetutils-2.5` in the pinned nixpkgs rev had lost `host` in a prior nixpkgs refactor (moved to `bind.host`).
5. **[tooling]** Python `b'...'` bytes literal with a U+2014 em-dash = SyntaxError. No CI step parsed embedded heredocs.
6. **[architectural]** FIX-11 DNS gate's docstring said "non-blocking by design"; the implementation used `set -euo pipefail` + command substitution, making every probe fatal.
7. **[tooling]** Pre-push hook only ran `tests/test_iso_*.py` (8 tests); CI ran all `tests/test_*.py` (120+). 5 consecutive CI failures were all in the missing 14 governance tests.
8. **[procedural]** Operator (Claude) pushed 5 commits without running full local pytest — cost a 5-cycle regression chain.
9. **[procedural]** `systematic-debugging` skill's "3+ failures = architectural" rule not invoked until failure #5. Compound-thrash instead of pause-and-question.
10. **[observability]** Daemon "systemd active but zero CPU, no logs" state (circuit breaker open) is externally invisible. No metric, no alert.
11. **[observability]** `/api/provision/rekey` rate limit (1/hr/appliance) combined with circuit breaker = daemon cannot self-recover for 55+ min once wedged.
12. **[external]** VPS disk filled to 100% from accumulating ISO build artifacts — daily `nix-gc` at 7d retention insufficient for multi-ISO-per-day pace.

## Root cause

**The same bearer-token value was stored in multiple places with no enforced synchronization** — across the DB (`appliance_provisioning` vs `api_keys`), the on-disk config (`config.yaml` nested vs top-level), and daemon memory (`d.config.APIKey` vs N sub-component copies) — such that any of the three independently could drift and silently break authentication.

## Detection

- **Who detected it:** operator (Claude session) observing that `last_checkin` stayed stale in `site_appliances` across multiple reflash attempts.
- **How long from start to detection:** ≈ 20 min (first reflash 14:30, operator noticed pattern ≈ 14:50).
- **What SHOULD have detected it first:**
  - `installer_halted_early` invariant (exists now, didn't exist then) — would have fired at 14:52.
  - `appliance_daemon_last_checkin_age_seconds` Prometheus alert (doesn't exist) — would fire at 14:35.
  - QEMU boot-integration test (added tonight as `checks.x86_64-linux.appliance-boot`) — would have caught the Phase 0 deadlock + the missing `host` binary at build time, before any ISO shipped.

## Recovery

- **What we did:**
  1. `nix-collect-garbage --delete-older-than 7d` — reclaimed 71 GiB.
  2. Iterative ISO rebuilds v40.1 → v40.2 → v40.3 → v40.4 → v40.5.
  3. Hand-crafted rescue SQL: compute sha256 of daemon's current `config.yaml` api_key, `INSERT INTO api_keys ... active=true` per appliance (Migration 209 trigger auto-deactivates prior active row).
  4. Physical power-cycle for the two wedged daemons (circuit breaker couldn't self-recover during rate-limit window).
- **What would have been faster:**
  1. A `scripts/rescue_appliance.py <mac>` CLI that does the hash + INSERT + verification in one command. Estimated implementation: 45 min. Saved time tonight: ≈ 2 h.
  2. Prometheus alert `daemon_last_checkin_age > 300s` with a Slack webhook. Saved time: ≈ 20 min per incident.
  3. Canary deploy (one box at a time) instead of parallel flash — would have caught the brick class after box #1, not after box #3.
- **Total recovery time from detection:** ≈ 7 h 40 min.

## Action items

| # | Item | Owner | Due | Tracked in |
|---|------|-------|-----|------------|
| 1 | Ship v40.7 ISO with daemon 0.4.9 (CredentialProvider) + flat config.yaml + migration 241 | operator | 2026-04-25 | Task #129 |
| 2 | Reflash `.242`, `.246` with v40.7 (kill auth-loop class on current fleet) | operator | 2026-04-25 | Task #128 |
| 3 | Define SLO: "99% of reflashed appliances green in < 5 min; fleet error-budget 0.5%/mo" | PM | 2026-04-30 | — |
| 4 | Prometheus gauge `appliance_daemon_last_checkin_age_seconds` + alert at 180s | SRE | 2026-04-30 | Task #129 |
| 5 | `scripts/rescue_appliance.py <mac>` CLI (one-command rescue with audit log) | backend | 2026-05-02 | Task #129 |
| 6 | Wire `nix flake check` (QEMU boot test) into CI deploy gate | release eng | 2026-05-05 | Task #129 |
| 7 | VPS nix.gc: tighten from 7d retention to 3d + disk-pressure watcher at 85%/90% | SRE | 2026-04-30 | Task #129 |
| 8 | `/api/provision/rekey` rate-limit: first-hour-of-appliance-lifetime bypass (5/hr instead of 1/hr) | backend | 2026-04-30 | Task #129 |
| 9 | Circuit breaker: self-exit daemon process after 3 open cycles (systemd gives clean slate) | daemon | 2026-05-05 | Task #129 |
| 10 | Migration CI lint: reject `SELECT apply_migration(`  + raw `INSERT INTO schema_migrations` | DBA | 2026-04-30 | Task #129 |
| 11 | Pre-push hook must run every `tests/test_*.py` file the CI `test` job runs (or documented subset) | operator | 2026-04-26 | done (commit `6b424a96`) ✅ |
| 12 | Consolidate scattered `docs/*_RUNBOOK.md` files into `docs/runbooks/` subdirectory | doc migration | 2026-04-26 | — |
| 13 | Policy: no single commit may change both `iso/` and `mcp-server/central-command/backend/` if one depends on the other being deployed first | operator | 2026-04-26 | — |

## What worked (keep-list)

- **Invariant engine** correctly detected `evidence_chain_stalled`, `auth_failure_lockout`, `offline_appliance_over_1h` at the outcome layer. All three fired, on time.
- **Ed25519 evidence chain** held across the outage. No chain-hash gaps on surviving bundles.
- **RLS multi-tenant isolation** was not breached. No cross-site data exposure during the 401 storm.
- **Ed25519 signing key rotation state** on `site_appliances.agent_public_key` remained consistent; no re-enrollment needed.
- **OpenTimestamps anchoring** continued running in the background throughout the outage (`OTS_PROOF_ANCHORED` audit event mid-incident confirmed).
- **systematic-debugging skill** (once invoked) produced the pause that led to the correct multi-split diagnosis. Needs earlier invocation, not replacement.
- **Operator SSH + ed25519 pubkey baked into v40.2+ ISO** (Session 207 Phase S recovery substrate) gave us an escape hatch for every wedged box. Without that, this is a 24-h outage with physical-console drives.

## Related

- Decision record: `docs/adr/2026-04-24-source-of-truth-hygiene.md` (this incident's structural takeaway).
- Previous post-mortem with adjacent class: `docs/postmortems/2026-04-13-migration-162-outage.md` (migration auto-apply gap; different class, same operational pattern of silent cascading failure).
- Session logs with richer narrative: `.agent/sessions/2026-04-23-*.md` + `.agent/sessions/2026-04-24-*.md`.
- Code commits: `5a635c1e`, `a87dc9a6`, `5a6bd7df`, `67a06836`, `6b424a96`, `aef915e6`.

## References

- Commits in scope: [5a635c1e](../../commit/5a635c1e), [a87dc9a6](../../commit/a87dc9a6), [5a6bd7df](../../commit/5a6bd7df), [67a06836](../../commit/67a06836).
- CI runs showing the 5-failure chain: GH Actions run IDs 24865502450, 24865936067, 24866913991, 24867149290, 24867267930.
- VPS Postgres PANIC log: captured inline in `.agent/sessions/2026-04-23-*.md`.
- Circuit-breaker silence journal: captured inline at `.246` + `.242` during the wedge windows.
