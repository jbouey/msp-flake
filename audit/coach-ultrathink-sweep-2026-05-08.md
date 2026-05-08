# Coach ultrathink sweep — 2026-05-08

Adversarial 17+9 dimension review across this session's commits `c13672b9..4dbdb68e`. Read-only sweep. Findings cited file:line.

## Verdict summary

| Artifact | 17-dim PASS | DRIFT count | Critical drift | Notes |
|---|---|---|---|---|
| F3 Quarterly Summary | 16/17 | 1 | 0 | Footer verify line vs body verify-with-hash inconsistency (D-7) |
| F5 Wall Certificate | 14/17 | 3 | **1** | `ots_attestation` column does not exist — 500 on every render (D-1) |
| P-F6 BA Compliance + roster | 14/17 | 3 | 0 | `admin_connection` for multi-query render (D-2); empty `support_email` body subs |
| P-F8 Incident Timeline | 15/17 | 2 | 0 | `admin_connection` multi-query (D-2), `incident_type`/`runbook_id` unsanitized (D-4) |
| PartnerAttestations.tsx | 17/17 | 0 | 0 | Clean |
| DangerousActionModal | 17/17 | 0 | 0 | Clean |
| Audit fix-ups (2207bfcc, 02d03e64) | 17/17 | 0 | 0 | Clean |
| Counsel packet (plan 34) | 17/17 | 0 | 0 | Clean |
| Plans 35–37 | N/A | 0 | 0 | Headers + structure consistent |
| Memory files (7) | YAML 7/7 | 0 | 0 | All frontmatter valid; MEMORY.md=118 lines (cap 200) |
| setup-githooks.sh | N/A | 0 | 0 | Idempotent, well-documented |
| pre-push hook additions | N/A | 0 | 0 | vitest reporter fix + GIT_* sanitize correct |
| 2 CI gates | N/A | 0 | 0 | Both have synthetic positive controls |

## Critical drift findings (severity-ranked)

### CRITICAL (must-fix before next ship)

- **D-1 / F5 / dim 7 (RLS posture & runtime correctness)** — `mcp-server/central-command/backend/client_wall_cert.py:78-90` — query references `compliance_bundles.ots_attestation` column. **No migration creates that column.** Truth column is `ots_anchored_at TIMESTAMP` (mig 011) or `ots_status='anchored'` (mig 109). First customer click on Print → wall cert endpoint will throw `UndefinedColumnError` → 503. Tests are static-source-shape only; never executed the SQL. Fix: replace with `WHERE ots_anchored_at IS NOT NULL` (or `ots_status = 'anchored'`). Add a real query-execution test that wires a tiny in-memory partition.

### MAJOR (fix in next coach round)

- **D-2 / P-F6 + P-F7 + P-F8 / dim 11 + 18** — `partners.py:5640` (P-F7), `partners.py:5885` (P-F6 BA), `partners.py:5984` (P-F8) all use `admin_connection(pool)` while their inner functions issue **2+ admin queries** (P-F6 issues 5+: partner row, agreement, roster list, per-roster site_count + org_row, INSERT, UPDATE supersede). CLAUDE.md Session 212 rule: *"`admin_transaction()` for multi-statement admin paths … Use this — NOT `admin_connection`."* Routing-pathology class — under PgBouncer transaction-pool, second-query may land on a different backend without `app.is_admin='true'` (mig 234 default = false) → silent zero-row results in production. P-F6's INSERT/UPDATE pair is wrapped in `conn.transaction()` inside the module, but the reads before it are not. Fix: swap all three handlers to `admin_transaction(pool)`.

- **D-3 / P-F6 / dim 8** — `partner_ba_compliance.py:510-511` writes `support_email_snapshot=se or ""` and `support_phone_snapshot=sp or ""` *after* `_sanitize_partner_text` already returns `""` for None. The string `""` is fine, but downstream `attestation_facts` (line 451) uses `presenter_brand` only — does NOT bind support email/phone to the canonical hash. So if a partner edits support_email between issuances, the snapshot column moves but the `attestation_hash` stays valid for "different" content. Verify-by-hash payload doesn't expose support contacts, so the OCR-grade leak is bounded — but the auditor narrative "snapshot is FROZEN" (carried over from F1) is untrue here. Fix: include `support_email_snapshot` + `support_phone_snapshot` in `attestation_facts`, OR drop the snapshot columns and document presenter-contact as live.

- **D-4 / P-F8 / dim 8** — `partner_incident_timeline.py:130, 175` — `incident_type` and `runbook_id` flow into the rendered template **unsanitized**. Both come from substrate-controlled tables (incidents, execution_telemetry), so the immediate XSS surface is small, but Maya's P0 from F1 ("defense-in-depth on every interpolation") is broken. Fix: pass through `_sanitize_partner_text` (or rename to `_sanitize_text` since this isn't partner-controlled). 5-line change.

- **D-5 / F3 / dim 17 + auditor-kit determinism contract carry-over** — `client_quarterly_summary.py:480` uses `now.isoformat()` inside `attestation_facts`. The hash binds to wall-clock issuance time; that is the F1+P-F5 pattern, so it's intentional — but the artifact-level docstring (line 51-56) claims "Re-rendering an issued row reproduces the same hash." That's only true if the *same* row is re-fetched, not re-issued. Doc-level drift; the canonical contract is **chain-anchored issuance, not deterministic re-render**. Fix: clarify the docstring (re-rendering an existing row by hash via the verify endpoint is byte-stable; re-issuing a new row produces a new hash bound to a new wall-clock).

### MINOR (track but don't block)

- **D-6 / F3 / dim 14 (Brian phone-first)** — `templates/quarterly_summary/letter.html.j2:11` page-footer reads `Verify: {{ verify_phone }} or {{ verify_url_short }}.` with no hash slug. Body line 190 includes `/{{ attestation_hash[:32] }}`. F5 wall_cert footer DOES include the slug. Inconsistency vs F1+F5 footer pattern. Fix: append `/{{ attestation_hash[:32] }}` to the footer in F3.

- **D-7 / F3 / dim 5 (hash-length floor)** — F3 module advertises a `verify/quarterly/{hash}` endpoint accepting 32-char prefix with ambiguity detection (client_portal.py:5970-6033). `partner_ba_compliance.py:54` uses `verify/ba-attestation`, not `verify/ba/`. Fine, but `verify/ba-attestation` collides with the *issuance* endpoint name on the partner side (`/me/ba-attestation`) — not a routing collision (different prefix), but a naming inconsistency. Fix: rename public route to `/api/verify/ba-attestation/{hash}` (already that), code is fine — minor doc clarification only.

- **D-8 / X-Letter-Valid-Until on F3** — F3 issuance emits `X-Summary-Valid-Until` (client_portal.py:5942), not `X-Letter-Valid-Until`. The header-parity gate (test_artifact_endpoint_header_parity.py:43) explicitly allows both. UI consumer for F3 is the client portal (not yet wired); when it lands, must read `X-Summary-Valid-Until`. No regression — track as a follow-up to memory file `feedback_multi_endpoint_header_parity.md`.

- **D-9 / round-table verdict in commit body / dim 19** — Commits `de252f7a`, `2dc26875`, `83493a8d`, `1acb8284`, `bade8c9a`, `adace8a2`, `2690c73f`, `e963c31f`, `88dd5e49` (all infra/UI) lack the 4-voice round-table matrix in the body. Customer-facing F3+F5+P-F6+P-F8 commits DO carry it. Pattern is "round-table required for customer-facing; infra commits exempt" — implicit but not documented. Fix: add an inline note to `feedback_round_table_at_gates_enterprise.md` clarifying the carve-out for pure-infra commits.

## Cross-cutting drift patterns

- **Pattern A — `admin_connection` for multi-query reads survives across endpoints.** P-F7 (pre-session), P-F6, P-F8 all repeated the pattern. The Session 212 rule is in CLAUDE.md but no static gate enforces it. **Pattern that needs a CI gate: AST-walk for `admin_connection` blocks containing 2+ `conn.fetch*` or `conn.execute` calls without an inner `conn.transaction()`.** Companion to existing `test_artifact_endpoint_header_parity.py`.

- **Pattern B — Tests check source shape, not runtime correctness.** F5 wall_cert ships a query against a non-existent column, and the static-source-shape tests pass. F1's same shape worked because F1 doesn't reference OTS at all. Pattern: **every new SQL query SHOULD be exercised against a real schema**, even if minimal. Tests that only `assert "_gather_ots_pct" in source` are insufficient.

- **Pattern C — `_sanitize_partner_text` discipline is per-author, not per-codepath.** F1 + F3 + P-F5 + P-F6 all sanitize. P-F8 does not (D-4). No source-level rule enforces "every interpolation of an external string into a Jinja2 template has a sanitize step." Could pin via AST gate on `render_template(...)` callers.

## Enterprise-plus standards gaps

- **E1 — `admin_connection` AST gate missing.** Add `tests/test_admin_connection_no_multi_query.py`. Walk client_portal.py + partners.py + every backend module with admin handlers; any `async with admin_connection(...)` block with 2+ DB calls and no inner `conn.transaction():` fails. Baseline starts non-zero (~5 known); ratchet down.

- **E2 — Real-schema query gate.** Backstop the source-shape tests with a `tests/test_artifact_queries_execute_pg.py` (PG-tier, CI-only) that bootstraps the test schema and runs every artifact's read query against it. Catches D-1 class deterministically.

- **E3 — `_sanitize_partner_text` audit gate.** Hash all `render_template(...)` calls in backend modules; assert every kwarg whose value flows from a `conn.fetch*`/`request.json()` chain passes through a sanitize step. Source-level ratchet, like the CSRF gate.

- **E4 — F3 footer-slug inconsistency** — easy fix-up, also minor consistency-coach lapse worth pinning into `feedback_multi_endpoint_header_parity.md` companion: "verify-line slug parity across artifacts."

## Recommendations (prioritized for parent agent)

1. **CRITICAL — fix D-1 (wall_cert SQL).** Single 2-line change: `client_wall_cert.py:81-83` → `WHERE ots_anchored_at IS NOT NULL`. Add `tests/test_client_wall_cert_pg.py` exercising a tiny PG fixture so the next miss is caught at CI, not in the customer's first click.
2. **MAJOR — fix D-2 (admin_connection→admin_transaction).** Three handlers in `partners.py` (5640, 5885, 5984). Same change pattern, swap import + context manager. Verify with a re-run of test_partner_*.
3. **MAJOR — fix D-3 (BA attestation snapshot completeness).** Add `support_email_snapshot` + `support_phone_snapshot` into `attestation_facts` dict in `partner_ba_compliance.py:445-467` so the canonical hash binds. Bump module's internal version to `"1.1"` if downstream verify cares.
4. **MAJOR — fix D-4 (P-F8 sanitize gap).** Wrap `incident_type`, `runbook_id`, and `r["runbook_id"]` strings through `_sanitize_partner_text` in `partner_incident_timeline.py`. ~6 lines.
5. **MAJOR — add E1 (admin_connection AST gate).** Pin Pattern A class structurally.
6. **MAJOR — add E2 (real-schema query gate).** Pin Pattern B class structurally.
7. **MINOR — batch into ONE fix-up commit:** D-5 (docstring), D-6 (footer slug), D-8 (header header parity follow-up note), D-9 (round-table carve-out).
8. **MINOR — track E3 + E4** as next-sprint coach items.

## Round-table 2nd-eye candidates per recommendation

| Rec | Steve | Maya | Carol | Coach | Adam-DBA | Janet-OCR | Brian-MSP |
|---|---|---|---|---|---|---|---|
| 1 D-1 | ✓ | | | ✓ | ✓ | | |
| 2 D-2 | ✓ | | | ✓ | ✓ | | |
| 3 D-3 | | ✓ | ✓ | ✓ | | | |
| 4 D-4 | | ✓ | | ✓ | | | |
| 5 E1 | ✓ | ✓ | | ✓ | ✓ | | |
| 6 E2 | ✓ | | | ✓ | ✓ | | |
| 7 minors | | | ✓ | ✓ | | ✓ | ✓ |
| 8 E3+E4 | | ✓ | | ✓ | | | |

## What I checked but did NOT find drift on

- F3 + F5 + P-F6 + P-F8 / dim 1 (banned legal words) — clean across module + template prose.
- F3 + F5 + P-F6 / dim 13 ("documents that" not "confirms that") — F3 + F1 use the canonical verb; wall_cert uses passive "are monitored" (acceptable).
- F3 + F5 + P-F6 / dim 14 (1-800 phone first / no QR code) — phone is first in body+footer; no `qr_code` Jinja in any new template.
- F3 + P-F6 / dim 4 (Ed25519 anchor namespace) — `partner_baa_*` events anchor at synthetic `partner_org:<id>` per Session 216 (partner_ba_compliance.py:210, 269). F3 quarterly is on the org-level chain via `compliance_bundles.site_id` — fine.
- F3 + P-F6 / dim 6 (ALLOWED_EVENTS lockstep) — both new event types added to `privileged_access_attestation.ALLOWED_EVENTS` (line 244-245), test_privileged_chain_allowed_events_lockstep.py expectation set (line 171-172), counted toward 59-event total. P-F6 anchor docstring note matches.
- F3 / dim 5 (hash floor + ambiguity) — public_verify endpoint at client_portal.py:5970 enforces 32-char floor + ambiguity detection (line 6014-6027), parity with F4.
- F3 + P-F6 / dim 7 (RLS + SECURITY DEFINER) — mig 290+291+292 all `ENABLE ROW LEVEL SECURITY` + create `tenant_org_isolation` / `partner_self_isolation` policies + ship SECURITY DEFINER `public_verify_*` functions with REVOKE PUBLIC + GRANT mcp_app.
- F1 + F3 + P-F5 + P-F6 / dim 9 (asyncio.to_thread) — every PDF endpoint wraps WeasyPrint via `_asyncio.to_thread(html_to_pdf, ...)` (client_portal.py:5503, 5616, 5924; partners.py:5565, 5649, 5897, 5995).
- All public-verify endpoints / dim 10 (X-Forwarded-For) — first hop extracted via `request.headers.get("x-forwarded-for", "").split(",")[0].strip()` (5 endpoints).
- Issuance endpoints / dim 11 (rate-limit per-user) — F3=5/hr, F5=10/hr, P-F6=5/hr, P-F8=60/hr — all keyed on `partner_user:`/`client:` user_id.
- New tests / dim 18 (ratchet baseline + positive control) — both `test_partner_button_to_route_audit.py` (line 294-323) and `test_artifact_endpoint_header_parity.py` (line 354-411) ship synthetic-fixture positive controls in addition to baseline-0 production scans.
- Memory files (7) — all 7 carry valid YAML frontmatter (name/description/type/decay_after_days/last_verified). MEMORY.md = 118 lines (cap 200). All index entries are pointer-rows ≤150 chars.
- Plan docs 34-37 — consistent structure, dated headings, voice attributions, decision log.
- Frontend CSRF posture (PartnerAttestations) — every state-change goes through `postJson`/`deleteJson`; only raw `fetch` is GET `/api/partners/me/orgs` with `credentials:'include'` (line 223), which is correct (no CSRF needed for GET).
- `setup-githooks.sh` — idempotent, fails clear if run outside repo, sets `core.hooksPath = .githooks` correctly.
- Pre-push hook additions — vitest 4 `--reporter=default` (vitest dropped 'basic' in v4) is correct; `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` stripped via dict-comprehension `env=` filter in 2 helper paths.
- `partners.py` BA roster mutations / dim 22 (CSRF + role gating) — POST/DELETE on `/me/ba-roster*` use `require_partner_role("admin")` (line 5719, 5802), GET allows admin+tech (line 5677). Matches RT31 site-state class rule.

---

End of sweep. Total: **1 critical (D-1), 4 major (D-2..D-5), 4 minor.** Recommendation #1 (D-1) is a customer-visible 503 if shipped; recommend a fix-up commit before any further partner-portal sprint work merges.
