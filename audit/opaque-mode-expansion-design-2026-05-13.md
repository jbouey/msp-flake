# Opaque-Mode Harmonization Expansion — Design v2 (Task #53, Counsel Priority #5, Rule 7)

> **v2 changes (Gate A APPROVE-WITH-FIXES, 3 P0s applied 2026-05-13):**
> **P0 #1:** Resolved §2.A classification ambiguity via SPLIT-RECIPIENT MODEL — operator-facing alerts stay verbose (operator-internal channel; no Rule 7 exposure); customer-facing alerts go opaque (separate codepath, separate subject construction). `email_alerts.py:947 SRA-reminder` is reclassified as customer-facing (the SRA reminder is sent to the practice owner — customer surface — not the operator). The existing `_OPAQUE_MODULES` allowlist gates customer-facing modules ONLY; operator-facing email modules are out of scope for Rule 7. **P0 #2:** Phase 2 public-verify path opacity (Option A vs B) is **DEFERRED to its own Class-B Gate A** because of the auditor-kit URL contract regression risk — every kit issued in the last 18 months hard-codes site_id-in-path URLs. Phase 2 must be paired with (a) auditor-kit version bump 2.1 → 2.2 (same lockstep as D1 P0 #5), (b) HTTP 308 redirect from deprecated paths, (c) 24-month deprecation window for legacy clients. Not in Phase 0/1 scope. **P0 #3:** PagerDuty BAA-on-file precondition is **REMOVED from Task #53 scope** — Lens 4 (Attorney) correctly classified it as Rule 8 (counsel-priority #2), not Rule 7 (counsel-priority #5). Spawned as sibling-task addendum to subprocessor v2 §5 future-engineering item; not absorbed into this task. **Concrete recommendations applied:** PagerDuty payload scrubber MUST preserve `dedup_key` + `severity` enum (Lens 1 Steve operationally-required); 3 separate test files (`test_email_opacity_harmonized.py` + future `test_webhook_opacity_*.py` + `test_public_verify_path_opacity.py`) sharing a `_opacity_ast.py` helper module (Lens 3 Coach — not monolithic); class-hint subjects over fully-generic (Lens 5 PM + Lens 6 medical-tech — `[OsirisCare] Compliance digest` not `[OsirisCare] Action required` which looks like phishing).

> **Counsel Rule 7 (gold authority, 2026-05-13):** *"No unauthenticated channel gets meaningful context by default. Opaque by default for every email / webhook preview / SMS / notification subject / unauthenticated response. Context belongs behind auth. If a safer alternative is cheap, use it instead of arguing doctrine."*

> **Multi-device-enterprise lens:** at N customers × M outbound channels, each non-opaque channel is N×M leak surfaces. The Session 218 RT21 cross-org-relocate opaque-mode lesson (counsel-adversarial review 2026-05-06) established the canonical pattern; v1 covers email only. v2 (this task) extends to webhooks + PagerDuty + public-verify endpoints + notification subjects.

---

## §1 — Current state — Rule 7 v1 (email-only)

**`_OPAQUE_MODULES` allowlist** at `tests/test_email_opacity_harmonized.py:68` covers 9 modules:
- `_OWNER_TRANSFER` (`client_owner_transfer.py`)
- `_EMAIL_RENAME` (`client_user_email_rename.py`)
- `_CLIENT_PORTAL` (`client_portal.py`)
- `_ORG_MGMT` (`org_management.py`)
- `_RELOCATE` (`cross_org_site_relocate.py`)
- `_PORTAL` (`portal.py`)
- `_SITES` (`sites.py`)
- `_BG_TASKS` (`background_tasks.py`)
- `_CLIENT_SIGNUP` (`client_signup.py`)

**Forbidden helper-param names** in opaque-mode helpers: `org_name`, `actor_kind`, `reason`, `clinic_name`, etc. Recipient-address params (`target_email`, `initiator_email`) are NOT forbidden — they're the to-address, not context leak.

**Gates enforced today** by `test_email_opacity_harmonized.py` (8 tests):
- No `org_name` in email subject
- No `clinic_name` in body
- No `actor_kind` in any opaque-mode helper signature
- No f-string subjects (use plain string literals)
- No `{old_email}` interpolation in NEW-recipient body
- Etc.

---

## §2 — v2 scope — channels to expand

### A. Email modules NOT yet in `_OPAQUE_MODULES` (Rule 7 violation)

| Module | Line | Violation |
|---|---|---|
| `alert_router.py:350` | `subject = f"[OsirisCare] Compliance digest — {org_name}"` | `org_name` in subject |
| `alert_router.py:406` | `subject = f"[OsirisCare] Your compliance monitoring is active — {org_name}"` | `org_name` in subject |
| `alert_router.py:530` | `subject = f"[OsirisCare] {row['severity'].upper()} alert — {row['org_name']}"` | `org_name` + severity in subject |
| `alert_router.py:619` | `subject=f"[OsirisCare Partner] Client non-engagement — {row['org_name']}"` | `org_name` in partner-facing subject |
| `email_alerts.py:947` | `subject = f"[OsirisCare] {count} SRA remediation item{'s' if count != 1 else ''} overdue for {org_name}"` | `org_name` in subject |

**Fix shape:** add `alert_router` + `email_alerts` to `_OPAQUE_MODULES` allowlist; rewrite 5 subject lines to be plain-string opaque (e.g. `"[OsirisCare] Action required for your account"`; body redirects to authenticated portal for full context).

### B. Webhook outbound — PagerDuty payload (subprocessor §11 BAA REQUIRED — structural)

`escalation_engine.py:196` POSTs to `events.pagerduty.com/v2/enqueue` with payload containing:
- `site_id` (customer-org-identifying)
- `incident_type`, `severity`, `summary` (potentially org-identifying)
- `partner_id`

**Rule 7 implication at multi-device-enterprise scale:** PagerDuty subprocessor sees customer-org-identifying metadata on every alert. Even though BAA is required (subprocessor v2 §11), opaque-mode hardening reduces context leakage:

- Replace `site_id` in summary with opaque token (`OSI-<hash8>` style)
- Replace `org_name` in summary with `Customer org [<hash8>]`
- Preserve `severity` + `incident_type` (operationally required)
- Add `osiriscare.net/incident/<hash>` link back to authenticated portal for full context

**Fix shape:** add `PAGERDUTY_OPAQUE_MAP` constant + scrubbing helper; gate via new `test_pagerduty_payload_opacity.py`.

### C. Public-verify endpoint paths (Rule 7 path-leak class)

`evidence_chain.py` exposes:
- `GET /sites/{site_id}/verify/{bundle_id}` — site_id in path
- `GET /sites/{site_id}/public-keys` — site_id in path
- `GET /sites/{site_id}/verify-chain` — site_id in path
- `GET /sites/{site_id}/verify-merkle/{bundle_id}` — site_id in path
- `GET /{bundle_id}/verify` — hash-only (this is the F4 pattern, opaque)
- `GET /public-key` — global

**Rule 7 implication:** the `site_id` in URL path is org-identifying. An external observer (DNS log, HTTP-mitmproxy, web-server log) sees `site_id` in every public-verify request. Per the master BAA v1.0-INTERIM F4 pattern, the hash-only verify route (`/{bundle_id}/verify`) is the opaque-preferred surface.

**Fix shape:**
- **Option A (preferred):** deprecate the `site_id`-in-path variants; redirect to hash-only route. Auditor uses `bundle_id` (already opaque hash); never sees `site_id`.
- **Option B (transitional):** keep site_id paths for backward-compat but add `/verify/{bundle_id}` (no site_id) as primary; cite Option A as v0.5 deprecation deadline.

### D. SMS / Twilio non-SendGrid

**No SMS sender in codebase today** (verified by grep). Documented absence — no action required for v2.

### E. Notification subjects within the platform (in-portal notifications)

Need source-grep for `notifications.py`, `portal.py` notification-creation paths. Out of v2 scope unless found to leak org-identifying data.

---

## §3 — Implementation plan (v2 per Gate A recommended phasing)

**Phase 0 (Counsel Priority #5 — shippable this sprint, ~150 LOC, single PR):**

1. Per P0 #1 split-recipient model: classify each existing email path as operator-facing OR customer-facing. Operator-facing stays verbose (out of Rule 7 scope). Customer-facing goes opaque.
2. Add `_ALERT_ROUTER` (customer-facing subset) + customer-facing subset of `_EMAIL_ALERTS` to `_OPAQUE_MODULES` allowlist. Operator-facing paths stay outside the allowlist by design.
3. Rewrite 5 subject lines using **class-hint copy** (Lens 5 PM + Lens 6 medical-tech consensus — fully-generic looks like phishing):
   - `alert_router.py:350` → `"[OsirisCare] Compliance digest"`
   - `alert_router.py:406` → `"[OsirisCare] Compliance monitoring active"`
   - `alert_router.py:530` → `"[OsirisCare] Compliance alert"` (drop severity per Lens 5 default — was leaking `{severity}` + `{org_name}`)
   - `alert_router.py:619` → `"[OsirisCare Partner] Client non-engagement"`
   - `email_alerts.py:947` → `"[OsirisCare] SRA remediation reminder"` (no count, no org_name)
4. Extend existing 8 gates in `test_email_opacity_harmonized.py` to cover the new modules — no new test file in Phase 0.
5. Pre-push full-CI-parity sweep + Class-B Gate B before commit body cites "shipped" (per round-table TWO-GATE protocol).

**Phase 1 (PagerDuty payload opacity, separate PR):**
- Add `escalation_engine.py::scrub_pagerduty_payload()` helper that replaces `site_id` + `org_name` + customer-identifying summary fields with opaque tokens. **MUST preserve `dedup_key` + `severity` enum** (Lens 1 Steve — operationally-required for routing and alert-fatigue control).
- Wrap `events.pagerduty.com/v2/enqueue` POST call with the scrubbing helper.
- Ship `test_webhook_opacity_pagerduty.py` (separate file, NOT merged with email gate per Lens 3 Coach — each channel class gets its own test file; all 3 share a `_opacity_ast.py` helper module).

**Phase 2 (Public-verify path opacity — DEFERRED to own Class-B Gate A):**
- Auditor-kit URL contract regression risk: every kit issued in the last 18 months hard-codes site_id-in-path URLs. Phase 2 cannot ship without:
  - Auditor-kit version bump 2.1 → 2.2 (same lockstep as D1 P0 #5)
  - HTTP 308 redirect from deprecated paths
  - 24-month deprecation window (Lens 2 HIPAA-auditor)
- This work spawns its own Class-B Gate A. Not in this design's scope.

**Phase 3 (PagerDuty BAA-on-file precondition — sibling-task, NOT this task):**
- Per Lens 4 Attorney: PagerDuty BAA precondition is Rule 8 (counsel-priority #2), not Rule 7. Already documented in subprocessor v2 §5 future-engineering item. Engineering action: create a new task #57 to track it as a sibling of Task #55 (Rule 8), NOT absorbed into Task #53.

**Phase 4 (in-portal notification subject scan):** deferred — needs source-grep first.

---

## §4 — CI gate skeleton

```python
# tests/test_webhook_opacity_harmonized.py (Phase 1)

import ast
import pathlib

_OUTBOUND_WEBHOOK_TARGETS = {
    "events.pagerduty.com": "scrub_pagerduty_payload",
    # Future webhook targets register here with their scrubbing helper.
}

def test_outbound_webhook_calls_use_scrubber():
    """Every aiohttp.post / httpx.post / requests.post to a known
    outbound-webhook target MUST be preceded by a call to its
    registered scrubbing helper. Caught at AST-level via call-chain
    inspection.
    """
    ...
```

```python
# tests/test_public_verify_path_opacity.py (Phase 2)

def test_no_site_id_in_public_verify_paths():
    """Every @router.get path under evidence_chain.py that's marked
    public-verify (no auth dependency) MUST NOT include {site_id}
    in its path template. Hash-only routes only.
    """
    ...
```

---

## §5 — Multi-device-enterprise lens

At N customers × M outbound channels, every non-opaque channel is N×M leak surfaces:
- Email: ~5 customers × 5 subject types = 25 emails per day with org_name in subject (today)
- PagerDuty: N partners × M alerts/day = ~50 alerts/day with site_id in payload (today)
- Public-verify: ~10 verify-requests/day per customer × N customers = N×10 web-server-log entries with site_id

Each leak surface compounds at fleet scale. Rule 7 expansion closes these at the platform level, not per-customer.

---

## §6 — Open questions for Class-B Gate A

- (a) Phase 2 — Option A (deprecate site_id paths) vs Option B (transitional with deprecation deadline)? Auditor-grade preference?
- (b) PagerDuty payload scrubbing — should the opaque token be deterministic (`hash(site_id)`) or random per-event? Deterministic lets operators correlate; random is more opaque.
- (c) For alert_router subject rewrites — should the new subjects be FULLY generic (`"[OsirisCare] Action required"`) or include a class hint (`"[OsirisCare] Compliance digest"`)? Latter is more user-friendly; former is more opaque.
- (d) Should `test_webhook_opacity_harmonized.py` extend the existing `test_email_opacity_harmonized.py` pattern (single file) or be separate (one test file per channel class)?
