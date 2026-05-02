# Security Advisory — OSIRIS-2026-05-02-PACKET-AUTOGEN-GAP

**Title:** Monthly HIPAA compliance packet auto-generation timed out for one site
**Date discovered:** 2026-05-02 (caught by `compliance_packets_stalled` substrate sev1)
**Date remediated:** 2026-05-02 (same day; packet backfilled from existing bundles)
**Affected versions:** mcp-server commits prior to query-optimization fix (tracked separately)
**Severity:** MEDIUM (HIPAA monthly attestation gap; underlying evidence intact)
**Status:** REMEDIATED + DISCLOSED + BACKFILLED

---

## Summary

OsirisCare auto-generates a monthly HIPAA compliance packet (a single-row
attestation in `compliance_packets`) for every active site on the 1st of
each month. The packet is an aggregate report — compliance score,
control posture, MTTR, critical-issue counts — derived from the
underlying `compliance_bundles` (Ed25519-signed, hash-chained,
OpenTimestamps-anchored) emitted during that month.

For one site, `physical-appliance-pilot-1aea78`, the auto-generation
job for **April 2026** repeatedly failed with
`QueryCanceledError: canceling statement due to statement timeout`. The
underlying CompliancePacket query plan exceeded the connection pool's
default `command_timeout` because the site emitted 962 bundles during
April (above the 60-second budget). The error was correctly logged at
`ERROR` level (`compliance_packet_autogen_failed`) per the no-silent-
failures rule, but no automatic recovery path existed — the next
hourly retry hit the same timeout.

The substrate `compliance_packets_stalled` sev1 invariant — added in
Session 214 (round-table 2026-05-01) precisely to catch this class —
fired at 2026-05-02 00:00:29 UTC, the first scheduled assertion run
after the calendar rolled over.

We have backfilled the missed packet from the **same crypto-verified
bundles** that existed throughout April, generated a forensic
disclosure (this document), and are publishing it as part of our
standing commitment to disclose any compliance-attestation gap
proactively.

We are aware of **zero** customer reports affected by this gap and
**zero** evidence the gap was exploited or relied upon by an external
auditor during the gap window.

---

## Affected scope (verified on production database)

- **Sites affected:** 1 (`physical-appliance-pilot-1aea78`)
- **Period missed:** April 2026 (2026-04-01 → 2026-04-30 UTC)
- **Bundles emitted during the period (intact, signed, anchored):** 962
- **Gap window (packet missing):** 2026-05-01 02:00 UTC → 2026-05-02 08:49 UTC (~31 hours)
- **Other sites' April packets:** unaffected (north-valley-branch-2 packet generated normally)
- **Other months for the affected site:** unaffected (Jan, Feb, Mar 2026 packets generated normally)

The underlying compliance_bundles for April 2026 were
**never lost, never altered, never re-signed.** Their Ed25519
signatures, prev-hash chain links, and OTS proofs are exactly what
they were on 2026-04-30. An auditor verifying the 962 bundles for
the affected period today against the auditor-kit verification
script will get the same byte-for-byte verification result they
would have gotten on May 1.

---

## What we did

1. **Detection (substrate-internal, automated, 2026-05-02 00:00 UTC):**
   `compliance_packets_stalled` sev1 invariant fired with structured
   details `{site_id, year=2026, month=4, framework='hipaa'}`.

2. **Root-cause investigation (round-table audit, 2026-05-02):**
   Searched mcp-server logs for the corresponding
   `compliance_packet_autogen_failed` entries. Identified
   `QueryCanceledError: statement timeout`. Verified 962 bundles
   exist for the affected period — no evidence loss.

3. **Backfill (2026-05-02 08:49 UTC):**
   Ran `scripts/backfill_compliance_packet.py` with
   `statement_timeout=0` against the orphan site. Script verifies
   bundle presence in the period before generation (refuses to
   create a phantom attestation if zero bundles exist) and uses the
   identical INSERT logic as the auto-gen loop. Packet generated;
   `compliance_score=82.2`, `packet_id=MON-202604-physical-applian`,
   `generated_by=backfill-script-2026-05-02`.

4. **Verification:**
   - `compliance_packets` row present and queryable.
   - `compliance_packets_stalled` substrate invariant auto-resolved on
     next assertion cycle.
   - `auditor-kit` ZIP regenerates with the new packet; the underlying
     bundle chain is unchanged.

5. **Followups filed:**
   - **Query optimization** (P1) — investigate the CompliancePacket
     query plan; either rewrite for index-friendliness or set per-call
     `statement_timeout=0` inside the auto-gen loop. The current loop
     already logs at `ERROR` so future failures will surface, but it
     should not RELY on substrate-detection + manual backfill as the
     recovery path.
   - **Auditor-kit auto-include** (P1) — make `auditor-kit` ZIP
     auto-include all `docs/security/SECURITY_ADVISORY_*.md` files
     (today only the Merkle disclosure is hard-coded into chain.json).
     Disclosure-first commitment requires every advisory be visible
     to the auditor without an auditor having to know to ask.

---

## Auditor verification

Any auditor with access to the affected site can confirm:

```bash
# 1. Download the auditor kit
curl -H "Authorization: Bearer <token>" \
  https://api.osiriscare.net/api/evidence/sites/physical-appliance-pilot-1aea78/auditor-kit?range=1..1000 \
  -o site-kit.zip

# 2. Verify the chain (verify.sh ships in the ZIP)
unzip site-kit.zip && cd site-kit && bash verify.sh

# 3. Inspect the April 2026 packet
docker exec mcp-postgres psql -U mcp -d mcp -c "
  SELECT site_id, year, month, framework, compliance_score, generated_by, generated_at
  FROM compliance_packets
  WHERE site_id='physical-appliance-pilot-1aea78'
    AND year=2026 AND month=4 AND framework='hipaa'
"
```

The packet's `generated_by='backfill-script-2026-05-02'` field is the
honest disclosure marker — auto-generated packets carry
`generated_by='system'`. Future auto-gen invocations for this period
will UPSERT the row with `generated_by='system'` if they succeed; the
disclosure remains valid as long as this advisory is publicly
accessible.

---

## Why we are disclosing this

Two reasons:

1. **HIPAA §164.316(b)(2)(i) requires monthly attestations be
   retrievable for 6 years.** A missing packet — even one whose
   underlying evidence is intact — is an auditor-visible gap. The
   honest path is to publish the gap, the cause, the fix, and the
   verification script.

2. **Standing commitment from Session 203.** When we discovered the
   Merkle batch-id collision (OSIRIS-2026-04-09), we publicly
   committed to disclose every future evidence-integrity or
   attestation event the same way. This is the second such
   disclosure. The first was a writer bug; this is an
   operational gap. Both deserve the same posture.

We will continue to disclose. If you operate a site under our
substrate and have questions about this advisory, please contact
support@osiriscare.net.

---

**Discovered:** 2026-05-02 00:00 UTC by `compliance_packets_stalled` sev1
**Backfilled:** 2026-05-02 08:49 UTC
**Disclosed:** 2026-05-02
**Advisory ID:** OSIRIS-2026-05-02-PACKET-AUTOGEN-GAP
