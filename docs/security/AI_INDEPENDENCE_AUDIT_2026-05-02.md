# AI-Independence Audit — Central Command

**Audit ID:** AI-INDEP-2026-05-02
**Date:** 2026-05-02
**Auditor's question:** "If Anthropic disappears tomorrow OR LLM costs spike dramatically, can the Administrator/owner-operator still operate Central Command and remediate issues — are all the administrative buttons there?"
**Boundary:** Operator surface (admin/client/partner/portal UIs, fleet ops, substrate health, auditor kit, evidence chain). FLYWHEEL surface (L1/L2/L3 healing pipeline) is explicitly out of scope per user framing.
**Methodology:** 8 audit dimensions, scoping round-table (Brian SWE / Diana DBA / Camila Compliance / Steve SRE / Priya PM / Coach + CFO voice), parallel forks for high-stakes dims (1, 4, 5), synchronous synthesis for dependent dims.

---

## Headline Verdict

> **YES — Central Command operates fully without Anthropic / LLM access. The cryptographic compliance chain is LLM-FREE. The administrative dashboard has manual buttons for every operator workflow. Three cosmetic UX gaps identified for future polish; none block operation.**

**HIPAA §164.312(b) integrity posture:** confirmed sound — the cryptographic chain has zero LLM dependencies.

**Severity counts:**
- **sev0** (cryptographic chain touched by LLM): **0**
- **sev1** (operator blocked without LLM): **0**
- **sev2** (functional but degraded): **1** (L4 queue priority sort)
- **sev3** (cosmetic UX): **2** (L2-degraded banner, runbook search)

---

## Per-dimension findings

### Dim 1 — Direct LLM call-site enumeration

**Verdict:** ZERO operator-surface LLM dependencies.

| Surface | LLM call sites | Sev |
|---------|---------------|-----|
| Admin/client/partner/portal routes | 0 | n/a |
| Evidence chain (`evidence_chain.py`) | 0 | n/a |
| Fleet operations (`fleet_cli.py`, fleet endpoints) | 0 | n/a |
| Substrate health (`assertions.py`, runbooks) | 0 | n/a |
| Auditor kit | 0 | n/a |
| **Flywheel (out of scope)** | 5 (`l2_planner.py`) | sev2 with `is_l2_available()` fallback |

Network destinations seen (flywheel only):
- `api.anthropic.com/v1/messages` (call_anthropic)
- `api.openai.com/v1/chat/completions` (call_openai)
- Azure OpenAI endpoint (call_azure_openai)

Egress block on these hosts → degraded L2 healing only; operator surface unaffected.

### Dim 2 — Indirect LLM dependency mapping

**Verdict:** All consumers of L2 outputs (`l2_decisions` table, `promoted_rules.source='l2_promoted'`) are READ-ONLY-graceful. Each gracefully reports "0" / "stalled" when L2 is off; none break.

Substrate `l2_decisions_stalled` invariant correctly fires when L2 produces nothing — that's the design intent.

### Dim 3 — Schema/data LLM-dependency

**Verdict:** Schema does NOT enforce LLM-generated values.

- `l2_decisions` has only 2 NOT NULL columns: `id` (autoincrement bigint), `incident_id`. Other columns (`llm_model`, `llm_latency_ms`, `llm_response`) all NULLABLE.
- Zero CHECK constraints requiring LLM-generated values across chain-adjacent tables.
- `promoted_rules.source` CHECK allows multiple values; `l2_promoted` is one of them — operator-promoted via fleet_cli is parallel path.

### Dim 4 — Compliance chain LLM-touch verification

**Verdict:** Cryptographic compliance chain is LLM-FREE. Zero sev0 findings.

| Component | File(s) | Verdict |
|-----------|---------|---------|
| Ed25519 signing | evidence_chain.py, signing_backend.py, order_signing.py | LLM-FREE |
| Hash chain | evidence_chain.py | LLM-FREE |
| OTS anchoring | evidence_chain.py::submit_hash_to_ots | LLM-FREE |
| Merkle batching | evidence_chain.py::process_merkle_batch | LLM-FREE |
| BAA signatures | client_signup.py | LLM-FREE |
| Auditor kit | evidence_chain.py::download_auditor_kit | LLM-FREE |
| Compliance scores | mig 271 plpgsql function | LLM-FREE |
| Compliance packets | compliance_packet.py | LLM-FREE |
| Privileged-access chain | privileged_access_attestation.py | LLM-FREE |
| Audit logs | audit_package.py, audit_report.py | LLM-FREE |

CI gate `tests/test_compliance_chain_llm_free.py` pins this property as a ratchet (dim 8).

### Dim 5 — Administrative UI button audit

**Verdict:** YES — owner-operator can operate every workflow via manual UI.

| Workflow | Manual button | LLM-hidden in path | Sev |
|----------|--------------|---------------------|-----|
| Incident triage | ✅ | ❌ | OK |
| Runbook execution | ✅ | ❌ | OK |
| Compliance investigation | ✅ | ❌ | OK |
| Fleet operations | ✅ (UI + CLI parity) | ❌ | OK |
| Substrate response | ✅ (per-violation runbook + Run action) | ❌ | OK |
| Operator-action queue (L4) | ✅ (manual resolve modal) | ❌ | OK |

Model citizen: `AdminSubstrateHealth.tsx` exposes per-violation runbook drawer + "Run action" modal + every action has a documented `cliFallback` for CLI parity.

### Dim 6 — Manifest

This document.

### Dim 7 — Cost projection

**Status:** BLOCKED-WAITING-INPUT. Requires Anthropic dashboard data (current monthly LLM spend, per-incident costs). User to provide. CFO voice's intended deliverables:

1. Cost-per-incident breakdown by tier (L1 free / L2 LLM / L3 escalation)
2. Per-customer LLM $ spend at current pricing
3. "AI off" projection: operator-time labor cost replacing $X LLM cost
4. Pricing-tier alignment check (margin risk identification)

### Dim 8 — CI gate (chain LLM-free ratchet)

**Status:** SHIPPED. `tests/test_compliance_chain_llm_free.py` pins the cryptographic chain's LLM-free property. 13/13 tests pass on current code; future PR adding LLM imports to chain-component file fails CI.

---

## Followup tasks filed

| Task ID | Description | Sev |
|---------|-------------|-----|
| #61 | L2-degraded banner on Incidents page when LLM unavailable | P3 |
| #62 | Runbook catalog full-text search in Runbooks.tsx | P3 |
| #63 | L4 queue priority sort (severity ASC, created_at DESC) | P2 |
| #59 | Cost-per-tier projection (BLOCKED-WAITING-INPUT) | — |

---

## Out-of-scope findings (separate cleanup)

- **Dead code**: `mcp-server/planner.py` legacy `import openai` with gpt-4o default. Only referenced by `mcp-server/test_integration.py` — not in production main.py path. Recommend cleanup task.
- **`.env` API key handling** verified: file is gitignored, NEVER committed, mode-600 on VPS. Standard local-dev pattern. No security incident (initial fork claim was false alarm).

---

## Compliance + business posture summary

**For an enterprise-grade compliance-substrate pitch:** the LLM-INDEPENDENT components (cryptographic chain, evidence ingestion, auditor kit, fleet ops, substrate health) are the value prop. The product survives an Anthropic outage as a product — the operator just loses the L2 auto-healing convenience layer.

**For AI-cost trajectory risk:** today's dependency footprint is concentrated in `l2_planner.py` (flywheel surface) + `Companion` chat features. A 10x model-cost spike compresses margins on those features specifically; the operator surface and pricing tiers excluding those features have zero LLM cost exposure.

**Forward-design recommendation:** the CI gate `test_compliance_chain_llm_free.py` is the durable artifact. Any future feature considering an LLM dependency must explicitly choose between flywheel (allowed) or operator/chain (forbidden) and the gate enforces that boundary at PR time.

---

**Audit closed: 2026-05-02. Reviewed by: Brian (Principal SWE), Coach (Consistency). All 8 dimensions complete or BLOCKED-WAITING-INPUT (dim 7 only).**
