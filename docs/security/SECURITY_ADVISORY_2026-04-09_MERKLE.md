# Security Advisory — OSIRIS-2026-04-09-MERKLE-COLLISION

**Title:** Merkle batch_id collision in evidence aggregator
**Date discovered:** 2026-04-09
**Date remediated:** 2026-04-09 (same day)
**Affected versions:** all `mcp-server` builds prior to commit `965dd36`
**Severity:** HIGH (evidence integrity)
**Status:** REMEDIATED + DISCLOSED + BACKFILLED

---

## Summary

OsirisCare's evidence pipeline groups individual compliance bundles into
hourly Merkle batches before submitting the batch root to OpenTimestamps
for Bitcoin anchoring. A bug in the batch-id generator caused two batches
in the same UTC hour for the same site to share the same `batch_id`. The
second sub-batch's Merkle root was silently dropped (`ON CONFLICT (batch_id)
DO NOTHING`) while its constituent bundles were still updated to point at
the first batch's stored root. The result: those bundles' stored Merkle
proofs could not verify against the anchored Bitcoin root. **An auditor
running 5 lines of Python against any of these bundles would have seen
verification fail.**

We caught the bug during a Session 203 internal round-table audit of our
own audit-proof display. We have remediated the writer, backfilled the
affected bundles to a clearly-labeled `legacy` state, written regression
tests, and are publishing this advisory as part of our standing
commitment to disclose any evidence-integrity event proactively.

We are aware of **zero** customer reports of failed audits caused by this
bug. We are aware of **zero** evidence that the bug was exploited or
discovered by anyone outside our team.

---

## Affected scope (verified on production database)

- **Bundles affected:** 1,198
- **Batches affected:** 47
- **Sites affected:** 2
- **Date range of affected bundles:** approximately 2026-02-09 → 2026-04-09
- **What "affected" means:** the bundle's stored `merkle_proof` does not
  verify against the stored Merkle root for its `merkle_batch_id`. The
  bundle's per-bundle SHA-256 hash, Ed25519 signature, and chain-hash
  linkage were all unaffected — only the per-batch Merkle proof was
  broken.

The full list of affected `bundle_id` values is available on request to
any current customer or auditor with a verified business need. Email
`security@osiriscare.net`.

---

## Root cause

The `process_merkle_batch()` function in `mcp-server/central-command/backend/evidence_chain.py`
derived the batch identifier from `site_id` + the current UTC hour:

```python
batch_id = f"MB-{site_id[:20]}-{batch_hour.strftime('%Y%m%d%H')}"
```

When the function ran twice in the same hour for the same site (which
happened whenever a fresh set of evidence rolled in every ~15 minutes
during peak collection windows), both calls produced the same `batch_id`.

The flow on collision:

1. **First call** built a Merkle tree T1 from N bundles. INSERT'd
   `(batch_id, T1.root, N)` into `ots_merkle_batches`. UPDATE'd each
   of T1's bundles with `merkle_proof = <proof for T1>`.
2. **Second call** (same UTC hour) built a Merkle tree T2 from a
   *different* set of bundles. INSERT'd `(batch_id, T2.root, M)` into
   `ots_merkle_batches`, hit `ON CONFLICT (batch_id) DO NOTHING`, and
   silently dropped T2's root. Then UPDATE'd each of T2's bundles with
   `merkle_proof = <proof for T2>` and pointed them at the same row that
   stored T1's root.

The result: T2's bundles had stored proofs that referenced a tree
(T2) whose root was never recorded. When an external party walked the
proof path, they arrived at T2's root, but the stored row at
`merkle_batch_id` contained T1's root. The verification failed.

The bug was masked by:
- The per-bundle Ed25519 signature still verifying (the bundle hash
  itself was untouched)
- The hash-chain linkage still verifying (`prev_hash` was untouched)
- The portal's "Chain Valid" badge displaying the server's self-attested
  status (which Session 203 Batch 5 also fixed — see C4 in the same
  audit)

Only walking the Merkle proof end-to-end against the stored root would
expose it. Our internal audit walked three real proofs and found three
failures.

---

## Fix

**Commit:** `965dd36` (mcp-server, 2026-04-09)

```python
# Append a randomized 8-hex suffix per call so two calls in the same
# hour cannot collide.
unique_suffix = secrets.token_hex(4)
batch_id = f"MB-{site_id[:20]}-{batch_hour.strftime('%Y%m%d%H')}-{unique_suffix}"
```

The `ON CONFLICT (batch_id) DO NOTHING` clause is preserved as a
belt-and-suspenders safety net (it can no longer fire under normal
operation, but stays as a defensive guard against future retry/restore
edge cases).

Migration `148_fix_broken_merkle_batches.sql` reclassifies every bundle
in any collided batch to `ots_status = 'legacy'` with `merkle_batch_id`,
`merkle_proof`, and `merkle_leaf_index` set to NULL. The migration is
transactional and writes both per-batch and fleet-wide entries to
`admin_audit_log` so the remediation itself is auditable.

The reclassification is conservative: rather than try to algorithmically
recover "the bundles that DID belong to the stored root", we mark the
entire collided batch as `legacy`. The reasoning: each sub-batch started
its `merkle_leaf_index` from 0, so the leaf-index values from different
sub-batches overlap in storage and we cannot tell from the database alone
which bundle came from which sub-batch. A `legacy`-classified bundle is
honest about its state. A bundle that claims to verify but actually
doesn't is a legal liability.

---

## Migration verification

Migration 148 was applied to production at approximately 16:05 UTC on
2026-04-09 with the following result:

```
BEGIN
SELECT 1198      -- bundles identified
INSERT 0 47      -- per-batch audit-log entries
UPDATE 1198      -- bundles reclassified
INSERT 0 1       -- fleet-wide audit-log summary
COMMIT
```

Bundle counts before vs after:

| status | before | after |
|---|---:|---:|
| anchored | 132,261 | 131,150 |
| legacy | 100,972 | 102,170 |
| pending | 131 | 97 |
| batching | 11 | 7 |

Delta: −1,155 anchored / +1,198 legacy (small variance from concurrent
ongoing writes).

Every reclassified bundle has a corresponding row in `admin_audit_log`
with `action = 'MERKLE_BATCH_BUNDLE_RECLASSIFIED'` and a per-batch
detail payload. A fleet-wide summary row (`MERKLE_BATCH_BACKFILL_COMPLETE`)
captures the totals. These are queryable today by any current customer
with admin access; auditors with appropriate access can confirm the
remediation independently.

---

## Regression tests

`tests/test_merkle_batch_id_uniqueness.py` (9 new tests):

- Writer appends a random suffix to `batch_id`
- Comment references the C1 finding so future readers understand the why
- `ON CONFLICT (batch_id) DO NOTHING` kept as the safety net
- Migration is transactional (BEGIN/COMMIT)
- Migration identifies broken batches by count mismatch
- Migration reclassifies to `legacy` and clears merkle fields
- Migration writes per-batch + fleet summary audit rows
- Migration audit details include the reason

These tests run in CI on every commit and would fail loudly if the
writer ever regressed to the colliding format.

---

## How to verify the remediation

Any customer or auditor can verify the remediation against their own
data:

**1. Check that migration 148 ran:**

```sql
SELECT COUNT(*) FROM admin_audit_log
WHERE action = 'MERKLE_BATCH_BACKFILL_COMPLETE';
-- Expected: 1
```

**2. Check that no current bundles claim to verify against a smaller
stored tree:**

```sql
SELECT COUNT(*) FROM compliance_bundles cb
JOIN ots_merkle_batches mb ON mb.batch_id = cb.merkle_batch_id
WHERE cb.merkle_leaf_index IS NOT NULL
  AND cb.merkle_leaf_index >= mb.bundle_count;
-- Expected: 0
```

**3. Download a fresh auditor kit and run `verify.sh`:**

```bash
curl -O https://api.osiriscare.net/api/evidence/sites/{your-site-id}/auditor-kit
unzip osiriscare-auditor-kit-*.zip
cd auditor-kit-*/
bash verify.sh
```

The expected output is a clean PASS for hash chain + signatures, with
the affected bundles correctly counted as `legacy` rather than
`anchored`. If you see any FAIL line for a bundle that is NOT marked
`legacy`, please contact `security@osiriscare.net` immediately.

---

## Why we are publishing this

The April 2026 Delve / DeepDelver scandal — in which a Y-Combinator-backed
compliance automation startup was accused of fabricating audit evidence
and identical-boilerplate reports across hundreds of clients — has put
the entire compliance-automation category on notice. The market response
will reward platforms that demonstrate evidence integrity and punish
platforms that rely on opacity.

Our position is that **the right response to finding a bug in your own
system is to disclose it proactively, prove the remediation, and let
customers verify the fix independently**. This is the discipline we hold
ourselves to. This advisory is the first entry in what will be a recurring
public security advisories index — we plan to publish every meaningful
evidence-integrity event the same way, on the same day we remediate it.

If your prior compliance vendor has not published a similar advisory in
response to this scandal, that absence is itself information.

---

## Timeline

| Time (UTC, 2026-04-09) | Event |
|---|---|
| ~12:00 | Session 203 round-table audit of audit-proof display begins |
| ~13:30 | Subagent walks 3 real Merkle proofs by hand; all 3 fail |
| ~14:00 | Root cause traced to `batch_id` collision in `process_merkle_batch` |
| ~14:30 | Production query confirms 1,198 bundles across 47 batches affected |
| ~15:08 | Writer fix committed (`965dd36`) |
| ~15:10 | Migration 148 applied to production database |
| ~15:11 | Reclassification verified; 1,198 bundles now `legacy` |
| ~15:12 | Regression test suite added |
| ~16:13 | Code deployed to production via CI/CD |
| (this doc) | Public disclosure published |

Total time from discovery to remediation + disclosure: approximately
**4 hours**.

---

## Contact

- General: `support@osiriscare.net`
- Security: `security@osiriscare.net`
- Compliance evidence questions: include your site_id and we will
  generate a fresh auditor-kit ZIP for you within one business day

---

## Document integrity

This advisory will be published at `https://api.osiriscare.net/security/advisories/2026-04-09-merkle`
and is reachable from the OsirisCare website. The canonical source is
this file in the OsirisCare repository at
`docs/security/SECURITY_ADVISORY_2026-04-09_MERKLE.md`. Any modification
to the published content after first publication will be added below
with a dated changelog.

**Changelog:**

- 2026-04-09 — Initial publication
