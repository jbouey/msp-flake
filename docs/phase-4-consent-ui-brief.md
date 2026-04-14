# Migration 184 Phase 4 — Consent UI Brief

**Status:** SPEC ONLY. NOT YET BUILT. Starts after Phase 3 enforce has been
stable for 7+ days.
**Owner:** Phase 2.5 backend complete; this doc scopes the UI build that
unlocks enterprise-deal demos.

## Why this exists

Phase 2 ships the plumbing; Phase 2.5 ships the real hash chain; Phase 3
flips one class to enforce. What's missing is the **customer-facing
moment of consent** — the screen where a practice manager says
"yes, I authorize Acme IT to run DNS rotations on my network."

That screen is what closes enterprise deals. Without it, the consent
story is "trust us, we log intent to a database." With it, it's
"a named human from your practice authorized this, you can revoke in
one click, and every action is tied back to their signature."

## Three screens to build (no more)

### 1. Client portal — `/portal/consent`

Audience: practice manager (non-technical).

Layout: one card per class (12 total from `runbook_classes` seed),
grouped by risk level (low / medium / high).

Each card shows:
- `display_name` + short `description` (from the class seed)
- HIPAA controls badge list (`hipaa_controls` array)
- Current state: `Not requested` / `Pending` / `Active since {date}` /
  `Revoked {date}` / `Expired {date}`
- Primary action button:
  - `Not requested` → "Grant consent" (opens modal — see below)
  - `Active` → "Revoke" (red)
  - `Revoked / Expired` → "Re-grant"

Grant modal:
- Shows the class description + example actions (from
  `example_actions` JSONB)
- Email input (prefilled with portal session email if available)
- TTL slider: 90d / 180d / 365d / 730d (default 365)
- Legal boilerplate: "By clicking Grant, you authorize {partner_name}
  to execute OsirisCare-verified runbooks in the {class_name}
  category on your site. This consent can be revoked in one click
  from this same page. A cryptographic record is created at grant +
  revoke and stored in your compliance packet chain for 7 years."
- "Grant" button → POST /api/portal/site/{id}/consent/grant

Revoke modal:
- Reason textarea (min 10 chars — matches backend)
- "Your partner will be notified immediately. Remediation already
  queued will be canceled."
- "Revoke" button → PUT /api/portal/site/{id}/consent/{cid}/revoke

### 2. Partner portal — `/partner/site/:siteId/consent`

Audience: MSP tech (technical, speed-first).

Layout: compact table, 12 rows (one per class), sortable by
`active/missing`. Columns:
- Class ID + display name
- Risk level pill
- Active consent: email + grant date + expires-at
- Last action: "Client revoked 3d ago" / "—"
- Button: "Request consent" (partner-initiated flow — see below)

Request-consent flow (magic-link):
- Partner clicks "Request consent" for a class at a site
- POST /api/partners/me/consent/request — server creates a one-time
  token, inserts a row in `consent_request_tokens` (new table in
  migration 189), sends an email to the client's primary contact:
  "Acme IT is requesting your authorization to run {class} remediations
  on {site}. Review and approve: {link}"
- Client clicks link → portal authenticates via token → shows the
  grant-consent modal for that specific class
- On grant, token is consumed, row updated

### 3. Operator admin — `/admin/consent-rollout`

Audience: OsirisCare operator (internal).

Read-only dashboard showing rollout progress:
- Pie chart of class enforcement state (shadow / enforce / graduated)
- Table: per-site consent coverage % (active_consents / 12 classes)
- Recent `promoted_rule_events` filtered to `event_type LIKE 'runbook.%'`
- Button to toggle `RUNBOOK_CONSENT_ENFORCE_CLASSES` (ops-only; writes
  to a central config row; requires 2FA re-prompt)

## Net-new migration (189)

```sql
CREATE TABLE consent_request_tokens (
    token_hash TEXT PRIMARY KEY,          -- sha256 of the emailed token
    site_id TEXT NOT NULL REFERENCES sites(site_id),
    class_id TEXT NOT NULL REFERENCES runbook_classes(class_id),
    requested_by_email TEXT NOT NULL,     -- partner asking
    requested_for_email TEXT NOT NULL,    -- customer who will approve
    expires_at TIMESTAMPTZ NOT NULL,      -- default +72h
    consumed_at TIMESTAMPTZ,
    consumed_consent_id UUID REFERENCES runbook_class_consent(consent_id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_consent_request_tokens_site ON consent_request_tokens(site_id);
```

Add a nightly `expire_consent_request_tokens_loop` that zeroes expired
tokens (not deletes — audit trail).

## Components (new)

- `PortalConsentPage.tsx` — the client `/portal/consent` screen
- `PortalConsentCard.tsx` — one card per class (status + action button)
- `PortalConsentGrantModal.tsx` — 4-step form with legal boilerplate
- `PortalConsentRevokeModal.tsx` — 2-step form with reason
- `PartnerConsentTable.tsx` — dense partner table
- `PartnerRequestConsentModal.tsx` — email + TTL form
- `AdminConsentRollout.tsx` — operator dashboard

## Backend endpoints (net-new)

- `POST /api/partners/me/consent/request` — partner initiates, emails token
- `GET  /api/consent/approve/{token}` — customer clicks email link;
  renders the grant modal with token prefilled
- `POST /api/consent/approve/{token}` — consumes token + writes consent
- `GET  /api/admin/consent/rollout` — operator dashboard data

All existing endpoints (list/grant/revoke on portal + read on partner)
stay as-is from Phase 2. They work under the new UI without changes.

## What the sales demo looks like (3-minute loop)

1. Partner (OsirisCare prospect) logs into partner portal, clicks on
   their test client's site → consent tab → 0/12 classes active.
2. Partner clicks "Request consent" on `LOG_ARCHIVE`.
3. Client (demo actor) pulls up their portal in a second browser,
   opens the email that just arrived, clicks the link.
4. Grant modal appears. Client clicks "Grant". UI confirms success +
   shows the bundle_id + signature fingerprint.
5. Partner tab refreshes → now 1/12 active.
6. Operator tab refreshes → rollout % ticks up.
7. Demo closes: "Every action from this point on is attributed to
   this named human. Revoke takes effect at the next check-in."

## Anti-goals (don't build these in Phase 4)

- ❌ Bulk-grant all 12 classes with one click — the consent is
  categorical, not a terms-of-service
- ❌ Auto-renew on expiry — must be explicit; renewal is the
  audit event
- ❌ Partner can also revoke — only the client revokes (partner
  revocation would undermine the "customer is in control" positioning)
- ❌ Consent inheritance across sites in a client_org — each site's
  practice manager signs their own
- ❌ WebCrypto client-side signing for v1 — server-generated key is
  fine for enterprise launch; WebCrypto lands in Phase 5 as a
  "bring-your-own-key" option

## Definition of done

1. All three screens exist + route-registered
2. Consent lifecycle (request → grant → revoke → re-grant) works
   end-to-end in dev + staging
3. Every state change writes a `compliance_bundles` row (Phase 2.5
   plumbing — test this in dev)
4. Magic-link flow: request → email sent → click → approve → token
   consumed. Token expires after 72h with no consumption.
5. 10 vitest + 5 PG-integration tests:
   - portal grant-modal form validation
   - partner request flow ID isolation (token → site mapping)
   - magic-link token consumption is single-use
   - token rejection when wrong site
   - revoke gives client, not partner
6. Contract test: `RUNBOOK_CONSENT_ENFORCE_CLASSES=*` in staging +
   walking the full lifecycle without a block
7. Changelog entry on `/changelog` public page
8. Sales demo script recorded (2 min, captioned)

## Scope / estimate

- ~1200 LOC frontend (7 components)
- ~600 LOC backend (4 new endpoints + 1 migration + background loop)
- ~15 tests
- **Estimate: 3-4 focused days** of work once Phase 3 stability window
  closes

## What Phase 4 deliberately defers

- Amendments table (`consent_amendments`) — schema exists from
  migration 184; amendment UI + flow comes in Phase 5
- Runbook registry population (`runbook_registry`) — stays empty;
  classifier fallback in `classify_runbook_to_class()` handles
  mapping from runbook_id prefix for as long as we need
- Per-site enforcement override — ops can toggle class enforcement
  globally; no per-site exemption in Phase 4 (escape hatch is
  emergency_override bundle, already spec'd)

## Pitfalls from the round table

- **Legal:** don't use absolute language in the grant modal ("we
  will protect you"). Use "we log every action" instead.
- **Security:** magic-link tokens must be hashed-at-rest (sha256),
  never stored plaintext. Tokens sent in URL must be unguessable
  (≥32 bytes of entropy).
- **UX:** revoke must show the actual classes blocked in the next
  24h so customers understand what they just gave up. Not an
  existential dread screen, just a specific one.
- **Sales:** demo the revoke before the grant in the loop — makes
  the reversibility story more visible.
