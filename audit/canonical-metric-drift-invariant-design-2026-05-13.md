# `canonical_metric_drift` Substrate Invariant — Design (Task #50 Phase 2, Counsel Rule 1)

> **Counsel Rule 1 (gold authority):** every customer-facing metric must declare a canonical source. The CI gate (Phase 0+1, shipped) is the static-AST half. This substrate invariant is the RUNTIME half — per Task #50 Gate B P0-A, the comparison MUST be **display-time vs chain-time** (most recent signed `compliance_bundles` row), NOT display-time vs fresh-helper-recompute. Same-helper-same-data proves nothing; chain-time comparison materializes the Master BAA Article 3.2 cryptographic-attestation-chain claim without double-building (chain already exists; invariant reads its head).

> **Multi-device-enterprise lens:** at multi-tenant scale, a substrate invariant that detects drift between customer-facing surface values and chain-attested values is the auditor-grade evidence that "what we show customers matches what we attested." Without it, the canonical-source registry is a static-AST claim with no runtime proof.

---

## §1 — The invariant's contract

For every tenant T with a recent signed `compliance_bundles` row containing a chain-attested value for metric M:

**Invariant:** the canonical helper's CURRENT output for tenant T's metric M either (a) matches the chain-attested value OR (b) chain-attestation has rolled forward to a newer bundle that reflects the current state.

**Violation:** the canonical helper's output diverges from chain-attested value AND no newer bundle exists explaining the drift.

Two divergence classes:

| Class | Likely cause | Severity |
|---|---|---|
| **Class A — helper-output ≠ chain-head, chain is fresh** | Canonical helper logic changed semantics without re-attesting the chain. Bug. | sev2 |
| **Class B — helper-output ≠ chain-head, chain is stale** | Chain hasn't been re-attested recently; helper reflects newer state. Expected drift. | sev3-info (operational, not bug) |

The invariant auto-resolves once chain-attestation rolls forward (Class B → Class A transition self-heals).

---

## §2 — Display-time capture — three mechanisms considered

Per Gate B P0-A: "display-time" comparison needs a captured display value. The substrate engine doesn't make HTTP requests, so we need an in-DB source for the "what was displayed" reading.

### Mechanism A — Re-compute via canonical helper (REJECTED per Gate B P0-A)

Substrate calls `compute_compliance_score(conn, site_ids)` and compares vs `compliance_bundles` head. Gate B explicitly rejected this — "two calls to the same helper proves nothing."

### Mechanism B — Response-sampling table (HEAVY)

Wrap customer-facing endpoints with a decorator that writes the response to a sampling table (`customer_metric_response_samples`); invariant reads the sample table.

PRO: actual response captured.
CON: heavy infrastructure; samples consume DB space; sampling cadence + retention design needed; high implementation cost.

### Mechanism C — Helper-output recorded into compliance_bundles signing chain (CHOSEN)

Every signed `compliance_bundles` row already contains the attested metric values (compliance scores, evidence counts, check statuses) at attestation time. The "chain-time" value IS the bundle's content. The "display-time" value comes from the canonical helper at invariant-query-time. The substrate invariant compares these two — but crucially, the helper's output is computed against THE SAME data the chain attested (rather than fresh data) by querying compliance_bundles + reconstructing the helper's input from the bundle's `period_start`/`period_end`/`check_window`.

In other words: the invariant asks "given the same input data the chain was attested against, does the canonical helper still produce the same output?" If yes, the helper hasn't drifted. If no, the helper's semantics changed and the chain is now stale.

This is the auditor-grade comparison Gate B P0-A demanded:
- It's NOT fresh-helper-recompute (which proves nothing) — it's helper-recompute against the chain's input.
- It IS chain-time comparison — the chain attested a specific value at a specific input; the invariant verifies the helper still produces that value for that input.
- Class A (helper diverged) is detected.
- Class B (chain stale but helper still consistent under chain's input) is NOT a violation — the helper would produce the same output if given the chain's input again.

---

## §3 — Invariant query shape (Mechanism C)

```python
async def _check_canonical_metric_drift(conn: asyncpg.Connection) -> List[Violation]:
    """Sev2 — canonical helper's output diverges from chain-attested
    value when given the same input data the chain attested against.

    For each tenant with a signed compliance_bundles row from the
    last 7 days, this invariant:
      1. Reads the bundle's attested metric value(s).
      2. Reads the bundle's attestation input (period_start, period_end,
         site_ids covered).
      3. Re-runs the canonical helper against that SAME input.
      4. Compares helper output to chain-attested value.
      5. If they differ (Class A drift), fires sev2.
    """
    rows = await conn.fetch(
        """
        SELECT
            cb.site_id,
            cb.bundle_id,
            cb.period_start,
            cb.period_end,
            cb.signed_at,
            -- The bundle's attested metric value (extracted from
            -- the canonical signed payload).
            cb.attested_compliance_score
          FROM compliance_bundles cb
         WHERE cb.signed_at > NOW() - INTERVAL '7 days'
           AND cb.attested_compliance_score IS NOT NULL
           AND cb.deleted_at IS NULL  -- soft-delete filter
        """,
    )
    out: List[Violation] = []
    for r in rows:
        # Re-run canonical helper against the chain's input
        from compliance_score import compute_compliance_score
        helper_result = await compute_compliance_score(
            conn,
            site_ids=[r["site_id"]],
            window_start=r["period_start"],
            window_end=r["period_end"],
        )
        helper_score = helper_result.get("score")
        chain_score = r["attested_compliance_score"]
        # Allow up to 0.1% rounding tolerance (compliance scores
        # are decimal with floor at 0.1).
        if helper_score is None or abs(helper_score - chain_score) > 0.1:
            out.append(Violation(
                site_id=r["site_id"],
                details={
                    "bundle_id": r["bundle_id"],
                    "chain_attested_score": chain_score,
                    "helper_current_score": helper_score,
                    "chain_attested_at": r["signed_at"].isoformat(),
                    "interpretation": (
                        f"Canonical helper output ({helper_score}) "
                        f"diverges from chain-attested value "
                        f"({chain_score}) for bundle {r['bundle_id']} "
                        f"at site {r['site_id']}. Helper semantics may "
                        f"have changed without re-attestation."
                    ),
                    "remediation": (
                        "Investigate: did compute_compliance_score "
                        "change recently? If yes, re-attest the affected "
                        "compliance_bundles. If no, suspect data "
                        "corruption or schema-migration drift in the "
                        "underlying check tables."
                    ),
                },
            ))
    return out
```

---

## §4 — Tolerance + thresholds

- **Floating-point tolerance:** 0.1 (compliance scores have decimal floor of 0.1 per `compute_compliance_score` rounding).
- **Recency window:** last 7 days of signed bundles (older bundles are pre-canonical-helper-deployment; not in scope).
- **Per-tenant limit:** 50 violations max per tick (avoid runaway scan; if more than 50, surface as a meta-finding "widespread drift — investigate platform-wide").
- **Auto-resolution window:** invariant clears when chain rolls forward (new bundle attests current helper output).

---

## §5 — Mechanism C precondition

The `compliance_bundles.attested_compliance_score` column does NOT exist today. The invariant requires this column to be populated at bundle-signing time. Migration design:

```sql
-- mig 314 (or next-available): attested_compliance_score column.
ALTER TABLE compliance_bundles
  ADD COLUMN IF NOT EXISTS attested_compliance_score NUMERIC(5,1) NULL;
COMMENT ON COLUMN compliance_bundles.attested_compliance_score IS
  'Compliance score (0-100, 1-decimal) attested in this bundle at sign '
  'time. Populated by compliance_packet.sign_bundle when computing the '
  'canonical signed payload. NULL for pre-2026-05-13 bundles + any '
  'bundle that doesn''t attest a compliance score.';
```

The signing path (likely `compliance_packet.sign_bundle` or `evidence_chain.sign`) must populate this column from the canonical helper's output at sign-time. This is a TWO-PHASE rollout:
- Phase 2a: mig 314 + signing-path update (column populates for NEW bundles).
- Phase 2b: substrate invariant ships ONCE there's enough population in the column (e.g. 7 days worth of bundles).

---

## §6 — Multi-device-enterprise lens

At multi-tenant scale (N customers × M sites):
- Each tenant produces ~1 signed bundle per day → ~30 bundles/30d-window per tenant.
- N=50 customers × 30 bundles = 1500 bundles in the 7-day window.
- Each invariant tick reads 1500 rows + runs the canonical helper 1500 times.
- Canonical-helper cost is ~50ms each (asyncpg query + Python arithmetic).
- Total tick cost: ~75 seconds per tick.
- Substrate engine runs every 60s — this invariant would dominate the tick.

**Mitigation:** sample-based rather than full-scan. Random-sample 10% of bundles per tick; full-tenant coverage every 10 ticks (~10 min). Trades full-real-time-coverage for tick-cost-discipline.

```python
# Sample 10% of bundles per tick.
SAMPLE_RATE = 0.1
sample_size = max(10, int(SAMPLE_RATE * total_bundle_count))
```

---

## §7 — Open questions for Class-B Gate A

- (a) Mechanism C vs Mechanism B — Mechanism C requires a schema column (mig 314) + signing-path update. Is the marginal complexity worth it vs Mechanism B's separate sampling table?
- (b) Tolerance value 0.1 — is 1-decimal precision right, or should the invariant accept up to 1.0 drift (treating score as integer-grade)?
- (c) Recency window 7 days — appropriate, or should it be a rolling window matching the canonical compliance-score helper's default 30-day window?
- (d) Sample rate 10% — too low (slow detection of platform-wide drift) or too high (tick cost)?
- (e) Cross-task lockstep: should this invariant ship in the SAME commit as the Task #50 Phase 0+1 already shipped, or as a follow-up sprint? (Per Gate B P0-A: must land BEFORE drive-down begins. Drive-down hasn't begun yet, so timing-wise both are valid.)
- (f) Per-class scope: this design covers `compliance_score` only. The other 3 CANONICAL_METRICS classes (baa_on_file, runbook_id_canonical, l2_resolution_tier) need their own invariant queries or this invariant extends to cover all. Single-invariant-multi-class is more complex but auditor-friendlier.

---

## §8 — Implementation order

**Phase 2a (this commit's design covers, separate impl PR):**
1. Mig 314: `compliance_bundles.attested_compliance_score` column.
2. Update `compliance_packet.sign_bundle` (or equivalent) to populate column at sign-time.
3. Class-B Gate A on mig 314 + signing-path change.

**Phase 2b (subsequent PR, after 7d of population data):**
1. Add `_check_canonical_metric_drift` to `assertions.py` with this design's query shape.
2. Add `daemon_canonical_metric_drift_*` to `_DISPLAY_METADATA`.
3. Add `substrate_runbooks/canonical_metric_drift.md`.
4. Add fixture parity for `attested_compliance_score` in pg-tests.
5. Class-B Gate A + Gate B on the substrate invariant.

**Phase 3 (drive-down — unblocked once Phase 2 lands):**
- Migrate the 7 `migrate`-class allowlist entries one PR at a time per the Phase 3 plan in `audit/canonical-source-registry-design-2026-05-13.md`.
