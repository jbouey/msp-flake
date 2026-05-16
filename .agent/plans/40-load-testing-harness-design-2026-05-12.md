# Task #97 — Multi-tenant Load Testing Harness Design

**Status:** RESEARCH DELIVERABLE — Gate A required before implementation.
**Date:** 2026-05-12.
**Companion:** task #94 plan-24 (substrate-MTTR soak — DIFFERENT scope; this is HTTP throughput).

## Why now

Phase 5 multi-tenant readiness (#95) passed without quantified throughput evidence — we have substrate-MTTR latency SLAs but no req/s ceiling on the API surface. Three concrete drivers:

1. **No production capacity number.** Operations can't answer "how many simultaneous appliance checkins before PgBouncer or Uvicorn falls over?" — we just know the current fleet (3 appliances) is fine.
2. **#98 SLA soak needs a load floor.** A 24h MTTR soak under zero load is unrealistic; we need to inject HTTP traffic alongside synthetic incidents.
3. **#94 v2 redesign blocked.** Phase 4 v1 was BLOCKED by user 2026-05-11 ("qa round table adversarial 2nd eye ran? — author-written counter-arguments don't count"); the v2 redesign needs throughput baseline as one input.

## Tool choice

| Tool | Pros | Cons | Verdict |
|---|---|---|---|
| **k6** | Single Go binary; native scenarios DSL (`stages`, `executor`); first-class Prometheus output; sub-ms ramp control; scripts are JS (easy to read) | New dep; ops needs k6-cloud or self-host for Grafana visualization | **Recommended** |
| Locust | Pure Python (devs already write Python); web UI for live monitoring | Async semantics fragile under high concurrency; less predictable load-shape control; gevent monkey-patching foot-gun | Acceptable for ad-hoc; bad for SLA-grade runs |
| wrk / wrk2 | Battle-tested HTTP load; Lua scripting for stateful flows | Bearer/cookie auth + multi-step flows clunky in Lua; no scenarios DSL | Bad fit for our auth-heavy API |
| Custom Python asyncio | We already have asyncio + httpx familiarity | Reinventing scheduling + ramping + reporting; SLAs need rigor | NO — high effort, low rigor |

**Pick: k6.** Sibling pattern: Vault Transit rollout uses Prometheus scrape on Hetzner; k6's prom output integrates without new infra.

## In-scope endpoints (Wave 1)

The "load floor" the substrate-MTTR soak needs. Hot machine-to-machine paths only — admin UI is sub-Hz per operator and not on the critical perf path.

| Endpoint | Method | Auth | Rationale | Target req/s |
|---|---|---|---|---|
| `/api/appliances/checkin` | POST | bearer | Highest-volume appliance call (every 60s × N appliances) | 100 |
| `/api/appliances/order` | GET | bearer | Order-poll cadence | 100 |
| `/api/evidence/sites/{id}/submit` | POST | bearer | Compliance bundle submission (1982 rows/24h on north-valley alone today) | 50 |
| `/api/journal/upload` | POST | bearer | Journal-batch from `msp-journal-upload.timer` | 25 |
| `/health` | GET | — | Baseline (no DB, just liveness) | 1000 |

Total simulated fleet: **~100 simultaneous appliances** worth of traffic.

## Out-of-scope (Wave 2 and later)

- Frontend (browser) load — separate concern; portal pages are sub-req/s per user.
- WebSocket / SSE channels — none in active use.
- Substrate engine internal load — covered by #94 plan-24.
- Login/auth burst (request-magic-link rate-limit boundary) — separate spec.

## Test scenarios (Wave 1)

### Scenario A — Steady-state (target SLA validation)
- Duration: 30 min.
- Load shape: ramp-up 60s → constant for 28 min → ramp-down 60s.
- Per-endpoint rate per the table above.
- **Pass:** p95 latency < 200ms on each endpoint; 0 5xx; mcp-server CPU < 70%; PgBouncer pool ≥ 5 idle.

### Scenario B — Burst (transient spike survival)
- Duration: 10 min.
- Load shape: 5× steady-state for 60s, drop to 1× for 60s, repeat 5×.
- **Pass:** p99 latency < 1s during burst; recovery to baseline p95 in < 30s; 0 5xx-storms (> 10/min).

### Scenario C — Slow ramp (find the breaking point)
- Duration: 60 min.
- Load shape: start at 1× steady-state, linear ramp to 10× over 60 min.
- **Pass:** Document the req/s point where p99 > 1s OR 5xx rate > 1%. NO hard SLA — this is data-collection.
- **Anti-goal:** crashing prod. Run against staging or against a snapshot DB.

## Where to run

| Environment | Use | Risk |
|---|---|---|
| GitHub Actions runner | NO — runners are 2-core, too underpowered + non-deterministic noisy | rejected |
| Hetzner Vault host (`vault.osiriscare.com` 89.167.76.203 / WG 10.100.0.3) | NO — Vault is production; same host as Prometheus/AlertManager. Don't risk it. | rejected |
| **New small Hetzner box** | YES — dedicated 2-vCPU/4GB ARM CX22 (€4/mo). WG peer .4. Runs k6 binary + small Grafana frontend if we want graphs. | **Recommended** |
| iMac | NO — chaos-lab already runs here; resource contention risks chaos-lab freezes (memory rule about chaos-lab on iMac) | rejected |
| VPS itself (`178.156.162.116`) | NO — k6 against `localhost:8000` IS prod; loopback bandwidth distorts results | rejected |

## Staging vs production

Run against `https://central-command.osiriscare.com` (production) BUT:
- Pin a synthetic site `synthetic-load-test` (mirrors the soak isolation pattern from plan 24).
- Every request carries header `X-Load-Test: true` and an `appliance_id` matching the synthetic site's appliances.
- Backend: add a single filter at the `/api/appliances/checkin` handler that recognizes the header AND the synthetic site_id, persists to a SEPARATE `load_test_checkins` table (or partition), excludes from real-customer aggregations.

Reason: staging environments drift; only production has the real PgBouncer settings, the real compliance_bundles partitioning, the real Vault Transit signing path. Run against prod with an isolation marker → real numbers without contaminating real data.

## Implementation plan (after Gate A)

1. **Spin up load-test Hetzner CX22.** Documented in `.agent/reference/NETWORK.md` (will need updating). WG peer .4.
2. **Add `X-Load-Test` header recognition** at backend boundary. Single migration + 4 handler-side checks.
3. **Write k6 scripts** at `tooling/load_test/scenarios/{steady,burst,ramp}.js`.
4. **Wire Prometheus scrape** for k6 → existing Grafana → new dashboard.
5. **Bake first run** of Scenario A. Capture baseline numbers.
6. **Document SLA pass/fail** in `docs/operations/LOAD_TEST_SLA.md`.

Estimated effort: 2-3 sessions (infra + scripts + first soak interpretation).

## Gate A asks

1. **Steve:** Is k6 the right tool given the existing Go ecosystem? Could `vegeta` (also Go, simpler) be better? — Counter: vegeta lacks scenarios DSL; for SLA-grade work we want stages/executor primitives.
2. **Maya:** Customer-facing risk of running against prod with synthetic marker — is the isolation marker robust to a misconfigured run that forgets the header? — Counter: enforce via per-script `defaultHeaders` constant; CI gate to fail if any scenario file is missing the literal.
3. **Carol:** Auth surface — every load-test request carries a real appliance bearer token. Where does that token live? — Counter: rotate a dedicated synthetic-appliance bearer that's keyed to `synthetic-load-test` site_id only; revocable independently.
4. **Coach:** Does this design follow the `_extract_<X>_aliases` codified pattern any other CI gate uses? — N/A, this is infra not test code.
5. **Phase 5 round-table referenced in #95:** does that body's "load testing harness" recommendation specify wave 1 endpoint priorities differently? If so, surface the diff.

## Why this design ships before #94/#98

- **#94 v2 (plan-24)** explicitly says "Load testing — separate task #97" — depends on this design.
- **#98 24h SLA soak** is meaningless without a real load floor — depends on Wave 1 being live.
- **#95 Phase 5 readiness verdict** flagged "no quantified throughput evidence" — closing that gap.

If Gate A approves this design, downstream tasks #94 v2 + #98 can advance with throughput data instead of guesswork.
