# Class-B 7-lens Gate A — D1 backend signature verification + substrate invariants

**Reviewer:** Fresh-context Gate A fork (Class-B, 7-lens)
**Date:** 2026-05-13
**Scope:** Task #40 reframed — backend-side Ed25519 verification of `appliance_heartbeats.agent_signature` + two substrate invariants (`daemon_heartbeat_unsigned` sev2, `daemon_heartbeat_signature_invalid` sev1) at multi-device-enterprise fleet scale (Rule 4 orphan-coverage).
**Posture:** Pre-distribution enterprise hardening. Substrate Integrity Engine + privileged-chain-of-custody + Session 196 per-appliance-keys + Session 218 dual-key (evidence + identity) are in scope as prior art.

---

## Counsel-rule binding (Rule 4 + multi-device-enterprise scale)

Counsel's Rule 4 states: *"orphan coverage — every block in the cryptographic-attestation chain MUST be attestable, NOT silent. A daemon that should be signing but is silently NOT signing is an orphan block."*

At multi-device-enterprise scale (per CLAUDE.md fleet status, dozens of clinics × multiple appliances/clinic), an unsigned heartbeat is indistinguishable today from a legitimate-but-old daemon. **This is the orphan-coverage gap Rule 4 forbids.** Per v1.0-INTERIM master BAA Article 3.2 ("cryptographic attestation chains"), the safeguard claim is materially weakened until backend-side verification exists.

Two distinct failure modes MUST be separable:

1. **Unsigned-but-known-keyed** (sev2): appliance has `site_appliances.agent_public_key` SET, but heartbeats arrive with NULL `agent_signature`. Daemon regression / version-rollback / signing-loop bug. Operator-actionable.
2. **Signed-but-invalid** (sev1): `agent_signature` is non-NULL but does not verify against the known public key. Potential key compromise OR canonical-payload format drift. Security-incident-class.

A third state — `agent_public_key IS NULL` — is informational ("appliance never enrolled with evidence-submitter key") and must NOT page. The substrate engine MUST distinguish these three states.

---

## Lens-by-lens findings on each design question

### Lens 1 — Engineering (Steve)

**Verification timing (Q1):** APPROVE async-on-substrate-sweep + a fast-path synchronous "soft-verify" that logs but does NOT block checkin.

Verify-on-RECEIVE in `sites.py:4205-4230` is wrong as the gating decision: a synchronous verify-and-reject path would let a checkin failure cascade across the fleet during a key-rotation race or canonical-payload-format drift. Rule 4 says "block must be attestable, not silent" — but attestable does NOT mean synchronously enforced; it means recorded + queryable. Substrate sweep (60s cadence) is well within the SLA for sev1/sev2.

However, the checkin handler SHOULD still call `verify_heartbeat_signature(...)` opportunistically and populate a `signature_valid BOOLEAN NULL` column at insert time. NULL = "not yet computed"; TRUE/FALSE = decisive. The substrate invariants then operate over the column, not over a re-verify loop. This is the same pattern as `compliance_bundles.signature_verified_at` (which we already do for evidence bundles).

**Storage shape (Q2):** APPROVE `signature_valid BOOLEAN NULL` + `signature_verified_at TIMESTAMPTZ NULL` on `appliance_heartbeats`. Reject trigger-computed verification — pgcrypto can do `digest()` but Ed25519 verify needs pynacl/cryptography and cannot live in plpgsql. Computing at INSERT in Python (sites.py:4205-4230) is correct; substrate sweep re-verifies NULL rows + audits any historical FALSE rows that aged out.

**Canonical-payload tokenization (Q3):** RECONSTRUCT, do NOT send. Daemon-supplied canonical-payload-string opens the trivial attack where daemon signs payload-A but sends fields B — backend would happily verify payload-A and store fields B, attesting to a lie. Backend MUST rebuild `{site_id}|{MAC.upper()}|{timestamp_unix}|{agent_version}` from the request's actual fields and verify against that. This is the same anti-pattern lesson as canonical_site_id in compliance_bundles (never trust caller-supplied canonical form).

**Daemon vs DB canonical-format MISMATCH (CRITICAL finding):** Mig 197 trigger computes `heartbeat_hash = sha256(site_id|appliance_id|observed_at|status)`. Daemon signs `sha256(site_id|MAC.upper()|timestamp_unix|agent_version)`. **These are different inputs.** The DB-computed hash is NOT what the daemon signed. Backend verification logic must use the DAEMON's format, not the DB trigger's. Recommend documenting this in mig-313 comment + dropping any presumption that `agent_signature ↔ heartbeat_hash` are bound.

**Key rotation (Q6):** BLOCK without `previous_agent_public_key TEXT NULL` + `previous_agent_public_key_retired_at TIMESTAMPTZ NULL` columns on `site_appliances`. During rotation, in-flight heartbeats signed under the old key would mass-fail-verify and trigger sev1 storms. Verifier tries new key first, falls back to previous-key if non-NULL AND retired_at < (NOW() + 5min grace window). Without this column, every rotation is a guaranteed false-positive sev1 page.

**Sigauth interaction (Q7):** SEPARATE. `agent_public_key` (evidence-submitter) and `agent_identity_public_key` (sigauth/sites.py:4553-4589) are deliberately distinct — Session 218 mig 251. Heartbeat signature uses the EVIDENCE key (per phonehome.go:875-879 `evidenceSubmitter.SigningKey()`). Document this explicitly in the verifier; do NOT fall back to the identity key on evidence-key-NULL.

### Lens 2 — HIPAA auditor surrogate

APPROVE-WITH-FIXES. The substrate invariants materialize the Rule 4 orphan-coverage claim and would satisfy an OCR auditor's question "how does the BA verify liveness claims weren't fabricated by a compromised appliance?" — provided the auditor kit surfaces:

(a) the heartbeat row's `signature_valid` status,
(b) the appliance's public key fingerprint at the time of the heartbeat,
(c) the canonical-payload format used (versioned, so format-drift is auditable).

P0 finding: **kit_version bump required.** Adding `signature_valid` to the heartbeat ledger surface in the auditor kit changes the cross-download determinism contract. kit_version must bump 2.1 → 2.2 across all 4 surfaces simultaneously per Session 218 round-table 2026-05-06 lockstep rule.

### Lens 3 — Coach (consistency + no double-build)

CONCERN — possible double-build with `signature_auth.py::verify_appliance_signature` (line 267). That verifier is per-request (sigauth headers, identity key). Heartbeat verification needs a SEPARATE function over the EVIDENCE key against canonical payload `site_id|MAC|ts|version`. Recommend `signature_auth.py::verify_heartbeat_signature(conn, site_id, mac, timestamp_unix, agent_version, signature_hex)` reusing `Ed25519PublicKey.from_public_bytes` + the pubkey-resolver pattern, BUT against `site_appliances.agent_public_key` (evidence), NOT `agent_identity_public_key` (sigauth). Single new function, ~30 LOC, no double-build of crypto primitives.

Existing prior art to NOT redo: `compute_compliance_score` canonicalization pattern, `admin_transaction` per-assertion isolation (Session 220), `_kit_zwrite` determinism contract.

### Lens 4 — Product manager (false-positive rate at scale)

CONCERN — sev1 paging on signature-invalid at fleet scale produces alert fatigue if key rotation isn't smooth. Recommend:

- **N-threshold:** 3 consecutive invalid signatures within 15 minutes (covers 3× 5-min checkin cadence). Single invalid sig = transient (clock skew, in-flight rotation) → do NOT page.
- **Lookback window for unsigned (sev2):** last hour (12 consecutive 5-min checkins). Catches version-rollback within 1 hour without flapping on a single missed checkin.
- **Suppression rule:** if `site_appliances.previous_agent_public_key_retired_at > NOW() - INTERVAL '15 minutes'`, suppress sev1 entirely (still log audit row) — rotation grace window.

False-positive math: ~50 appliances × 288 heartbeats/day = 14,400 heartbeats. At even a 0.01% false-invalid rate that's 1.4 false sev1s/day fleet-wide. The 3-consecutive gate compresses this to near-zero.

### Lens 5 — Attorney surrogate (Article 3.2 materialization)

APPROVE-CONDITIONAL on three artifacts: (a) backend verification ships, (b) substrate invariants ship + alert, (c) auditor kit surfaces `signature_valid` per heartbeat. Without (c), the v1.0-INTERIM master BAA Article 3.2 claim is "we sign heartbeats" — true but unfalsifiable from the auditor's seat. With (c), the claim becomes "we sign AND we verify AND the verification result is in the audit trail" — defensible.

NEVER ship verification without (c) — that's the same "code-true but runtime-false" antipattern from feedback_runtime_evidence_required_at_closeout (2026-05-09).

### Lens 6 — Medical-technical (clinic-side reality)

CONCERN — sev1 page at 3 AM for "heartbeat signature invalid" is meaningless to a clinic admin and dangerous to a non-technical operator (action: power-cycle the appliance? unplug it? call us?). The sev1 path MUST route to the MSP operator, NOT the clinic. The clinic-facing surface (PracticeHomeCard, client portal) MUST translate this into an opaque status ("Your IT partner has been notified — no action required from you") per the opaque-email-parity rule (Session 218 task #42).

P0: substrate-invariant runbook copy must specify operator-routing and explicitly state "DO NOT surface to clinic-facing channels."

### Lens 7 — Legal-internal (Maya + Carol — banned-word scan)

PASS for the invariant names + descriptions provided. Reject any runbook copy that says "ensures", "prevents", "guarantees", "protects", "100%". Acceptable framing: "detects", "helps identify", "monitors for", "surfaces". Required disclaimer in the invariant description: "Liveness signature verification is audit-supportive; appliance-side signing is best-effort and dependent on daemon version + key-availability."

---

## Recommended design

### Verification timing
- **Insert-time soft-verify** in `sites.py:4205-4230`: call `signature_auth.verify_heartbeat_signature(...)` opportunistically; populate `signature_valid` + `signature_verified_at`. Verification FAILURE does NOT block insert — row lands with `signature_valid=FALSE`, substrate engine pages on it.
- **Substrate sweep** re-verifies any `signature_valid IS NULL` rows from the prior tick (handles backend-side outage / pubkey-resolution races).

### Storage shape — Migration 313 (proposed)
```sql
ALTER TABLE appliance_heartbeats
    ADD COLUMN IF NOT EXISTS signature_valid BOOLEAN NULL,
    ADD COLUMN IF NOT EXISTS signature_verified_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS signature_canonical_format TEXT NULL;
    -- 'v1' = site_id|MAC|ts_unix|agent_version (daemon phonehome.go:838)
    -- Future format-drift bumps this.

ALTER TABLE site_appliances
    ADD COLUMN IF NOT EXISTS previous_agent_public_key TEXT NULL,
    ADD COLUMN IF NOT EXISTS previous_agent_public_key_retired_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_heartbeats_sigvalid_recent
    ON appliance_heartbeats(appliance_id, observed_at DESC)
    WHERE signature_valid IS NOT NULL;
```

### Canonical payload — frozen format v1
Backend MUST reconstruct: `{site_id}|{mac.upper()}|{timestamp_unix}|{agent_version}` from request fields; NEVER trust a daemon-supplied canonical string. `timestamp_unix` comes from request-arrival time on a +/- 60s skew window (daemon-supplied `ts` cannot be authoritative without replay-resistance which heartbeats don't have today).

**Open question for user-gate:** does the daemon need to ALSO send `req.HeartbeatTimestamp` in the request body so the backend has the EXACT integer the daemon signed over? Current phonehome.go:837 uses `time.Now().UTC().Unix()` at signing time, but the request body does not carry that integer separately — server-side `NOW()` will differ by tens of ms to seconds. **This is a blocking protocol gap.**

### Substrate invariant queries

```python
# daemon_heartbeat_unsigned (sev2) — 12 consecutive unsigned within 1h
async def _check_daemon_heartbeat_unsigned(conn) -> List[Violation]:
    rows = await conn.fetch("""
        SELECT sa.appliance_id, sa.site_id,
               COUNT(*) FILTER (
                   WHERE ah.agent_signature IS NULL
               ) AS unsigned_count,
               COUNT(*) AS total_count
        FROM site_appliances sa
        JOIN appliance_heartbeats ah USING (appliance_id)
        WHERE sa.agent_public_key IS NOT NULL
          AND sa.status = 'online'
          AND ah.observed_at > NOW() - INTERVAL '1 hour'
        GROUP BY sa.appliance_id, sa.site_id
        HAVING COUNT(*) FILTER (WHERE ah.agent_signature IS NULL) >= 12
           AND COUNT(*) >= 12
    """)
    return [Violation(site_id=r['site_id'], details={...}) for r in rows]

# daemon_heartbeat_signature_invalid (sev1) — 3 consecutive within 15min
async def _check_daemon_heartbeat_signature_invalid(conn) -> List[Violation]:
    # filter rotation grace
    rows = await conn.fetch("""
        SELECT sa.appliance_id, sa.site_id,
               COUNT(*) FILTER (WHERE ah.signature_valid = FALSE) AS invalid_count
        FROM site_appliances sa
        JOIN appliance_heartbeats ah USING (appliance_id)
        WHERE sa.agent_public_key IS NOT NULL
          AND (sa.previous_agent_public_key_retired_at IS NULL
               OR sa.previous_agent_public_key_retired_at < NOW() - INTERVAL '15 minutes')
          AND ah.observed_at > NOW() - INTERVAL '15 minutes'
        GROUP BY sa.appliance_id, sa.site_id
        HAVING COUNT(*) FILTER (WHERE ah.signature_valid = FALSE) >= 3
    """)
    return [Violation(site_id=r['site_id'], details={...}) for r in rows]
```

Both wrapped in `admin_transaction(pool)` per Session 220 commit `57960d4b` per-assertion isolation rule.

### Key rotation handling
On `agent_public_key` UPDATE in sites.py: copy old value → `previous_agent_public_key`, set `previous_agent_public_key_retired_at = NOW()`. Verifier tries new key first; falls back to previous within 15-min grace window. After grace, previous is cleared by a cleanup pass.

### CI lockstep gate
Canonical-payload format `v1 = site_id|MAC|ts_unix|agent_version` MUST stay in lockstep across **4 surfaces**:
1. `appliance/internal/daemon/phonehome.go:838`
2. `mcp-server/central-command/backend/signature_auth.py::verify_heartbeat_signature` (new)
3. Substrate invariant runbook documentation
4. Auditor kit verify.sh (when shipped)

Add `tests/test_heartbeat_canonical_format_lockstep.py` that greps for the format string in all 4 files (analogous to privileged-chain 3-list lockstep). Bumping format → bump `signature_canonical_format` column default + version-gate substrate invariants.

---

## Implementation order

1. **Mig 313** — add `signature_valid`, `signature_verified_at`, `signature_canonical_format`, `previous_agent_public_key`, `previous_agent_public_key_retired_at`. Backfill NULL — safe.
2. **signature_auth.py::verify_heartbeat_signature** — new function, reuses `_resolve_pubkey` shape but against `agent_public_key` (NOT `agent_identity_public_key`). Pinned by `tests/test_heartbeat_canonical_format_lockstep.py`.
3. **sites.py:4205-4230 insert-time soft-verify** — populate `signature_valid` at insert. Failure logged at WARNING, never raises.
4. **Daemon protocol gap fix** (BLOCKING for full correctness): add `HeartbeatTimestamp int64` to `CheckinRequest` proto. Without this, server-side timestamp reconstruction has +/- skew. **Decision required at user-gate before steps 5-6.**
5. **assertions.py** — add `_check_daemon_heartbeat_unsigned` + `_check_daemon_heartbeat_signature_invalid` + runbook stubs at `substrate_runbooks/daemon_heartbeat_*.md`.
6. **Auditor kit surface bump** — kit_version 2.1 → 2.2 across all 4 surfaces simultaneously; `signature_valid` column added to heartbeat ledger export.
7. **Key-rotation copy logic** in sites.py:4495-4540 — preserve old key on rotation.
8. **Backfill substrate sweep** — populate `signature_valid` for historical NULL rows (last 7 days only — older data is allowed to stay NULL).

Backend ships FIRST (Layer-2 safety-net pattern from Session 220 commit `3b2b8480`). Daemon protocol-gap fix ships SECOND. Substrate invariants ship THIRD (need data to fire on).

---

## Open questions reserved for user-gate

1. **Protocol gap (Q3 follow-up):** add `HeartbeatTimestamp` to `CheckinRequest`? Or accept +/- 60s skew via server-side reconstruction? Skew-tolerant verification works for the v1 canonical format but feels brittle.
2. **N-thresholds:** are 12-consecutive-unsigned/1h (sev2) and 3-consecutive-invalid/15min (sev1) acceptable, or does the PM want tighter at the cost of false-positive risk?
3. **Daemon force-rollout policy:** north-valley-branch-2 on v0.4.9 — does it have the signing key set, or is it a "appliance never enrolled" → sev2-info case? Need fleet inventory query before sev2 thresholds finalize.
4. **Rotation grace window:** 15 minutes proposed. Long enough for in-flight heartbeats; short enough for an attacker not to weaponize the old key.
5. **Kit-version surface:** four kit surfaces today (X-Kit-Version header + 3 JSON payloads). Bumping 2.1 → 2.2 requires lockstep — confirmed in Session 218 round-table; cite that explicitly in implementation commit.

---

## Final recommendation

**APPROVE-WITH-FIXES.**

The design materializes Rule 4 orphan-coverage and the v1.0-INTERIM master BAA Article 3.2 claim. Soft-verify-at-insert + substrate-sweep-as-gate is the right partition between checkin-availability and chain-attestability. However, FIVE P0s must close before any commit:

### Top 5 P0s

1. **P0 — Daemon vs DB canonical-format mismatch documented + verifier uses DAEMON format.** Mig 197's `heartbeat_hash` (DB trigger format) is NOT what the daemon signs over. New verifier MUST use `{site_id}|{MAC.upper()}|{ts_unix}|{agent_version}` from phonehome.go:838. Document explicitly to prevent future drift.

2. **P0 — `previous_agent_public_key` column + 15-min rotation grace.** Without this, the first key-rotation produces sev1 storm + alert fatigue. Mig 313 adds the column; verifier falls back during grace window.

3. **P0 — Daemon-supplied canonical-payload-string BANNED.** Backend reconstructs from request fields; rejects any daemon-provided canonical string. Document in verifier docstring + add a static check.

4. **P0 — Daemon `HeartbeatTimestamp` protocol gap.** Today the daemon signs `time.Now().UTC().Unix()` but the request body doesn't carry that integer. Server-side `NOW()` reconstruction has +/- ms-to-seconds skew that will mass-invalidate signatures. Either (a) add explicit `HeartbeatTimestamp` to `CheckinRequest`, OR (b) document the +/- 60s skew window in the verifier + accept the false-positive class. Decision required before steps 5-6 of implementation order.

5. **P0 — Auditor kit version bump 2.1 → 2.2 in lockstep across 4 surfaces.** New `signature_valid` heartbeat-ledger surface changes the determinism contract per Session 218 round-table 2026-05-06. All 4 kit surfaces bump together or NONE bump.

### Per-lens verdicts
- Lens 1 (Engineering): APPROVE-WITH-FIXES (P0s 1, 2, 3, 4)
- Lens 2 (HIPAA auditor): APPROVE-WITH-FIXES (P0 5)
- Lens 3 (Coach): APPROVE — no double-build
- Lens 4 (PM): APPROVE-WITH-FIXES (N-thresholds + rotation grace)
- Lens 5 (Attorney): APPROVE-CONDITIONAL (all 3 artifacts ship together)
- Lens 6 (Medical-technical): APPROVE-WITH-FIXES (operator-routing only; opaque clinic-facing)
- Lens 7 (Legal-internal): PASS (banned-word scan clean; runbook copy to be reviewed at Gate B)

**Overall: APPROVE-WITH-FIXES.** Proceed to implementation only after the 5 P0s above are addressed; Gate B fork required pre-completion per two-gate lock-in (Session 219 2026-05-11 extension).
