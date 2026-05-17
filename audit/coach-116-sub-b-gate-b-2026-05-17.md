# Gate B verdict — #116 Sub-B vault_key_approval_api admin endpoint

**Commit:** `068e3719` — `feat(#116 Sub-B): vault_key_approval_api admin endpoint`
**Date:** 2026-05-17
**Reviewer:** fork-based fresh-context Gate B per TWO-GATE protocol
**Test sweep:** **282/282 passed, 0 skipped** (`bash .githooks/full-test-sweep.sh`)

## Overall verdict: **APPROVE-WITH-FIXES**

Endpoint is correctly shaped, all 4 Gate A P0s closed, sweep clean, named-actor + TOCTOU + idempotency present, attestation precedes UPDATE (transaction abort-safe). Two P1s + three P2s should land as a follow-up commit before any operator first-use, but none gate the prod ship.

## Per-lens verdict

1. **Steve (Principal SWE):** APPROVE-WITH-FIXES — endpoint shape is clean, txn ordering correct (attestation BEFORE UPDATE so any failure rolls back atomically), single-pool acquire, both ImportError fallbacks present. **P1-1** below.
2. **Maya (Security/HIPAA):** APPROVE-WITH-FIXES — named-actor enforced via `_BANNED_ACTORS` + `@` requirement; banned set matches `fleet_cli` discipline; TOCTOU defense is operator-asserted not Vault-fetched (acceptable per Gate A trade-off, see below); audit row writes denormalized `attestation_bundle_id` + `pubkey_hex` for forensic correlation. **P2-1** below.
3. **Carol (CCIE/Ops):** APPROVE — 404/409/502 error messages are operator-actionable, explicitly explaining "Vault rotated — re-fetch + reissue". Discoverability gap (no admin UI yet) is acknowledged as P1-3 deferred.
4. **Coach (DBA):** APPROVE-WITH-FIXES — `SELECT FOR UPDATE` correct; `json.dumps(...)::jsonb` cast pattern correct (mirrors the Session 220 `jsonb_build_object` lesson by side-stepping it entirely with explicit dumps). **P1-1: admin_connection + conn.transaction() composition is exactly the pattern docs warn against**, see below.
5. **Auditor:** APPROVE — chain anchor `vault:<key_name>:v<key_version>` matches Sub-A invariant literal byte-for-byte; `_get_prev_bundle` walks per-anchor → each (key_name, version) gets its OWN chain starting at `chain_position=0`. Confirmed desired semantic per Gate A P0-3.
6. **PM:** APPROVE — scope held to admin endpoint; mig + invariant + runbook + ALLOWED_EVENTS extension all stayed in Sub-A. No bleed.
7. **Counsel (7 rules):** APPROVE — R3 PASS (ALLOWED_EVENTS-registered, chain anchored, audit row), R4 PASS (Sub-A sev1 invariant catches orphans), R7 PASS (admin-auth-gated, no unauth surface, 409 leak is admin-only so within-trust-boundary).

## Findings

### P1-1 (Coach + Steve) — `admin_connection + conn.transaction()` is documented anti-pattern

`tenant_middleware.py:147-157` explicitly warns: *"Use `admin_connection` for SINGLE-statement reads… use `admin_transaction` for any multi-statement work."* The endpoint at `vault_key_approval_api.py:123` does `async with admin_connection(pool) as conn, conn.transaction():` then issues 4 statements. The `SET app.is_admin TO 'true'` runs in autocommit BEFORE the `conn.transaction()` opens — under PgBouncer txn-pooling these can route to different backends. Symptom: spurious 404 (RLS hides the row because `app.is_admin` isn't set on the txn's backend). Mitigation: precedent exists at `appliance_relocation_api.py:82` (same pattern); not a fresh regression. **Fix:** swap to `admin_transaction(pool)` which is the documented helper for ≥2 statements with `SET LOCAL` inside the txn. One-line change, zero behavioral risk, closes the routing-pathology class. Both endpoints should migrate together.

### P1-2 (Maya) — `expected_pubkey_hex` is operator-asserted, not Vault-fetched

The TOCTOU defense compares the operator's submitted pubkey to the row's stored pubkey — both are first-observation values. If an attacker socially engineers the operator into submitting the WRONG-but-row-matching pubkey (e.g. by tricking them into reading from a stale Vault replica), the defense passes. Gate A's stronger variant ("re-fetch from Vault inside the txn") was deferred because the backend may not always be Vault-mode. **Acceptable** per Gate A trade-off — the audit row records `pubkey_hex` so post-hoc verification is possible. **Followup:** add a runbook step requiring the operator to `vault read transit/keys/<name>` from a TTY they control, NOT from a copy-paste they were sent. Flag as known-limitation in the v2.0 BAA prerequisites doc.

### P2-1 (Maya) — 409 message leaks prior approver email to new approver

`"approved_by={old_actor!r} at {old_at}"` exposes the prior named-human's email to the new caller. Within admin trust boundary so acceptable, but consider truncating to local-part or `redacted@<domain>` for principle-of-least-leak. Counsel R7 only covers unauthenticated channels; this is admin-to-admin so PASS.

### P2-2 (Steve) — `max_length=128` on `expected_pubkey_hex` is over-permissive

Ed25519 raw pubkey is exactly 64 hex chars. `max_length=128` allows future key types (Ed448 = 114 hex) but accepts garbage in the 65-127 range that will always fail the row compare. Tighten to `min_length=64, max_length=64` for Ed25519-only, OR add a `re.fullmatch(r"[0-9a-fA-F]{64,128}")` validator. Cosmetic; not a security gap.

### P2-3 (Steve) — `actor_email.lower()` normalization is one-way

Local-part is case-sensitive per RFC 5321, but lowercasing for audit-actor naming is universal practice in this codebase (matches `fleet_cli` discipline). Document as a stable convention — the endpoint's normalized `actor` is what writes to `admin_audit_log.username`. No fix needed.

## TOCTOU defense assessment

Operator-asserted vs Vault-fetched: **operator-asserted is the correct ship-now choice.** Vault-fetched requires the backend to be Vault-mode (currently shadow-mode rollout per `project_vault_transit_rollout.md`); shipping a Vault-fetch dependency now would block the endpoint on the entire Vault cutover. Operator-asserted is auditable post-hoc (pubkey in attestation + audit row), and the runbook can require fresh Vault read. **Upgrade path:** when Vault Transit hits steady-state, replace `expected_pubkey_hex` with an in-txn `vault read` call + remove the body field. Class-correct.

## Migration ledger check

Mig 328 SHIPPED on disk; zero ledger row — correct per CLAUDE.md rule "REMOVE the ledger row in the same commit when migration ships". No drift.

## Recommendation

Land follow-up commit that:
1. Swaps `admin_connection + conn.transaction()` → `admin_transaction(pool)` (P1-1) in `vault_key_approval_api.py` AND `appliance_relocation_api.py`.
2. Tightens `expected_pubkey_hex` to `min_length=64, max_length=64` (P2-2).
3. Adds runbook step requiring operator-controlled `vault read` for TOCTOU verification (P1-2).

#116 Sub-B is otherwise ship-ready. Cluster #116 close-out PROCEED after P1-1 fix.
