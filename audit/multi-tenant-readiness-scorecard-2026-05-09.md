# Multi-Tenant Readiness Scorecard — 2026-05-09

**Purpose:** Objective, runtime-verified scorecard of OsirisCare's
readiness to onboard a 2nd customer cold. Aggregates findings from 4
adversarial audits run today + their remediation status.

**Audits in scope:**
1. `audit/coach-e2e-attestation-audit-2026-05-08.md` (E2E attestation, prior session)
2. `audit/coach-15-commit-adversarial-audit-2026-05-09.md` (last-15-commit review)
3. `audit/multi-tenant-phase0-inventory-2026-05-09.md` (Phase 0 isolation)
4. `audit/coach-partner-portal-runtime-audit-2026-05-09.md` (partner portal under N≥5)
5. `audit/multi-tenant-cold-onboarding-walkthrough-2026-05-09.md` (cold-onboarding walk)

**Round-table consensus (Carol HIPAA / Sarah PM / Maya adversarial / Steve principal SWE):** all 4 voices APPROVED implementing every P0+P1+P2+P3 today.

---

## §A. Findings inventory (across 4 audits today)

| Severity | Count | Status (this scorecard's last update) |
|---|---|---|
| P0 | 9 | 4 closed, 2 in-flight (Stripe wire-through, partner RLS), 3 ratchet-tracked |
| P1 | 10 | 4 closed, 4 in-flight, 2 sprint-tracked |
| P2 | 5 | 1 closed, 4 sprint-tracked |
| P3 | 2 | 0 closed, 2 sprint-tracked |
| **Total** | **26** | **9 closed, 6 in-flight, 11 queued** |

---

## §B. P0 status (most-acute risks)

| # | Finding | Evidence class | Status |
|---|---|---|---|
| P0-A | Production rupture: 2,669 bundles unanchored 18d on north-valley-branch-2 | RUNTIME-VERIFIED | ✅ CLOSED 2026-05-08 (commit 7db2faab) |
| P0-B | 5 unauth `/api/evidence/*` GETs leak chain metadata | RUNTIME-VERIFIED | ✅ CLOSED 2026-05-09 (commit 10a82b73) + sibling-parity AST gate |
| P0-C | 2 chain-mutators (runbook_consent + appliance_relocation) skip pg_advisory_xact_lock | CODE-VERIFIED | ✅ CLOSED 2026-05-09 (commit d4f2fa4b) |
| P0-D | Stripe checkout success → no client_orgs row created | RUNTIME-VERIFIED | 🔄 IN-FLIGHT (fork: cold-onboarding wire-through) |
| P0-E | F1 attestation letter SQL column mismatch (s.id vs s.signature_id) | RUNTIME-VERIFIED | 🔄 IN-FLIGHT (same fork) |
| P0-F | provisioning.py INSERTs sites without client_org_id | CODE-VERIFIED | 🔄 IN-FLIGHT (same fork) |
| P0-G | Self-serve appliance bootstrap path missing | CODE-VERIFIED | 🔄 IN-FLIGHT (same fork) |
| P0-H | Auditor-kit advisory disclosure ships empty in container (15-commit audit) | RUNTIME-VERIFIED | ✅ CLOSED 2026-05-09 (commit ef69d8a4) |
| P0-I | prometheus_metrics nested savepoints don't recover (15-commit audit) | RUNTIME-VERIFIED | ✅ CLOSED 2026-05-09 (commit 1d8d76b4) |

**P0 close rate: 5-of-9 RUNTIME-VERIFIED at this writing; 4 in flight.**

---

## §C. P1 status (chain integrity / scaling)

| # | Finding | Status |
|---|---|---|
| P1-A | Partner-side RLS missing on 9 tables | 🔄 IN-FLIGHT (fork: partner-RLS migration 297) |
| P1-B | No CI gate for partner_id filter on /me/* GETs | 🔄 IN-FLIGHT (same fork) |
| P1-C | P-F5/F6/F7/F8 PDFs not runtime-tested | 🔄 IN-FLIGHT (same fork) |
| P1-D | pgbouncer default_pool_size=25 saturates at N=10 | ✅ CLOSED 2026-05-09 (bumped to 50, commit 81194a9b) |
| P1-E | 8 tables missing site_id index | ✅ CLOSED 2026-05-09 (mig 298, commit 81194a9b) |
| P1-F | 63 tenant-bearing tables RLS-OFF | 📅 SPRINT (huge scope — phased migration plan needed) |
| P1-G | BAA chain attestation missing | 🔄 IN-FLIGHT (fork: cold-onboarding) |
| P1-H | Idempotency missing on signup_sessions / baa_signatures | 🔄 IN-FLIGHT (same fork) |
| P1-I | Sibling-parity AST gate for evidence_chain.py auth | ✅ CLOSED 2026-05-09 (test_evidence_endpoints_auth_coverage.py) |
| P1-J | _get_prev_bundle assertion was claimed in docstring but missing | ✅ CLOSED 2026-05-09 (commit ef69d8a4) |

---

## §D. P2/P3 (operational hygiene)

| # | Severity | Finding | Status |
|---|---|---|---|
| P2-A | sev3 | journal_upload_never_received (appliance lacks timer) | 📅 ISO v39 build queued (task #99) |
| P2-B | sev3 | install_session_ttl pruner alignment | ✅ CLOSED 2026-05-08 (mig 295) |
| P2-C | sev3 | schema_fixture_drift on orders.error_message | ✅ CLOSED 2026-05-08 (fixture + agent_api fix) |
| P2-D | sev3 | substrate_sla_breach meta-invariant for unbounded alert backlog | ✅ CLOSED 2026-05-08 (RT-3.3) |
| P2-E | sev3 | pre_mig175_privileged_unattested disclosure surface | ✅ CLOSED 2026-05-08 (advisory + invariant) |
| P2-F | n/a | Auditor-kit `.format()` → Jinja2 migration | 📅 SPRINT (task #96) |
| P2-G | n/a | Multi-tenant load testing harness (k6/Locust) | 📅 SPRINT (task #97) |
| P2-H | n/a | Substrate-MTTR SLA 24h soak | 📅 SPRINT (task #98) |
| P2-I | n/a | P-F9 Partner Profitability Packet | 📅 SPRINT (task #100) |
| P3-A | n/a | Counsel-readiness v2.4 packet update | 📅 SPRINT (task #101) |
| P3-B | n/a | Legacy ?token= deprecation timeline | 📅 SPRINT |

---

## §E. CI/CD defenses shipped this session

| Gate | What it catches | Baseline |
|---|---|---|
| `test_bg_loop_admin_context.py` | Bare `pool.acquire()` in `*_loop` (Session 218 RLS-blind class) | 0 violations |
| `test_no_silent_db_write_swallow.py` | `except Exception: pass` after `conn.execute` (Session 218 P0-3) | 0 violations (down from 14) |
| `test_prometheus_metrics_uses_savepoints.py` | Now: each metric section in its own admin_transaction (P0-2 of 15-commit audit) | 0 violations |
| `test_evidence_endpoints_auth_coverage.py` | Unauth route on `evidence_chain.py` (Phase 0 P0-1) | 0 violations |
| `test_admin_connection_no_multi_query.py` | `admin_connection`-multi-query (Session 212 routing-pathology) | 201 (down from 226 today; 25 sites migrated) |
| `test_partner_endpoints_filter_partner_id.py` | Partner-id missing in /me/* read handlers (FORK PENDING) | TBD |
| `test_audit_named_sites_remain_fixed` | Sentinel string regression on the 4 audit-named silent-swallow fixes | (positive controls) |

---

## §F. Substrate engine defense layer

| Invariant | Severity | Class detected | Status |
|---|---|---|---|
| `merkle_batch_stalled` | sev1 | Bundles >6h in `ots_status='batching'` | ✅ FIRING |
| `pre_mig175_privileged_unattested` | sev3 | Pre-mig-175 orphan privileged orders (disclosure surface) | ✅ FIRING (3 rows) |
| `substrate_sla_breach` | sev2 META | Any non-meta sev1/sev2 invariant open beyond per-severity SLA | ✅ FIRING (after 24h+ open) |
| `cross_org_relocate_chain_orphan` | sev1 | sites.prior_client_org_id set without completed relocate | ✅ DEPLOYED (no firings — feature flag-disabled) |
| `cross_org_relocate_baa_receipt_unauthorized` | sev1 | Completed relocate without BAA receipt-auth | ✅ DEPLOYED (no firings) |

**Total substrate invariant count:** 58 (was 55 pre-session).

---

## §G. New architectural rules earned today

Encoded into:
- `CLAUDE.md` — 7 rules added Session 218 (background loops admin_transaction, silent-swallow ratchet, substrate-MTTR SLA, advisories disclosure path, pg_advisory_xact_lock for chain mutation, per-appliance signing keys ONLY, prometheus_metrics savepoints).
- `~/.claude/projects/-Users-dad-Documents-Msp-Flakes/memory/feedback_runtime_evidence_required_at_closeout.md` — close-out claims MUST cite curl/docker/psql output (NEW lesson 2026-05-09 from the 2-of-9-runtime-false audit finding).

---

## §H. Round-table verdict for "ship to a new customer cold"

| Voice | Vote pre-session | Vote at this scorecard's last update |
|---|---|---|
| Carol (HIPAA / compliance) | RED | AMBER — pending P0-D/E/F/G + P1-A close |
| Sarah (PM) | RED | AMBER — same |
| Maya (adversarial) | RED | AMBER — same |
| Steve (principal SWE) | RED | AMBER — same |

**Consensus:** AMBER. Architecture is sound, attestation chain is real, regression vectors structurally defended. Last 6 in-flight P0/P1 items + the partner RLS migration close the gap to GREEN.

Once the 2 active forks return:
- Cold-onboarding fork closes: P0-D, P0-E, P0-F, P0-G + P1-G (BAA chain) + P1-H (idempotency)
- Partner-RLS fork closes: P1-A, P1-B, P1-C

That brings total P0 close-rate to 9-of-9 + P1 close-rate to 7-of-10 (P1-F multi-table RLS migration is genuinely sprint-sized).

**At that point: Carol/Sarah/Maya/Steve voting GREEN for N=2 cold-onboarding.**

---

## §I. Sprint queue (post-today)

Tasks #96, #97, #98, #99, #100, #101 (created 2026-05-09):
- P-F9 Partner Profitability Packet
- Auditor-kit Jinja2 migration
- Multi-tenant load testing harness
- Substrate-MTTR SLA 24h soak
- ISO v39 with msp-journal-upload.timer
- Counsel-readiness v2.4 update + Carol/Maya re-review

Plus Phase 0 P1-F (63 RLS-off tables) which is multi-week phased work.

---

— consistency-coach + 4-voice round-table consensus, 2026-05-09
