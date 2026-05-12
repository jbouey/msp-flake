# Maya P0-C Verdict — Backfill Decision

## Decision: OPTION B (Parallel `l2_escalations_missed` table + explicit disclosure)

## Reasoning (≤400 words)

**§164.528 lens.** L2 decisions do not contain PHI (boundary scrubbed at
appliance egress), so this is NOT a §164.528 disclosure-accounting
event in the strict sense. However, the auditor kit's `v_l2_outcomes`
is consumed by auditors as technical evidence that the auto-healing
flywheel operates as documented. The Dashboard.tsx:764 customer copy —
*"Incident types recurring 3+ times in 4 hours. These bypass L1 and go
to L2 for root-cause analysis"* — is a contractual technical-control
claim. A 320-incident gap at multi-daemon sites means we silently
under-delivered that claim. The honest framing is "platform control
gap," not "PHI disclosure."

**Mig-300 precedent is NOT equivalent.** Mig 300 backfilled rows where
the L2 LLM *actually ran* but the audit-row INSERT raised mid-flight —
synthetic rows preserved the FACT of an event that demonstrably
occurred. Here, the L2 LLM **never ran**. Inserting synthetic rows
with `escalation_reason='recurrence_backfill'` into `l2_decisions`
would fabricate evidence of a root-cause analysis that did not happen.
That is the exact forgery pattern Session 218's privileged-pre-trigger
round-table rejected: *"Synthetically grafting attestation rows…
creates a chain that appears to satisfy the inviolable rule but does
not."* Option A is forbidden by that precedent.

**Auditor-kit determinism contract.** Session 218's byte-identical
promise is the load-bearing tamper-evidence guarantee. Option A
mutates `v_l2_outcomes` retroactively — every auditor who downloaded
the kit before backfill versus after sees a different chain.json /
v_l2_outcomes payload with NO chain advancement signal. That violates
the contract. Option B's parallel table is OUTSIDE `v_l2_outcomes` and
ships in a dedicated `disclosures/missed_l2_escalations.json` section
of the kit — same shape as the privileged-pre-trigger precedent — with
a kit_version bump (2.1 → 2.2) that gives the chain a legitimate
forward-progression signal explaining the new section's appearance.

**Why not Option C.** Pure-advisory disclosure (Option C) is the
right shape for the 3 pre-mig-175 privileged orders because there were
THREE rows and they're enumerable in a markdown table. 320 incidents
across 7 days of one site is operationally a different scale —
auditors need a queryable, JOINable artifact, not prose. Option B
delivers both: a structured table for query/JOIN AND an advisory
narrative for context. It is the strict superset of Option C.

**Net:** Option B preserves chain immutability (no historical mutation
of `l2_decisions`), gives auditors a queryable record of the gap,
ships an honest customer-facing advisory, and follows the Session 218
disclosure-over-backfill precedent for fabrication-class gaps.

## Downstream artifact list

- **Migration name + number:** `307_l2_escalations_missed_table.sql`
  (next free; mig 305 = delegate_signing_key, mig 306 = L1 false-heal
  backfill per CLAUDE.md Session 220 lock-in — confirm before commit).
  Creates `l2_escalations_missed` with columns: `id BIGSERIAL PK`,
  `incident_id TEXT NOT NULL`, `site_id TEXT NOT NULL`,
  `incident_type TEXT NOT NULL`, `appliance_id TEXT`,
  `recurrence_count INT NOT NULL`, `window_start TIMESTAMPTZ`,
  `window_end TIMESTAMPTZ`, `detected_at TIMESTAMPTZ NOT NULL`,
  `disclosure_reference TEXT NOT NULL DEFAULT
  'SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING'`,
  `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`. Indexes on
  `(site_id, incident_type)` and `(detected_at)`. NO foreign key to
  `incidents` — the incident may age out before the table is queried.
  Comment field on table cites Session 220 + the advisory.

- **ALLOWED_EVENTS entry needed:** **NO.** This is operational gap
  disclosure, not a privileged action against a customer appliance.
  The privileged-access chain protects ACTIONS that change appliance
  state with named-human authorization; recording a control-gap
  disclosure is closer to `appliance_relocation` (system-signed, NOT
  in `ALLOWED_EVENTS`). The advisory itself + `admin_audit_log` row
  for the migration execution are sufficient.

- **Auditor-kit changes:**
  1. New section `disclosures/missed_l2_escalations.json` —
     deterministic dump of `l2_escalations_missed` rows WHERE
     `site_id = <kit site>`, sorted by `(detected_at, incident_id)`,
     emitted via `_kit_zwrite` with `sort_keys=True`. Empty array
     when no rows for the site.
  2. New advisory file `disclosures/SECURITY_ADVISORY_2026-05-12_
     RECURRENCE_DETECTOR_PARTITIONING.md` shipped to every kit
     (parity with existing advisories per Session 218 sibling-header
     parity rule).
  3. **kit_version bump 2.1 → 2.2** across ALL FOUR surfaces
     (X-Kit-Version header, chain_metadata, pubkeys_payload,
     identity_chain_payload, iso_ca_payload) per CLAUDE.md rule.
     Determinism contract preserved: this is a structural version
     advance, not a mutation of prior payload shape.
  4. README amendment documents the new `disclosures/` entry and
     reaffirms that L2-SLA is technical-control evidence, NOT a
     §164.528 disclosure accounting.

- **Customer-notification required:** **YES, advisory shape.**
  - The advisory file ships in EVERY auditor kit (substrate-engine
    informational invariant `l2_recurrence_partitioning_disclosed`
    surfaces on substrate dashboard, same shape as
    `pre_mig175_privileged_unattested`).
  - Direct customer email opt-IN: only for sites with non-zero
    `l2_escalations_missed` rows. Email is OPAQUE-mode per Session
    218 rule — subject "Service advisory — auditor kit update
    available", body redirects to authenticated portal. No clinic
    names / count / incident types in the SMTP channel.
  - NO PDF amendment to prior auditor kit downloads. The advisory's
    independent-verification section (mirror of Session 218
    privileged-pre-trigger) lets an auditor reconcile their old kit
    against the new one without us re-issuing PDFs.
  - Marketing copy review: Dashboard.tsx:764 stays AS-IS (the
    semantic is correct; this was a bug not a doc lie). No legal-
    language audit needed — "root-cause analysis" is not in the
    Session 199 banned-word list.

- **New CI gates:**
  1. `tests/test_recurrence_detector_uses_site_id.py` — AST gate on
     `agent_api.py` + `mcp-server/main.py` recurrence-detector
     callsites: any `COUNT(*)` query against incidents within a
     recurrence-velocity context MUST partition by `site_id` +
     `incident_type`, NEVER by `appliance_id`. Forward-fix lockdown.
  2. `tests/test_l2_escalations_missed_immutability.py` — table is
     INSERT-ONLY; any code that writes UPDATE/DELETE against it
     fails the gate (parallels mig 273 transfer-table immutability
     pattern).
  3. `tests/test_auditor_kit_disclosures_section.py` — kit must
     contain `disclosures/missed_l2_escalations.json` AND the
     advisory `.md` file when kit_version >= 2.2; both routed through
     `_kit_zwrite`.
  4. Extend `tests/test_auditor_kit_deterministic.py` to cover the
     new disclosures section (two consecutive downloads with no
     new `l2_escalations_missed` rows must be byte-identical).

- **Backfill SQL shape (Option B):** pseudocode only —
  ```sql
  -- mig 307 backfill detector — runs once at migration apply, uses
  -- the canonical chronic-pattern detector (NOT the buggy one) to
  -- enumerate what should have escalated.
  INSERT INTO l2_escalations_missed (
      incident_id, site_id, incident_type, appliance_id,
      recurrence_count, window_start, window_end, detected_at,
      disclosure_reference
  )
  SELECT
      i.id::text, i.site_id, i.incident_type, i.appliance_id,
      v.recurrence_count, v.window_start, v.window_end, NOW(),
      'SECURITY_ADVISORY_2026-05-12_RECURRENCE_DETECTOR_PARTITIONING'
  FROM incidents i
  JOIN incident_recurrence_velocity v
       ON v.site_id = i.site_id
      AND v.incident_type = i.incident_type
  WHERE v.is_chronic = TRUE
    AND i.created_at BETWEEN v.window_start AND v.window_end
    AND i.resolution_tier != 'L2'  -- L2 never ran
    AND NOT EXISTS (
        SELECT 1 FROM l2_decisions d
        WHERE d.incident_id = i.id::text
    );
  ```
  Wrapped in `BEGIN; ... COMMIT;` with `admin_audit_log` row
  identical-shape to mig 300's, `username='system:mig-307'`.

- **Substrate invariant impact:**
  1. NEW sev3-informational invariant
     `l2_recurrence_partitioning_disclosed` — fires while any rows
     exist in `l2_escalations_missed`; never auto-resolves;
     disclosure IS the resolution (mirrors Session 218
     `pre_mig175_privileged_unattested`).
  2. EXISTING `l2_resolution_without_decision_record` (mig 300
     class) is UNCHANGED — it asserts L2-tier-without-decision-row,
     a different class.
  3. NEW sev2 invariant
     `chronic_pattern_without_l2_escalation_or_disclosure` — fires
     for any incident in `incident_recurrence_velocity is_chronic=
     TRUE` window that has neither (a) an `l2_decisions` row nor
     (b) an `l2_escalations_missed` row. This is the forward-
     looking gate that prevents the bug from regressing silently
     between fix-deploy and any future detector partitioning
     mistake.

## Risk if chosen option is wrong

If Option B is wrong (auditor or counsel argues the parallel table is
itself a chain manipulation), the fallback is Option C — the advisory
+ substrate-disclosure invariant survives untouched; we drop the table
and the kit's `disclosures/` JSON section, leaving only the markdown
advisory. The Option B → Option C downgrade is one rollback migration
and a kit_version revert. The reverse (starting at A and discovering
the forgery framing) requires explicit retraction in a follow-up
advisory and a documented chain-mutation event — significantly worse
forensic posture.
