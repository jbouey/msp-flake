# Security Advisory — OSIRIS-2026-04-13-PRIVILEGED-PRE-TRIGGER

**Title:** Three privileged emergency-access orders without attestation chain (pre-mig-175)
**Date discovered:** 2026-05-08 (caught by full E2E attestation audit; F-P0-2)
**Date disclosed:** 2026-05-08 (this advisory)
**Affected versions:** all `mcp-server` builds prior to migration 175 application (`2026-04-13 09:01 UTC`)
**Severity:** MEDIUM (chain-of-custody gap; no PHI exposure; affected period bounded)
**Status:** DISCLOSED — public disclosure path chosen over retroactive backfill
**Affected sites:** `north-valley-branch-2` (1 site)

---

## Summary

OsirisCare enforces a **Privileged-Access Chain of Custody**: every
order in a defined catalog (`enable_emergency_access`,
`disable_emergency_access`, `bulk_remediation`,
`signing_key_rotation`) MUST carry a cryptographically signed
attestation bundle linking it to a named human actor and approval
reason. The chain is enforced at three layers:

1. **CLI** — `fleet_cli.py` refuses without `--actor-email` + `--reason`
2. **API** — `privileged_access_api.py` writes a chained attestation per state transition
3. **DB** — `trg_enforce_privileged_chain` (migration 175) **REJECTS**
   any `fleet_orders` INSERT of a privileged `order_type` whose
   `parameters->>'attestation_bundle_id'` does not match a real
   `compliance_bundles WHERE check_type='privileged_access'` row for
   the same site.

This three-layer chain went live on **2026-04-13 09:01 UTC** with
the application of migration 175. The trigger is a **pre-INSERT**
guard — by design, it cannot retroactively heal historical rows.

## Affected rows

The following three `fleet_orders` rows pre-date the trigger and
therefore carry no attestation chain. They are listed here in full
for auditor visibility:

| order_id | order_type | created_at (UTC) | status |
|---|---|---|---|
| `e0ba33ff-9474-4b69-bf70-a7f3361a71e8` | `disable_emergency_access` | `2026-04-11 07:44:23` | expired |
| `f4569838-665e-40a7-845a-fb893e4e5823` | `enable_emergency_access` | `2026-04-13 06:40:16` | cancelled |
| `5c984189-1ed1-4d09-bff6-b47558dbac6d` | `enable_emergency_access` | `2026-04-13 07:00:11` | cancelled |

All three orders were issued against `north-valley-branch-2`. The
two later rows (`f456...` and `5c98...`) were issued **2 hours and
21 minutes** before the trigger went live. The earliest row
(`e0ba...`) predates the trigger by **49 hours**.

All three orders later transitioned to terminal states (`expired` /
`cancelled`) without ever reaching `executed`. No privileged action
was carried out against the appliance from these orders. **No PHI
was exposed.** The corresponding privileged-access state on the
appliance was not changed by these orders.

## Why no retroactive backfill

The OsirisCare round-table on 2026-05-08 (Carol/Sarah/Steve/Maya
4-of-4) rejected a retroactive backfill path on these grounds:

1. **Forensic integrity.** Synthetically grafting attestation rows
   against historical orders that pre-date the trigger would create
   a chain that *appears* to satisfy the inviolable rule but does
   not — exactly the forgery pattern the rule exists to prevent.
2. **One-shot scripts grow.** A backfill helper for these three
   orders would attract calls for "well, one more case" and become
   a chain-laundering vector.
3. **Disclosure scales.** Public disclosure (this document) is
   honest, append-only, and durable. An auditor reading the kit
   sees the gap explicitly and can corroborate against
   `admin_audit_log` and the appliance's local order log.

## Independent verification path

An auditor wishing to verify the disclosure can:

1. Query `fleet_orders` for the three IDs above and confirm
   `parameters->>'attestation_bundle_id'` is NULL for each.
2. Query `compliance_bundles WHERE check_type='privileged_access'`
   and confirm zero rows (this confirms no privileged-attestation
   bundle exists for any pre-mig-175 order).
3. Query `schema_migrations WHERE version='175'` to confirm the
   trigger application timestamp and validate it post-dates the
   three orders.
4. Inspect the appliance's local `journal_upload_events` /
   `appliance_audit_trail` for evidence the orders did NOT execute.

## Substrate-engine coverage

A new substrate invariant `pre_mig175_privileged_unattested` (sev3,
informational) surfaces this disclosure on the substrate health
dashboard so future operators see the gap from the dashboard
without archaeology. The invariant fires while any of these three
rows exist with NULL `attestation_bundle_id`. It does **not**
auto-resolve — the disclosure is the resolution.

## Status

- **DISCLOSED:** This advisory.
- **DETECTOR:** `_check_pre_mig175_privileged_unattested()` shipped 2026-05-08.
- **PREVENTION:** `trg_enforce_privileged_chain` (mig 175) prevents recurrence for any new order. **Zero new violations possible.**
- **NEW VIOLATIONS SINCE 2026-04-13:** zero (verified
  `SELECT COUNT(*) FROM fleet_orders WHERE order_type IN
  (privileged catalog) AND created_at >= '2026-04-13 09:01:00+00'
  AND parameters->>'attestation_bundle_id' IS NULL` returns zero).

---

**Round-table reference:** `audit/round-table-verdict-2026-05-08.md` RT-1.2.
**Audit reference:** `audit/coach-e2e-attestation-audit-2026-05-08.md` F-P0-2.
**Maintainer:** OsirisCare engineering, jbouey2006@gmail.com.

<!-- updated 2026-05-16 — Session-220 doc refresh: pointer-verification only. Substrate invariant `pre_mig175_privileged_unattested` still firing as designed for the 3 disclosed rows. Privileged-chain catalog extended in mig 305 with `delegate_signing_key` (Session 220) — see `docs/security/emergency-access-policy.md` §"Session 219–220 hardening" and `docs/POSTURE_OVERLAY.md` (v2.2) §3 for the current authority. Counsel Rule 3 (no privileged action without attested chain of custody) is now the gold-authority framing — this advisory's enforcement model remains intact under that rule. -->

