# Gate A — compliance_score slice (6 entries)

**Date:** 2026-05-15
**Verdict:** APPROVE-WITH-FIXES (P0s: 2)
**Reviewers:** Steve / Maya / Carol / Coach / Auditor / PM / Counsel

## Per-callsite classification

| # | Signature | Action | Rationale | Effort | Soak Y/N |
|---|---|---|---|---|---|
| 1 | metrics.calculate_compliance_score | RECLASSIFY-AS-operator_only | Stateless 7-boolean averager. Real callers: metrics.py:257+374 (internal wrappers) + fleet.py:171 (`get_fleet_overview` admin path). Different semantics from bundle aggregator — not a Rule 1 surface. Add per-line `# canonical-skip: operator-only` markers + reclassify in registry. | 15min | N |
| 2 | compliance_packet.CompliancePacket._calculate_compliance_score | MIGRATE (with methodology bump) | Customer-facing PDF disclosure. Already reads compliance_bundles directly with JSONB-expand + per-control averaging — a NEAR-canonical shape, but NOT the canonical helper. Replace body with `compute_compliance_score(conn, [site_id], window_days=...)`; bump `_PACKET_METHODOLOGY_VERSION` 2.0→2.1; update disclosure copy in same commit. SQLAlchemy→asyncpg conn handoff required (CompliancePacket uses `self.db: AsyncSession`). | 90min | Y (1 disclosure diff diff vs prior packet) |
| 3 | db_queries.get_compliance_scores_for_site | MIGRATE | Customer-facing — feeds portal.py:1923 (`/api/portal` legacy `?token=` surface) AND routes.py:282+417 admin fleet endpoints. Returns category breakdown + per-category scores; canonical helper returns ComplianceScore dataclass (different shape). Need adapter that calls canonical for `score`, keeps category breakdown from category-level deltas, OR extend canonical to expose categories. | 4–6h | Y (portal score parity vs prior) |
| 4 | db_queries.get_all_compliance_scores | MIGRATE (batched) | 4 admin callsites (routes.py:178 list_clients, 4702 list_orgs, 4869 org_detail, 5033 org_health). Per-site iteration via `compute_compliance_score(conn, [sid])` is O(N) RTTs; canonical helper accepts list-of-sites so a single call with `site_ids=[...]` is the right shape. Cache hit-path (`admin:compliance:all_scores`) must be preserved. | 4–6h | Y (org dashboard parity) |
| 5 | frameworks.get_compliance_scores | RECLASSIFY-AS-operator_only | Reads denormalized `compliance_scores` table (separate pipeline / per-framework rollup), NOT compliance_bundles. Different metric class (per-framework rather than overall). Belongs in PLANNED_METRICS (`evidence_chain_count`-shape entry: per-framework rollup) or its own metric class — NOT compliance_score. Reclassify + add per-line `# canonical-skip: per-framework-rollup-different-class`. | 30min | N |
| 6 | frameworks.get_appliance_compliance_scores | RECLASSIFY-AS-operator_only | FastAPI endpoint wrapper around #5; same justification. Reclassify together with #5. | 5min | N |

## Lens findings (1-2 sentences each)

- **Steve:** Half this slice is mis-classified — #1, #5, #6 aren't bundle aggregators and shouldn't be in `compliance_score`'s allowlist at all. Reclassify before migrating, or you'll waste cycles migrating non-targets and break two distinct semantics.
- **Maya:** §164.528 + §164.524 risk lives in #2 (PDF disclosure) and #3 (portal customer surface). #2 score-formula change MUST bump `_PACKET_METHODOLOGY_VERSION` and update disclosure copy in the SAME commit — otherwise auditors see methodology v2.0 PDFs produced by v2.1 code. P0.
- **Carol:** #3 portal surface flows through legacy `?token=` auth path — telemetry-warned but still in service. If migration regresses portal output shape (`{has_data, patching, antivirus, …, score}` dict), portal frontend will silently render zeros. Adapter-not-replacement pattern required. P0.
- **Coach:** Six entries are not six migrations. Split into commits: (1) reclassify commit for #1+#5+#6 with markers + registry edit + ratchet bump; (2) migrate #2 with methodology bump; (3) migrate #3 with portal adapter; (4) migrate #4 with batched single-call. Four small commits, four small Gate Bs.
- **Auditor:** Methodology version pin on compliance_packet is load-bearing — disclosure-version drift across consecutive packet downloads breaks `kit_version`-style determinism contract. Same family as auditor-kit `kit_version 2.1`. Cite the disclosure-version bump in the commit body.
- **PM:** Sequencing matters for customer impact. Land reclassify commit first (no behavior change, ratchet drops 3), then #2 (disclosure cohort opt-in via methodology version), then #3 (portal — soak 24h), then #4 (admin dashboards — internal soak ok). Total ~10–12h engineering.
- **Counsel:** Rule 1 says "every customer-facing metric declares a canonical source." #5+#6 expose `score_percentage` per framework on a customer-facing endpoint — even if reclassified as operator_only here, the per-framework score class needs its OWN canonical helper entry (PLANNED_METRICS extension). Don't just hide it — register it.

## Execution order

1. **Commit 1 — reclassify (15min eng + Gate B):** edit `canonical_metrics.py` `allowlist` to mark #1+#5+#6 as `operator_only`; add PLANNED_METRICS entry `per_framework_compliance_score` (blocks #5+#6 from customer surfaces until canonical lands); per-line `# canonical-skip:` markers at the 3 callsites; ratchet baseline drops by 3.
2. **Commit 2 — migrate #2 (90min eng + Gate B):** replace `_calculate_compliance_score` body with `await compute_compliance_score(asyncpg_conn, [self.site_id], window_days=window)`; SQLAlchemy→asyncpg conn handoff via existing pool; bump `_PACKET_METHODOLOGY_VERSION = "2.1"`; update disclosure copy + boilerplate; per-line marker.
3. **Commit 3 — migrate #3 (4–6h eng + Gate B + 24h portal soak):** introduce `get_compliance_scores_for_site_v2` that delegates `score` to canonical helper, keeps category breakdown computation locally; swap callers (portal.py:1923, routes.py:282+417, db_queries.py:1506); per-line markers; verify portal frontend renders identical shape.
4. **Commit 4 — migrate #4 (4–6h eng + Gate B):** single-call `compute_compliance_score(conn, list(site_ids))` returning dict; preserve `admin:compliance:all_scores` cache; swap 4 admin callsites; per-line markers.

## Pre-execution blockers (P0s)

- **P0-1 (Maya):** Commit 2 MUST bump `_PACKET_METHODOLOGY_VERSION` 2.0→2.1 AND update disclosure copy in the SAME commit. Verify no parallel PDF generation path retains v2.0 formula post-commit.
- **P0-2 (Carol):** Commit 3 MUST preserve `get_compliance_scores_for_site` return-shape exactly (dict with `patching/antivirus/.../score/has_data` keys) — portal frontend + routes.py:285-292 destructure these keys. Adapter pattern, not replacement; portal-side smoke test that confirms shape parity before merge.

## Gate B preview

- Verify ratchet baseline drops as predicted per commit (3 for commit 1; 1 each for 2/3/4).
- `tests/test_no_ad_hoc_score_formula_in_endpoints` must still pass post-commit-3.
- Run full pre-push sweep on every commit (Session 220 Gate B lock-in).
- For commit 2: diff one prior packet PDF vs one new packet PDF; confirm score delta ≤ 2pp OR methodology change explains it.
- For commit 3: hit `/api/portal/{site}?token=…` pre/post and diff JSON shape; zero key delta.
- For commit 4: hit `/api/clients`, `/api/organizations`, `/api/organizations/{id}`, `/api/organizations/{id}/health` pre/post and diff `compliance_score` field per site; ≤1pp drift allowed (cache-hit timing).
- Reject any commit body claiming "shipped" without `/api/version` SHA assertion (Session 215 rule).
