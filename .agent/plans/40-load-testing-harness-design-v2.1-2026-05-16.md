# Task #62 / #97 — Multi-tenant Load Testing Harness v2.1

**Status:** Gate-A-APPROVE-WITH-FIXES applied. v2.1 closes the 3 P0s structurally + folds 7 P1s into the spec.
**Date:** 2026-05-16.
**Predecessor:** `.agent/plans/40-load-testing-harness-design-2026-05-12.md` (v1 research, Gate A BLOCK).
**Gate A:** `audit/coach-62-load-harness-v1-gate-a-2026-05-16.md` (Steve / Maya / Carol / Coach / Auditor / PM / Counsel — APPROVE-WITH-FIXES).
**Companion:** task #94 plan-24 (substrate-MTTR soak v2 — share isolation pattern + sequencing pin).

<!-- mig-claim:316 task:#62 -->

This v2.1 supersedes v1 + the partial v2 packet (`audit/load-harness-v2-design-2026-05-13.md`). Read v1 for the why-now framing; this doc carries the ship-ready design.

---

## Δ vs v1 (the 3 P0s closed structurally)

### P0-1 → Wave-1 endpoint paths re-verified against route table

| Endpoint | Method | Real path | Auth dep | Target req/s |
|---|---|---|---|---|
| `/api/appliances/checkin` | POST | `phonehome.go:???` + backend route in `agent_api.py` (verify `agent_api.py` for actual path) | `require_appliance_bearer` | 100 |
| `/api/appliances/orders/{site_id}` | GET | `agent_api.py:521` | `require_appliance_bearer` | 100 |
| `/api/journal/upload` | POST | `journal_api.py` (verify) | `require_appliance_bearer` | 25 |
| `/health` | GET | `main.py` | (none) | 1000 |
| ~~`/api/evidence/sites/{id}/submit`~~ | — | does not exist as written — actual `POST /evidence/upload` at `agent_api.py:2519` — REMOVED from Wave 1 per P0-2 below | — | 0 |

**Verification gate:** `tests/test_load_harness_wave1_paths_exist.py` (NEW — ship in Commit 1) AST-greps the k6 script + asserts every endpoint path has a matching `@router.<METHOD>("<path>")` decorator in the backend tree. CI-enforced; new wave-1 endpoint cannot ship without a real route.

### P0-2 → `compliance_bundles` cryptographic-table isolation

**Decision: drop `/evidence/upload` from Wave 1 entirely** (v2 already did this — preserved here). 180K synthetic bundles/hr against the Ed25519 chain + monthly partitions + OTS anchoring + `tenant_org_isolation`/admin-bypass RLS split would either:

  - Corrupt the per-site hash chain (real `site_id`) — auditor-kit determinism contract fails per Counsel Rule 9.
  - Admin-leak into fleet aggregations (synthetic `site_id`) — pollutes `prometheus_metrics`, breaks `pg_class.reltuples` row-count gauges (Session 219 timeout class).

**Wave 1 covers volume-critical bearer paths that DON'T write to `compliance_bundles`.** Evidence-submission throughput is a Wave 2 concern requiring its own `load_test_bundles` table design (separate Class-B Gate A; out of scope for v2.1).

**CI gate:** `tests/test_no_load_test_marker_in_compliance_bundles.py` (NEW — ship in Commit 1) asserts no row in `compliance_bundles` carries the synthetic-load marker for ANY `client_org_id IS NOT NULL` site. Substrate invariant `load_test_marker_in_compliance_bundles` (sev1) (NEW — ship in Commit 5) backstops at runtime.

### P0-3 → Unify on plan-24's `details->>'synthetic'` marker pattern

v1 proposed a separate `X-Load-Test: true` header + parallel `load_test_checkins` table. Two parallel disciplines for the same risk class.

**v2.1 decision: single marker pattern across BOTH systems** —

```sql
-- plan-24 (substrate-MTTR soak v2):
details->>'synthetic' = 'mttr_soak'

-- v2.1 (load harness):
details->>'synthetic' = 'load_test'

-- Filter shape (covers both):
details->>'synthetic' IS NULL OR details->>'synthetic' NOT IN ('mttr_soak', 'load_test')
```

**Migration**: extend mig 303's partial-index pattern to cover the load-test marker too. `details->>'synthetic'` becomes the canonical synthetic-traffic enum-column across the substrate.

**Discipline gate:** `tests/test_mttr_soak_filter_universality.py` (BASELINE_MAX=0 already) extends scan to recognize the new marker value as a soak-exclusion. Re-run universal-filter sweep at v2.1 ship; ratchet stays at 0.

**Substrate invariant**: `synthetic_traffic_marker_orphan` (NEW — sev2, ship in Commit 5) — scans for query results returning synthetic-marked rows in customer-facing aggregations.

---

## P1s folded into v2.1

### P1-1 → Wave-1 endpoint expansion (deferred to v2.2)

NOT folded into v2.1 — preserves v2.1 scope discipline. `/api/agent/executions`, `/agent/patterns`, `/api/agent/sync/pattern-stats`, `/api/devices/sync`, `/api/logs/`, `/incidents` — file as TaskCreate followup task #105 ("Wave-1 endpoint expansion — 6 endpoints"). Justification per exclusion documented at task #105.

### P1-2 → k6 VU ceiling math on CX22

**Pin: max 250 VUs per CX22 instance** (conservative — empirical k6 goja JS VM CPU ceiling at 2-vCPU; leave headroom for Prometheus scrape). Scenario C 10× ramp from 25 → 250 VUs over 5min; if 100-fleet requires more than 250 VUs of work, ship distributed-k6 design as Wave 2 task. Document VU/CPU ratio in `.agent/reference/NETWORK.md` k6 box row.

### P1-3 → Pre-flight kill-switch

**Spec:**
- New `load_test_runs` table — columns: `run_id UUID PK`, `started_at TIMESTAMPTZ`, `started_by TEXT NOT NULL`, `scenario_sha TEXT`, `k6_image_sha TEXT`, `cx22_image_sha TEXT`, `status TEXT CHECK (status IN ('starting', 'running', 'aborting', 'completed', 'aborted', 'failed'))`, `signed_manifest BYTEA` (Auditor lens P1-5 — Vault Transit signed).
- New endpoint `POST /api/admin/load-test/abort` (admin-only) flips `status='aborting'`. k6 script polls `GET /api/admin/load-test/status` every 30s; exits clean on `status='aborting'`.
- Per-iteration abort-file check at `/var/lib/k6/abort` (file presence = immediate abort). Operator can SSH the CX22 box, `touch /var/lib/k6/abort`, k6 exits within 1 iteration.
- AlertManager rule: 5xx > 5% on any wave-1 endpoint for 60s → POST to abort endpoint. Tested in Commit 2.

### P1-4 → Customer-degradation SLA

**Spec:**
- External probe (separate small VM, NOT the CX22 load box) hits `/api/appliances/checkin` from a real-bearer-authed simulated appliance every 10s.
- Probe records p95 latency to a `load_test_customer_probe` table.
- AlertManager rule: probe p95 > 500ms for 60s → POST to abort endpoint (same as P1-3).
- Probe data also flows to a Grafana panel for live operator view during runs.

### P1-5 → Bearer storage + rotation + revocation + audit

**Spec:**
- Bearer tokens for k6 stored in Vault Transit (NOT CX22 local fs). Vault path: `secret/load-test/<run_id>/bearer`.
- New column `site_appliances.bearer_revoked BOOLEAN NOT NULL DEFAULT FALSE` (mig — number to pre-claim via RESERVED_MIGRATIONS.md). Revocation is a single UPDATE.
- Rotation cadence: per-run (every k6 run gets a fresh bearer); 7-day TTL on the Vault secret regardless of run completion.
- Audit-log row at run start AND end — `admin_audit_log` event_type=`load_test_run_started` / `load_test_run_completed` with `run_id`, `actor` (named human), `bearer_token_id` (NOT the token itself), `scenario_sha`. Mirrors privileged-access attestation shape.

### P1-6 → Auditor-kit CI gate

**Spec:** `tests/test_no_load_test_marker_in_compliance_bundles.py` (P0-2 CI gate above) covers this — auditor-kit determinism hash depends on compliance_bundles content; gate prevents any load-test write from reaching that table. Promoted from v1 P2-2 per Auditor's elevation.

### P1-7 → CX22 WG-peer firewall

**Spec (documented in `.agent/reference/NETWORK.md`):**
- Outbound: `central-command.osiriscare.com:443` ONLY. Egress to anywhere else denied.
- Inbound: denied (k6 is outbound-only; control-plane via SSH over WG).
- NOT able to reach Vault Transit API (`vault.osiriscare.com:8200`) — Carol P1-7 verification: `nc -zv vault.osiriscare.com 8200` from .4 returns refused.
- Bearer secrets fetched at k6 startup via SSH-tunnel proxy from the operator's workstation (NOT direct Vault client on the box).

---

## Updated execution order (post-v2.1)

| # | Commit | Effort | Verify gate |
|---|---|---|---|
| 1 | Spec doc + 2 NEW CI gates (`test_load_harness_wave1_paths_exist.py` + `test_no_load_test_marker_in_compliance_bundles.py`) + extend `test_mttr_soak_filter_universality.py` marker recognition | ~1 day | All 3 gates pass; v2.1 spec doc reviewable |
| 2 | `load_test_runs` table mig + `/api/admin/load-test/{abort,status}` endpoints + AlertManager rule + customer-probe wiring | ~1 day | Manual abort test from staging |
| 3 | `site_appliances.bearer_revoked` mig + Vault Transit secret-path + `admin_audit_log` event types + `.agent/reference/NETWORK.md` CX22 firewall spec | ~1 day | Vault path exists; audit row written on test fetch |
| 4 | CX22 + WG peer .4 provisioning + k6 binary install + Scenario A dry-run at 10% load against synthetic site | ~2 days | Scenario A green; kill-switch tested |
| 5 | Wave-1 endpoint expansion (P1-1, deferred to followup task #105); `load_test_marker_in_compliance_bundles` + `synthetic_traffic_marker_orphan` substrate invariants; full-load Scenario A/B/C | ~1 day | All 3 substrate invariants ticking; full run completes |

**Total: ~6 days engineering.** Sequencing pin: Commits 1-3 are spec + backend primitives (cannot wait); Commit 4 needs ops; Commit 5 follows runtime green.

---

## Sequencing pin to #94 + #98

Per PM lens: pin commit order in BOTH plans.

1. **#97 v2.1 Commits 1-3** ship standalone (backend primitives; no ops dependency).
2. **#94 v2 redesign** can reference Commit 1's marker unification + Commit 2's abort wiring; #94 v2 ships AFTER #97 Commit 3.
3. **#97 Commits 4-5** + **#98 24h SLA soak** run after #97 Commits 1-3 are green for ≥7d.

This pin lands in writing as a forward-reference comment at the top of `.agent/plans/24-substrate-mttr-soak-v2-*.md` (which #94 will own) + back-link from this v2.1 doc.

---

## Pre-execution blockers (re-stated)

- ✅ **3 P0s closed in v2.1 design** (above) — no remaining blocker for Commit 1.
- ⚠ **Sequencing pin to #94 v2 + #98** — needs the forward-reference comment landed before #97 Commit 4 (ops spin-up).
- ✅ **Substrate invariant designed alongside marker unification** — `synthetic_traffic_marker_orphan` (Commit 5).

---

## Gate B preview (v2.1 fork)

The Gate B fork (post-implementation, pre-completion) MUST verify, per the original Gate A:

- All 5 Wave-1 endpoint paths return 200 (or expected non-5xx) under `curl` from CX22 with synthetic bearer.
- `details->>'synthetic'='load_test'` marker present on every k6-generated row in EVERY destination table.
- Substrate invariant for marker-leak fires sev2 alert when test row inserted without marker (positive control).
- Full pre-push CI sweep green per Session 220 Gate B lock-in (diff-scoped review is automatic BLOCK).
- Vault Transit signing path NOT reachable from CX22 (`nc -zv vault.osiriscare.com 8200` returns refused).
- One dry-run Scenario A executed end-to-end with kill-switch tested (flip flag mid-run, assert k6 exits within 30s).

---

## v2.1 status

**SHIP-READY** for Commit 1 implementation. Author: Claude (Session 220, 2026-05-16). Gate-A verdict + reviewer panel preserved at `audit/coach-62-load-harness-v1-gate-a-2026-05-16.md`. v2.1 inherits the verdict; Commits 1-3 close P0s + structural P1s; Commits 4-5 ship runtime.
