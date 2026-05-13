# Multi-tenant Load Testing Harness — v2 Design (Task #38 / plan-40)

**Status:** RESEARCH DELIVERABLE — Gate A v2 required before implementation.
**Date:** 2026-05-13.
**Supersedes:** `.agent/plans/40-load-testing-harness-design-2026-05-12.md` (v1, BLOCKED).
**Companion blockers closed:** P0-1 (real endpoint paths), P0-2 (cryptographic-table isolation), P0-3 (plan-24 pattern reuse).
**Companion verdict:** `audit/coach-load-test-harness-design-gate-a-2026-05-12.md` (v1 BLOCK).
**Capacity story:** the "30 clinics onboarding" credibility number for enterprise sales — see
`coach-enterprise-backlog-2026-05-12.md` OPERATOR-PAIN class.

> v1 author-written counter-arguments were called out 2026-05-11 ("qa round table
> adversarial 2nd eye ran?"). v2 carries zero in-doc counter-arguments —
> the §"Open questions for user-gate" section is the explicit Gate A v2 input.

---

## §1 — Scope

### In-scope (Wave 1)
- HTTP throughput characterization of the **highest-volume bearer-authenticated
  appliance endpoints** under simulated 100-appliance fleet load.
- Concrete capacity target: **sustained 100 simultaneous appliance checkins
  every 60s for 30 min** (= ~100 req/min on `/checkin` baseline) without 5xx
  storms, p95 < 200ms, and no degradation > 100ms on the real-customer probe.
- A single **published capacity number** the sales/operations narrative can cite:
  "we have measured headroom for N simultaneous clinics on current
  infrastructure" — N derived from Scenario C (slow ramp).
- Isolation pattern that **reuses plan-24's `details->>'synthetic'='load_test'`
  marker discipline** (generalized — see §3) and inherits mig 304's
  status='inactive' quarantine for blast-radius safety.

### Out-of-scope (Wave 2+)
- `POST /evidence/upload` — DROPPED from Wave 1 per P0-2 fix below
  (cryptographic-chain incompatibility). Wave 2 will spec a separate
  `load_test_bundles` table if the capacity story needs evidence-pipeline data.
- Frontend / browser load — portal pages are sub-Hz per operator.
- WebSocket / SSE channels — none in active use.
- Substrate engine internal load — covered by plan-24 (task #94).
- Login/auth burst — `request_magic_link` rate-limit boundary is a separate
  spec; load-test bearer is pre-provisioned.
- L1/L2 healing tier validation — substrate's job.

### Capacity target (definition of "load test green")
The harness produces ONE customer-facing number plus three operator-facing
numbers:

| Number | Surface | Target |
|---|---|---|
| **Max sustained simultaneous clinics** | sales/ops narrative | ≥ 30 (derived from Scenario C inflection) |
| `/checkin` p95 latency at target | ops dashboard | < 200ms |
| `/api/agent/executions` p95 latency at target | ops dashboard | < 250ms (writes 2 rows + flywheel hook) |
| Real-customer probe p95 during run | ops dashboard | degraded < 100ms vs. pre-run baseline |

---

## §2 — Real endpoint enumeration (P0-1 closure)

Every path below was grep-verified against the current tree on 2026-05-13.
agent_api.py mounts the router with **no prefix** (`router = APIRouter(tags=["agent"])`
at agent_api.py:78) — so route paths are literal as decorated. journal_api.py
mounts with prefix `/api/journal`. log_ingest.py mounts with prefix `/api/logs`.

### Wave 1 endpoints (canonical paths only)

| # | Path | Method | Source | Auth | Cadence | Wave-1 target |
|---|---|---|---|---|---|---|
| 1 | `/api/appliances/checkin` | POST | agent_api.py:3036 | `require_appliance_bearer` | every 60s × N appliances | 100 req/min for 100-appliance fleet |
| 2 | `/api/agent/executions` | POST | agent_api.py:2069 | `require_appliance_bearer` | every L1/L2 fire (~0.3/min/appliance observed) | 30 req/min |
| 3 | `/checkin` | POST | agent_api.py:362 | `require_appliance_bearer` | legacy alias — included to detect divergence | 10 req/min (parity-only probe) |
| 4 | `/api/agent/sync/pattern-stats` | POST | agent_api.py:1878 | `require_appliance_bearer` | hourly aggregated sync — payload-heavy | 1.6 req/min @ 100 appliances |
| 5 | `/api/journal/upload` | POST | journal_api.py:71 | `require_appliance_bearer_full` | `msp-journal-upload.timer` (hourly) | 1.6 req/min |
| 6 | `/api/devices/sync` | POST | device_sync.py:1088 | `require_appliance_bearer` | per-checkin device inventory delta | 100 req/min (mirrors checkin) |
| 7 | `/api/logs/ingest` | POST | log_ingest.py:57 | API-key auth | logshipper stream — highest payload-volume | 10 req/min (batched) |
| 8 | `/health` | GET | (root) | none | liveness probe | 1000 req/s — **labeled as infrastructure baseline only**, NOT application capacity (P2-1) |

### Explicit exclusions from Wave 1 (P1-1 closure)

| Path | Reason | Wave |
|---|---|---|
| `/evidence/upload` | P0-2 — cryptographic chain incompatibility (see §3) | deferred to Wave 2 with separate `load_test_bundles` table |
| `/incidents` (agent_api.py:705) | Triggers substrate + alertmanager fan-out; conflated with substrate-MTTR soak (plan-24); load-testing it duplicates plan-24's scope. | deferred to Wave 2 only if substrate-MTTR soak v2 fails to characterize fan-out |
| `/api/agent/l2/plan` | LLM-bound — load on this endpoint actually loads OpenClaw (`178.156.243.221`), not central-command. Wrong dependency surface. | wave 2 (target OpenClaw separately) |
| `/api/agent/sync/promoted-rules` (GET) | Read-only, cached — not a write-amplification surface. Throughput trivially scales. | wave 2 |
| All `/api/admin/*` | Sub-Hz per operator, browser-session-authenticated, irrelevant to fleet-scale story. | never |

### Path-divergence guard (P0-1 secondary fix)

Three paths in the wave-1 list (`/api/appliances/checkin`, `/checkin`,
`/api/agent/executions`) are legitimately valid simultaneously. A CI gate
`tests/test_load_harness_path_freshness.py` (NEW, ships with the harness)
parses every `endpoint_path` literal from `tooling/load_test/scenarios/*.js`
and `grep -F`s each against the backend source tree — fail if any literal
isn't a decorator argument. Closes the v1 "made up paths" class structurally.

---

## §3 — Isolation pattern (P0-2 + P0-3 closure)

### Inherit plan-24's pattern, do not duplicate it

Plan-24 + mig 303 + mig 304 already shipped the canonical synthetic-data
isolation pattern. v2 **generalizes the marker rather than introducing a
parallel one**:

**Generalized marker (mig 310, ships with harness):**
- Soak: `details->>'synthetic' = 'mttr_soak'` (renamed from `soak_test='true'`
  via backfill UPDATE in same migration — same partial index re-created).
- Load test: `details->>'synthetic' = 'load_test'`.
- Future: `details->>'synthetic' IN (...)` — single allowlist.

**Synthetic site reuse:**
- Site row: `synthetic-mttr-soak` is repurposed to a generic
  `synthetic-load-and-soak` (no row churn — only a `clinic_name` UPDATE +
  audit-log entry). Status remains `inactive` per mig 304 quarantine —
  every admin enumeration query already filters `WHERE status != 'inactive'`
  so the site is invisible to /api/fleet, /admin/metrics, recurrence_velocity,
  federation candidates, and auditor-kit walks.
- client_org row: same `00000000-0000-4000-8000-00000000ff04` UUID — name
  updated to drop the soak-specific wording. **No client_org_id remap.**

**Per-request marker:**
- Header `X-Synthetic-Run: load_test` carried by every k6 request.
- Backend validates the header value against an allowlist
  (`{"mttr_soak", "load_test"}`) at a single middleware checkpoint —
  rejects unknown values → 400.
- Header presence + auth-site = `synthetic-load-and-soak` together gate
  marker injection. Either alone fails closed (bearer scoped to synthetic
  site cannot inject without header; non-synthetic bearer cannot pass header).

**Storage:**
- Wave-1 endpoints write to the existing tables (`site_appliances`,
  `execution_telemetry`, `incidents`, `device_inventory`, etc.) — every row
  carries `details->>'synthetic'='load_test'` (or column-level equivalent
  where `details` doesn't exist; see §4 fixture-parity work).
- All admin aggregation queries gain the universal filter
  `details->>'synthetic' IS NULL` — added by extending plan-24's existing
  `test_mttr_soak_filter_universality.py` to `test_synthetic_marker_filter_universality.py`
  (single curated SQL-AST gate; supersedes plan-24's narrower test).

### Why this closes P0-2 (compliance_bundles)

`POST /evidence/upload` is **dropped from Wave 1.** Without it, the harness
never touches `compliance_bundles` — the Ed25519 chain, OTS anchoring, RLS
policies, and monthly partitioning are not crossed. The cryptographic
invariants stay sacred; the load-test scope shrinks to throughput
characterization of non-evidence paths.

If Wave 2 ever needs `/evidence/upload` numbers, the design will spec
a **dedicated `load_test_bundles` table** (own DDL, no chain coupling, no
OTS enqueue, no RLS policies). That decision is deferred until the Wave 1
capacity number drives the question.

### Why this closes P0-3 (plan-24 duplication)

| Concern | plan-24 (v1 ship) | v1 load-test (BLOCKED) | v2 load-test (this doc) |
|---|---|---|---|
| Synthetic site | `synthetic-mttr-soak` | `synthetic-load-test` (new) | reuse `synthetic-load-and-soak` (renamed in mig 310) |
| Marker column | `details->>'soak_test'='true'` | `X-Load-Test` header → separate table | `details->>'synthetic'='load_test'` (unified) |
| Filter discipline | per-query `WHERE ... != 'true'` | header-based filter at handler | unified `WHERE details->>'synthetic' IS NULL` |
| CI gate | `test_mttr_soak_filter_universality.py` | (not specified) | `test_synthetic_marker_filter_universality.py` (supersedes) |
| Quarantine | mig 304 `status='inactive'` | (would have needed its own quarantine) | inherited from mig 304 — single quarantine for both |

Single isolation discipline. Single CI gate. Single quarantine. One ledger
table (`substrate_mttr_soak_runs` extended to `synthetic_runs` with a
`run_type` column in mig 310).

---

## §4 — Test fixture parity

### `_pg.py` fixtures that gain new columns/markers

| Fixture | Today | Needed for harness | Action |
|---|---|---|---|
| `test_appliance_offline_detection_pg.py` | Inserts `site_appliances` row with no `details->>'synthetic'` | Gain `synthetic_marker` parameter in helper | Backwards-compatible: marker defaults to NULL; ratchet-only addition |
| `test_flywheel_spine_pg.py` | Inserts `incidents` rows | Test that flywheel-spine reader filters `details->>'synthetic' IS NULL` | NEW test case in same file |
| `test_chain_tamper_detector_pg.py` | Inserts `compliance_bundles` rows | (no change — chain detector never sees synthetic) | unchanged |
| `test_partner_security_hardening_pg.py` | Inserts site_appliances + sites rows | Verify the synthetic site's `status='inactive'` filter excludes it from every partner-portal enumeration | NEW assertion |
| NEW `test_synthetic_marker_filter_universality.py` | (does not exist) | Supersedes `test_mttr_soak_filter_universality.py`; curated allowlist + AST scan | NEW file, ships with mig 310 |
| NEW `test_load_harness_path_freshness.py` | (does not exist) | Validates k6 scenario JS paths exist as `@router` decorators | NEW file, ships with k6 scripts |

### Migration 310 (ships before any harness code)

```sql
-- 310_unify_synthetic_marker.sql (sketch)
BEGIN;

-- 1. Rename synthetic site to drop soak-specific label.
UPDATE sites
   SET clinic_name = 'Synthetic Load + MTTR Soak (NOT a real customer)',
       updated_at  = NOW()
 WHERE site_id = 'synthetic-mttr-soak';
-- (site_id unchanged for chain idempotency — same well-known UUID)

-- 2. Backfill existing soak_test markers to unified shape.
UPDATE incidents
   SET details = details - 'soak_test'
              || jsonb_build_object('synthetic', 'mttr_soak')
 WHERE details->>'soak_test' = 'true';

-- 3. Drop old partial index, create unified one.
DROP INDEX IF EXISTS idx_incidents_soak_test;
CREATE INDEX IF NOT EXISTS idx_incidents_synthetic
    ON incidents((details->>'synthetic'))
    WHERE details->>'synthetic' IS NOT NULL;

-- 4. Extend the run-ledger table.
ALTER TABLE substrate_mttr_soak_runs
    RENAME TO synthetic_runs;
ALTER TABLE synthetic_runs
    ADD COLUMN IF NOT EXISTS run_type TEXT NOT NULL DEFAULT 'mttr_soak'
        CHECK (run_type IN ('mttr_soak', 'load_test'));

-- 5. Audit-log entry.
INSERT INTO admin_audit_log (username, action, target, details, created_at)
VALUES ('system:mig-310', 'unify_synthetic_marker', 'sites,incidents',
        jsonb_build_object('migration','310','supersedes','303 + 304 marker'),
        NOW());

COMMIT;
```

Gate A reviewers should validate: (a) the backfill UPDATE on `incidents`
respects partitioning (it does — UPDATE-in-place within partitions is fine),
(b) the index DROP+CREATE shape is plan-24-compatible — no new query plans
diverge.

---

## §5 — Tooling choice

**Pick: k6** — unchanged from v1 (Gate A v1 didn't dispute this), with explicit
addendum closing P1-2.

### Comparison (refreshed)

| Tool | Verdict | Notes |
|---|---|---|
| **k6** | **CHOSEN** | Single Go binary; scenarios DSL (`stages`, `executor`); first-class Prometheus output; bearer + cookie + header fixtures trivial; goja-based JS scripts. |
| Locust | Acceptable for ad-hoc | gevent monkey-patching footgun under high concurrency. |
| vegeta | Considered + rejected | No scenarios DSL — Scenario B (burst pattern) requires manual orchestration. |
| wrk / wrk2 | Bad fit | Auth-heavy multi-step flows clunky in Lua. |
| Custom asyncio | NO | Reinventing scheduling, ramping, reporting. |

### VU ceiling math (P1-2 closure)

k6 scripts run in goja (a JS VM). Empirical k6 docs: **a 2-vCPU/4GB node
holds ~500 sustained VUs before goja GC pressure distorts latency
measurements**. Our Scenario C target (10× steady-state) is 100 simulated
appliances × 10 = 1000 VUs.

**Resolution: two-stage hardware plan.**

| Stage | Hardware | VU ceiling | Use |
|---|---|---|---|
| Phase 0 + Phase 1 | Hetzner CX22 (2 vCPU / 4 GB / €4/mo) | ≤ 500 VUs | smoke test (Phase 0) + steady-state Scenario A (Phase 1) |
| Phase 2 | Hetzner CX32 (4 vCPU / 8 GB / €8/mo) — **upgrade only if Phase 1 numbers warrant Phase 2** | ≤ 1500 VUs | Scenario B (burst) + Scenario C (10× ramp) |

If Phase 1 reveals the capacity ceiling is < 500 VUs (i.e. central-command
saturates before k6 does), we never upgrade — CX22 sufficient. The upgrade
is a runtime decision, not pre-committed spend.

### Where to run

| Environment | Verdict |
|---|---|
| **Hetzner CX22 (Phase 0/1), CX32 (Phase 2)** | **CHOSEN** — WG peer .4. New host. |
| Hetzner Vault host (89.167.76.203) | NO — production, same host as Prometheus/AlertManager. |
| iMac | NO — chaos-lab runs there. |
| VPS itself | NO — loopback distorts numbers. |
| GitHub Actions | NO — 2-core noisy. |

---

## §6 — Phased implementation

### Phase 0 — Smoke test (~1 day)
- Spin up CX22, add WG peer .4, document in `.agent/reference/NETWORK.md`.
- Ship mig 310 (synthetic-marker unification) + ratcheted tests.
- Provision one synthetic bearer token via Vault Transit (Carol's pattern —
  see §"Bearer storage" P1-5 closure below).
- Write `tooling/load_test/scenarios/smoke.js` — 10 VUs × 60 sec against
  `/api/appliances/checkin` only.
- **Pass criterion:** 100% 2xx, no 5xx in central-command logs, `X-Synthetic-Run`
  header reached the handler (verified via structured log assertion), no rows
  written without the synthetic marker.
- **Output:** "harness wired correctly" — no capacity number yet.

### Phase 1 — Capacity number (~1 day + 30 min run)
- Write `tooling/load_test/scenarios/steady.js` — Scenario A (steady-state)
  + `ramp.js` (Scenario C slow-ramp).
- Run Scenario A for 30 min at target rates per §2 Wave 1 table.
- Run Scenario C ramping 1× → 10× over 60 min.
- **Pass criterion (Scenario A):** all four §1 capacity-target numbers met.
- **Pass criterion (Scenario C):** document the req/s point where p99 > 1s
  OR 5xx rate > 1%. NO hard SLA — data-collection.
- **Output:** the published capacity number for the sales narrative
  ("≥ 30 simultaneous clinics measured").

### Phase 2 — Production-load soak (~1 day, then 24h run)
- Only fires if Phase 1 numbers justify it (real-customer probe degradation
  was acceptable + central-command had headroom).
- CX22 → CX32 upgrade decision happens here.
- Write `tooling/load_test/scenarios/burst.js` (Scenario B) and
  `soak.js` (24h sustained at 80% of Phase 1 capacity).
- Couple with substrate-MTTR soak (plan-24 v2) — the unified synthetic-marker
  means MTTR injector + load-test injector can run concurrently into the same
  synthetic site without contamination.
- **Pass criterion (24h):** all Scenario A criteria sustained for 24h +
  zero `P0-CHAIN-GAP` operator alerts on real-customer chain operations
  during the run.
- **Output:** "production-load soak passed" — closes #98 SLA soak dependency.

### Pre-flight kill-switch (P1-3 closure)

**Three layers, all required, all live before Phase 0:**

1. **Local abort file:** k6 scripts poll `/run/load-test/abort` between
   iterations (k6 `setup()` + per-VU `if (open()` check). File present →
   `exec.test.abort()` → exits 0.
2. **Backend kill flag:** new endpoint `GET /api/admin/load-test-status`
   (admin-auth, cached 30s) returning `{"abort": <bool>, "reason": str}`.
   k6 scripts re-read every 30s; `abort=true` → graceful shutdown.
3. **AlertManager auto-trip:** new alert rule
   `load_test_5xx_storm:rate(http_5xx_total{X-Synthetic-Run!=""}[1m]) > 50`
   → flips kill flag via webhook → all three layers converge.

Without all three, the v1 "60 min slow ramp against prod" risk class is
unmanaged. With all three, MTTM (mean-time-to-mitigate) is < 60s on any
class of failure.

### Real-customer probe (P1-4 closure)

A separate small process (lives on a different host — VPS itself is fine
since it's testing latency, not generating load) issues one
`POST /api/appliances/checkin` per minute as the **real production
appliance** at `192.168.88.241` (osiriscare-prod). The 24h baseline p95 of
this probe (pre-run) is the reference. During every load-test run, this
probe MUST stay within +100ms of baseline p95.

**Soft fail:** > +100ms for 5 consecutive minutes → AlertManager warning,
operator notified, load-test continues.
**Hard fail:** > +500ms for 2 consecutive minutes → kill-switch tripped
automatically.

---

## §7 — Pass/fail criteria

### Phase 0 (smoke)
- 100% 2xx on 600 requests (10 VUs × 60 sec).
- `X-Synthetic-Run` header logged at handler entry.
- Zero rows written to `incidents`/`site_appliances`/etc. **without**
  `details->>'synthetic'='load_test'`.
- Real-customer probe latency unaffected (< +10ms delta).

### Phase 1 (capacity)
- **/api/appliances/checkin:** p95 < 200ms, p99 < 500ms, 0 5xx at 100 req/min.
- **/api/agent/executions:** p95 < 250ms, p99 < 750ms, 0 5xx at 30 req/min.
- **/api/devices/sync:** p95 < 300ms, p99 < 1s, 0 5xx at 100 req/min.
- **/api/journal/upload:** p95 < 500ms (payload-heavy), 0 5xx at 1.6 req/min.
- **PgBouncer:** server-side connection pool ≥ 5 idle slots throughout.
- **mcp-server CPU:** < 70% per Uvicorn worker.
- **Real-customer probe:** p95 within +100ms of pre-run baseline.
- **Scenario C inflection:** documented req/s at which p99 > 1s OR
  5xx rate > 1%; published capacity number = that req/s ÷ per-appliance
  req/s × 0.7 safety margin.

### Phase 2 (24h soak)
- All Phase 1 criteria sustained for 24h.
- Zero `P0-CHAIN-GAP` alerts on real-customer chain operations.
- Zero new rows in `auditor_kit_download_audit_log` for the synthetic site
  (= isolation didn't leak to auditor surfaces).
- Substrate engine MTTR SLAs (plan-24 / task #94 v2) held under
  concurrent load + incident injection.

### Master fail conditions (any phase)
- Any row written to a real-customer site_id during a synthetic run = **P0
  isolation violation**, halt all load testing pending root-cause.
- `compliance_bundles` write attributed to synthetic-run client_org_id =
  **P0 cryptographic-chain contamination**, halt + Maya §164.528 review.
- Real-customer probe p95 > +500ms for 2 consecutive minutes = **automatic
  abort** (kill-switch trip).

---

## §8 — Open questions for user-gate (v2 Gate A input)

These replace v1's author-written counter-arguments. Each requires an
explicit decision before Phase 0 fires.

### Q1 (Steve) — Phase 2 hardware upgrade commitment
Phase 2 is gated on Phase 1 numbers, but if we never upgrade CX22→CX32,
we never produce a "headroom for 100+ clinics" number — only "headroom for
~30 clinics" (Phase 1 ceiling). Sales narrative wants the bigger number.
**Decision required:** commit €8/mo for CX32 unconditionally, OR accept
the ≤30-clinic ceiling as the public number? Recommend the latter
(operator-pain class is closed by ANY published capacity number).

### Q2 (Maya) — `/evidence/upload` Wave 2 commitment
Wave 1 drops `/evidence/upload` to protect `compliance_bundles`. The
"30 clinics onboarding" sales story doesn't strictly need evidence-pipeline
numbers — checkin + executions characterize the steady-state surface.
**Decision required:** is Wave 2 (separate `load_test_bundles` table) a
near-term must-have, or deferred indefinitely until a customer asks?

### Q3 (Carol) — Bearer storage path (P1-5 spec)
v2 commits to "Vault Transit issues a dedicated Ed25519 bearer keyed to
`synthetic-load-and-soak`". Concrete decisions:
- **Storage location:** 1Password vault item `osiris-load-test-bearer`
  (sibling pattern: Vault rollout uses 1Password for share custody).
- **Rotation cadence:** 30 days, auto-rotated via a new cron on the Vault
  host (NOT on the CX22 — separation of concerns).
- **Revocation path:** new column `site_appliances.bearer_revoked_at`
  (NULLable timestamp) + `auth.py` check. **NEW column required.**
- **Audit log:** every load-test run START writes a structured
  `load_test_run` row to `admin_audit_log` with `run_id + actor_email +
  bearer_token_id_hash`.

**Decision required:** approve the four items as a unit, OR push back on
the new `site_appliances.bearer_revoked_at` column (alt: dedicated
`load_test_bearer_revocations` table).

### Q4 (Carol) — CX22 WG-peer firewall scope (P2-3 closure)
WG peer .4 sits alongside Vault (.3), VPS (.1), Appliance (.2) on the
WG mesh. Default WG ACL is "all peers can reach all peers." For the load
host, recommended ACL:
- **Outbound allow:** `central-command.osiriscare.com:443` only.
- **Outbound deny:** Vault Transit API (.3:8200), VPS-internal services,
  Appliance.
- **Inbound deny:** all (SSH via separate management path or `gh ssh`).

**Decision required:** approve the deny-by-default WG ACL, OR keep default
WG mesh + rely on application-layer auth as defense-in-depth (current Vault
+ VPS posture).

### Q5 (Coach) — Sequencing pin (P1-2c closure)
v2 design ships these tasks in this order:
1. mig 310 (synthetic-marker unification) — ships first.
2. CI gate `test_synthetic_marker_filter_universality.py` — ratchet against
   mig 310 changes.
3. Phase 0 smoke — depends on (1) + (2).
4. Phase 1 capacity — depends on (3).
5. plan-24 v2 redesign — depends on (4) for "load floor" input AND inherits
   mig 310's marker (no parallel work).
6. task #98 24h SLA soak — depends on (4) for capacity number.

**Decision required:** approve this sequencing, OR pull plan-24 v2 ahead
of #97 (alternative: plan-24 v2 can ship with the old `soak_test` marker
and mig 310 backfills both).

### Q6 (Coach) — Gate B coverage
Per CLAUDE.md "TWO-GATE adversarial review" lock-in 2026-05-11, this design
gets Gate A NOW + Gate B before Phase 1 numbers are claimed as "shipped."
Gate B MUST run the full pre-push sweep + cite pass/fail counts in the
verdict (Session 220 lock-in).

**No decision required** — flagging the process commitment so it lives in
the design rather than getting forgotten.

---

## Appendix A — What v1 got right and v2 keeps

- Tool choice (k6) — kept.
- Hetzner CX-class hardware on WG peer .4 — kept (with two-stage upgrade plan).
- Production target with synthetic-site isolation — kept (now under unified
  marker discipline).
- `/health` baseline labeled as infrastructure, not application — kept
  with explicit P2-1 callout.
- Sequencing: this harness unblocks plan-24 v2 + task #98 — kept, sequencing
  now pinned in §8 Q5.

## Appendix B — What v1 got wrong and v2 fixes

| v1 mistake | v2 fix |
|---|---|
| `/api/appliances/order` path didn't exist | Replaced with `/api/agent/executions` (real high-volume bearer endpoint); also added `/api/devices/sync`, `/api/agent/sync/pattern-stats`, `/api/logs/ingest` from P1-1. |
| `/api/evidence/sites/{id}/submit` path didn't exist | `/evidence/upload` (the real path) is the chain-anchored surface — **DROPPED from Wave 1** per P0-2 chain-incompatibility. |
| Separate `load_test_checkins` table proposed | Reuse plan-24's marker pattern, unified via mig 310. |
| Different synthetic-site name from plan-24 | Rename plan-24's site to be neutral; single site for both classes. |
| Header-only marker (`X-Load-Test: true`) | Header + column marker (`details->>'synthetic'='load_test'`) for durable row-level isolation. |
| No kill-switch | Three-layer kill-switch (P1-3). |
| No real-customer-degradation SLA | Quantified probe + soft/hard fail (P1-4). |
| Bearer storage one-liner | 4-point spec (P1-5) in §8 Q3. |
| In-doc counter-arguments | Removed entirely — replaced by §8 explicit user-gate questions. |

---

**End of v2 design.** Awaiting Gate A v2 fork-based review (Steve / Maya /
Carol / Coach). Verdict file expected at
`audit/coach-load-test-harness-design-gate-a-v2-2026-05-13.md`.
