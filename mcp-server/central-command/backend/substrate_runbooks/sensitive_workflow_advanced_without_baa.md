# sensitive_workflow_advanced_without_baa

**Severity:** sev1
**Display name:** Sensitive workflow advanced without an active BAA

## What this means (plain English)

A BAA-gated sensitive workflow **advanced** in the last 30 days for a
`client_org` that has **no active formal BAA** — and there is **no
logged admin carve-out** for it.

The five workflows this invariant watches:

| Workflow                | Source                                   | "Advanced" means |
|-------------------------|------------------------------------------|-------------------|
| `cross_org_relocate`    | `cross_org_site_relocate_requests`       | `source_release_at` or `executed_at` set |
| `owner_transfer`        | `client_org_owner_transfer_requests`     | `current_ack_at` or `completed_at` set |
| `evidence_export`       | `admin_audit_log WHERE action='auditor_kit_download'` | a download row written in the last 30 days |
| `new_site_onboarding`   | `sites`                                  | row `created_at` in the last 30 days |
| `new_credential_entry`  | `site_credentials` JOIN `sites`          | credential `created_at` in the last 30 days |

"No active formal BAA" = `baa_status.baa_enforcement_ok()` returns
FALSE for the org: either no `baa_signatures` row with
`is_acknowledgment_only = FALSE` at the current required version, OR
`client_orgs.baa_expiration_date` is in the past.

**`evidence_export` carve-outs (Task #92, 2026-05-15):** the scan only
considers rows where `details->>'auth_method' IN ('client_portal',
'partner_portal')`. The `admin` and legacy `?token=` branches are
excluded — admin is the platform operator (Carol carve-out #3), and
blocking the legacy-token external-auditor path would itself be a
§164.524 access-right violation (Carol carve-out #4, legally
mandatory). `site_id` and `client_org_id` are denormalized at audit-
write time (`evidence_chain.py` enriched by commit 5ce77722) so the
invariant SQL doesn't need a JOIN to `sites` — and the org-at-the-
time-of-download is preserved against future reparenting. Unlike the
two state-machine workflows, `evidence_export` does NOT use the
`baa_enforcement_bypass` audit-row exclusion: its inline gate
(`check_baa_for_evidence_export`) raises 403 rather than logging a
bypass, so a violation here is unambiguously a gate-bypass or a
post-action BAA lapse with no legitimate-operator escape hatch.

## Why this matters (architectural + legal)

45 CFR §164.504(e) requires the Business Associate Agreement to be in
place **before** the BA performs services involving PHI. A sensitive
workflow advancing for a non-signed Covered Entity is a substantive
compliance gap — not a paperwork lag.

This invariant is **List 3** of the BAA-enforcement lockstep
(Task #52, Counsel Rule 6):

- **List 1** — `baa_enforcement.BAA_GATED_WORKFLOWS` (the canonical set).
- **List 2** — the enforcing callsites (`require_active_baa`,
  `enforce_or_log_admin_bypass`, `baa_gate_passes`).
- **List 3** — *this invariant* — the runtime backstop.

The CI gate `test_baa_gated_workflows_lockstep.py` catches an un-gated
endpoint at **build time**. This invariant catches, at **runtime**,
the two failure modes the build gate cannot:

1. A code path that advanced the workflow **bypassing** the
   enforcement entrypoints entirely (a new endpoint, a script, a
   direct state-machine write).
2. An org whose BAA was active when the workflow started but
   **lapsed** before this check ran (`baa_expiration_date` passed).

## Root cause categories

- **Un-gated code path.** A new endpoint or background job advanced a
  relocate/owner-transfer row without calling `require_active_baa` or
  `enforce_or_log_admin_bypass`. The build gate should have caught a
  missing `require_active_baa` literal — but a *direct* state-machine
  write (not via the named entrypoints) slips past it. Find the
  writer and route it through the enforcement layer.
- **BAA lapsed mid-flow.** The org had an active BAA at initiate-time;
  `baa_expiration_date` passed before the workflow completed. This is
  a genuine §164.504(e) gap — the workflow should not have been
  allowed to complete. Escalate per the disclosure path below.
- **Missing admin carve-out audit row.** An admin legitimately
  advanced the workflow (`enforce_or_log_admin_bypass` path) but the
  `baa_enforcement_bypass` `admin_audit_log` row was not written
  (audit-log INSERT failure — check ERROR logs). The action was
  legitimate; the audit trail is incomplete. Backfill the audit row
  and fix the write failure.
- **Org genuinely never had a BAA.** The most serious case — a
  sensitive workflow ran end-to-end for a Covered Entity with no
  formal BAA ever on file.

## Immediate action

1. **Confirm the BAA state of the named org:**
   ```python
   # baa_status.baa_signature_status(conn, client_org_id) returns
   #   admin_flag / has_formal_signature / has_acknowledgment /
   #   verified / latest_signature_version / latest_signature_at
   ```
   Run it for `details.client_org_id`. `has_formal_signature = FALSE`
   confirms the gap is real.

2. **Identify which failure mode** from the categories above. The
   violation's `details.advanced_at` vs the org's
   `baa_expiration_date` distinguishes "lapsed mid-flow" from "never
   had one."

3. **Check for a bypass row that should exist:**
   ```sql
   SELECT * FROM admin_audit_log
    WHERE action = 'baa_enforcement_bypass'
      AND details->>'client_org_id' = '<org_id>';
   ```
   If the advance was admin-initiated and this returns nothing, the
   audit-log write failed — backfill it and check ERROR logs for the
   `baa_enforcement_bypass audit-log write failed` line.

4. **Per non-operator partner posture:** the substrate exposes the
   gap; the operator decides the BAA-class disclosure. A confirmed
   §164.504(e) gap (workflow completed for a never-signed CE) is an
   operator escalation, not a code cleanup.

## Verification

- Panel: the violation resolves on the next 60s tick once the org has
  an active formal BAA (`baa_enforcement_ok` flips TRUE) OR the
  missing `baa_enforcement_bypass` audit row is backfilled.
- CLI: re-run `baa_status.baa_enforcement_ok(conn, org_id)` — must
  return TRUE.

## Escalation

Sev1 — operator action within the workday. This is a §164.504(e)
compliance-substrate finding, not a paging-class outage: no data is
lost and no system is down. But a **confirmed** "workflow completed
for a never-signed CE" is an operator escalation for BAA-class
disclosure review — route it the same way as a `pre_mig175_
privileged_unattested` finding. Sustained firing (>7 days, same org)
means the enforcement gate has a hole — escalate to engineering to
find the un-gated code path.

## Related runbooks

- `cross_org_relocate_chain_orphan.md` — sibling sev1 for cross-org
  relocate rows that bypassed the attested state machine
  (cross-correlate — a workflow that advanced without a BAA may also
  have bypassed the chain).
- `l2_resolution_without_decision_record.md` — same shape (an action
  of class X exists with no authorization row of class Y).

## Change log

- 2026-05-14 — created — Task #52 (Counsel Priority #1, Rule 6). List
  3 of the BAA-enforcement lockstep. Scoped to the two durable
  state-machine workflows (`cross_org_relocate`, `owner_transfer`);
  `evidence_export` is gated inline only.
- 2026-05-15 — Task #92 — extended scan to include `evidence_export`
  via `admin_audit_log WHERE action='auditor_kit_download'` (only the
  `client_portal` + `partner_portal` auth-method branches; admin +
  legacy-token carved out). Audit-row enrichment shipped in commit
  `5ce77722` so the scan can read `details->>'site_id'` +
  `details->>'client_org_id'` directly — no JOIN to `sites` required.
- 2026-05-15 — Task #98 — extended scan to include `new_site_onboarding`
  (via `sites` table) + `new_credential_entry` (via `site_credentials`
  JOIN `sites`). Closes the runtime-backstop gap surfaced by Task #90
  Gate B: those two workflows became gated when #90 moved them from
  `_DEFERRED_WORKFLOWS` → `BAA_GATED_WORKFLOWS`, but the invariant
  itself hadn't yet been taught to scan their evidence rows. A code
  path that bypassed `enforce_or_log_admin_bypass` on either workflow
  is now caught at runtime, not just at build time.
