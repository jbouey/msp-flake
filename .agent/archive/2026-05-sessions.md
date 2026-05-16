# Session Archive - 2026-05


## 2026-05-02-session-215-adversarial-audit-killswitch-billing-substrate.md

# Session 215 — Adversarial UI audit, kill-switch P0, admin billing, substrate hygiene

**Date:** 2026-05-02
**Volume:** 29 commits, all CI-green and prod-runtime-SHA-verified
**Theme:** Owner-operator administrative surface. "If Anthropic disappears tomorrow, can the operator still operate Central Command?" — answer: yes, the audit + ship batch made that 100% true.

## Workstream summary

### 1. Round-1 audit-closure fixups (early session)
Tail of the Session-214 D1/D2/D4 close: per-control lockstep ratchets to 0, score-threshold canon collapse (27 → 1 site), pre-push parity gate (#46), 8 supporting P1s (#41-48). Schema fixture forward-merged for mig 271.

### 2. AI-independence audit (#52-60, 9 items)
End-to-end: can Central Command run if Anthropic / OpenAI / Vertex disappears? Verdict YES. Compliance chain (evidence + OTS + privileged-access attestation) has zero LLM touch. L1 healing tier (deterministic rules) covers ~70% of incidents. L2 LLM tier degrades gracefully — `/incidents` UI shows "L2 unavailable" banner, operator queue continues. CI gate `tests/test_compliance_chain_llm_free.py` ratchets the LLM-free property.

### 3. ADVERSARIAL UI audit (#64-69, 6 deliverables)
User pivoted mid-session: *"oppositionally look for failure modes."* Surfaced 6 missing operator-class buttons in the admin surface:
- **#64 P0 — fleet-wide healing kill-switch** (incident response). Three endpoints (`/api/admin/healing/global-pause/resume/global-state`), banner across all admin pages, Ed25519 attestation per-site, HIPAA disclosure on client portal. Round-table found this was missing — operator had no way to pause healing fleet-wide if a runbook started misbehaving.
- **#65 P1** — org deprovision/reprovision UI (Organizations page)
- **#66 P1** — bulk multi-select on Sites + Fleet pages, generic `BulkActionToolbar`
- **#67 P1** — admin billing read-only view (subscription list)
- **#68 P2** — user API-key rotation UI
- **#69 P3** — audit log filter by actor + IP + date

### 4. Kill-switch sub-followups (#73-76, post-#64 round-table)
Three round-table-flagged gaps closed in the same day:
- **#73 (Camila P1)** — kill-switch HIPAA disclosure surfaces on **client portal home** so customers see "healing paused fleet-wide" with operator+reason, not just admin
- **#74 (Camila P1)** — kill-switch fan-out writes per-site Ed25519 attestation via `privileged_access_attestation.create_*` (same chain as #72 below)
- **#75 (Steve P2)** — `KillSwitchBanner` component renders on every admin page, not just /healing
- **#76 (Steve P3)** — banner + dropdown share `useKillSwitchState()` React Query hook (was 4 inline pollers)

### 5. #72 — admin destructive billing actions (cancel + refund)
Privileged-access chain: typed `confirm_phrase` + reason ≥20 chars + email-format actor + Stripe `idempotency_key` + `admin_audit_log` + Ed25519 attestation to customer's `site_id`. Both actions added to `ALLOWED_EVENTS` (admin-only, lockstep asymmetry permitted).

**Adversarial round-table caught two real bugs before ship:**
- **B-1 (Brian P1)** — refund endpoint didn't verify `Charge.customer == URL.customer`. Stripe accepts any charge_id in the account; an operator typo would silently misattribute the audit_log + Ed25519 attestation to the wrong site_id. Fix: `stripe.Charge.retrieve()` + ownership check before `Refund.create()`.
- **D-1 (Diana P1)** — SQLAlchemy queries didn't use `execute_with_retry()`, violating the CLAUDE.md PgBouncer rule (`DuplicatePreparedStatementError` class on rotation).

Both regressions pinned by 13-assertion CI gate `tests/test_admin_billing_destructive.py`.

### 6. Process discipline (#77 + the CI cascade)
Mid-session, a 14-deploy CI cascade hit because `test_sql_on_conflict_uniqueness.py` was untracked locally but in the pre-push allowlist (passed local fs, failed git tree). Fixed root cause + added new gate `test_pre_push_allowlist_only_references_git_tracked_files` using `git ls-files`. User filed **#77 P0 PROCESS rule**: *"you must ensure each deploy passes before moving on."* In effect for the rest of the session — every commit waited on CI green + `curl /api/version` SHA verify before claiming shipped.

### 7. Lockstep test promotion (#79)
The `test_allowed_events_matches_privileged_order_types` assertion lived in `_pg.py` (TIER-3, CI-only). #72 lockstep drift was caught by CI but missed by pre-push, forcing a round-trip. Promoted the assertion to a new TIER-1 file `tests/test_privileged_chain_allowed_events_lockstep.py`. Auto-discovered by the existing `_lockstep(?:_pg)?\.py$` pattern in `_TIER1_PATTERNS`.

### 8. partition_maintainer permission fix (#78)
Pre-fix: `partition_maintainer_loop` ran as `mcp_app` (PgBouncer) and hit `permission denied for schema public` every 24h. Survived only because partitions were pre-created through Dec 2026. Fix: open single-shot `asyncpg` connection to `MIGRATION_DATABASE_URL` (superuser, bypassing PgBouncer) — same pattern as `heartbeat_partition_maintainer_loop`. Round-table chose consistency over a SECURITY DEFINER migration.

### 9. Substrate `journal_upload_stale` canonicalization fix
Found while inspecting open violations: `physical-appliance-pilot-1aea78` (orphan site_id from Apr 25 relocate) was firing `journal_upload_stale` sev2 since the relocate. Root cause: assertion's CTE didn't filter `site_appliances.deleted_at IS NULL` and used the historical `journal_upload_events.site_id` instead of the live `site_appliances.site_id`. Fix: JOIN + filter + use `sa.site_id`. Same class as Session 213 F1/F3 telemetry orphans, but `appliance_id` is the natural key here so no `canonical_site_id()` needed.

[truncated...]

---
