# Cross-Org Site Relocate — Enablement Runbook

**Purpose:** procedure for enabling the cross-org site relocate
feature in production after outside HIPAA counsel has approved.
Companion to `.agent/plans/21-counsel-briefing-packet-2026-05-06.md`.

**Audience:** Osiris admins (two distinct humans for the dual-admin
flip), contracts-team operator (for per-org BAA-receipt-authorization
population).

**Status:** Engineering complete. Counsel approved 2026-05-06
contingent on five conditions; conditions #1, #3, #4, #5 are
engineering-shipped, condition #2 is engineering-enforced via
contracts-team operational input through the endpoints below.

---

## Pre-flight checklist (before any human action)

Run the readiness endpoint to confirm the operational state:

```bash
curl -s -H "Authorization: Bearer <admin token>" \
  https://www.osiriscare.net/api/dashboard/admin/orgs/cross-org-relocate-readiness \
  | jq
```

Expected JSON shape:

```json
{
  "flag_state": "disabled" | "proposed_pending_approval" | "enabled",
  "flag_proposed_by_email": null | "admin1@osiriscare.io",
  "flag_proposed_at": null | "2026-...Z",
  "flag_enabled_by_email": null | "admin2@osiriscare.io",
  "flag_enabled_at": null | "2026-...Z",
  "eligible_target_org_count": <int>,
  "in_flight_relocate_count": <int>,
  "checklist": [...]
}
```

Confirm:

- `flag_state == "disabled"` (no prior flip; first time enabling)
- `eligible_target_org_count >= 1` (at least one receiving org has
  contracts-team-recorded BAA receipt-authorization). If 0, contracts
  team must run Step A below for at least one prospective receiver
  org BEFORE the flag flip is useful.

If either fails, fix before proceeding.

---

## Step A — contracts-team: per-org BAA receipt-authorization

For each prospective receiving client_org, contracts-team:

1. Pulls the org's standard substrate-class BAA from the contracts
   archive.
2. Confirms the permitted-use clause expressly covers receipt +
   continuity of transferred site compliance records (counsel-
   approval condition #2).
3. **If standard BAA covers it:** find the row in `baa_signatures`
   whose `signature_id` corresponds to the standard BAA the org
   signed at onboarding; capture that signature_id.
4. **If standard BAA is silent:** prepare an addendum with the
   transfer/continuity language counsel provided; capture the
   addendum's e-signature; the new row in `baa_signatures` carries
   the addendum's signature_id.
5. POST to the contracts-team endpoint:

```bash
curl -X POST \
  -H "Authorization: Bearer <contracts-team admin token>" \
  -H "Content-Type: application/json" \
  -d '{
    "signature_id": "<the-baa-or-addendum-signature_id>",
    "is_addendum": false,           # true if step 4 path
    "reason": "Standard substrate-class BAA v3.2 §2(b) reviewed by [name]; covers transferred-site receipt and continuity. Per RT21 counsel-approval condition #2."
  }' \
  https://www.osiriscare.net/api/dashboard/admin/orgs/<target_org_id>/baa-receipt-authorize
```

Response contains the `authorized_at` + `next_step` fields. The
client_orgs row is updated; the `_check_target_org_baa` endpoint
will admit this org as a relocate target.

A row in `admin_audit_log` is written: `action =
record_baa_receipt_authorization`, `target = client_org:<id>`,
`details` carries the signature_id + reason.

---

## Step B — admin #1 (proposer): propose-enable

**Constraint:** must be a different human than the admin who will
do Step C. The DB CHECK at schema layer enforces
`lower(approver_email) <> lower(proposer_email)`; same-admin
self-approval will be rejected.

```bash
curl -X POST \
  -H "Authorization: Bearer <admin #1 token>" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Q3-2026 [customer name] hospital-network acquisition customer signed receiving-org BAA addendum [date]; ready to enable cross-org relocate for that engagement."
  }' \
  https://www.osiriscare.net/api/admin/cross-org-relocate/propose-enable
```

Reason must be **>=20 characters**. Captures the OPERATIONAL
trigger, NOT the legal authority — that goes in Step C's reason.

Response:

```json
{
  "flag_name": "cross_org_site_relocate",
  "proposed_by": "admin1@osiriscare.io",
  "next_step": "A second distinct admin must POST /approve-enable..."
}
```

Flag is still `disabled`. The `feature_flags` row is updated with
`enable_proposed_*` columns; the `admin_audit_log` row is written
with `action = propose_enable_cross_org_site_relocate`.

---

## Step C — admin #2 (approver): approve-enable

**Constraint:** must be a different human than admin #1.

The reason must be **>=40 characters** and should reference the
outside-counsel opinion identifier (date + doc-ID).

```bash
curl -X POST \
  -H "Authorization: Bearer <admin #2 token>" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Outside-counsel HIPAA opinion 2026-MM-DD doc-ID YYYYY: permitted scope under both source-org and target-org BAAs confirmed (§164.504(e)); §164.528 substantive accounting + production posture confirmed; opaque-mode email defaults accepted; dual-admin governance accepted; receipt-authorization control (mig 283) accepted."
  }' \
  https://www.osiriscare.net/api/admin/cross-org-relocate/approve-enable
```

Response:

```json
{
  "flag_name": "cross_org_site_relocate",
  "enabled": true,
  "proposer": "admin1@osiriscare.io",
  "approver": "admin2@osiriscare.io"
}
```

The flag is now `enabled`; the relocate endpoints will return
non-503 responses; the magic-link emails will be deliverable.

`admin_audit_log` row written: `action =
approve_enable_cross_org_site_relocate`, `details` carries both the
proposer's reason and the approver's reason (counsel-opinion ID).

---

## Step D — counsel hand-back artifact

Per the §6 hand-back list of the counsel briefing packet, capture:

1. The `admin_audit_log` row IDs for both
   `propose_enable_cross_org_site_relocate` (admin #1) and
   `approve_enable_cross_org_site_relocate` (admin #2):

```sql
SELECT id, username, action, target, details, created_at
  FROM admin_audit_log
 WHERE target = 'feature_flag:cross_org_site_relocate'
   AND action IN (
       'propose_enable_cross_org_site_relocate',
       'approve_enable_cross_org_site_relocate'
   )
 ORDER BY created_at ASC;
```

2. The `feature_flags` row contents:

```sql
SELECT * FROM feature_flags
 WHERE flag_name = 'cross_org_site_relocate';
```

3. A confirmation page screenshot from the admin tooling showing
   the row contents post-flip.

Send these to outside counsel for their file.

---

## Verification (post-flip)

Re-run the readiness endpoint:

```bash
curl -s -H "Authorization: Bearer <admin token>" \
  https://www.osiriscare.net/api/dashboard/admin/orgs/cross-org-relocate-readiness \
  | jq
```

Expected:

```json
{
  "flag_state": "enabled",
  "flag_proposed_by_email": "admin1@osiriscare.io",
  "flag_proposed_at": "2026-...",
  "flag_enabled_by_email": "admin2@osiriscare.io",
  "flag_enabled_at": "2026-...",
  ...
  "checklist": [
    { "condition": "feature_flag enabled", "met": true, "next_step": null },
    ...
  ]
}
```

---

## Rollback (if counsel later withdraws approval, or for any reason
the flag should go disabled)

The disable path uses a parallel propose/approve-style flow not yet
shipped — for now, disable is a direct admin action with an
attestation reason. If you need to disable, do NOT just UPDATE the
`feature_flags` row directly; the substrate invariant
`cross_org_relocate_chain_orphan` and `cross_org_relocate_baa_receipt
_unauthorized` (sev1) defend the integrity of completed-relocate
historical records.

If a disable becomes necessary, file a ticket; engineering will
ship a `/propose-disable` + `/approve-disable` parallel flow.

---

## Related artifacts

- Counsel briefing packet (v2.3 — Approved + Hardened):
  `.agent/plans/21-counsel-briefing-packet-2026-05-06.md`
- Engineering design round-table:
  `.agent/plans/21-cross-org-site-relocate-roundtable-2026-05-05.md`
- Implementation lessons:
  `docs/lessons/sessions-218.md`
- Module:
  `mcp-server/central-command/backend/cross_org_site_relocate.py`
- Org-management endpoint (contracts-team flow):
  `mcp-server/central-command/backend/org_management.py`
- Migrations: 279, 280, 281, 282, 283
- Substrate runbooks:
  - `cross_org_relocate_chain_orphan.md`
  - `cross_org_relocate_baa_receipt_unauthorized.md`
- CI gate:
  `mcp-server/central-command/backend/tests/test_cross_org_relocate_contract.py`
