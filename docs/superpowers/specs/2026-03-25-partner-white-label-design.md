# Partner White-Label Branding

## Problem

The client portal always shows OsirisCare branding. Partners selling to mid-size medical groups need their own branding — logo, colors, name — so clients see the MSP's product, not the platform vendor.

## Solution

Runtime white-labeling via partner branding config. Client portal, emails, and PDF reports adapt to the partner's brand. OsirisCare is invisible to end clients except for a small "Powered by" footer.

## Data Model

Extend existing `partners` table (already has brand_name, logo_url, primary_color):

```sql
ALTER TABLE partners ADD COLUMN IF NOT EXISTS secondary_color VARCHAR(7) DEFAULT '#6366F1';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS tagline TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS support_email TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS support_phone TEXT;
```

No new tables. Branding lives on the partner record. Client portal resolves branding via `client_orgs.current_partner_id → partners`.

## API Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/portal/branding/{partner_slug}` | None | Public branding for login page |
| GET | `/api/partners/me/branding` | Partner | Read own branding config |
| PUT | `/api/partners/me/branding` | Partner (admin) | Update branding |

### Branding Response Shape

```json
{
    "brand_name": "North Valley IT Solutions",
    "logo_url": "https://storage.osiriscare.net/logos/northvalley.png",
    "primary_color": "#2563EB",
    "secondary_color": "#6366F1",
    "tagline": "HIPAA Compliance Made Simple",
    "support_email": "support@northvalleyit.com",
    "support_phone": "+1-570-555-1234",
    "partner_slug": "northvalley"
}
```

Null fields fall back to OsirisCare defaults in the frontend.

## Client Portal White-Labeling

### URL Scheme

Phase 1: `/portal/{partner_slug}/login` — slug-based routing
Phase 2 (future): Custom domain mapping via CNAME + cert provisioning

### Login Page

- Partner logo centered above login form
- Partner brand name as page title
- Primary color on buttons and accents
- Tagline below logo (if set)
- "Powered by OsirisCare" small muted footer

### Authenticated Pages

- Header: partner logo (small) + partner brand name
- Primary color drives CSS accent via custom properties
- Support link in sidebar footer → partner support_email
- "Powered by OsirisCare" in page footer

### CSS Theming

Runtime injection via CSS custom properties — no build-time theming:

```css
:root {
    --brand-primary: var(--partner-primary, #0D9488);
    --brand-secondary: var(--partner-secondary, #6366F1);
}
```

Set at page load from branding API response:

```javascript
document.documentElement.style.setProperty('--partner-primary', branding.primary_color);
document.documentElement.style.setProperty('--partner-secondary', branding.secondary_color);
```

Components use `var(--brand-primary)` for buttons, links, active states.

## Email White-Labeling

All client-facing emails (magic links, escalation notifications, compliance alerts):

- **Subject prefix:** `[Partner Brand Name]` instead of `[OsirisCare]`
- **Header:** partner logo + primary color bar
- **Footer:** partner support_email + support_phone
- **Sender name:** partner brand_name (sender address stays OsirisCare for deliverability)

Implementation: email templates receive a `branding` dict. Template conditionally renders partner or OsirisCare defaults.

## PDF Report White-Labeling

Compliance reports and evidence packets:

- **Header:** partner logo (left) + partner brand name (center)
- **Title:** "[Partner Brand] Compliance Report" not "OsirisCare Compliance Report"
- **Footer:** partner support contact info + "Powered by OsirisCare"

Implementation: PDF generation functions receive branding config from the partner lookup.

## Branding Resolution Chain

```
1. Client logs in at /portal/{slug}/login
2. Frontend calls GET /api/portal/branding/{slug}
3. Backend queries: partners WHERE slug = $1
4. Returns branding (or OsirisCare defaults if partner not found)
5. Frontend applies CSS custom properties + renders partner assets
6. After auth, session includes partner_id → branding cached for session
```

For emails/PDFs:
```
1. System needs to send email/generate PDF for a site
2. Look up site → client_org → current_partner_id → partners
3. Extract branding fields
4. Pass to template/generator
```

## Migration

```sql
-- Migration 101: Partner white-label branding extensions
ALTER TABLE partners ADD COLUMN IF NOT EXISTS secondary_color VARCHAR(7) DEFAULT '#6366F1';
ALTER TABLE partners ADD COLUMN IF NOT EXISTS tagline TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS support_email TEXT;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS support_phone TEXT;
```

## Files to Create/Modify

| File | Action |
|---|---|
| `backend/migrations/101_partner_white_label.sql` | New — extend partners table |
| `backend/partners.py` | Modify — add branding GET/PUT endpoints |
| `backend/client_portal.py` | Modify — include branding in session/login context |
| `frontend/src/client/ClientLogin.tsx` | Modify — render partner branding |
| `frontend/src/client/ClientLayout.tsx` | New or modify — branded header/footer |
| `frontend/src/hooks/useBranding.ts` | New — fetch + cache branding, apply CSS vars |
| `frontend/src/constants/copy.ts` | Modify — add WHITE_LABEL defaults |

## Security

- Branding endpoint is public (needed before auth) but read-only
- Logo URLs must be HTTPS
- No script injection — branding fields are sanitized (no HTML, only text + hex colors)
- Partner can only update their own branding (scoped by session)

## Test Coverage

- Branding endpoint returns correct partner data
- Unknown slug returns OsirisCare defaults (not 404)
- CSS custom properties applied correctly
- Email templates use partner branding when available
- PDF headers show partner logo
- Branding PUT validates color format (#XXXXXX)
- XSS prevention on brand_name/tagline fields
