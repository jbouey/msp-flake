# Client Portal OIDC SSO + Partner Portal RBAC

## Problem

Partners sign clients that may have their own identity provider (Azure AD, Google Workspace, Okta). Those client users need to authenticate into the client portal via SSO instead of email/password. Currently there's no per-org SSO configuration.

Additionally, `partner_users.role` (admin/tech/billing) exists in the schema but is not enforced on any route. All partner staff have identical access.

## Solution

1. **Client Portal OIDC SSO** — per-org OIDC configuration managed by the partner, with auto-provisioning and optional enforcement.
2. **Partner Portal RBAC** — enforce the existing role column on partner portal routes.

## Data Model

### New Table: `client_org_sso`

```sql
CREATE TABLE client_org_sso (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_org_id UUID NOT NULL UNIQUE REFERENCES client_orgs(id) ON DELETE CASCADE,
    issuer_url TEXT NOT NULL,              -- e.g. https://login.microsoftonline.com/{tenant}/v2.0
    client_id TEXT NOT NULL,               -- OIDC client ID from IdP
    client_secret_encrypted BYTEA NOT NULL, -- Fernet-encrypted via encrypt_secret() from oauth_login.py
    allowed_domains TEXT[] NOT NULL DEFAULT '{}', -- restrict SSO to specific email domains
    sso_enforced BOOLEAN NOT NULL DEFAULT false,  -- true = disable password/magic link
    created_by_partner_id UUID REFERENCES partners(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### New Table: `client_oauth_state`

```sql
CREATE TABLE client_oauth_state (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    state_hash TEXT NOT NULL UNIQUE,       -- HMAC-SHA256 of state token
    code_verifier TEXT NOT NULL,           -- PKCE code_verifier (plaintext, 10-min TTL, server-side only)
    nonce TEXT NOT NULL,                   -- OIDC nonce (verified in ID token)
    client_org_id UUID NOT NULL REFERENCES client_orgs(id),
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL        -- 10 minutes from creation
);
CREATE INDEX idx_client_oauth_state_hash ON client_oauth_state(state_hash);
```

### Partner Sessions: Add User-Level Identity

`partner_sessions` currently only tracks `partner_id` (the MSP company), not which staff member is logged in. RBAC requires knowing the individual.

```sql
ALTER TABLE partner_sessions ADD COLUMN partner_user_id UUID REFERENCES partner_users(id);
```

The partner OAuth callback (`partner_auth.py`) must be updated to:
1. After authenticating the partner, look up or create the `partner_users` row by email.
2. Store `partner_user_id` in the new session column.
3. `require_partner` must JOIN through `partner_user_id` → `partner_users.role` and include it in the returned dict.

API key auth bypasses `partner_users` — implicitly gets `admin` role.

### No Other Schema Changes for RBAC

`partner_users.role` already exists with constraint `CHECK (role IN ('admin', 'tech', 'billing'))`.

## SSO Login Flow

1. User visits client portal login page.
2. User enters email address and clicks "Sign in with SSO".
3. Frontend calls `POST /client/auth/sso/authorize` with `{ email }`.
4. Backend looks up `client_users` by email (globally unique index). Gets `client_org_id`. Queries `client_org_sso` for that org. Returns 404 if no SSO configured.
5. Backend fetches IdP's `{issuer_url}/.well-known/openid-configuration` to discover `authorization_endpoint`.
6. Backend generates PKCE pair (code_verifier + S256 challenge), state token, and nonce. Stores HMAC hash of state + code_verifier + nonce in `client_oauth_state` with 10-minute TTL.
7. Backend returns `{ auth_url }` to frontend (includes `nonce` in authorization request).
8. Frontend redirects user to IdP authorization URL.
9. IdP authenticates user, redirects to `GET /client/auth/sso/callback?code=...&state=...`.
10. Backend validates state token (HMAC hash lookup, single-use — delete row on fetch). Exchanges code for tokens at IdP's `token_endpoint` using stored code_verifier.
11. Backend extracts `email` and `nonce` claims from ID token. Validates nonce matches stored value. Validates email domain against `allowed_domains` using `ANY()` array matching.
12. Backend looks up `client_users` by email:
    - Found: update `last_login_at`.
    - Not found: auto-provision with `role: viewer`, `email_verified: true`, `client_org_id` from SSO config, no `password_hash`.
13. If user has `mfa_enabled: true`, return MFA pending token (same flow as password login). User completes TOTP before session is created.
14. Otherwise, create `client_sessions` row, set `osiris_client_session` cookie.
15. Redirect to client portal dashboard.

**Note on email lookup:** `client_users.email` has a unique index (`idx_client_users_email`), so email alone is sufficient to identify the user and their org. No org picker or domain disambiguation needed.

### SSO Enforcement

When `sso_enforced: true` on a `client_org_sso` record:
- `POST /client/auth/login` (password) checks the user's org for SSO enforcement. Returns 403 with message "This organization requires SSO login".
- `POST /client/auth/request-magic-link` same check, same 403.
- The frontend hides password/magic-link tabs and shows only the SSO button.
- The MFA-complete endpoint also checks enforcement — if SSO enforcement was enabled between SSO login and MFA completion, the session is still created (the user already authenticated via SSO; enforcement change takes effect on next login).

The partner can toggle this per org. Default is `false` (SSO available alongside password).

## Partner RBAC

### Dependency

```python
def require_partner_role(*allowed_roles):
    """Dependency that checks partner_users.role against allowed roles.
    Returns 403 for unauthorized roles (not 404 — user is authenticated,
    this is an authorization check, not existence check)."""
    async def checker(partner=Depends(require_partner)):
        if partner.get("user_role") not in allowed_roles:
            raise HTTPException(403, "Insufficient permissions for this action")
        return partner
    return checker
```

The existing `require_partner` dependency is updated to:
- When authenticating via session cookie: JOIN `partner_sessions.partner_user_id` → `partner_users.role`, include `user_role` in returned dict.
- When authenticating via API key: set `user_role: "admin"` implicitly (API keys are partner-level, full access).

### Route Protection

| Route Group | Allowed Roles | Key Endpoints |
|---|---|---|
| View sites, incidents, devices, compliance | admin, tech, billing | `GET /api/partners/me/orgs`, `GET /api/partners/me/sites/*` |
| Run drift scans, escalate, deploy fleet orders | admin, tech | `POST /api/partners/me/sites/*/scan`, escalation endpoints |
| Manage org SSO config, drift config | admin, tech | `PUT /api/partners/me/orgs/*/sso`, drift config endpoints |
| Manage partner users, invites | admin | `POST /api/partners/me/users`, invite endpoints |
| View billing, subscription | admin, billing | Stripe/billing endpoints |
| Configure org settings, decommission | admin | `POST /api/partners/me/sites/*/decommission` |

### Implementation

Apply `Depends(require_partner_role("admin", "tech"))` (or appropriate roles) to each partner portal endpoint in `partners.py`. No new routes needed.

## API Endpoints

### Client SSO

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/client/auth/sso/authorize` | None | Accept email, return IdP auth URL |
| GET | `/client/auth/sso/callback` | None (state token) | OIDC callback, create session |
| GET | `/api/partners/me/orgs/{org_id}/sso` | Partner (admin, tech) | Read SSO config for org |
| PUT | `/api/partners/me/orgs/{org_id}/sso` | Partner (admin, tech) | Create/update SSO config |
| DELETE | `/api/partners/me/orgs/{org_id}/sso` | Partner (admin, tech) | Remove SSO config |

SSO endpoints use the existing `/client/auth` prefix (matching `client_portal.py`'s `public_router`).

### SSO Config PUT Body

```json
{
    "issuer_url": "https://login.microsoftonline.com/{tenant}/v2.0",
    "client_id": "abc-123",
    "client_secret": "secret-value",
    "allowed_domains": ["northvalleydental.com"],
    "sso_enforced": false
}
```

On PUT, backend validates by fetching `{issuer_url}/.well-known/openid-configuration` and confirming it returns valid `authorization_endpoint` and `token_endpoint`. Returns 400 if discovery fails.

## Security

- **PKCE** on all OIDC flows (S256 challenge, same as partner OAuth).
- **Nonce** parameter in authorization request, verified in ID token to prevent replay.
- **State tokens** single-use, 10-minute TTL, HMAC-SHA256 hashed at rest (more secure than partner_auth's plaintext pattern).
- **Code verifier** stored in plaintext in `client_oauth_state` — acceptable because rows are server-side only, 10-minute TTL, and deleted on use.
- **Client secret** encrypted at rest using `encrypt_secret()`/`decrypt_secret()` from `oauth_login.py` (Fernet with key derived from `SESSION_TOKEN_SECRET`). Same pattern as partner OAuth credentials.
- **Domain validation** — ID token email domain must match `allowed_domains` array using SQL `ANY()`. Prevents users from other tenants authenticating.
- **MFA stacking** — if user has TOTP enabled, SSO + TOTP required. SSO authenticates identity, TOTP provides second factor under our control.
- **Auto-provisioned users** get `viewer` role — least privilege. Partner or org owner upgrades via existing `update_user_role` endpoint.
- **SSO enforcement** blocks password/magic-link at the API level, not just UI.
- **Expired state cleanup** — authorize endpoint deletes expired rows (`WHERE expires_at < NOW()`) before inserting new state.

## RLS Policies

```sql
-- client_org_sso: admin bypass + tenant isolation
ALTER TABLE client_org_sso ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_org_sso FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_bypass ON client_org_sso FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');
CREATE POLICY tenant_isolation ON client_org_sso FOR ALL
    USING (client_org_id::text = current_setting('app.current_org', true));

-- client_oauth_state: admin-only (consumed by server callback, not tenant queries)
ALTER TABLE client_oauth_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_oauth_state FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_bypass ON client_oauth_state FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');
```

## Test Coverage

### SSO Tests
- Authorize: generates valid PKCE + state + nonce, returns auth URL with correct params
- Authorize: unknown email returns 404
- Authorize: email with no SSO config returns 404
- Callback: valid code exchange creates session + cookie
- Callback: auto-provisions new user with viewer role and correct org
- Callback: existing user updates last_login_at
- Callback: email domain not in allowed_domains returns 403
- Callback: expired state token returns 400
- Callback: replayed state token returns 400
- Callback: nonce mismatch returns 400
- Enforced mode: password login returns 403 for SSO-enforced org
- Enforced mode: magic link returns 403 for SSO-enforced org
- Enforced mode: non-SSO org still allows password login
- MFA: SSO user with mfa_enabled gets pending token, must complete TOTP
- Config PUT: validates issuer discovery endpoint
- Config PUT: invalid issuer returns 400
- Config DELETE: removes config, SSO login stops working
- Cleanup: expired state rows deleted on new authorize call

### RBAC Tests
- Admin can access all route groups
- Tech can access operational routes, rejected from user management (403)
- Billing can access financial routes, rejected from operational actions (403)
- API key auth gets admin role implicitly
- Missing role returns 403 with clear message
- Session without partner_user_id (legacy) gets admin role (backward compatible)

## Migration

Single migration file: `100_client_sso_partner_rbac.sql`
- Creates `client_org_sso` table with RLS
- Creates `client_oauth_state` table with RLS (admin-only)
- Adds `partner_user_id` column to `partner_sessions`
- No changes to `partner_users` (role column already exists)

## Files to Create/Modify

| File | Action |
|---|---|
| `backend/client_sso.py` | New — OIDC authorize/callback + SSO config CRUD |
| `backend/client_portal.py` | Modify — check `sso_enforced` on login/magic-link endpoints |
| `backend/partners.py` | Modify — add `require_partner_role()`, update `require_partner` to include user_role |
| `backend/partner_auth.py` | Modify — store `partner_user_id` in session on login |
| `backend/main.py` | Modify — include SSO router |
| `backend/migrations/100_client_sso_partner_rbac.sql` | New — tables + RLS + partner_sessions column |
| `tests/test_client_sso.py` | New — SSO flow tests |
| `tests/test_partner_rbac.py` | New — RBAC enforcement tests |
| `frontend/src/pages/ClientLogin.tsx` | Modify — SSO button + enforced mode |
| `frontend/src/pages/partner/SSOConfig.tsx` | New — partner SSO management UI |
