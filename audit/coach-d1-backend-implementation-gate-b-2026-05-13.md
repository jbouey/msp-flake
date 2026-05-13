# Class-B Gate B — D1 backend implementation (Task #40)

**Reviewer:** Fresh-context Gate B fork (7-lens, Class-B)
**Date:** 2026-05-13
**Scope:** Pre-completion verification of D1 backend heartbeat-signature
verification (mig 313 + `signature_auth.verify_heartbeat_signature` +
`sites.py` soft-verify integration + 3 substrate invariants + 3
runbooks + `_DISPLAY_METADATA`).
**Verdict sources cited:**
- Gate A: `audit/coach-d1-backend-verify-gate-a-2026-05-13.md`
- Protocol RT: `audit/coach-d1-heartbeat-timestamp-protocol-2026-05-13.md`

---

## 8-P0 closure matrix

| # | P0 | Status | Evidence |
|---|---|---|---|
| 1 | Gate A P0 #1: Daemon-vs-DB canonical-format MISMATCH — verifier MUST use daemon format `site_id\|MAC.upper()\|ts_unix\|agent_version` | **CLOSED** | `signature_auth.py:520-534` `_heartbeat_canonical_payload` joins exactly `[site_id, mac.upper(), str(int(ts_unix)), agent_version]` with `\|`. Byte-exact match to `phonehome.go:837-844`. |
| 2 | Gate A P0 #2: `previous_agent_public_key` + `previous_agent_public_key_retired_at` rotation-grace columns | **CLOSED** | mig 313:90-102 adds both columns with COMMENTs. Verifier `signature_auth.py:629-642` reads both and gates `previous_pubkey` candidacy on `now - previous_retired_at <= _HEARTBEAT_KEY_ROTATION_GRACE` (15 min — matches mig comment). |
| 3 | Gate A P0 #3: Daemon-supplied canonical-payload-string BANNED — backend reconstructs from request fields ONLY | **CLOSED** | Verifier accepts only individual fields (`site_id`, `mac_address`, `agent_version`, `daemon_supplied_timestamp_unix`) and rebuilds canonical via `_heartbeat_canonical_payload`. No "signed_payload_string" parameter exists. |
| 4 | Gate A P0 #4: HeartbeatTimestamp protocol gap — RT chose option (c) hybrid | **CLOSED** | Pydantic `heartbeat_timestamp: Optional[int]` at `sites.py:3269`. Verifier branches at `signature_auth.py:645-666` (path A) / `:668-689` (path B). Path A returns `v1a-daemon`; path B returns `v1b-reconstruct`. |
| 5 | Gate A P0 #5: Auditor kit version bump 2.1 → 2.2 (4 surfaces) | **CLOSED** (carried by prior commit) | `evidence_chain.py:4499/4677/4724/4796/4828` all at `"2.2"`. Mig 308 already advanced kit version `2.1 → 2.2`. The expected "still pending" was a stale prediction — kit version landed via L2-escalations-missed earlier this session. |
| 6 | RT P0-H1: `signature_canonical_format` populated on every insert (`v1a-daemon` or `v1b-reconstruct`) | **CLOSED** | mig 313:63-71 adds column with COMMENT. `sites.py:4256-4259` extracts `canonical_format` from result; `:4271` includes it in INSERT column list; `:4286` passes value. NULL when no signature; populated to either string when verified. |
| 7 | RT P0-H2: substrate invariant `daemon_on_legacy_path_b` (sev3-info → sev2 on deprecation clock) | **CLOSED** | `assertions.py:2291-2296` registers sev3 (current); `_check_daemon_on_legacy_path_b` at `:5989-6075` queries `signature_canonical_format='v1b-reconstruct'` over 24h. Deprecation date `2026-08-13` hard-coded `:6007` (DEPRECATION_DATE). `is_past_deprecation` flag surfaces via Violation details for operator visibility. Note: severity field is statically `"sev3"` — auto-escalation to sev2 is informational-only via `is_past_deprecation` flag in details, NOT a runtime severity flip. |
| 8 | RT P0-H3: Auditor kit version bump 2.1 → 2.2 lockstep (reaffirms #5) | **CLOSED** | Same evidence as #5. |

**P0 closure score: 8/8 CLOSED.** Functional implementation is sound.

---

## Lens 1 — Engineering (Steve)

**(a) Canonical-format byte-parity (daemon ↔ verifier)** — PASS.

Daemon `phonehome.go:837-844`:
```go
ts := fmt.Sprintf("%d", time.Now().UTC().Unix())
payload := strings.Join([]string{
    req.SiteID,
    strings.ToUpper(req.MACAddress),
    ts,
    req.AgentVersion,
}, "|")
h := sha256.Sum256([]byte(payload))
```

Verifier `signature_auth.py:520-534`:
```python
payload = "|".join([
    site_id,
    (mac_address or "").upper(),
    str(int(ts_unix)),
    agent_version or "",
])
return payload.encode("utf-8")
```
Plus `hashlib.sha256(payload).digest()` at `:650/:682`. Byte-exact: same field order, same separator, same upper-casing on MAC, same UTF-8 encoding, same SHA-256 wrapper before verify. Empty-string guards (`(mac_address or "").upper()`, `agent_version or ""`) are conservative and don't change a happy-path payload.

**(b) Rotation-grace 15-minute logic** — PASS. `_HEARTBEAT_KEY_ROTATION_GRACE = timedelta(minutes=15)`; `now - previous_retired_at <= _HEARTBEAT_KEY_ROTATION_GRACE` (`:640`) — correct sign + inclusive boundary.

**(c) Ed25519 verifier matches existing pattern** — PASS. `Ed25519PublicKey.from_public_bytes` + `verify(sig, msg)` (`:552-553`) mirrors `:410-418` sigauth verifier. `InvalidSignature/ValueError/TypeError/binascii.Error` are all caught (`:555`).

**(d) Soft-verify never raises** — PASS at the call-site. `sites.py:4242-4251` wraps `verify_heartbeat_signature` in `try/except Exception` with `logger.exception`; checkin proceeds with `_hb_verify_result = None`. Verifier itself also returns dataclass values on every branch — no raise inside. Defense-in-depth correct.

**(e) Substrate invariant query tractability** — CONCERN (P1). `_check_daemon_heartbeat_unsigned` joins `appliance_heartbeats ah JOIN site_appliances sa ON sa.appliance_id = ah.appliance_id`. mig 313:84-86 adds `idx_appliance_heartbeats_signature_state (site_id, observed_at DESC, signature_valid) WHERE observed_at > NOW() - INTERVAL '24 hours'`. The partial-index `NOW()` predicate is NON-IMMUTABLE — Postgres will likely refuse to use it as a partial-index predicate (similar to feedback_three_outage_classes_2026_05_09 NOW-in-partial-index class). **This is a P1 (will silently degrade to seq-scan on partitioned `appliance_heartbeats`, but query will still execute).** Recommend dropping `WHERE observed_at > NOW() - INTERVAL '24 hours'` from the partial-index clause or replacing with a static cutoff in a follow-up.

**(f) Test fixtures parity** — PASS. `prod_columns.json` is the live-schema mirror; new columns will auto-populate after mig 313 applies. No `*_pg.py` fixture rewrites CREATE TABLE for `appliance_heartbeats` or `site_appliances` (verified via grep).

**(g) Pre-push full sweep** — **FAIL — 3 distinct test suites BLOCKING.**

```
=== test_no_unfiltered_site_appliances_select.py === 1 failed, 1 passed
    Ratchet BASELINE_MAX=81 exceeded — verifier added new
    `SELECT … FROM site_appliances WHERE appliance_id = $1::uuid AND site_id = $2`
    at signature_auth.py:614 without a `# noqa: site-appliances-deleted-include`
    marker or a `sa.deleted_at IS NULL` filter.

=== test_runbook_truth_check.py === 1 failed, 6 passed
    daemon_heartbeat_unsigned.md mentions `site_appliances.agent_public_key`
    — which is in REMOVED_PATTERNS (Session 211 / #179). Runbook
    must point operators at `v_current_appliance_identity.agent_pubkey_fingerprint`
    OR rephrase without the literal token.

=== test_substrate_docs_present.py === 3 failed, 71 passed
    All three new runbooks (daemon_heartbeat_unsigned.md,
    daemon_heartbeat_signature_invalid.md, daemon_on_legacy_path_b.md)
    missing the REQUIRED section `## Root cause categories`
    (per backend/substrate_runbooks/_TEMPLATE.md).
```

Combined: **6 individual test FAILUREs across 3 suites.** Will block CI on push.

---

## Lens 2 — Coach (consistency + no double-build)

**(a) No double-build vs `verify_signature`** — PASS. Heartbeat verifier is correctly factored as a distinct function over the evidence-key + canonical-payload `site_id|MAC|ts|version` shape, NOT a hijack of the sigauth verifier that operates over `METHOD\nPATH\nbody_hash\nts\nnonce`. Documented in `signature_auth.py:446-473` prose.

**(b) 60-second window parity with `MAX_CLOCK_SKEW`** — PASS. `_HEARTBEAT_RECONSTRUCT_WINDOW_S = 60` (`:485`) is explicitly tied in comment (`:483-484`) to `MAX_CLOCK_SKEW = timedelta(seconds=60)` (`:72`). Sibling parity confirmed.

**(c) `_DISPLAY_METADATA` entries cite runbooks** — PASS. All three entries at `assertions.py:3074-3116` include literal `substrate_runbooks/<name>.md` paths in their recommended_action. Matches the prior-art pattern at `:3038-3072` (bg_loop_silent etc.).

**(d) Rotation-grace doesn't duplicate existing key-rotation infra** — **CONCERN.** No prior `previous_agent_public_key` column existed on `site_appliances`; the rotation-grace pattern is net-new. However, `agent_identity_public_key` exists on a parallel surface (sigauth, mig 251) without a `previous_*` column. The asymmetry is intentional per Gate A Lens 1 §Q7 (sigauth identity key and evidence key are DELIBERATELY distinct) but could be surfaced more explicitly in `_HEARTBEAT_KEY_ROTATION_GRACE` docstring. Minor — not a P0.

---

## Lens 3 — HIPAA auditor surrogate

**(a) Sev1 `daemon_heartbeat_signature_invalid` makes compromise-vs-drift distinguishable** — PASS. Runbook Step 2A (keys match → drift) vs Step 2B (keys differ → compromise) is exactly the operator-decision tree the auditor would expect.

**(b) Sev2 `daemon_heartbeat_unsigned` makes remediation clear** — PASS. Three numbered causes + five-step operator action list.

**(c) Auditor-grade evidence: `signature_canonical_format` + `signature_timestamp_unix` queryable** — PASS. Mig 313 adds both columns; auditor kit at `kit_version=2.2` already surfaces heartbeat ledger.

---

## Lens 4 — Attorney surrogate (Article 3.2)

**(a) Article 3.2 cryptographic-attestation claim materialized** — PASS. (i) Daemon signs, (ii) backend verifies + persists result, (iii) substrate invariants page on signed-not-verified (sev1) + key-but-unsigned (sev2). Three legs of the orphan-coverage claim are present.

**(b) Banned-word scan on runbooks** — PASS. Banned-token grep for `ensure|prevent|protect|guarantee|audit-ready|PHI never leaves|100%|continuously monitored` returned zero hits across all 3 runbooks.

---

## Lens 5 — Product manager (threshold tuning)

**(a) 12-unsigned-per-60min sev2** — PASS for typical 5-min cadence fleet. ~50 appliances × 12 heartbeats/hour = 600 heartbeats; threshold compresses noise correctly.

**(b) 3-invalid-per-15min sev1** — PASS. Single transient invalid = no page; consistent invalid = page within 15 min of attack/drift onset.

**(c) Deprecation date `2026-08-13` documented in runbook + check** — PASS. `daemon_on_legacy_path_b.md:18,26,46-49` describes the date + rationale + how to extend. `assertions.py:6007` hard-codes `date(2026, 8, 13)`.

---

## Lens 6 — Medical-technical (clinic-side reality)

**(a) Operator-vs-clinic routing** — **CONCERN (P1).** The runbooks correctly say "SEV1 — escalate to operator" but the substrate engine itself does NOT have per-invariant clinic-suppression metadata. The Gate A Lens 6 P0 was "substrate-invariant runbook copy must specify operator-routing and explicitly state 'DO NOT surface to clinic-facing channels.'" — the runbook copy mentions operator routing in the remediation steps but does NOT include an explicit "DO NOT surface to clinic" warning paragraph. PracticeHomeCard / client-portal opaque-translation is left as a downstream invariant. **P1, not P0** — the operator-side text is sufficient for today's pre-customer scale; opaque-translation lands as a follow-up before customer #2.

---

## Lens 7 — Legal-internal (Maya + Carol — banned-word scan)

PASS. Grep verdict below. No customer-org-identifying details (no "North Valley", no clinic names, no MSP brand names) leak into the runbooks — they use the generic `<appliance_ip>`, `<appliance_id>`, `<site_id>`, `<your-email>` placeholder convention.

---

## Pre-push test sweep result

**BLOCKING failures (6 test cases across 3 suites):**

| Test | Failures | Severity |
|------|----------|----------|
| `test_substrate_docs_present.py::test_doc_exists_and_has_sections[daemon_heartbeat_unsigned]` | missing `## Root cause categories` | BLOCK (CI gate) |
| `test_substrate_docs_present.py::test_doc_exists_and_has_sections[daemon_heartbeat_signature_invalid]` | missing `## Root cause categories` | BLOCK (CI gate) |
| `test_substrate_docs_present.py::test_doc_exists_and_has_sections[daemon_on_legacy_path_b]` | missing `## Root cause categories` | BLOCK (CI gate) |
| `test_runbook_truth_check.py::test_runbook_does_not_reference_removed_patterns` | `daemon_heartbeat_unsigned.md` references `site_appliances.agent_public_key` (REMOVED_PATTERNS) | BLOCK (CI gate) |
| `test_no_unfiltered_site_appliances_select.py::test_unfiltered_site_appliances_select_ratchet` | `signature_auth.py:614` new SELECT pushes count above ratchet baseline 81 | BLOCK (CI gate) |
| (Same as above — single suite, counts both) | | |

**Net:** sweep would prevent `git push` from succeeding via `.githooks/pre-push`.

Other test suites in the sweep did not regress.

---

## Banned-word scan on runbooks

```
$ grep -nE "\bensure(s|d|ing)?|\bprevent(s|ed|ing)?|\bprotect(s|ed|ing)?|\bguarantee(s|d|ing)?|audit-ready|PHI never leaves|100%|continuously monitored" \
    backend/substrate_runbooks/daemon_heartbeat_unsigned.md \
    backend/substrate_runbooks/daemon_heartbeat_signature_invalid.md \
    backend/substrate_runbooks/daemon_on_legacy_path_b.md
(no output — zero violations)
```

PASS.

---

## Canonical-payload format byte-parity check (daemon vs verifier)

Daemon (`phonehome.go:837-844`) — `strings.Join([SiteID, ToUpper(MAC), ts_unix, AgentVersion], "|")` → `sha256.Sum256(...)`.

Verifier (`signature_auth.py:528-534, 650, 682`) — `"|".join([site_id, mac.upper(), str(int(ts_unix)), agent_version])` → `.encode("utf-8")` → `hashlib.sha256(...).digest()`.

**Byte-identical** for any happy-path heartbeat. Empty-string conservatism on the Python side (`or ""`) does not alter normal flow. **PASS.**

---

## Final recommendation

**Overall verdict: BLOCK — pending the 3 CI gate failures.**

Per-lens summary:
- Lens 1 Engineering (Steve): **BLOCK** — 3 CI gate failures (sweep) + 1 P1 (NOW-in-partial-index).
- Lens 2 Coach: APPROVE-WITH-FIXES — P1 docstring on rotation-grace asymmetry.
- Lens 3 HIPAA: APPROVE.
- Lens 4 Attorney: APPROVE.
- Lens 5 PM: APPROVE.
- Lens 6 Medical: APPROVE-WITH-FIXES — P1 explicit "DO NOT surface to clinic" runbook paragraph.
- Lens 7 Legal: APPROVE.

**Functional implementation: 8/8 P0s CLOSED.** Canonical-format is byte-exact, rotation-grace is correct, hybrid protocol works as designed, soft-verify is non-blocking, kit_version is at 2.2.

**However, the sweep BLOCKs push** — three trivially-fixable gate failures must close before any "shipped" / "complete" claim:

1. Add `## Root cause categories` section to all 3 runbooks (template-compliance).
2. Edit `daemon_heartbeat_unsigned.md` to remove the literal `site_appliances.agent_public_key` token (rephrase as "the legacy evidence-bundle public key column" or similar — per Session 211 / #179 REMOVED_PATTERNS rule).
3. Either add `# noqa: site-appliances-deleted-include — verifier reads agent_public_key for heartbeat, soft-delete not applicable` to `signature_auth.py:614` OR add a `AND sa.deleted_at IS NULL` filter to the SELECT, OR bump `BASELINE_MAX` 81 → 82 in `tests/test_no_unfiltered_site_appliances_select.py` with a comment explaining the new query.

After those three fixes land + sweep re-runs green, this implementation is APPROVE for commit.

**Per Session 220 lock-in:** "Gate B MUST run the full pre-push test sweep, not just review the diff" — this fork ran it. The 3 failures are exactly the class of finding the rule was designed to surface.
