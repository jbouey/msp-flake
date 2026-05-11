# Gate B re-review verdict — zero-auth hardening Commit 2 (2026-05-11)

**Verdict:** APPROVE

## Fix verification

- **P0-1 soft-delete filters: ✓**
  - `provisioning.py:470` UPDATE has `AND deleted_at IS NULL` (was :466 pre-edit, line drift expected)
  - `provisioning.py:500` SELECT subquery has `AND deleted_at IS NULL`
  - `provisioning.py:565` heartbeat MAC lookup has `AND deleted_at IS NULL`
  - `provisioning.py:481` cross-site forensic SELECT carries `# noqa: site-appliances-deleted-include — cross-site forensic 403 audit lookup must see soft-deleted rows` — Carol-satisfactory: WHY is in the comment, not just a silent exemption.

- **P0-2 'active' removed: ✓**
  - `provisioning.py:544` reads `AND status IN ('pending', 'claimed')`. Matches mig 003:73 CHECK constraint enum.

- **P1-3 Optional + handler 401: ✓**
  - `HeartbeatRequest.provision_code: Optional[str] = None` at line 117.
  - Handler at lines 528-531: `code = (heartbeat.provision_code or "").upper().strip()` THEN `if not code: raise HTTPException(status_code=401, detail="Missing provision_code")` — 401 raised BEFORE any DB query. Steve's pre-DB-resource concern satisfied.

- **BASELINE_MAX lowered to 81: ✓**
  - `tests/test_no_unfiltered_site_appliances_select.py:75` reads `BASELINE_MAX = 81`. Gain locked in, ratchet tightened.

## Full sweep result

`bash .githooks/full-test-sweep.sh` from repo root: **230 passed, 0 skipped (need backend deps)**. No failures. Schema-tightening (Pydantic Optional + status enum + deleted_at filter) did not break any sibling test.

## Adversarial findings (any NEW)

- **Steve:** Heartbeat behavior on soft-deleted-mid-provisioning row: handler returns 403 `Invalid or expired provision_code` via the auth subquery (line 544 won't match a row whose `provision_code` still exists but appliance was soft-deleted in `site_appliances` only blocks the status-write at :565). Correct fail-closed semantics — no resurrection.
- **Maya:** Ratchet baseline literally `81`, not just a snapshot. Next followup must lower further.
- **Carol:** noqa comment carries explicit auditor-readable justification — passes the "no silent exemption" rule.
- **Coach:** Searched for sibling MAC-lookup endpoints (`grep site_appliances.*WHERE.*mac`) — only hit is a string literal in `assertions.py:4715` (an invariant remediation message), no live sibling endpoint at risk. No widening required.

No NEW critical issues surfaced.

## Recommendation

**APPROVE.** All 3 fixes verified at the cited lines, ratchet locked at 81, full sweep 230/0, no sibling endpoints need parallel updates. Ship Commit 2.
