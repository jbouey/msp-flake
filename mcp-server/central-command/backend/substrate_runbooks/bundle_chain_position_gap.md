# bundle_chain_position_gap

**Severity:** sev1
**Display name:** Evidence chain has a position gap (chain-integrity violation)

## What this means (plain English)

A site's `compliance_bundles` rows in the last 24 hours have a
non-contiguous `chain_position` sequence. The chain should be
strictly sequential per-site (chain_position 0, 1, 2, 3, ...) —
a gap means either:

- The per-site `pg_advisory_xact_lock(hashtext(site_id),
  hashtext('attest'))` in `evidence_chain.py::create_compliance_
  bundle` failed to serialize a concurrent writer (lock acquired
  outside a transaction = no-op; or the lock contract was
  bypassed by a code path that doesn't go through
  `create_compliance_bundle`)
- A row was deleted or skipped post-INSERT (forbidden by mig 151
  `trg_prevent_audit_deletion` — if this fires, the trigger
  itself was disabled or bypassed, which is a separate sev1)

**Why this is sev1:** chain-position gaps are the most direct
form of chain-of-custody corruption. The auditor kit's
`verify.sh` walks the chain and would diverge on subsequent
downloads (kit hash flips → tamper-evidence violation). Same
severity class as `load_test_marker_in_compliance_bundles` +
`cross_org_relocate_chain_orphan`.

## Root cause categories

1. **Concurrent writers without serialization** — the prerequisite
   for Task #117 load test exists exactly to detect this case.
   If the load test is running against the affected site, verify
   the synthetic-site carve-out matches.
2. **Direct INSERT bypassing `create_compliance_bundle`** — any
   code path that writes to `compliance_bundles` MUST go through
   the helper. Grep for `INSERT INTO compliance_bundles` outside
   of `evidence_chain.py`.
3. **Disabled or bypassed `trg_prevent_audit_deletion`** — if a
   row was deleted, the trigger should have rejected. Verify the
   trigger is `ENABLE ALWAYS` (not just `ENABLE`) per mig 179.
4. **Migration backfill that skipped chain_position** — rare;
   would only apply within 24h if a recent mig touched the
   chain. Check `schema_migrations` for migrations applied in
   the last 24h that referenced `compliance_bundles`.

## Immediate action

1. **Identify the affected site + chain range:**
   ```sql
   SELECT site_id, chain_position, prev_chain_position, gap_size,
          bundle_id, created_at
     FROM (SELECT *, LAG(chain_position) OVER (
                       PARTITION BY site_id
                       ORDER BY chain_position
                   ) AS prev_chain_position
             FROM compliance_bundles
            WHERE created_at > NOW() - INTERVAL '24 hours'
          ) g
    WHERE g.prev_chain_position IS NOT NULL
      AND g.chain_position - g.prev_chain_position > 1
    ORDER BY site_id, chain_position;
   ```

2. **Verify the per-site advisory lock is firing for this site:**
   - Tail mcp-server logs since the first gap's `created_at` for
     `pg_advisory_xact_lock` references
   - Verify no `caller-not-in-transaction` assertion fired (would
     have raised LOUDLY per the Session 219 audit)

3. **Check for direct INSERT writers (forbidden):**
   ```bash
   grep -rn 'INSERT INTO compliance_bundles' \
       mcp-server/central-command/backend/*.py \
       | grep -v evidence_chain.py
   ```
   Any callsite found is a violation of the helper contract.

4. **Quarantine the affected range — do NOT delete:**
   - The kit's `verify.sh` will diverge on every download until
     the gap is resolved
   - DO mark the affected `bundle_id` range as `quarantined=TRUE`
     (if the schema supports it) OR record a `chain_gap_notice`
     entry in `admin_audit_log` with details of the gap and the
     remediation taken
   - Counsel-grade rule: NEVER delete a compliance_bundles row.
     §164.316(b)(2)(i) 7-year retention + mig 151
     `trg_prevent_audit_deletion` enforce.

5. **Operator notification:** the customer's auditor kit hash
   will have flipped if they download during the gap window.
   Reach out proactively — this is a chain-integrity event +
   meets the bar for §164.504(e)(2)(ii)(D) disclosure
   consideration (Counsel-queue Item 2).

## Verification

- Substrate panel: invariant clears on next 60s tick once the
  gap is closed (gap closes when a quarantine record is written
  + the affected range is excluded from the LAG window — currently
  not implemented; the invariant continues firing until the
  cluster has rolled out a gap-quarantine table).
- Two consecutive auditor-kit downloads for the affected site
  MUST produce byte-identical ZIPs.

## Escalation

- **>3 chain gaps in 24h on the same site:** P0 — the per-site
  advisory lock is functionally broken for that site. Pause all
  writes to the site (via flipping `sites.status='paused'` or a
  service-bus rate-limit) and investigate before reopening.
- **Gap on a customer-active site:** mandatory customer
  notification + counsel review.
- **Gap on the Task #117 load-test site:** EXPECTED if the
  carve-out is missing — verify and re-run the soak under the
  carve-out.

## Related runbooks

- `cross_org_relocate_chain_orphan.md` (sibling chain-integrity
  invariant — different class but same severity)
- `load_test_marker_in_compliance_bundles.md` (sibling — catches
  synthetic-site writes that shouldn't be in the chain)

## Change log

- 2026-05-16 — initial — Task #117 Sub-commit A. Prerequisite
  for the chain-contention load test; ships standalone so the
  load test (Sub-commits B/C/D) has a runtime gate that proves
  the per-site advisory lock works.
