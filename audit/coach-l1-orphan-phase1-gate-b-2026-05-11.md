# Gate B verdict — L1-orphan substrate Phase 1 (2026-05-11)

**Verdict:** APPROVE

As-implemented artifact matches Gate A v2 directives. No new P0/P1 issues. Two minor (P2) observations noted, non-blocking.

## Gate A v2 directive compliance
- **P0-1 (mig 305 absent):** PASS — `git status migrations/` is empty; only pre-existing `304_quarantine_synthetic_mttr_soak.sql` is present. No 305+ migration added.
- **P0-2 (no immutable-row writes):** PASS — implementation is detection-only (substrate invariant + CI gate + opt-out comments). Zero `INSERT/UPDATE` to `compliance_bundles` or any mig-151-frozen table.
- **P0-3 (3-file CI gate scope):** PASS — `test_l1_resolution_requires_remediation_step.py:52-56` defines `_FILES_TO_SCAN = [agent_api.py, sites.py, main.py]`, identical to L2 sibling at `:37-41`.
- **P1-1 (window-runs-before-LIMIT comment):** PASS — `assertions.py:1183-1185` carries the explicit one-line explanation.
- **P1-2 (no `monitoring_reason` discriminator):** PASS — not in Phase 1.
- **P1-4 (no daemon-side mirror):** PASS — backend-only.

## Adversarial findings

**No P0, no P1.** Two P2 observations:

- **P2-1 (Carol — operator UX nuance).** `fleet_orphan_count_24h` is computed via a SECOND `conn.fetchval` query (`assertions.py:1207-1218`). Both queries run inside the per-assertion `admin_transaction(pool)` wrapper (`:5534`), so they see the same MVCC snapshot — correct. Surfaced count saturates at `LIMIT 50` but the `fleet_orphan_count_24h` detail field carries the true number for the dashboard. Confirmed via `display_metadata` entry at `:2870-2884`.
- **P2-2 (Maya — defensive coding).** `DISTINCT ON (i.site_id, COALESCE(i.dedup_key, i.id::text))` at `:1189` with `ORDER BY i.site_id, COALESCE(i.dedup_key, i.id::text), i.resolved_at DESC` at `:1202` — DISTINCT ON columns lead the ORDER BY, satisfying Postgres's requirement. Tie-break on `resolved_at DESC` picks the freshest flap, which is the right operator signal.

## Per-lens analysis

### Steve (Principal SWE)
CI gate shape mirrors L2 sibling exactly (lookback=80, guard regex, allowlist-comment shape). Opt-out shape `# l1-orphan-allowed: proposal-write — …` at `agent_api.py:1105` + `:1131` matches the regex `"# l1-orphan-allowed" in line` at `test:124`. Synthetic positive + negative + allowlist controls cover regex-rot detection. Three-file scope avoids the test-fixture/migration false-positive class.

### Maya (DBA)
Window function semantics correct (`COUNT(*) OVER PARTITION BY site_id` evaluates before LIMIT). DISTINCT ON ordering correct. `LEFT JOIN ... WHERE irs.id IS NULL` is the canonical anti-join shape. 24h window bounds the violation set. Index coverage on `incidents.resolution_tier + status + resolved_at` should be verified in Phase 2 substrate dashboard tuning (not a Phase 1 blocker — 1131 rows is trivial).

### Carol (Security/Audit)
Detection-only Phase 1 = zero attack surface added. No new endpoint, no new SQL writes, no new auth path. `Violation.details` dict is JSON-serializable (`int(...)`, `.isoformat()`, `str(...)`) — fits the existing JSONB column at `substrate_violations`. No PHI in violation details (incident_pk + incident_type + counts only).

### Coach (Process)
Gate A v2 P0-1/P0-2/P0-3 all enforced. Pre-push allowlist updated at `.githooks/pre-push:256`. Test file is git-tracked. No "shipped" claims yet — Phase 1 is staged, not deployed.

## Recommendation

**APPROVE.** Phase 1 commit may proceed. Commit body must:
1. Cite Gate A v2 verdict + Gate B verdict paths.
2. Reference both audit files.
3. Defer mig 305 backfill + `monitoring_reason` discriminator + daemon-side mirror to Phase 2/3 explicitly in the commit body.
4. After push: wait CI green → `curl /api/version` → assert `runtime_sha == deployed commit` per Session 215 #77 rule before claiming Phase 1 shipped.

P2-1 and P2-2 are observations, not fixes-required. No follow-up tasks needed.
