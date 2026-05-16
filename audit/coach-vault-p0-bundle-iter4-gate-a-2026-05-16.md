# Gate A — Vault P0 bundle iter-4 (re-fork after 3-day pause)
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context, read-only)
Verdict: **BLOCK** — schema-fixture/prod-parity contract violation must be reconciled BEFORE Commit 2 ships; additional P0s on `current_signing_method` exception-swallowing + commit-boundary doc drift.

## Q1 — iter-4 substantively different from iter-1/2/3?

Three reverted-iteration root causes (per `memory/feedback_vault_phase_c_revert_2026_05_12.md`):

1. **Fixture-vs-prod-schema drift** — `*_pg.py` CREATE TABLE blocks did not include `fleet_orders.signing_method`; INSERT-with-column then broke every pg test.
2. **Startup-invariant timeout** — `INV-SIGNING-BACKEND-VAULT` called `vault_backend.public_key()` (lazy-init over WireGuard) without `asyncio.wait_for`; `/health` blocked 120s → deploy timeout → rollback.
3. **Fix-forward without per-iteration Gate A** — each iter unmasked a NEW bug class; the meta-defense is per-bundle Gate A + Gate B.

iter-4 closure bindings — verified IN-TREE:
- (1) ✓ Lockstep updated. Verified `signing_method TEXT NOT NULL DEFAULT 'file'` present in `test_startup_invariants_pg.py:59`, `test_privileged_chain_adversarial_pg.py:66`, `test_promotion_rollout_pg.py:90` (3 of 6 spot-checked). CI gate `tests/test_pg_fixture_fleet_orders_column_parity.py` per design — must verify present in Commit 2 (currently NOT verified live).
- (2) ✓ Designed — `asyncio.wait_for(timeout=5.0)` on entire `_vault_probe` coroutine + lifespan eager-warm step before `check_all_invariants()`. See Q5 for residual concerns.
- (3) ✓ This Gate A IS that defense. Mandatory citation of audit paths in commit body (P0 #7).

## Q2 — ship-Commit-2-alone safety

Commit 1 shipped `59e6d4c2` on 2026-05-13 and has soaked for 3 days. `fleet_updates.py`, `flywheel_promote.py`, `cve_watch.py`, `sites.py` (2 callsites) all write `current_signing_method()` at INSERT. No revert noise in `git log` since. Write-path is stable.

**Verdict on framing:** the "2-commits-1-push" constraint in the redesign doc was a mid-bundle fix-forward defense. With Commit 1 stable in prod for 72h+, shipping Commit 2 alone is **safer than the original bundle** — failures in Commit 2 cannot poison Commit 1's already-soaked write path. **APPROVE the standalone framing in principle.**

However the commit body MUST cite the asymmetry explicitly so a future grep for "1-push-bundle" doesn't conclude the design was violated. Add a §"why standalone is safer than the original bundle" paragraph.

## Q3 — Vault reachability + ship-safety

Operator-stated state: Vault `https://10.100.0.3:8200/v1/sys/health` healthy; `SIGNING_BACKEND_PRIMARY=file` in prod; every new fleet_order tagged `signing_method='file'` (Commit 1 live).

Net effect at Commit 2 ship-time (assuming Vault reachable):
- INV probes Vault → bootstrap-INSERT `(key_name, key_version=N, pubkey_hex=...)` with `known_good=FALSE`. Operator manually approves via UPDATE.
- Substrate invariant scans last-hour `fleet_orders.signing_method` against env primary. Observed='file', env='file' → returns `[]`. **Safe.**

Failure modes not explored in design:
- **Vault transient unreachable at ship-time:** lifespan eager-warm times out (5s), INV times out (5s) → `ok=False detail="vault probe exceeded 5s — startup proceeding non-blocking"`. **Acceptable — INV is credibility event, not availability event.** Good.
- **Vault returns a key_version we've never seen:** bootstrap-INSERT with `known_good=FALSE`. Substrate invariant does NOT check `known_good` (it compares signing_method observed vs env). **Question:** is there an invariant for "Vault reports key version N but no `known_good=TRUE` row"? Design doc does not bind one. **P1** — see findings.
- **Vault returns a key_version that EXISTS in the table with `known_good=FALSE`:** ON CONFLICT DO NOTHING is correct (don't overwrite operator-approval state). Good.

The reverse-shadow soak (#48) is NOT a prerequisite for shipping the INV + invariant — they're observational, no behavior change. Shipping now produces 72h+ of bootstrap-row observation that informs the reverse-shadow design.

## Q4 — pg-test fixture drift mitigation

Fixture parity check:
- `fleet_orders.signing_method` — ✓ present in `prod_columns.json` (added Commit 1 era).
- **`vault_signing_key_versions` — ANOMALOUS PRESENCE.** All 10 columns + types present in `prod_columns.json` / `prod_column_types.json` (added via `ad2f3281` schema-fixture regen on 2026-05-14). Per `test_sql_columns_match_schema.py` docstring + Session 220 #77 lock-in, these sidecars are extracted from PROD schema. **But mig 311 has NEVER landed in prod** (claimed in RESERVED_MIGRATIONS as BLOCKED; the reverted commit `5da797b3` never re-shipped).

**Two possible explanations, both require investigation BEFORE Commit 2 ship:**
1. Someone hand-applied mig 311 SQL on the VPS db outside the migration runner → the table exists in prod without a corresponding committed migration. **Severity: P0 (provenance violation; Counsel Rule 9 — provenance + determinism not decoration).**
2. The fixture regen was sourced from a non-prod database (staging? local?) → `test_schema_fixture_parity.py` parity contract is silently broken. **Severity: P0 (CI gate believed to be authoritative is actually drifted).**

Lockstep update required in Commit 2: if explanation (1), the mig 311 file must reconcile with what's already in prod (no-op if identical; `IF NOT EXISTS` guards). If explanation (2), the fixture regen MUST be re-run from authoritative prod source AND CI parity gate restored.

**This is the iter-1/2 root cause class re-emerging via a different surface.** Reverted-iter root cause was missing-column-in-fixture; iter-4 is extra-table-in-fixture-not-in-prod-migrations. Same drift class.

## Q5 — Startup INV timeout binding

Reading the design:

- **`asyncio.wait_for` semantics:** can only cancel awaits, not blocking sync code. The design wraps `_vault_probe` as a coroutine but `get_signing_backend()` is synchronous (verified at signing_backend.py:422). Inside the coroutine, calling `get_signing_backend()` directly would block the event loop — `asyncio.wait_for` cannot rescue that. The design's eager-warm uses `asyncio.to_thread` correctly. **The INV's internal probe must use `asyncio.to_thread` for every signing_backend call, OR be entirely on a thread.** Design doc P0 #1 says "the timeout MUST cover the singleton-build AND the key-version read AND the public-key read, all in one coroutine" — but does not bind the asyncio.to_thread wrapping. **P0** — must be explicit in implementation.

- **Thread-cancel semantics:** `asyncio.wait_for` on `asyncio.to_thread(get_signing_backend)` cancels the AWAITER, NOT the thread. The OS thread keeps spinning. If the next request hits `get_signing_backend()` it joins the in-flight singleton-build (via `_BACKEND_SINGLETON_LOCK`) and inherits the hang. Design says "Container starts anyway; INV will fire ok=False detail" — but does NOT address worker hang on subsequent request. **P0 carry-forward from iter-3 root cause** — the design's footnote "P1 followup tracked (hvac socket-timeout to bound the OS thread)" admits this gap exists. The P1 must be promoted to a SHIP-BLOCKING P0 OR the hvac client must already have a socket-level timeout configured BEFORE Commit 2 ships. Otherwise we are one Vault-network-hiccup away from re-living the iter-3 outage.

- **5.0s ceiling:** WireGuard + TLS handshake + Transit read — generous. Fine.

- **`current_signing_method` exception swallow:** at signing_backend.py:451 the helper does `except Exception: return SIGNING_BACKEND_PRIMARY...`. This is the IDENTICAL antipattern that iter-1 was reverted for: silent fallback under exception. Currently Commit 1 is live with this shape — every fleet_order INSERT during a Vault-build-failure window will tag `signing_method=<env primary>` regardless of actual signing path. The substrate invariant (Commit 2) compares observed vs env — observed will FALSELY MATCH env, hiding the failure. **P0** — must add `logger.error(exc_info=True)` before the fallback return, AND the substrate invariant must additionally scan `_BACKEND_SINGLETON is None` or surface a Prometheus gauge for `current_signing_method_fallback_total`.

## Q6 — Counsel's 7 Rules

- **Rule 1 (canonical metric):** `signing_method` is operator-only (substrate-health panel + admin Prometheus). Not customer-facing. No new customer-facing metric introduced. ✓
- **Rule 2 (no raw PHI):** Vault Transit signs hashes, never PHI. INV `detail` field carries `key_name`, `key_version`, pubkey fingerprint — operationally sensitive but not PHI. ✓ (P0 #4 admin-only readability test pins this.)
- **Rule 3 (privileged chain):** `signing_key_rotation` is in all 3 lockstep lists (verified: `fleet_cli.PRIVILEGED_ORDER_TYPES`, `privileged_access_attestation.py:55`, `migration 175 v_privileged_types` + `migration 305` re-anchor). Commit 2 does NOT add a new privileged order type. ✓ Future Vault Phase C cutover (`SIGNING_BACKEND_PRIMARY=file→vault`) is an env flip, not a fleet_order — does NOT route through privileged chain. **Question:** should the cutover itself be attested? Not in scope for iter-4, but worth a TaskCreate for the cutover sprint.
- **Rule 4 (no orphan coverage):** N/A — no new collector/segmentation.
- **Rule 5 (no stale doc):** design doc is dated 2026-05-13 (3 days stale). Verify the design still matches current code reality (Q7).
- **Rule 6 (BAA state not in human memory):** N/A — Vault signing keys are not BAA state.
- **Rule 7 (opaque by default):** INV `detail` exposed only via `/api/admin/substrate-health` (admin-gated). P0 #4 test pins this. ✓

## Q7 — Coach: 3-day staleness

Intervening commits since 2026-05-13 Gate A approval (16 substantive commits touching `signing_backend.py` / `assertions.py` / `startup_invariants.py` / `RESERVED_MIGRATIONS.md`):

Key intervening surface:
- `ad2f3281` (2026-05-14) — **schema-fixture regen with typed sidecar.** This is THE commit that introduced `vault_signing_key_versions` into `prod_columns.json` without a corresponding live migration. P0 contamination (see Q4).
- `0b1581e0` (2026-05-14) — fold prod_column_widths into combined regen. Same fixture-regen lineage.
- `27c8fdc1`, `fd54e637`, `b615c1ab`, `dea8b964`, `3b055427`, `d042802e` — multiple new substrate invariants added to `assertions.py`. **Question:** does the new `signing_backend_drifted_from_vault` invariant's ASSERTION_METADATA slot collide with any of these names? Design doc was written BEFORE these landed. Must grep at Commit 2 implementation time.
- `56d14e22` (mig 314), `508c5922` (mig 315), `e5793fc0` (mig 319), `b615c1ab` (mig 323), `a85d8775` (mig — bearer_revoked), `76f0e86b` (mig 321) — migration numbers 312–323 have shipped since 2026-05-13. Mig 311 is the only gap. **No collision risk for mig 311 itself**, but RESERVED_MIGRATIONS ledger row must be reconciled (currently says BLOCKED — needs status update or removal).
- `df12cbfb` (enterprise hardening), `2713d177`, `8ed1e01d`, `4e79f26d` — new BAA / canonical-source enforcement. None directly conflict with Vault P0 work.

Breakage classes introduced:
- Schema-fixture/prod-parity contract is now load-bearing for CI gates (`test_no_param_cast_against_mismatched_column.py` reads `prod_column_types.json`). Drift in this fixture has downstream blast radius beyond just the Vault work. **Reinforces Q4 P0.**

## Findings

### P0 (BLOCK)
- **P0-A — schema-fixture/prod-parity violation (Q4).** `vault_signing_key_versions` present in `prod_columns.json` + `prod_column_types.json` but no migration shipped. Either prod has been hand-touched (provenance breach) or the fixture was regenerated from a non-prod source (CI parity gate silently broken). **MUST reconcile BEFORE Commit 2 ships.** Direct `psql` query against prod to confirm table existence + extracted-from-prod parity, then either (a) ship mig 311 with `CREATE TABLE IF NOT EXISTS` guards if prod already has it OR (b) regen fixtures from the authoritative prod source.
- **P0-B — `asyncio.to_thread` wrapping not bound in design (Q5).** Design P0 #1 says "wrap the entire Vault block in `asyncio.wait_for`" but does not require each sync call (`get_signing_backend()`, key-version read, public-key read) to go through `asyncio.to_thread`. Without that wrapping, the coroutine BLOCKS the event loop and `wait_for` is a no-op. Implementation MUST wrap each sync hop in `asyncio.to_thread` OR run the entire probe on a single thread. Pin via grep gate.
- **P0-C — hvac socket-level timeout (Q5).** The design's P1 footnote about hvac socket-timeout admits the thread-cancel gap. This MUST be a P0: configure hvac client's underlying `requests.Session` with `timeout=(connect=2, read=3)` BEFORE Commit 2 ships, OR the iter-3 root cause re-emerges via a different surface (thread leak instead of coroutine block).
- **P0-D — `current_signing_method` silent exception swallow (Q5).** signing_backend.py:451 `except Exception: return SIGNING_BACKEND_PRIMARY...` is the iter-1 antipattern in a different jacket. Add `logger.error(exc_info=True)` AND surface `current_signing_method_fallback_total` Prometheus gauge AND extend the substrate invariant to detect non-zero fallback count over last hour. Otherwise Commit 2's substrate invariant has a false-negative blind spot.
- **P0-E — RESERVED_MIGRATIONS ledger row reconciliation.** Current ledger says "BLOCKED on staging precondition". If shipping mig 311 now, the row MUST be REMOVED in the same commit per CLAUDE.md rule. If shipping a no-op `IF NOT EXISTS`-guarded version (because prod already has the table), commit body MUST explain.

### P1 (MUST-fix-or-task)
- **P1-A — bootstrap-row `known_good=FALSE` operator-action invariant.** Add a future substrate invariant (or extend the proposed one) to alert when a Vault `key_version` has been observed for >7 days but `known_good=FALSE`. Otherwise approval rot is invisible. TaskCreate is sufficient if not in Commit 2.
- **P1-B — Vault Phase C cutover attestation.** When `SIGNING_BACKEND_PRIMARY` flips to `vault`, should that be an attested privileged event? Not in scope for iter-4 but TaskCreate for the cutover sprint.
- **P1-C — commit body must address "standalone safer than bundle" framing (Q2).** Avoid future "the design said 2-commits-1-push" grep confusion.
- **P1-D — verify no `ASSERTION_METADATA` name collision** (Q7) with the 4+ invariants added since 2026-05-13.

### P2 (consider)
- Design doc is 3 days stale; consider re-stamping with `last_verified: 2026-05-16` + a "post-pause delta" §.
- The `_kit_compresslevel`-style determinism rigor applied to auditor-kit is worth applying to the substrate invariant's emitted detail JSON (sort_keys) so two consecutive runs produce comparable diffs.

## Final
**BLOCK** — Commit 2 cannot ship until P0-A (fixture/prod-parity) is reconciled with direct prod-db evidence, and P0-B/C/D (thread-cancel + hvac timeout + silent-fallback) are bound to concrete implementation shape with grep-able CI gates. P0-E is a 30-second ledger edit but must land in the same commit. P1s convert to TaskCreate items if not in Commit 2.

Verdict file: `audit/coach-vault-p0-bundle-iter4-gate-a-2026-05-16.md`
