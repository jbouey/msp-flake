# Class-B 7-lens round-table — D1 HeartbeatTimestamp protocol decision

**Reviewer:** Fresh-context fork (Class-B, 7-lens, protocol-decision posture)
**Date:** 2026-05-13
**Scope:** P0 #4 from `audit/coach-d1-backend-verify-gate-a-2026-05-13.md` — daemon signs `site_id|MAC.upper()|ts_unix|agent_version` but request body does not carry `ts_unix`. Choose between (a) daemon protocol bump, (b) ±60s server skew-window, or (c) hybrid.
**Per-lens verdict:** see Lens 1–7 below — UNANIMOUS APPROVE-(c) HYBRID, with one P0.
**Overall recommendation:** **(c) HYBRID** with the protocol bump shipping immediately (low marginal cost) and the skew-window kept as the backward-compat backstop for the deprecation window.

---

## Counsel-rule binding + scope reminder

Rule 4 (orphan coverage) requires **attestability**, not synchronous enforcement. Whatever option we pick MUST allow the substrate engine + auditor kit to separate three classes deterministically:

- **Verified-via-daemon-supplied-ts** (path A — strongest evidence)
- **Verified-via-skew-window** (path B — accepted-with-disclosure)
- **Unverifiable** (sev1 or sev2 per Gate-A invariants)

If path B and "unverifiable" collapse into the same audit row, OCR has a defensible attack: *"how can you tell whether the BA verified the daemon's actual signing intent vs. a window-shaped guess?"*

---

## Lens 1 — Engineering (Steve, principal SWE)

**Verdict:** APPROVE-(c) HYBRID, but ship (a) FIRST and treat (b) as transitional backstop.

The daemon protocol bump cost is trivial in absolute terms:

```go
// CheckinRequest already has 23 optional fields. Adding HeartbeatTimestamp
// is one int64 + one omitempty tag.
HeartbeatTimestamp int64 `json:"heartbeat_timestamp,omitempty"`
```

Backend reads `checkin.heartbeat_timestamp` if present (`!= 0`); falls back to skew-window reconstruction otherwise. Both code paths exist in the verifier. New rows get `signature_canonical_format = 'v1a'` (daemon-supplied ts) or `'v1b'` (server-reconstructed) so substrate + auditor kit can distinguish.

**Why hybrid over pure (a):** the fleet at multi-device-enterprise scale will NOT upgrade in lockstep. CLAUDE.md confirms north-valley-branch-2 is on v0.4.9; if (a) is required, every v0.4.x heartbeat is sev1-unverifiable for the entire rollout window (days-to-weeks given the daemon's fleet-update cadence). That is a Steve-grade red flag — *fail-closed-by-default during a known rollout is an alert-fatigue self-inflicted wound*. Hybrid lets path B catch v0.4.x while path A immediately covers new builds.

**Why hybrid over pure (b):** "BA accepts unverifiable signature within ±60s window" is a defensible-but-weaker auditor narrative than "BA verifies daemon's actual signing timestamp." Once path A exists, every new daemon ships strongest-evidence; path B becomes named legacy that retires on a clock.

**First-boot / air-gapped edge case:** daemon `time.Now().UTC().Unix()` can be wildly wrong on first boot before NTP sync. Path A doesn't help here — the daemon will sign a 1970-epoch timestamp and the backend will record it faithfully. **Path A is NOT a clock-correctness mechanism; it is a tamper-evidence mechanism.** Solution: substrate invariant `daemon_clock_skew_excessive` flags any heartbeat where `|daemon_ts - server_arrival| > 5 minutes` regardless of verification outcome. That's a separate invariant from the verification invariants — out of scope for this decision, in scope for a follow-up.

**P0 from this lens:** the verifier MUST log the `signature_canonical_format` it used at verify time. Without that, post-hoc auditing cannot tell whether a `signature_valid=TRUE` row was path A or path B. Mig 313 already adds `signature_canonical_format TEXT NULL` — make it NOT NULL on insert in `sites.py:4205-4230` (default `'v1b-reconstruct'`, override to `'v1a-daemon'` when `checkin.heartbeat_timestamp > 0`).

## Lens 2 — CCIE-grade network/protocol engineer

**Verdict:** APPROVE-(c). The hybrid pattern is textbook protocol-version evolution.

Three recognized patterns for fleet protocol evolution:
1. **HTTP `User-Agent`-style** version sniffing (string compare, brittle) — REJECT.
2. **gRPC field-presence** (proto3 optional / hasField) — the JSON `omitempty` equivalent is *exactly* path A's design: presence of `heartbeat_timestamp` ≠ 0 signals new-protocol daemon. ACCEPT.
3. **Schema versioning header** (`X-Protocol-Version: 2`) — overkill for a single field addition; appropriate for breaking-change boundaries. REJECT for this scope.

Pattern (2) is what the existing `AgentIdentityPublicKey` field used in Session 218 mig 251 — same precedent (`omitempty`, server tolerates absence, falls back to older behavior). Lens 6 (Coach) calls this out as anti-double-build. The decision is *already-established platform convention*; option (a) is the "this is how we do protocol additions on this platform" answer.

**Skew-window calibration (the load-bearing parameter):** ±60s is conservative. Typical NTP-synced fleet skew is sub-second; the long tail is unsynced/drifting clocks at <±30s. ±60s costs ~120 Ed25519 verify attempts × ~25µs/verify on modern CPUs ≈ ~3ms per checkin worst-case. With 50 appliances × 288 checkins/day = 14,400 checkins/day, that's ~43 seconds/day of compute fleet-wide on path B. **Negligible.**

But: do NOT calibrate to ±60s blindly. The substrate engine has heartbeat data — run a one-time analysis post-mig-313 over `appliance_heartbeats` joined to the daemon `heartbeat_timestamp` field (once path A daemons start reporting) and compute the actual p99 skew. If p99 < 10s, tighten to ±30s. If p99 > 30s, you have a clock-management problem and ±60s is masking it.

**Replay-resistance gap (orthogonal but worth noting):** neither (a) nor (b) makes heartbeats replay-resistant — an attacker who captures one signed heartbeat can replay it within the timestamp validity. This is a known limitation of the heartbeat signature (it's *liveness attestation*, not *request authentication* — that's sigauth's job). Path A makes it marginally easier to replay because the attacker doesn't need to guess the server's clock. Mitigation: substrate invariant on `(appliance_id, heartbeat_timestamp)` uniqueness — duplicate ts within a 1h window = replay. **Add to Gate A scope.** This raises the priority of the canonical-format-format column carrying the actual ts integer (which mig 313 should add: `signature_timestamp_unix BIGINT NULL`).

## Lens 3 — HIPAA auditor surrogate

**Verdict:** APPROVE-(c) — the hybrid SURVIVES OCR scrutiny in a way that pure (b) does not.

OCR's expected line of questioning on each option:

**Option (a) pure:** *"How does your BA verify heartbeats from appliances that pre-date the protocol upgrade?"* — answer is "we treat them as unverifiable and page our operators." Defensible IF the unverifiable class is small + temporally bounded. **Risk: if the upgrade stretches >30 days, the unverifiable class becomes a chronic gap, not a transitional one.**

**Option (b) pure:** *"How does your BA know it verified the daemon's actual signing intent, not a same-looking signature within your acceptance window?"* — answer is "the window is small and Ed25519 is collision-resistant; the probability of a coincidental valid signature within ±60s is negligible." TECHNICALLY defensible, but *narratively* weaker: the auditor hears "we widened the cryptographic gate to accept things we weren't sure about." **Risk: OCR's lay-reader view is that BAs should NOT loosen cryptographic verification.**

**Option (c) hybrid:** *"How does your BA verify heartbeats?"* — answer is "for daemons supporting the v1a protocol (~all current builds within N days of feature ship), we verify against the daemon's exact signing timestamp. For pre-v1a legacy daemons during the deprecation window, we accept signatures within a documented ±60s clock-skew window, with the verification method recorded per-row in the audit trail." This is *defensible AND narratively strong* — explicit, dated, retired-on-a-clock.

**P0:** the auditor kit MUST surface `signature_canonical_format` per heartbeat. Path A vs path B is the load-bearing audit distinction. Without that column in the kit export, the hybrid's narrative advantage evaporates. (Gate-A P0 #5 — kit_version bump 2.1 → 2.2 — already covers this; reaffirmed here.)

## Lens 4 — Product manager (fleet rollout coordination)

**Verdict:** APPROVE-(c). Pure (a) is a customer-experience disaster during the rollout window.

Pure (a) timeline:
- Day 0: backend ships path A. All v0.4.x appliances = sev1 (per Gate A invariant `daemon_heartbeat_signature_invalid`). North-valley-branch-2 alone fires 3×288 = 864 sev1 events/day.
- Day 0-7: daemon protocol bump v0.5.0 builds + smoke-tests.
- Day 7-30: rolling fleet update via existing `update_daemon` fleet order infra. CLAUDE.md notes appliances at customer sites — update cadence is days-to-weeks.

**Pure (a) = 7-30 days of sev1 alert storm.** Operator NPS collapses. Customer-facing surfaces (PracticeHomeCard) show "verification pending" for weeks if we propagate the signal. Unacceptable.

Pure (b):
- Day 0: backend ships path B. All current appliances verify (assuming clocks within ±60s, which is the case for any NTP-synced fleet).
- No daemon work, no rollout coordination.

**Pure (b) is fastest-to-coverage but auditorally weakest** (Lens 3). And the deprecation never happens, because "deprecate the only verification path" is impossible.

Hybrid (c):
- Day 0: backend ships BOTH paths, gates on `heartbeat_timestamp` field presence. All current appliances verify via path B (immediate coverage). New daemons immediately upgrade to path A.
- Day 0-30: rolling daemon update via existing fleet-order infra. As each appliance upgrades, it auto-promotes to path A — no per-appliance config change.
- Day 30: substrate invariant `daemon_on_legacy_path_b` (sev3-info) lights up any appliance still on path B after grace. Operator-actionable.
- Day 60: invariant escalates to sev2 (the deprecation deadline).

**Hybrid is the only option that ships immediate coverage + auditor-grade verification without rollout-window alert fatigue.**

**Customer-experience guidance:** during the transition window, the client-portal/PracticeHomeCard MUST NOT differentiate between path A and path B. Both look like "verified" to the clinic. Path-distinction is an MSP-operator + auditor concern (opaque-mode parity rule, Session 218 task #42).

## Lens 5 — Medical-technical (clinic-side reality)

**Verdict:** APPROVE-(c) NO clinic-side noise.

Per Lens 4: hybrid means zero customer-visible change during rollout. Clinics see "verified" throughout. The only clinic-side risk is if path B fails for clock-skew reasons (e.g., a clinic with a broken NTP setup drifting >60s). In that case the appliance flips to "unverifiable" (sev1) — but this routes to the MSP operator per Gate-A Lens 6 P0, NOT to the clinic. Clinic message stays "Your IT partner has been notified."

Edge case: a clinic appliance whose CMOS battery dies and clock resets to 2000-01-01 on reboot. Path A faithfully records the wrong timestamp; path B fails the ±60s window. Either way the substrate engine should fire `daemon_clock_skew_excessive` (Lens 1 P0 follow-up). This is GOOD — we WANT to detect dead-CMOS-battery as an operator-actionable signal.

## Lens 6 — Coach (consistency + no over-engineering + no double-build)

**Verdict:** APPROVE-(c) — the hybrid is the documented platform pattern, NOT a new invention.

Precedent in the codebase for the exact `omitempty` field-presence-versioning pattern:
- `AgentIdentityPublicKey` (Session 218 mig 251 — server tolerates absence, older daemons silently use evidence key as fallback)
- `BootSource` (added without protocol bump; server tolerates NULL)
- `BootCounter`, `GenerationUUID`, `ReconcileNeeded` (Session 205 Phase 2 reconcile — same pattern)
- `AgentPublicKey` itself was added this way in Session 196

**This is THE platform's protocol-evolution convention.** Option (a) is literally the existing pattern; the only novelty is the second code path on the verifier. That second path is ALSO consistent with the platform's tolerance pattern (server gracefully handles old-daemon shape). Hybrid is the *least-novel* option of the three.

**Double-build risk check:** is there an existing skew-tolerant signature verifier on the platform that hybrid would duplicate? Searched — no. `signature_auth.py` is exact-payload (single canonical form, no time window). Evidence-bundle verification is exact-payload. Sigauth header signing is exact-payload (with separate replay protection via `X-Sigauth-Timestamp` if it existed — but it doesn't in current code, which is a separate concern). The ±60s window in path B is genuinely new code, but it's ~15 LOC inside a single function. Not double-build.

**No over-engineering check:** is hybrid's transition-window tracking worth the implementation cost? The substrate-invariant `daemon_on_legacy_path_b` is ~20 LOC over an existing pattern. The `signature_canonical_format` column is already in mig 313 from Gate A. The cost ABOVE pure (a) or pure (b) is one column-value-population and one substrate invariant. **Not over-engineering.**

## Lens 7 — Substrate-engineering

**Verdict:** APPROVE-(c) with a concrete invariant shape.

For hybrid, substrate engine needs to distinguish:
1. **Daemon legitimately on path B during deprecation window** — informational, NOT a violation.
2. **Daemon misconfigured / signing-loop-bug / regression** — that's the existing Gate-A `daemon_heartbeat_unsigned` (sev2) catch.
3. **Daemon stuck on path B past deprecation deadline** — new invariant `daemon_on_legacy_path_b` (sev3 → sev2 escalation on a clock).

Concrete shape:

```python
async def _check_daemon_on_legacy_path_b(conn) -> List[Violation]:
    # Deprecation deadline configurable; defaults to 60 days post-mig-313.
    DEPRECATION_DEADLINE = await conn.fetchval("""
        SELECT (created_at + INTERVAL '60 days')
        FROM schema_migrations WHERE version = 313 LIMIT 1
    """)
    now = await conn.fetchval("SELECT NOW()")
    severity = 'sev2' if now > DEPRECATION_DEADLINE else 'sev3-info'

    rows = await conn.fetch("""
        SELECT sa.appliance_id, sa.site_id, sa.agent_version,
               COUNT(*) FILTER (
                   WHERE ah.signature_canonical_format = 'v1b-reconstruct'
               ) AS path_b_count,
               COUNT(*) AS total_count
        FROM site_appliances sa
        JOIN appliance_heartbeats ah USING (appliance_id)
        WHERE sa.agent_public_key IS NOT NULL
          AND ah.observed_at > NOW() - INTERVAL '1 hour'
          AND ah.signature_valid = TRUE
        GROUP BY sa.appliance_id, sa.site_id, sa.agent_version
        HAVING COUNT(*) FILTER (
                   WHERE ah.signature_canonical_format = 'v1b-reconstruct'
               ) > 0
    """)
    return [Violation(severity=severity, ...) for r in rows]
```

Key invariants in this shape:
- Uses `ah.signature_valid = TRUE` filter — appliance is verifying, just on legacy path. Distinguishes from `daemon_heartbeat_signature_invalid`.
- `agent_version` in the row payload — operator knows which version to push the update to.
- Deadline-aware severity escalation — no operator action required during grace window; escalates automatically after deprecation.

**This shape is impossible under pure (a) or pure (b)** — pure (a) has nothing to distinguish (path A or nothing); pure (b) has nothing to deprecate to. Only hybrid creates the dimensional separation the substrate engine needs.

**P0 from this lens:** the substrate invariant `daemon_on_legacy_path_b` runbook MUST document the deprecation deadline + the operator action ("push fleet-order update_daemon to agent_version ≥ v0.5.0"). Without that, operators get an alert with no clear remediation step (which violates the `l1_resolution_without_remediation_step` substrate invariant by analogy — Session 220 commit `3f0e5104`).

---

## Quantitative analysis

### Clock-skew distribution assumption

NTP-synced fleet: p50 < 1s, p99 < 5s, p99.9 < 30s. NTP-unsynced (rare but observed): p99 can exceed 60s within 24h of clock drift.

The substrate engine has 90 days of `appliance_heartbeats.observed_at` data. Once path A ships, the actual daemon-vs-server skew distribution can be computed in one query:

```sql
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY skew_seconds) AS p50,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY skew_seconds) AS p99,
       MAX(skew_seconds) AS p_max
FROM (
    SELECT EXTRACT(EPOCH FROM (observed_at - to_timestamp(signature_timestamp_unix))) AS skew_seconds
    FROM appliance_heartbeats
    WHERE signature_canonical_format = 'v1a-daemon'
      AND observed_at > NOW() - INTERVAL '7 days'
) sub;
```

**Recommendation:** ship hybrid with ±60s. After 7 days of path-A data, recalibrate. If p99 < 10s, tighten path B to ±30s (still margin for slow paths). DO NOT calibrate before data exists — pre-judging the distribution is the over-engineering trap Lens 6 warns about.

### Verify-attempt cost on path B

±60s window = 121 timestamp candidates × Ed25519-verify cost.

Ed25519 verify benchmark on modern x86_64: ~25µs per verify (libsodium, single-thread). 121 × 25µs = ~3.0ms worst-case per checkin (signature actually at edge of window). Average case (signature near server's `NOW()`): ~25-50µs (1-2 attempts).

Fleet load: 50 appliances × 288 checkins/day = 14,400 verify operations/day. Worst-case path B = 14,400 × 3ms = 43 seconds/day fleet-wide. **Negligible.**

Path A verify: single attempt = ~25µs × 14,400 = 0.36 seconds/day. Even more negligible.

Hybrid total compute cost during the transition window (assume 50% on each path): ~20 seconds/day. **Below detection threshold for any reasonable monitoring.**

### Transition-window cost

| Phase | Days | Path-A fraction | Path-B fraction | Substrate noise |
|-------|------|-----------------|-----------------|-----------------|
| Phase 0 (pre-ship) | -∞ to 0 | 0% | N/A (no verification at all) | 100% orphan-coverage gap (Rule 4 violation) |
| Phase 1 (post-mig-313) | 0 to 7 | 0% | 100% | Zero (path B accepted, no `legacy_path_b` invariant yet) |
| Phase 2 (daemon v0.5 ships) | 7 to 14 | 20% (new appliances) | 80% | Zero |
| Phase 3 (fleet rollout) | 14 to 60 | 50%→95% | 50%→5% | sev3-info on remaining path-B appliances |
| Phase 4 (post-deadline) | 60+ | ~100% | <1% | sev2 on any remaining path-B (laggards, frozen fleet members) |

This curve is the *normal shape of a healthy protocol deprecation*. Pure (a) skips phases 1-3 and goes from "100% gap" to "100% verified" in zero time — except it doesn't, because the fleet doesn't actually upgrade in zero time, so what really happens is phases 1-3 with `daemon_heartbeat_signature_invalid` SEV1 paging instead of `daemon_on_legacy_path_b` SEV3-INFO. **Same physical reality, dramatically worse alert posture.**

---

## Sibling parity check

Searched the platform for time-window-based signature verification precedent. **CRITICAL FINDING that changes the recommendation's strength:**

- **`signature_auth.py` (sigauth headers) — PRIOR ART FOR EXACTLY THIS PATTERN.** Reads as definitive precedent:
  - `MAX_CLOCK_SKEW = 60 seconds` (signature_auth.py:71, comment: *"Acceptable clock skew between daemon and server. 60s window."*)
  - Daemon sends explicit `X-Appliance-Timestamp` header (phonehome.go:1177) with RFC3339-second-Z UTC
  - Daemon sends `X-Appliance-Nonce` (32-hex, replay-resistance) (phonehome.go:1178)
  - Server checks timestamp within ±60s of `NOW()` (signature_auth.py:297-314, `reason="clock_skew"`)
  - Canonical signing form is `method|path|body_sha256|ts_iso|nonce` — server reconstructs EXACTLY this from request fields + headers (signature_auth.py:122)
  - The timestamp is **daemon-supplied** (path A model), and the server **also validates** that it's within a ±60s window (path B model — but as a freshness check, not a skew-tolerant signature acceptance).

  **This is what hybrid (c) does, with one twist:** sigauth combines *daemon-supplied-ts (path A)* with *server-side validation of that ts (±60s freshness check)*. It does NOT fall back to server-NOW reconstruction if the ts is missing — it rejects. The reason it can be strict: sigauth was designed with the daemon-supplied-ts from day one. **D1 heartbeat is fixing a design gap that sigauth didn't have** — heartbeat ships v0 with daemon-supplied ts MISSING, so we either rev daemon (path A) or reconstruct (path B).

- **`signature_auth.py` also has `nonce` replay protection** (HDR_NONCE, NONCE_RE, `_nonce_seen`/`_record_nonce`). Heartbeat today has NO nonce → replay-resistance gap. **Lens 2's call to add `(appliance_id, ts)` uniqueness substrate invariant is the cheap version of this.** A future hardening pass should add `X-Heartbeat-Nonce` to mirror sigauth's full pattern; out of D1 scope.

- **Evidence-bundle verification (`compliance_bundles.signature_verified_at`)**: exact-payload, no time window. The signed canonical form is the bundle body which is timestamp-immutable post-creation.

- **OTS-anchor verification**: exact-payload over the Merkle root; the Bitcoin block timestamp provides external time witness, no skew-window needed.

- **Vault Transit signing**: exact-payload (Vault signs the request hash, no embedded timestamp).

- **HIPAA-counsel-signed feature flags (mig 281+282)**: exact-payload signature over the flag-toggle attestation bundle.

### Sibling-parity implication for the decision

The sigauth precedent **STRONGLY REINFORCES (c) HYBRID** with a specific structural choice:

- **Path A (daemon-supplied `heartbeat_timestamp`):** matches sigauth's `X-Appliance-Timestamp` design exactly. Same canonical-payload-includes-ts shape. Same daemon `time.Now().UTC().Unix()` source. Treating heartbeat-ts as a NEW field on `CheckinRequest` body (instead of a new header) is JSON-convention-consistent for body-internal fields; sigauth uses headers because the signature covers method+path+body+ts+nonce (a request-authentication shape, not a body-content shape). Heartbeat's canonical form IS body content, so JSON field is right.

- **Path B (server-reconstructed ±60s):** has NO sibling precedent on this platform. Sigauth uses ±60s as a *freshness check on a known ts*, not as a *signature-acceptance window via brute-force candidates*. D1 path B is genuinely novel.

- **Implication:** hybrid's path A "looks like sigauth" — auditor-defensible by analogy ("we use the same liveness-attestation pattern we use for request authentication"). Path B is named legacy, transitional, retires on a clock. The hybrid is **even more enterprise-defensible than the initial round-table thought**, because path A has on-platform precedent and path B has a documented retirement deadline.

- **Coach update:** sigauth uses ±60s = 60. D1 hybrid path B should use ±60s for parity. Coach's "no double-build" verdict gets reinforced — the constant is platform-consistent.

The closest cousin in the codebase for the rotation-grace pattern is the `previous_agent_public_key` rotation grace from Gate A P0 #2 — which is ALSO a time-window-tolerant verification pattern (try new key, fall back to previous within 15 min). That precedent justifies the hybrid approach: *we already accept that key-rotation needs a grace window; clock-rotation by analogy can have one too.*

---

## Recommended option + rationale

# **Option (c) HYBRID — UNANIMOUS APPROVE across 7 lenses.**

### Why hybrid wins on every dimension that matters

| Dimension | (a) pure | (b) pure | (c) hybrid |
|-----------|----------|----------|------------|
| Immediate fleet coverage | ❌ (rollout window unverified) | ✅ | ✅ |
| Auditor-grade narrative | ✅ | ⚠️ "loosened gate" | ✅ + transition documented |
| Customer experience during rollout | ❌ alert storm | ✅ | ✅ |
| Substrate-engine actionability | weak (binary) | weak (no deprecation) | strong (3-state) |
| Platform-convention alignment | ✅ | ⚠️ skew is novel | ✅ (uses `omitempty` precedent) |
| Long-term maintainability | ✅ | ❌ permanent transitional state | ✅ (skew retires on deadline) |
| Implementation complexity | low | low | ~20 LOC above baseline |

### Hybrid is genuinely the lowest-cost option

The narrative "hybrid is more complex than (a) or (b)" is wrong at the *system* level. The complexity at the *code* level is +20 LOC. The complexity *removed at the operations level* is "weeks of sev1 alert storm" (vs pure (a)) or "permanent skew-window in our cryptographic story" (vs pure (b)). Both of those operational costs dwarf 20 LOC.

---

## Implementation order (post-decision)

1. **Mig 313 amended:** add `signature_timestamp_unix BIGINT NULL` AND `signature_canonical_format TEXT NULL DEFAULT 'v1b-reconstruct'` to `appliance_heartbeats`. (The original Gate A mig 313 listed `signature_canonical_format` already; adding the unix timestamp field too.)
2. **`signature_auth.py::verify_heartbeat_signature`:** dual-path verifier. If `daemon_supplied_ts is not None`, single-attempt verify against that integer (path A). Else iterate ±60s window (path B). Returns `(valid: bool, canonical_format: 'v1a-daemon'|'v1b-reconstruct', ts_used: int)`.
3. **`sites.py:4205-4230`:** insert-time soft-verify call; populate all three new columns from verifier return.
4. **Daemon protocol bump:** `CheckinRequest.HeartbeatTimestamp int64 \`json:"heartbeat_timestamp,omitempty"\``, populated in `SystemInfoSigned()` at phonehome.go:837 — same line that already computes `ts`, just store it on `req.HeartbeatTimestamp` before signing. Daemon ships as v0.5.0.
5. **CI lockstep gate** (`tests/test_heartbeat_canonical_format_lockstep.py`): canonical-form string is identical across daemon, backend verifier, substrate invariants, auditor kit verify.sh. Already required by Gate A — extend to cover the NEW column name + the `v1a` vs `v1b` enum.
6. **Substrate invariant `daemon_on_legacy_path_b`:** new (sev3-info pre-deadline, sev2 post). Shape per Lens 7. Runbook stub at `substrate_runbooks/daemon_on_legacy_path_b.md` (operator action: "push update_daemon fleet order for agent_version ≥ v0.5.0").
7. **Auditor kit bump 2.1 → 2.2:** include `signature_canonical_format` + `signature_timestamp_unix` + `signature_valid` in heartbeat-ledger export. ALL FOUR surfaces in lockstep per Session 218 round-table 2026-05-06 contract.
8. **Post-7-day skew analysis:** run the percentile_cont SQL above; if p99 < 10s, mig-314 tightens path B to ±30s.

Backend ships steps 1-3 + 5-7 FIRST (matches Session 220 commit `3b2b8480` Layer-2 safety-net rule — backend Layer 2 ships before daemon fleet update). Daemon step 4 ships SECOND. Step 6 substrate invariant ships THIRD (needs data to fire on). Step 8 post-deploy +7d.

---

## Gate B prerequisites

Gate B fork MUST verify:

1. **CI lockstep gate green** — canonical form + format-enum identical across all 4 surfaces.
2. **Substrate invariant runs cleanly post-deploy** — `daemon_on_legacy_path_b` lights up on north-valley-branch-2 (v0.4.9) at sev3-info as expected; does NOT fire on any v0.5+ appliance.
3. **Auditor kit determinism preserved** — two consecutive downloads byte-identical (Session 218 determinism contract); `signature_canonical_format` populated per row.
4. **Banned-word scan** — runbook copy + invariant descriptions + auditor-kit README narrative all pass `tests/test_auditor_kit_banned_words.py` (no "ensures/prevents/guarantees/protects/100%").
5. **Per-lens P0 closure documented in commit body** — each P0 from this round-table cited + closed.
6. **Pre-push full-sweep green** — `.githooks/full-test-sweep.sh` (~92s) per Session 220 lock-in 2026-05-11 — Gate B reviews DIFF + RUNS sweep.
7. **Runtime evidence** — curl `/api/appliances/checkin` from a v0.4.x daemon + a v0.5.0 daemon; psql `SELECT signature_canonical_format, signature_valid FROM appliance_heartbeats ORDER BY observed_at DESC LIMIT 10` showing both `'v1a-daemon'` and `'v1b-reconstruct'` rows post-deploy. Per `feedback_runtime_evidence_required_at_closeout` (2026-05-09).

---

## Per-lens verdict summary

| Lens | Verdict |
|------|---------|
| 1 — Engineering (Steve) | APPROVE-(c) + P0: log `signature_canonical_format` per row |
| 2 — CCIE protocol engineer | APPROVE-(c) + recalibrate ±60s after 7d data |
| 3 — HIPAA auditor surrogate | APPROVE-(c) + P0: kit_version bump (reaffirmed from Gate A) |
| 4 — Product manager | APPROVE-(c) — only option avoiding rollout-window alert storm |
| 5 — Medical-technical | APPROVE-(c) — zero clinic-facing differentiation |
| 6 — Coach | APPROVE-(c) — matches existing `omitempty` field-presence precedent |
| 7 — Substrate-engineering | APPROVE-(c) + P0: `daemon_on_legacy_path_b` invariant + runbook |

**Overall: APPROVE-(c) HYBRID, unanimous.** Three P0s from this round-table:
- P0-H1: `signature_canonical_format` populated NOT NULL on every insert (Lens 1)
- P0-H2: `daemon_on_legacy_path_b` substrate invariant + runbook with operator-action step (Lens 7)
- P0-H3: Auditor-kit surfaces `signature_canonical_format` + `signature_timestamp_unix` (Lens 3, reaffirms Gate-A P0 #5)

Proceed to implementation. Gate B fork required pre-completion per two-gate lock-in (Session 219 extension 2026-05-11).
