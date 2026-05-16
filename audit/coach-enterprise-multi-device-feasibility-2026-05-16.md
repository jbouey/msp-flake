# Adversarial Consistency Coach review — Enterprise multi-device feasibility
Date: 2026-05-16
Reviewer: fork-based 7-lens (general-purpose subagent, fresh context)
Verdict: **HOLDS-WITH-FIXES** for the 1-site/N-appliance (hospital VLAN) shape; **MATERIAL-GAPS** for the 50-clinic/N-appliance-per-clinic (MSP-partner) shape.

## 1-paragraph TL;DR
The cryptographic + RLS + per-appliance pubkey foundations are unusually mature for an SMB platform — per-site advisory-lock chain serialization (`evidence_chain.py:1384`), per-appliance pubkey verification (`evidence_chain.py:1092-1100`), row-guard against site-wide UPDATEs (mig 192/208), per-appliance `_enforce_site_id()` discipline, BAA-enforcement triad, substrate invariants that already explicitly name multi-device routing classes ("RT-P1 320 missed L2 escalations", `assertions.py:2765`), and an auditor kit that ships ALL appliances' bundles per site by design (`site_appliances` JOIN that intentionally includes decommissioned rows, `evidence_chain.py:2409`). **What does NOT hold up at MSP-partner scale**: (a) `fleet_cli` has NO first-class `--target-appliance-id` arg — multi-appliance per-site targeting is parameter-encoded only and unaudited for UX-confusion risk at 250-appliance scale; (b) per-site chain serialization (`pg_advisory_xact_lock(hashtext(site_id))`) becomes the single hot-lock at the 1-site/20-appliance hospital shape and has not been load-tested; (c) bulk-onboarding 15 fresh appliances has no enumerated workflow — provisioning paths assume one-at-a-time installer flow; (d) the partner-dashboard fleet-summary surface routes through MV-bypassing direct SQL (correct for RLS) but has not been measured for the 250-appliance render budget. None of the four is a does-not-hold blocker — all are bounded, fixable.

## Schema-level multi-device assumptions

- `site_appliances` (`mcp-server/.../backend/migrations/196_appliances_deprecation.sql`, mig 212 + 244 + 324): canonical multi-appliance table; per-appliance `agent_public_key` (mig 196), `signature_enforcement` (mig 212), `bearer_revoked` (mig 324), `deleted_at` (soft-delete with portal-gate enforcement RT33). Schema correctly N>1.
- `compliance_bundles.appliance_id`: column exists (mig 012a) but **deprecated** in favor of `site_id` join through `site_appliances`. Documented in mig 268 — "every bundle has NULL appliance_id AND NULL outcome (verified live)." Hash chain anchors on `site_id`, not `appliance_id`. **This is the deliberate design**, but the deprecation is incomplete: mig 047 still indexes `(appliance_id, reported_at)` and mig 271 has the canonical-join comment.
- `bearer_revoked` (mig 324): correctly per-appliance (column on `site_appliances`); consumed at `shared.py:623` via LEFT JOIN with `COALESCE(.., FALSE)`. Site-wide bearer revocation requires N row UPDATEs — no batch revocation primitive.
- `agent_signature` per-appliance verification: `evidence_chain.py:1090-1100` queries `site_appliances WHERE site_id=:site_id AND agent_public_key=:key` — correctly per-appliance.
- `discovered_devices`: `(LOWER(mac_address), site_id)` UNIQUE (mig 244). Dedup is **site-scoped not appliance-scoped** — when 2 appliances see the same MAC, last-writer-wins on `owner_appliance_id`. Fine for the intended "first-discovery owns" semantic, but no `seen_by_appliances[]` array exists; visibility loss when an appliance goes offline is undetectable from row state.
- Hash chain: **per-site chain only**, no per-appliance chain. All evidence from N appliances under one site serializes through one advisory lock + one `(prev_hash, chain_position)` lineage. **This is auditor-friendly** (kit is one chain per site) but creates a hot lock at high N.
- `fleet_orders.target_appliance_id`: in signed payload, verified by `processor.go::verifyHostScope` (per CLAUDE.md rule). Enforced. Three callsites in `partners.py` (4761, 5375) + `protection_profiles.py` (493) + `sites.py` (627, 2243) + `fleet_updates.py` (94) all populate it correctly.

## Read-path scaling

- `client_portal.py` already migrated away from `appliance_status_rollup` MV to direct `site_appliances` LATERAL heartbeat JOIN (Session 218 RT33 P2 Steve veto) — proper RLS defense in depth.
- Same-origin `WHERE site_id = $1 LIMIT 1` over `site_appliances`: BUG 2 audit drove this to 0 earlier in Session 220 (memory MEMORY.md "site_appliances 81→0"). One residual: `client_portal.py:4781` `SELECT unnest(ip_addresses::text[]) FROM site_appliances WHERE site_id = $1` — intentionally returns all rows (not LIMIT 1), correct.
- Customer-facing client portal: appliance LIST endpoint returns N rows via the LATERAL join. **Has not been measured at 20-appliance shape**; UI render budget unknown.
- Auditor kit (`evidence_chain.py:4245`): pulls bundles per-site, includes all appliances historically — correct multi-device semantics. Identity-chain explicitly includes decommissioned `site_appliances` rows (line 2409 noqa).
- Substrate invariants: `assertions.py:2765` (chronic-without-l2-decision) explicitly cites "pre-fix the agent_api.py recurrence detector partitioned counts by appliance_id, so multi-daemon sites silently never tripped >=3-in-4h (320 missed L2 escalations / 7d at north-valley-branch-2)" — i.e., a multi-appliance-class bug has already been caught and fixed at the substrate level. Strong signal that this dimension is actively audited.

## Write-path correctness

- Race on `site_appliances` UPDATE: mig 192 + 208 row-guard refuses UPDATEs touching >1 row unless `SET LOCAL app.allow_multi_row='true'`. Closes the "site-wide UPDATE footgun" memory item structurally.
- Two appliances heartbeat simultaneously: each is its own appliance_id row, no contention on UPDATE. Heartbeat INSERT to `appliance_heartbeats` is append-only per (appliance_id, observed_at) — no contention.
- Two appliances detect same incident: incidents key by `(site_id, check_type, hostname)` — dedup is correct for shared file server, but the credit-for-detection is the appliance whose bundle write wins the advisory lock. Acceptable.
- Provisioning Nth appliance at existing site: `provisioning.py` paths assume MAC-lookup → appliance_id. **Workflow for "add 3rd appliance to existing site" is not enumerated in any doc** — it works by accident (the MAC lookup creates a new `site_appliances` row), but bulk-onboarding has no first-class path.
- Per-appliance signing key gen at scale: rotation goes through `signing_key_rotation` privileged-chain. **No bulk-rotation primitive** — each appliance needs its own attested order.

## Hash-chain semantics

- Per-site chain serialized via `pg_advisory_xact_lock(hashtext(site_id))` (`evidence_chain.py:1381-1385`). Comment notes "Without this, concurrent submissions race (caused 1,125 broken links)." Lock held inside the bundle-INSERT transaction.
- At 1 site / 20 appliances each emitting 1 bundle/min: **20 contenders per minute on a single PG advisory lock**. Lock is in-process per PgBouncer backend; no measurement of contention exists.
- Scenario "appliance A offline → B writes N → A back online writes N (collision)": A would compute chain_position=N+M (M = bundles B wrote while A was offline), prev_hash from current head. **Per-site chain absorbs A's resumption naturally** — A doesn't have its own position. This is correct.
- Bundle-id dedup under the lock (`evidence_chain.py:1390-1408`): idempotent fast-exit. Survives A's silent re-submission of an in-flight bundle.

## Counsel's 7 Rules at multi-device

- **Rule 3 (privileged chain)**: ALLOWED_EVENTS + v_privileged_types include `signing_key_rotation` + `delegate_signing_key`. Per-appliance targeting via `target_appliance_id` in signed payload. At 250-appliance MSP, **no UX layer exists** to issue a privileged order to one of 250 appliances without typing the UUID — fleet_cli has no appliance picker. Risk: operator targets the wrong appliance and the chain attests it correctly.
- **Rule 4 (orphan coverage)**: substrate invariants exist (`appliance_stale_heartbeats`, `heartbeat_vs_checkin_drift`, `daemon_heartbeat_unsigned` w/ explicit "Counsel Rule 4 orphan coverage at multi-device-enterprise fleet scale" docstring at `assertions.py:2784`). Per-(site, appliance) granularity. **What's missing**: a partner-level rollup "X of Y appliances stale across this partner's 50 clinics." Operator sees per-site invariant fires, not a fleet aggregate.
- **Rule 6 (BAA enforcement)**: `baa_enforcement_ok()` is `client_org_id`-scoped. All N appliances under one client_org_id are gated together. Substrate invariant `sensitive_workflow_advanced_without_baa` (sev1) catches bypass. **Holds at multi-device scale** — BAA expiry blocks every appliance under that org uniformly.
- **Rule 7 (no unauth context)**: opaque-mode emails shipped (cross_org_relocate, owner_transfer, email_rename). No customer-facing email helper carries org/clinic/actor names. **Holds**.

## Operator workflow

- `fleet_cli.py`: `create <order_type> --param key=val --expires N --actor-email --reason`. No `--target-appliance-id`, no `--all-at-site`. Per-appliance targeting requires the caller to embed the appliance_id in `--param`, which the order_signing layer reads. **Friction at 250-appliance scale**.
- Dashboard for partner with 250 appliances: `/api/partners/me/appliances` uses LATERAL heartbeat join. No measurement of render time at 250 rows. **Likely needs pagination + grouping by site** — not currently designed for fleet-scale browsing.
- L1 healing at 250 appliances: each rule fires per-appliance. Aggregate alerting (operator-alert hook) is per-event. No fleet-level digest. **Noise risk** at scale.

## Recent commits — multi-device contract check

- **mig 324 bearer_revoked**: correctly per-appliance (column on `site_appliances`). LOAD-HARNESS scoped (synthetic-bearer teardown) — real-appliance bearer rotation continues to use the `signing_key_rotation` privileged path. **HOLDS**.
- **#93 BAA FK-join cutover** (`4af4ddc9`): `client_org_id` resolution path. All appliances under one org gate uniformly. **HOLDS**.
- **#62 load harness v2.1**: targets synthetic-bearer + synthetic-site only (`sites.synthetic = TRUE` gate at `load_test_api.py:423`). **Does NOT exercise multi-appliance write contention on the chain advisory lock** — gap for the 20-appliance/1-site shape.
- **8014979d Vault Phase C iter-4**: INV + substrate invariant. Vault is HMAC-signing primitive scope; appliance count is orthogonal. **HOLDS**.

## Scenarios A–E walkthrough

- **A: 5-clinic × 3 appliances (15 fresh)**. ISO + MAC-lookup provisioning is one-at-a-time. **Onboarding requires 15 ISO writes + 15 boots + 15 mac-lookup verifications**. No bulk-provisioning primitive. Carol-class operational pain.
- **B: 1 site × 20 appliances, auditor downloads kit**. Kit pulls per-site bundles (all appliances) in chain order. Identity-chain includes all 20 active + any historical. **HOLDS** — kit is coherent.
- **C: Privileged action on specific appliance in 50-site partner**. CLI requires UUID in `--param`. Audit trail correctly records `target_appliance_id`. Customer auditor reconstructs from `compliance_bundles WHERE check_type='privileged_access'` + `fleet_orders.parameters->>'target_appliance_id'`. **HOLDS technically, but UX-confusion risk** on operator side.
- **D: 1 of 250 appliances offline 4h**. `appliance_stale_heartbeats` substrate invariant fires per-appliance. Alert subject includes site_id + appliance_id. **HOLDS at the row level**, but no fleet-aggregate digest — operator sees individual invariant fires, not "3/250 stale."
- **E: BAA expires for one client_org_id (5 appliances)**. `baa_enforcement_ok()` returns FALSE for that org. All BAA-gated workflows (owner_transfer, cross_org_relocate, evidence_export) refuse for any caller in that org. Appliance write paths (checkin, evidence_submit) **are NOT in `BAA_GATED_WORKFLOWS`** — they continue accepting writes. By design (writes ≠ "advancing a sensitive workflow"), but worth re-confirming with counsel for the multi-device shape.

## Per-lens findings

- **Steve**: chain advisory lock at high N is unmeasured. Per-site lock + 20-appliance fleet at 1 bundle/min/appliance is plausible-but-untested. P1.
- **Maya**: schema is sound. `compliance_bundles.appliance_id` deprecation is incomplete (column still exists, indexed) — drift risk. P2.
- **Carol**: fleet_cli lacks first-class multi-appliance targeting; partner dashboard has no measured render budget at 250 rows. P1 + P2.
- **Coach**: lockstep registrations holding (BAA triad, privileged-chain 3-list+1). No half-implementations found in scope.
- **Auditor**: kit is per-site, includes historical appliances, hash chain is per-site coherent. **Strongest dimension**.
- **PM**: enterprise scope (50-site partner, 20-appliance hospital) is materially OUTSIDE the stated ICP ("1-50 provider practices in NEPA region", `CLAUDE.md` line 5). Holding the platform to enterprise-multi-device feasibility is aspirational, not contractual. P0 product question: **is this the right ICP to optimize for?**
- **Counsel**: Rules 3+4+6+7 all hold structurally. Rule 4 has a fleet-aggregate visibility gap (per-row alerts, no partner roll-up). P2.

## Top findings ranked

### P0 (does-not-hold blockers)
- None. No structural blocker found. The platform's multi-device foundations are unusually deliberate.

### P1 (must-close-before-onboarding-N>10-appliance-customer)
- **Load-test the per-site chain advisory lock at 1-site/20-appliance shape.** Current load harness (#62) is single-bearer/synthetic-site; does not exercise lock contention. Risk: chain-write tail latency at 20×1/min hits PgBouncer slot exhaustion.
- **First-class multi-appliance targeting in `fleet_cli`**: add `--target-appliance-id`, `--all-at-site <site_id>`, and a tab-completion or `appliances list --site <id>` helper. Closes operator-confusion risk on privileged orders at 250-appliance scale.
- **Bulk-onboarding workflow doc + primitive.** ISO + mac-lookup is one-at-a-time; no enumerated path for "add 5 appliances to an existing site." Carol-class operational gap.

### P2 (operational quality-of-life)
- Partner-level fleet-aggregate digest for substrate invariants — "X of Y stale across this partner" — closes Counsel Rule 4 visibility at fleet scale.
- Partner dashboard render budget measurement at 250-row appliance list. Likely needs pagination + group-by-site.
- Complete `compliance_bundles.appliance_id` deprecation: drop the column + mig 047 index after audit-period.
- Re-confirm with counsel: does BAA expiry need to block `evidence_submit` / `checkin` (write paths) or is "block sensitive-workflow advance" sufficient at the multi-appliance shape?
- Add per-batch revocation primitive for `bearer_revoked` (currently row-by-row) — useful for site-decommission flows.

## Recommended next moves

1. **Stand up an enterprise-shape load test variant of #62** — synthetic 1 site / 20 appliances / 1 bundle-per-minute-per-appliance / 24h soak. Measure (a) advisory lock wait p95/p99; (b) PgBouncer slot pressure; (c) chain-position monotonicity (no skipped or duplicate). Single highest-value next move.
2. **Add `--target-appliance-id` + `--all-at-site` to fleet_cli** + a `fleet_cli appliances list --site` subcommand. Low-effort, closes the Rule 3 UX-confusion class identified in Scenario C.
3. **Write the bulk-onboarding runbook + scripted primitive** (e.g. `provision_batch.py --site <id> --count 5`). Even a doc-only deliverable closes the Scenario A operational gap.
4. **Round-table the ICP question with PM hat**: target says "1-50 SMB practices in NEPA". Is enterprise multi-device (250-appliance partner / 20-appliance hospital) a target customer, or aspirational? Decision gates the priority of P1/P2 fixes.
5. **Partner-aggregate fleet digest** — `/api/partners/me/fleet-health` summarizing substrate invariants across all sites. Counsel Rule 4 fleet-scale visibility.

## Final
**HOLDS-WITH-FIXES** for 1-site/N-appliance (hospital VLAN shape) — chain serialization needs measurement, no design blocker.

**MATERIAL-GAPS** for 50-clinic × N-appliance (MSP-partner shape) — operator UX (fleet_cli targeting, partner dashboard render, bulk-onboarding) is not designed for this scale. None of the gaps is structural; all are bounded engineering work. Foundations (cryptography, RLS, BAA, substrate invariants, per-appliance pubkeys, advisory-lock chain) are unusually mature for the SMB ICP and would carry through to enterprise multi-device with the P1 work listed.
