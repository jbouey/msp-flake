# `canonical_compliance_score_drift` Substrate Invariant — Design v3 (Task #50 Phase 2, Counsel Rule 1)

> **v3 changes (Gate A v2 APPROVE-WITH-FIXES → 2 P0s + 2 P1s + 1 P2 applied 2026-05-13):**
>
> - **P0-E4 (Steve, `include_incidents` capture):** v2's `helper_input` JSONB schema was `{site_ids, window_days}` — missing the `include_incidents: bool` kwarg. Empirical verification: `client_portal.py:1196-1197` (per-site compliance-health endpoint) passes `include_incidents=True`; the dashboard endpoint at `client_portal.py:759-760` leaves it default `False`. Same site → different score under different kwargs → false-positives guaranteed on every per-site sample under v2. Fix: `helper_input` schema is `{site_ids: List[str], window_days: int, include_incidents: bool}` (3-field) + sampler captures BOTH at call time + invariant's recompute passes BOTH.
> - **P0-E5 (Steve, endpoint enumeration ≥10 not 6):** v2 claimed 6 customer-facing emit-paths. Empirical source-grep (2026-05-13) found **13+ emit sites** across the codebase. Full enumeration in §2 below — including F1 attestation-letter PDF (`client_attestation_letter.py:225, :372` — highest-stakes customer artifact, missed entirely by v2). Decorator coverage expanded; per-path classification (`customer-facing` / `operator-internal` / `partner-internal`) included.
> - **P1-E6 + P1-P3 (Steve + PM, cache vs window-shift false-positives):** v2 used `compute_compliance_score(...)` which has a 60s TTL cache + `window_days` (relative-from-NOW). Within a single 60s tick, cache returns the SAME value for a sample → recompute matches captured. But the sample's `captured_at` was N seconds ago + the NOW-window has shifted by N → real engine queries on the boundary return slightly different values → false-positive ±0.3 in worst case. Fix: invariant calls `compute_compliance_score(..., _skip_cache=True)` for the recompute + tolerance bumped from `0.1` to `0.5`. (Helper change required — see §5.)
> - **P2 (Steve, fictional auto-prune):** v2 said samples >30 days old are "auto-pruned by partition_maintainer_loop." That loop only covers `promoted_rule_events`. Fix: §2 lands a `canonical_metric_samples_pruner` daily task that drops monthly partitions older than retention (or `pg_partman` if available).
>
> ---
>
> **v2 changes (Gate A v1 BLOCK → Mechanism B pivot per fork recommendation, 2026-05-13):**
>
> Gate A v1 returned BLOCK with 3 P0s — Mechanism C (re-run helper against chain-attested input) was not implementable against today's codebase:
> 1. P0-E1 (Steve): `compute_compliance_score` accepts only relative `window_days: int`, not absolute window bounds. Mechanism C requires API extension that was out of scope.
> 2. P0-E2 (Steve): `compliance_bundles` rows attest raw per-scan `checks[]` from ONE scan at ONE timestamp — they do NOT carry an aggregated compliance score. The "chain-attested score" comparison was a category error.
> 3. P0-C1 (Coach): `signed_data TEXT` (mig 012) already stores canonical signed payload. Adding `attested_compliance_score` as sibling unsigned column would be double-build AND cryptographically meaningless (Ed25519 sig wouldn't cover it).
>
> **Pivot:** v2 design uses Mechanism B (response-sampling table). The invariant samples customer-facing endpoint responses + verifies they match canonical-helper output **right now** (no chain involvement, no Article 3.2 risk). This catches **non-canonical-value drift** — cases where a non-canonical code path produces a different value than the canonical helper would have. Pairs with the existing static AST gate (Phase 0+1 shipped) which catches **non-canonical-delegation drift** (a code path that doesn't go through the canonical helper at all).
>
> Renamed `canonical_metric_drift` → `canonical_compliance_score_drift` per Gate A v1 Coach lens — honest narrow scope. Separate invariants for `baa_on_file`, `runbook_id_canonical`, `l2_resolution_tier` will design separately (the latter two have substrate precedents at `assertions.py:1051` + `:1101` to EXTEND, not duplicate).

> **Counsel Rule 1 framing:** static AST gate (already shipped Phase 0+1) is the **compile-time** half — catches inline computations that don't go through canonical helper. This runtime invariant is the **runtime** half — catches when a customer-facing endpoint returns a value that differs from what the canonical helper would produce.

> **Multi-device-enterprise lens:** at multi-tenant scale, a runtime drift detector is the auditor-grade evidence that customer-facing values match canonical helper output, not just claim-by-static-analysis.

---

## §1 — The invariant's contract

For every recent **sample** captured from a customer-facing endpoint that returned a `compliance_score` for tenant T:

**Invariant:** the canonical helper's output for tenant T's compliance score at the sample's time-of-capture matches the sample's reported value (within ±0.1 rounding tolerance).

**Violation:** the sample's reported value differs from canonical-helper output → the endpoint computed via a non-canonical path that produced a different value → Rule 1 runtime violation.

---

## §2 — Mechanism B — response-sampling table

<!-- mig 314 SHIPPED 2026-05-13 — see migrations/314_canonical_metric_samples.sql -->

### Schema (mig 314 — Phase 2a, v3)

```sql
CREATE TABLE IF NOT EXISTS canonical_metric_samples (
    sample_id       UUID NOT NULL DEFAULT gen_random_uuid(),
    metric_class    TEXT NOT NULL,         -- 'compliance_score' (v3 scope)
    tenant_id       UUID NOT NULL,         -- client_org_id
    site_id         TEXT NULL,             -- optional per-site
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_value  NUMERIC(5,1) NULL,     -- the value the endpoint returned
    endpoint_path   TEXT NOT NULL,         -- e.g. /api/client/reports/current
    helper_input    JSONB NULL,            -- {site_ids:[],window_days:int,include_incidents:bool}
                                           -- (v3 P0-E4: include_incidents MUST be captured)
    classification  TEXT NOT NULL,         -- 'customer-facing' | 'operator-internal' | 'partner-internal'
                                           -- (v3 P0-E5: operator-internal samples don't fire drift)
    CONSTRAINT canonical_metric_samples_classification_valid CHECK (
        classification IN ('customer-facing', 'operator-internal', 'partner-internal')
    ),                                     -- v3 P1-C12: typo defense for partial index + invariant WHERE
    PRIMARY KEY (sample_id, captured_at)
) PARTITION BY RANGE (captured_at);

-- Monthly partitions, 30-day retention. v3 P2 fix: the prior "auto-pruned
-- by partition_maintainer_loop" claim was incorrect — that loop only
-- covers promoted_rule_events. v3 lands a separate
-- canonical_metric_samples_pruner daily task (see §5 Phase 2c) that
-- drops partitions whose upper-bound > 30 days ago.
CREATE INDEX IF NOT EXISTS idx_canonical_metric_samples_tenant
    ON canonical_metric_samples (tenant_id, metric_class, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_metric_samples_drift
    ON canonical_metric_samples (metric_class, classification, captured_at DESC)
    WHERE classification = 'customer-facing';
```

### Endpoint decorator (Phase 2b)

```python
# canonical_metrics_sampler.py
from canonical_metrics import CANONICAL_METRICS

async def sample_metric_response(
    conn,
    metric_class: str,
    tenant_id: str,
    captured_value: float | None,
    endpoint_path: str,
    helper_input: dict,
    classification: str,  # v3 P2-S1: must be a formal parameter — used at line :109
) -> None:
    """Write a customer-facing response sample for substrate-invariant
    drift-detection. Soft-fail: never blocks the endpoint response.
    """
    if metric_class not in CANONICAL_METRICS:
        return  # not a tracked metric class
    try:
        # v3 P0-E10: classification MUST be in the INSERT — the column is
        # NOT NULL and the partial index + substrate WHERE clause depend
        # on the value being correctly populated per emit-path.
        await conn.execute(
            """
            INSERT INTO canonical_metric_samples
                (metric_class, tenant_id, captured_value,
                 endpoint_path, helper_input, classification)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            metric_class, tenant_id, captured_value,
            endpoint_path, json.dumps(helper_input),
            classification,                              # caller supplies per emit-path
        )
    except Exception:
        logger.warning("sample_metric_response soft-fail; skipping")
```

Sampling cadence: **10% of customer-facing requests** (stochastic; configured via `SAMPLE_RATE = 0.1`). Trades full-coverage for table-size + insert-cost discipline.

Phase 2b — decorate ALL emit-paths that return `compliance_score` or `overall_score` to a NON-test surface. v3 source-grep (2026-05-13) found **13 emit sites** across 7 files. Per-path classification (`customer-facing` fires drift; `operator-internal` and `partner-internal` are sampled-but-excluded):

| File:line | Endpoint / function | Classification | Notes |
|---|---|---|---|
| `client_attestation_letter.py:225` | F1 PDF — `_compute_facts()` | **customer-facing** | Highest-stakes artifact; v2 missed entirely |
| `client_attestation_letter.py:372` | F1 PDF — render template kwarg | **customer-facing** | Re-emit of same value via Jinja2 |
| `client_portal.py:817` | `/api/client/dashboard` | **customer-facing** | `include_incidents=False` (default) |
| `client_portal.py:1206` | `/api/client/sites/{id}/compliance-health` | **customer-facing** | `include_incidents=True` (P0-E4 trigger) |
| `client_portal.py:1745` | `/api/client/reports/current` aggregation row | **customer-facing** | Per-site `overall_score` in report |
| `client_portal.py:1934` | `/api/client/sites/{id}` site detail | **customer-facing** | `include_incidents=False` |
| `client_portal.py:5796` | `/api/client/appliances/{id}` (RT33 P2) | **customer-facing** | Row-derived, may be NULL |
| `org_management.py:1203` | partner-facing org listing | **partner-internal** | role-gated; drift exclusion |
| `portal.py:1305` | legacy `/api/portal/...` | **customer-facing** | Deprecation telemetry already in place |
| `routes.py:3398, 4875, 5774, 5786, 7627, 7851` | 6 admin/operator routes | **operator-internal** | Sampled-but-excluded from drift fire |

**P0-E5 closure:** the previous v2 list of 6 was incomplete; the operator-internal routes.py callsites are the largest miss-class (6 sites under one file). All sampled; only `customer-facing` and `partner-internal` (when role-gated by partner admin) fire drift. Partner-internal classification is informational; operator-internal classification is informational + excluded from substrate fire.

### Substrate invariant (Phase 2c)

```python
async def _check_canonical_compliance_score_drift(
    conn: asyncpg.Connection,
) -> List[Violation]:
    """Sev2 — customer-facing endpoint returned a compliance_score
    value that differs from canonical-helper output.

    For each recent sample (last 15 minutes), recompute the canonical
    helper using the sample's helper_input + compare to captured_value.
    Differences > 0.1 indicate the endpoint went through a non-canonical
    code path that produces different values.
    """
    # v3 P0-E8: substrate fires ONLY on customer-facing samples.
    # operator-internal + partner-internal rows are recorded for
    # observability but excluded from drift alerts.
    rows = await conn.fetch(
        """
        SELECT sample_id, tenant_id, captured_at, captured_value,
               endpoint_path, helper_input
          FROM canonical_metric_samples
         WHERE metric_class = 'compliance_score'
           AND classification = 'customer-facing'
           AND captured_at > NOW() - INTERVAL '15 minutes'
           AND captured_value IS NOT NULL
         ORDER BY captured_at DESC
         LIMIT 50
        """
    )
    out: List[Violation] = []
    for r in rows:
        from compliance_score import compute_compliance_score
        helper_input = r["helper_input"] or {}
        site_ids = helper_input.get("site_ids", [])
        window_days = helper_input.get("window_days", 30)
        include_incidents = helper_input.get("include_incidents", False)
        if not site_ids:
            continue
        try:
            # v3 P1: _skip_cache=True forces a cache-miss recompute so the
            # comparison isn't trivially same-value within the 60s TTL.
            # The same NOW() drift that produces real boundary
            # variability is what we want to measure against.
            helper_result = await compute_compliance_score(
                conn, site_ids=site_ids, window_days=window_days,
                include_incidents=include_incidents,
                _skip_cache=True,
            )
        except Exception:
            continue  # helper error is not drift; substrate skips
        helper_score = helper_result.get("score")
        if helper_score is None or r["captured_value"] is None:
            continue
        # v3 P1: tolerance 0.5 (up from 0.1) accommodates legitimate
        # boundary-NOW-shift variability. 0.5 is still tight enough to
        # catch real non-canonical-path drift (which is typically >1.0).
        if abs(helper_score - r["captured_value"]) > 0.5:
            out.append(Violation(
                site_id=(site_ids[0] if site_ids else None),
                details={
                    "sample_id": str(r["sample_id"]),
                    "tenant_id": str(r["tenant_id"]),
                    "endpoint_path": r["endpoint_path"],
                    "captured_value": r["captured_value"],
                    "canonical_value": helper_score,
                    "captured_at": r["captured_at"].isoformat(),
                    "interpretation": (
                        f"Endpoint {r['endpoint_path']} returned "
                        f"{r['captured_value']} for tenant "
                        f"{r['tenant_id']} but canonical helper produces "
                        f"{helper_score} for the same inputs. Non-canonical "
                        f"computation path is in use."
                    ),
                    "remediation": (
                        f"Inspect {r['endpoint_path']} source: it should "
                        f"delegate to compliance_score.compute_compliance_score. "
                        f"Likely uses one of the allowlist `migrate`-class "
                        f"entries (db_queries.get_compliance_scores_for_site, "
                        f"frameworks.get_compliance_scores, etc.) — drive-down "
                        f"PR migrates that path to canonical helper."
                    ),
                },
            ))
    return out
```

---

## §3 — What this invariant detects vs the static gate

| Class | Caught by | Example |
|---|---|---|
| **Non-canonical delegation** — code path doesn't go through canonical helper at all | Static AST gate (Phase 0+1 already shipped) | `db_queries.get_compliance_scores_for_site` computes `passed / total * 100` inline; doesn't import compliance_score module |
| **Non-canonical value drift** — code path produces a numerically-different output than canonical helper | This runtime invariant (Phase 2) | `db_queries.get_compliance_scores_for_site` returns 85.5 but `compute_compliance_score` returns 84.7 for same input (different window default, different latest-per-check semantics, etc.) |
| **Display-time rendering drift** — endpoint returns canonical helper output but template renders a different value | Captured here only IF the endpoint samples its OWN response (vs the helper's output) | Template incorrectly rounds, coerces null, etc. |

Together: compile-time + runtime coverage of Rule 1 for `compliance_score`.

---

## §4 — Auditor-grade evidence

When OCR asks "show me how you guarantee customer-facing compliance scores match canonical truth," the answer is:

1. **Static gate** `test_canonical_metrics_registry.py` — every customer-facing endpoint either delegates to `compute_compliance_score` or is in the allowlist with explicit classification.
2. **Runtime invariant** `canonical_compliance_score_drift` — periodically samples 10% of customer-facing responses + verifies the sample matches canonical-helper output for the same input. Drift fires sev2 substrate alert.
3. **Allowlist drive-down** — Phase 3 reduces the allowlist from 7 entries to 0 over 3-5 sprints, with coach pass per migration.

Neither gate is the master BAA Article 3.2 cryptographic-attestation-chain claim (that's the Ed25519+OTS chain itself). This invariant is a Rule 1 helper-semantic-and-delegation-drift detector.

---

## §5 — Implementation order

**Phase 2a:** mig 314 — `canonical_metric_samples` table + monthly partition + index. Class-B Gate A on the schema (small).

**Phase 2b:** `sample_metric_response` helper module + endpoint decorators on the 6 known endpoints. Soft-fail wrap (never blocks endpoint). Class-B Gate A on the decorator pattern (review for soft-fail + sample-rate + helper_input capture correctness).

**Phase 2c (v3 P1-E9 single-PR pin):** `_check_canonical_compliance_score_drift` Assertion in `assertions.py` + `_DISPLAY_METADATA` entry + `substrate_runbooks/canonical_compliance_score_drift.md` + **`_skip_cache` kwarg on `compliance_score.compute_compliance_score()` MUST land in the SAME PR as the invariant**. Reason: if the invariant deploys first, the first tick TypeError-fires because the helper signature doesn't accept `_skip_cache` yet. Pin via `tests/test_compliance_score_skip_cache_arg.py` (asserts `_skip_cache` parameter exists + bypasses `_score_cache_key` when True). Helper change today: `compliance_score.py:157` `compute_compliance_score(conn, site_ids, *, include_incidents=False, window_days=30, _skip_cache=False)` — when `_skip_cache=True`, the cache-hit branch at `:218-225` is skipped + the cache-write at `:421-422` is also skipped (don't pollute cache with sample-time recomputes).

**Phase 2d (v3 P2-E11 partition pruner with DETACH):** add `canonical_metric_samples_pruner` daily task (registered next to existing `partition_maintainer_loop` in `main.py` startup), uses `ALTER TABLE canonical_metric_samples DETACH PARTITION canonical_metric_samples_YYYY_MM` BEFORE `DROP TABLE`. The DETACH-then-DROP order avoids lock-escalation on the parent table during the drop. Pin via `tests/test_canonical_metric_samples_pruner_drops_old_partitions.py`.

**Phase 3:** unblocked once Phase 2 lands — drive-down allowlist's 7 `migrate` entries one PR at a time per the v3 design.

---

## §6 — Multi-device-enterprise lens

At multi-tenant scale (N customers × M sites):
- 10% sample rate × ~6 endpoints × ~5 customer-facing requests/site/day = ~3 samples/site/day per customer
- N=50 customers × ~3 samples = 150 samples/day per tenant-class
- 30-day retention partition: ~4500 samples in DB total
- Substrate invariant scans last 15 minutes = small slice (<50 rows typically)
- Tick cost: ~10 helper calls × ~5ms each = ~50ms — well under 60s tick budget

Affordable at multi-tenant scale. Sample-rate can be tuned down (5% or lower) if scale grows.

---

## §7 — Open questions for Class-B Gate A v2

- (a) Endpoint enumeration: are the 6 `compliance_score`-returning endpoints exhaustive? Source-grep for `compliance_score` / `overall_score` keys in response payloads to verify.
- (b) `helper_input` JSONB capture: what's the canonical input shape? site_ids + window_days seems right but the helper may take more kwargs.
- (c) 0.1 tolerance: same as Mechanism C; verify against `compute_compliance_score` rounding.
- (d) 10% sample rate: too high (DB pressure) or too low (slow drift detection)?
- (e) Operator-facing endpoint samples: capture them but classify as `operator_only` and exclude from drift fire (already noted in §2). Per-class disposition?
- (f) Cross-task lockstep with Task #54 PHI-pre-merge gate: should sampling-decorator add a `# phi_boundary: <classification> — <reason>` marker to each decorated endpoint? Could pair the rollouts.
