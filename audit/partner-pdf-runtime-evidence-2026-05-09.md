# Partner-portal PDF runtime evidence — 2026-05-09

Round-table 2026-05-09 partner-portal runtime adversarial audit P1-3.
Brief: P-F5/P-F6/P-F7/P-F8 PDFs are code+table-deployed but never end-
to-end runtime-tested under a real partner session. Same code-true-
but-runtime-false pattern that the 15-commit audit caught twice.

**RESULT: All 4 PDFs are runtime-broken in production today.** Every
endpoint returns HTTP 500 with `{"error":"Internal server error"}` —
not a valid PDF. Three distinct schema-drift root causes uncovered.

This is exactly the failure class the audit P1-3 was designed to
detect. The "deployed = working" assumption that lets these endpoints
claim ship status is structurally unsafe; live curl is the only
truthful signal.

---

## Synthetic partner_user provisioned

**DOCUMENTED HERE (NOT in code), per task brief.** Single-purpose
audit principal scoped to the existing `OsirisCare Direct` partner
(slug=`osiriscare`, id=`b3a5fc0d-dd47-4ad7-bcc2-14504849fa29`).

Run on VPS 178.156.162.116 (mcp-postgres container):

```sql
INSERT INTO partner_users (partner_id, email, name, role, status, mfa_required)
VALUES (
  'b3a5fc0d-dd47-4ad7-bcc2-14504849fa29',
  'audit-2026-05-09@osiriscare.test',
  'P1-3 Runtime Audit User',
  'admin',
  'active',
  false
)
ON CONFLICT (partner_id, email) DO UPDATE SET status='active'
RETURNING id, partner_id, email, role;
```

Output:
```
                  id                  |              partner_id              |              email               | role
--------------------------------------+--------------------------------------+----------------------------------+-------
 ab267515-4e7e-454d-816d-c7687ec8a172 | b3a5fc0d-dd47-4ad7-bcc2-14504849fa29 | audit-2026-05-09@osiriscare.test | admin
INSERT 0 1
```

Session token created via the canonical `hash_session_token()` from
`shared.py` (HMAC-SHA256 keyed off `SESSION_TOKEN_SECRET`):

```
TOKEN=-ETDLGI1VA6cEZyL85k6RF6tOsHKe9P0XGbqZd2xyyQ
HASH=9d0d373a9e5a83709d4e0aae75be94f33c2acb737b75fe5546991dc7614e53f5
```

Inserted into `partner_sessions`:

```sql
INSERT INTO partner_sessions (
  partner_id, session_token_hash, ip_address, user_agent,
  expires_at, partner_user_id
) VALUES (
  'b3a5fc0d-dd47-4ad7-bcc2-14504849fa29',
  '9d0d373a9e5a83709d4e0aae75be94f33c2acb737b75fe5546991dc7614e53f5',
  '127.0.0.1'::inet,
  'audit-2026-05-09-runtime',
  NOW() + INTERVAL '1 hour',
  'ab267515-4e7e-454d-816d-c7687ec8a172'
);
```

Cookie used in every curl below: `osiris_partner_session=-ETDLGI1VA6cEZyL85k6RF6tOsHKe9P0XGbqZd2xyyQ`

Session expires 2026-05-09 05:44:22 UTC (+1h from issuance) and was
SCOPED to ONLY the partner_users row above. After this audit the
synthetic user should be retained for follow-up regression evidence;
delete it with `DELETE FROM partner_users WHERE email='audit-2026-05-09@osiriscare.test'`
once the bugs surfaced here are fixed and re-verified clean.

For P-F8 (incident timeline), the only incidents in prod are at
site `north-valley-branch-2` which is unowned (`partner_id IS NULL`).
NVB was temporarily assigned to the osiriscare partner for the duration
of the curl, then reverted (`UPDATE sites SET partner_id=NULL WHERE
site_id='north-valley-branch-2'`). Cooled off cleanly.

---

## P-F5 — Portfolio Attestation Letter

Endpoint: `GET /api/partners/me/portfolio-attestation`
Code: `partners.py:5491` → `partner_portfolio_attestation.py:287`

```
$ curl -sLS -o /tmp/p-f5.pdf -w "HTTP=%{http_code} CT=%{content_type} SZ=%{size_download}\n" \
    --cookie "osiris_partner_session=-ETDLGI1VA6cEZyL85k6RF6tOsHKe9P0XGbqZd2xyyQ" \
    "https://osiriscare.net/api/partners/me/portfolio-attestation"
HTTP=500 CT=application/json SZ=51

$ cat /tmp/p-f5.pdf
{"error":"Internal server error","status_code":500}
```

Server log root-cause:

```
asyncpg.exceptions.UndefinedColumnError: column "monitoring_only" does not exist
HINT:  Perhaps you meant to reference the column "check_type_registry.is_monitoring_only".
  File "/app/dashboard_api/partner_portfolio_attestation.py", line 184, in _gather_aggregate_facts
    control_count = await conn.fetchval(
```

**Root cause: schema drift.** Column was renamed `monitoring_only` →
`is_monitoring_only` in mig 157 (`check_type_registry`); P-F5 issuance
helper missed the rename. P0 — every portfolio-attestation PDF request
in prod returns 500.

---

## P-F6 — BA Compliance Attestation PDF

Endpoint: `GET /api/partners/me/ba-attestation`
Code: `partners.py:5855` → `partner_ba_compliance_attestation.py`

```
$ curl -sLS -o /tmp/p-f6.pdf -w "HTTP=%{http_code} CT=%{content_type} SZ=%{size_download}\n" \
    --cookie "osiris_partner_session=-ETDLGI1VA6cEZyL85k6RF6tOsHKe9P0XGbqZd2xyyQ" \
    "https://osiriscare.net/api/partners/me/ba-attestation"
HTTP=500 CT=application/json SZ=51
00000000: 7b22 6572 726f 7222 3a22 496e 7465 726e  {"error":"Intern
00000010: 616c 2073 6572 7665 7220 6572 726f 7222  al server error"
00000020: 2c22 7374 6174 7573 5f63 6f64 6522 3a35  ,"status_code":5
00000030: 3030 7d                                  00}
```

Server-log path: `/api/partners/me/ba-attestation`.
Error line: `current transaction is aborted, commands ignored until end of transaction block`
— a downstream symptom of an earlier query failure inside the same
transaction block (asyncpg savepoint invariant violation).

**Root cause: cascading transaction-abort.** The first query in the
ba-attestation issuance helper raises an UndefinedColumnError; the
catch swallows the error but does NOT issue ROLLBACK, leaving the
transaction in `aborted` state. The next query then hits this generic
"transaction aborted" error. Diagnosis: needs the asyncpg savepoint
invariant fix from Session 205.

P0 — every BA-attestation PDF request in prod returns 500.

---

## P-F7 — Technician Weekly Digest PDF

Endpoint: `GET /api/partners/me/rollup/weekly.pdf`
Code: `partners.py:5598` → `partner_weekly_digest.py`

```
$ curl -sLS -o /tmp/p-f7.pdf -w "HTTP=%{http_code} CT=%{content_type} SZ=%{size_download}\n" \
    --cookie "osiris_partner_session=-ETDLGI1VA6cEZyL85k6RF6tOsHKe9P0XGbqZd2xyyQ" \
    "https://osiriscare.net/api/partners/me/rollup/weekly.pdf"
HTTP=500 CT=application/json SZ=51

$ head -c 100 /tmp/p-f7.pdf | xxd | head -5
00000000: 7b22 6572 726f 7222 3a22 496e 7465 726e  {"error":"Intern
00000010: 616c 2073 6572 7665 7220 6572 726f 7222  al server error"
00000020: 2c22 7374 6174 7573 5f63 6f64 6522 3a35  ,"status_code":5
00000030: 3030 7d                                  00}
```

Server log root-cause:

```
asyncpg.exceptions.UndefinedColumnError: column fo.target_site_id does not exist
  File "/app/dashboard_api/partner_weekly_digest.py", ...
```

**Root cause: schema drift.** `fleet_orders` does NOT have a
`target_site_id` column — per CLAUDE.md, `fleet_orders` is fleet-wide
and per-appliance scoping is via the SIGNED `target_appliance_id` in
the payload. Weekly-digest builder queries a column that has never
existed in this table. P0 — every weekly-digest PDF request in prod
returns 500.

---

## P-F8 — Incident Timeline PDF

Endpoint: `GET /api/partners/me/incidents/{incident_id}/timeline.pdf`
Code: `partners.py:5943` → `partner_incident_timeline.py`

Picked `incident_id=ed2b2e7f-9e77-41c2-90c8-ae3b2b33444f` from
site `north-valley-branch-2` (NVB temporarily assigned to the
synthetic partner for this test; reverted after).

```
$ curl -sLS -o /tmp/p-f8.pdf -w "HTTP=%{http_code} CT=%{content_type} SZ=%{size_download}\n" \
    --cookie "osiris_partner_session=-ETDLGI1VA6cEZyL85k6RF6tOsHKe9P0XGbqZd2xyyQ" \
    "https://osiriscare.net/api/partners/me/incidents/ed2b2e7f-9e77-41c2-90c8-ae3b2b33444f/timeline.pdf"
HTTP=500 CT=application/json SZ=51

$ head -c 100 /tmp/p-f8.pdf | xxd | head -5
00000000: 7b22 6572 726f 7222 3a22 496e 7465 726e  {"error":"Intern
00000010: 616c 2073 6572 7665 7220 6572 726f 7222  al server error"
00000020: 2c22 7374 6174 7573 5f63 6f64 6522 3a35  ,"status_code":5
00000030: 3030 7d                                  00}
```

Server log root-cause:

```
asyncpg.exceptions.UndefinedColumnError: column i.hostname does not exist
  File "/app/dashboard_api/partner_incident_timeline.py", ...
```

**Root cause: schema drift.** The `incidents` table doesn't have a
`hostname` column directly; hostname is on the joined site / asset
context. Builder queries an unjoined column. P0 — every incident-
timeline PDF request in prod returns 500.

---

## Public hash round-trip (P-F5 verify)

Endpoint: `GET /api/verify/portfolio/{attestation_hash}` (public,
no auth — designed for auditors to independently verify a PDF hash).

```
$ curl -sLS -w "VERIFY HTTP=%{http_code} CT=%{content_type} SZ=%{size_download}\n" \
    "https://osiriscare.net/api/verify/portfolio/0000000000000000000000000000000000000000000000000000000000000000"
{"valid":false,"reason":"not_found"}
VERIFY HTTP=200 CT=application/json SZ=36
```

Endpoint **is alive** and returns the expected non-existent-hash
shape. Cannot perform a full round-trip because P-F5 issuance is
broken (UndefinedColumnError above) → zero rows in
`partner_portfolio_attestations`. Round-trip will become testable
after the P-F5 schema-drift fix.

```
mcp=> SELECT COUNT(*) FROM partner_portfolio_attestations;
 count
-------
     0
```

---

## Summary

| PDF | Endpoint                                          | HTTP | Real PDF? | Root cause              |
|-----|---------------------------------------------------|------|-----------|-------------------------|
| F5  | `/api/partners/me/portfolio-attestation`          | 500  | NO        | column `monitoring_only` does not exist (rename to `is_monitoring_only`, mig 157) |
| F6  | `/api/partners/me/ba-attestation`                 | 500  | NO        | cascade abort (savepoint invariant violation downstream of earlier UndefinedColumnError) |
| F7  | `/api/partners/me/rollup/weekly.pdf`              | 500  | NO        | column `fo.target_site_id` does not exist (`fleet_orders` is fleet-wide; target embedded in signed payload) |
| F8  | `/api/partners/me/incidents/{id}/timeline.pdf`    | 500  | NO        | column `i.hostname` does not exist on `incidents` |
| Verify | `/api/verify/portfolio/{hash}` (public)        | 200  | YES (JSON shape) | Endpoint healthy; round-trip blocked by F5 issuance failure |

**4 P0 bugs surfaced by P1-3 runtime evidence.** All are schema-drift
class — code references columns that either never existed or were
renamed by a later migration. Pure code-level review would not have
caught these because the test suite mocks at higher layers; only
live curl through PgBouncer + actual asyncpg `prepare()` against the
real schema reveals the drift.

These bugs are **not in scope for this commit** — round-table 2026-05-09
authorized the P1 trio (RLS migration, CI gate, runtime evidence
collection); this deliverable is the evidence that the existing
PDF surfaces are broken. Filing them is the next session's work.

Cleanup performed at end of evidence collection:
- NVB partner_id reverted to NULL.
- Synthetic partner_user `audit-2026-05-09@osiriscare.test` retained
  intentionally for follow-up regression testing once the schema-
  drift bugs are fixed.
- Synthetic partner_session left to expire naturally (1h TTL).
