# QA Round-Table — Coach E2E Attestation Audit Prioritization

**Date:** 2026-05-08
**Auditor input:** `audit/coach-e2e-attestation-audit-2026-05-08.md` (commit `7a478b09`)
**Voices:** Carol (Compliance Auditor / HIPAA), Sarah (Product Manager), Steve (Principal SWE), Maya (Adversarial Reviewer)

## Method

Four voices vote APPROVE / DENY / DEFER on each of the 8 round-table queue items the coach
surfaced. Items where any voice flags **legal-compliance blocker** go to a TODAY tier
regardless of vote count.

---

## Tier 1 — TODAY (must close before next push to a paying-customer surface)

### RT-1.1 — F-P0-1 — Unstall Merkle batcher + Prometheus alert

| Voice | Vote | Rationale |
|---|---|---|
| Carol | APPROVE | §164.312(c)(1) integrity-controls violation: 18d of unanchored evidence is forensically equivalent to no evidence. The customer cannot pass an audit for that period. |
| Sarah | APPROVE | The ONLY paying-customer site is in chain-rupture state. This is the single highest-impact item. Ship today. |
| Steve | APPROVE | Manual unstall is bounded scope (run `process_merkle_batch('north-valley-branch-2')` once). Alert is a 30-line prom-exporter add. |
| Maya | APPROVE + add: also surface a substrate invariant `merkle_batch_stalled` (sev1) so the engine catches the next stall in 60s, not 18 days. |

**Consensus:** APPROVE. Sub-deliverables:
- (a) manual one-shot unstall on VPS via admin shell;
- (b) `osiriscare_bundles_unanchored_age_hours{site_id}` Prometheus gauge in `prometheus_metrics.py`;
- (c) substrate invariant `merkle_batch_stalled` (sev1) — fires when any site has bundles `ots_status='batching' AND created_at < now() - interval '6 hours'`.

### RT-1.2 — F-P0-2 — Privileged-order backfill DECISION

| Voice | Vote | Rationale |
|---|---|---|
| Carol | DEFER-WITH-DISCLOSURE | The CLAUDE.md INVIOLABLE rule says breaking the chain is a security incident. Retroactively grafting attestation rows against a `pre-INSERT` trigger that didn't exist at the time would create a **forged audit trail** — exactly what the rule exists to prevent. The defensible path is a public disclosure into the auditor-kit `advisories/` folder: "Pre-mig-175 privileged orders #X #Y #Z carry no attestation; this is intentional and disclosed." |
| Sarah | APPROVE Carol | Disclosure is honest; backfill is risky. We have one customer; the disclosure cost is low. |
| Steve | APPROVE Carol | One-shot backfill scripts always grow into "well one more case…" and become a chain-laundering vector. Disclosure scales. |
| Maya | APPROVE Carol + add: ship the disclosure as an actual Markdown file in the kit, NOT a config flag. Auditors read files, not flags. |

**Consensus:** DISCLOSURE PATH. Sub-deliverables:
- (a) generate `advisories/pre_mig175_privileged_orders.md` content from the live row data (3 orders, dates, types, target appliance) into the auditor kit;
- (b) extend `auditor_kit_zip_primitives.py` to emit an `advisories/` folder when any rows match this class;
- (c) add a substrate invariant `pre_mig175_privileged_unattested` (sev3, INFORMATIONAL) so future operators see the disclosure surface from the dashboard, not by archaeology.

### RT-1.3 — F-P0-3 (sub) — Wrap `prometheus_metrics.py` reads in per-query savepoints

| Voice | Vote | Rationale |
|---|---|---|
| Carol | APPROVE | "Dashboard lies" is the single class that destroys operator trust during incidents — exactly when the chain-gap escalation rule needs the dashboard to be truthful. |
| Sarah | APPROVE | Lowest-risk highest-leverage item in the audit. Mechanical fix. Ship today. |
| Steve | APPROVE | The pattern is already proven (sites.py checkin savepoints, Session 200). Apply to prometheus_metrics. |
| Maya | APPROVE + add: ship the AST gate `tests/test_prometheus_metrics_uses_savepoints.py` BEFORE the wraps so the ratchet pins behavior. |

**Consensus:** APPROVE. Sub-deliverables:
- (a) AST gate first (red);
- (b) wrap each ~48 reads in `async with conn.transaction():` (green);
- (c) ratchet baseline at 0 violations.

---

## Tier 2 — THIS WEEK (close in next 5 days)

### RT-2.1 — F-P0-3 — Ratchet `logger.warning` on DB writes → `logger.error(exc_info=True)`

**Vote:** APPROVE 4/4. Mechanical AST sweep. Cost: 1-2hr. Outcome: closes the silent-failure
class CLAUDE.md was written to prevent. Carol pinned a CI-gate requirement; Maya pinned a
ratchet-baseline pattern matching the existing `admin_connection` ratchet shape.

**Sub-deliverables:** `tests/test_no_logger_warning_on_db_writes.py` (ratchet baseline), then
mechanical migration of `evidence_chain.py` (highest-density violator).

### RT-2.2 — F-P1-2 — Add `cross_org_site_relocate_requests` to immutable list

**Vote:** APPROVE 4/4. One-line SQL function migration. Substrate engine has been alerting
for 66h; closing the alert IS the test. Steve noted: bundle this with a substrate-MTTR-SLA
process item (separate ticket) so we don't reproduce the "alert exists, nobody acts" pattern.

---

## Tier 3 — DEFERRED (next sprint)

| Item | Reason for deferral |
|---|---|
| RT-3.1 — F-P1-1 (site-level fallback drop) | Only 1 of 5 appliances is affected today; scaling exposure but not blocking. Bundle with sites-table cleanup work. |
| RT-3.2 — F-P2-3 (race-harden `_get_prev_bundle`) | Latent race; rare today. Combine with chain-position UNIQUE INDEX migration. Cost = sprint, not session. |
| RT-3.3 — Process — substrate MTTR SLA | Process work. Spawn separate task with Operations PM. |

---

## Round-table verdict

**APPROVED for execution this session, in order:**

1. RT-1.1 (Merkle unstall + alert + invariant) — TODAY
2. RT-1.2 (auditor-kit advisories disclosure) — TODAY
3. RT-1.3 (prometheus savepoints) — TODAY
4. RT-2.1 (logger.warning ratchet) — TODAY if budget allows
5. RT-2.2 (immutable-list one-line migration) — TODAY (cheap, closes 66h alert)

Items 1–3 are non-negotiable for closing the **CONDITIONAL** verdict from the coach. Items
4–5 ride along if time allows; they are scheduled this week regardless.

---

## Close-out criteria

After implementation, re-invoke the coach with prompt: *"Verify F-P0-1, F-P0-2, F-P0-3 (sub),
F-P1-2 are closed at runtime. Confirm CONDITIONAL → READY transition criteria met for items
in scope. Rerun the relevant runtime checks (Merkle batching count, advisories file in
sample kit, prometheus error-count delta, substrate violation count). Issue final verdict."*

— round-table consensus, 2026-05-08
