# Client Portal OIDC SSO + Partner Portal RBAC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-org OIDC SSO to the client portal and enforce partner_users roles on partner portal routes.

**Architecture:** New `client_sso.py` module handles OIDC authorize/callback + SSO config CRUD. Partner RBAC adds `require_partner_role()` to `partners.py` and applies it to existing routes. Migration 100 creates SSO tables and adds `partner_user_id` to `partner_sessions`.

**Tech Stack:** FastAPI, asyncpg, OIDC (via httpx), Fernet encryption, PKCE, HMAC-SHA256

**Spec:** `docs/superpowers/specs/2026-03-24-client-sso-partner-rbac-design.md`

---

### Task 1: Migration — SSO Tables + Partner Session User Link

**Files:**
- Create: `mcp-server/central-command/backend/migrations/100_client_sso_partner_rbac.sql`

- [ ] **Step 1: Write migration**

```sql
-- Migration 100: Client SSO + Partner RBAC infrastructure

-- Per-org OIDC SSO configuration
CREATE TABLE IF NOT EXISTS client_org_sso (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_org_id UUID NOT NULL UNIQUE REFERENCES client_orgs(id) ON DELETE CASCADE,
    issuer_url TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_secret_encrypted BYTEA NOT NULL,
    allowed_domains TEXT[] NOT NULL DEFAULT '{}',
    sso_enforced BOOLEAN NOT NULL DEFAULT false,
    created_by_partner_id UUID REFERENCES partners(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- OIDC state tokens (PKCE + nonce, 10-min TTL)
CREATE TABLE IF NOT EXISTS client_oauth_state (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    state_hash TEXT NOT NULL UNIQUE,
    code_verifier TEXT NOT NULL,
    nonce TEXT NOT NULL,
    client_org_id UUID NOT NULL REFERENCES client_orgs(id),
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_client_oauth_state_hash ON client_oauth_state(state_hash);

-- Link partner sessions to individual staff members for RBAC
ALTER TABLE partner_sessions ADD COLUMN IF NOT EXISTS partner_user_id UUID REFERENCES partner_users(id);

-- RLS: client_org_sso
ALTER TABLE client_org_sso ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_org_sso FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_bypass_client_org_sso ON client_org_sso FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');
CREATE POLICY tenant_client_org_sso ON client_org_sso FOR ALL
    USING (client_org_id::text = current_setting('app.current_org', true));

-- RLS: client_oauth_state (admin-only, server-side consumption)
ALTER TABLE client_oauth_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_oauth_state FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_bypass_client_oauth_state ON client_oauth_state FOR ALL
    USING (current_setting('app.is_admin', true) = 'true');

-- Grant to app role
GRANT SELECT, INSERT, UPDATE, DELETE ON client_org_sso TO mcp_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON client_oauth_state TO mcp_app;
```

- [ ] **Step 2: Run migration on VPS**

```bash
ssh root@178.156.162.116 "docker cp /dev/stdin mcp-server:/app/dashboard_api/migrations/100_client_sso_partner_rbac.sql < mcp-server/central-command/backend/migrations/100_client_sso_partner_rbac.sql && docker exec mcp-server python3 -c \"
import asyncio, asyncpg
async def run():
    conn = await asyncpg.connect('postgresql://mcp:@mcp-postgres:5432/mcp')
    sql = open('/app/dashboard_api/migrations/100_client_sso_partner_rbac.sql').read()
    await conn.execute(sql)
    print('Migration 100 applied')
    await conn.close()
asyncio.run(run())
\""
```

- [ ] **Step 3: Commit**

```bash
git add mcp-server/central-command/backend/migrations/100_client_sso_partner_rbac.sql
git commit -m "feat: migration 100 — client SSO tables + partner session user link"
```

---

### Task 2: Partner RBAC — `require_partner_role()` + Session User Link

**Files:**
- Modify: `mcp-server/central-command/backend/partners.py:171-213` (require_partner)
- Modify: `mcp-server/central-command/backend/partner_auth.py` (store partner_user_id in session)
- Test: `packages/compliance-agent/tests/test_partner_rbac.py`

- [ ] **Step 1: Write RBAC tests**

Create `packages/compliance-agent/tests/test_partner_rbac.py` with tests for:
- `test_admin_allowed_all_routes` — admin role passes require_partner_role for any role combo
- `test_tech_allowed_operational` — tech passes for ("admin", "tech"), rejected for ("admin",)
- `test_billing_allowed_financial` — billing passes for ("admin", "billing"), rejected for ("admin", "tech")
- `test_api_key_gets_admin_role` — API key auth returns user_role="admin"
- `test_legacy_session_no_user_id_gets_admin` — backward compat: NULL partner_user_id → admin
- `test_missing_role_returns_403` — correct HTTP 403 with message

Mock pattern: use `unittest.mock.patch` and `AsyncMock` for `admin_connection` and `get_pool`, same as `test_escalation_engine.py`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/test_partner_rbac.py -v --tb=short
```

- [ ] **Step 3: Update `require_partner` to include user_role**

In `partners.py:195-203`, update the session query to JOIN `partner_users`:

```python
# Session auth path — JOIN partner_users for role
row = await conn.fetchrow("""
    SELECT ps.partner_id, p.id, p.name, p.slug, p.status,
           pu.role AS user_role, pu.id AS user_id
    FROM partner_sessions ps
    JOIN partners p ON p.id = ps.partner_id
    LEFT JOIN partner_users pu ON pu.id = ps.partner_user_id
    WHERE ps.session_token_hash = $1
      AND ps.expires_at > NOW()
      AND p.status = 'active'
""", token_hash)
```

Add `user_role` to returned dict: `result["user_role"] = row.get("user_role") or "admin"` (NULL → admin for backward compat).

For API key path: set `result["user_role"] = "admin"`.

- [ ] **Step 4: Add `require_partner_role()` factory**

After `require_partner` in `partners.py`:

```python
def require_partner_role(*allowed_roles):
    """Dependency that checks partner_users.role against allowed roles."""
    async def _check(partner: dict = Depends(require_partner)):
        if partner.get("user_role") not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions for this action"
            )
        return partner
    return Depends(_check)
```

- [ ] **Step 5: Apply RBAC to partner portal routes**

In `partners.py`, update each endpoint's dependency:

```python
# View endpoints — all roles
@router.get("/me")
async def get_my_partner(partner: dict = require_partner_role("admin", "tech", "billing")):

# Operational endpoints — admin + tech
@router.put("/me/orgs/{org_id}/drift-config")
async def update_partner_org_drift_config(..., partner: dict = require_partner_role("admin", "tech")):

# Provisioning — admin only
@router.post("/me/provisions")
async def create_provision_code(..., partner: dict = require_partner_role("admin")):
```

Apply per the route table in the spec.

- [ ] **Step 6: Update partner_auth.py to store partner_user_id**

In the OAuth callback, after finding/creating the partner, look up or create `partner_users` row:

```python
# After partner is found/created, link to partner_users
user_row = await conn.fetchrow(
    "SELECT id FROM partner_users WHERE partner_id = $1 AND email = $2",
    partner_id, email
)
partner_user_id = user_row["id"] if user_row else None
```

Include `partner_user_id` in the `INSERT INTO partner_sessions` statement.

- [ ] **Step 7: Run tests**

```bash
python -m pytest tests/test_partner_rbac.py -v --tb=short
```

- [ ] **Step 8: Commit**

```bash
git add mcp-server/central-command/backend/partners.py \
       mcp-server/central-command/backend/partner_auth.py \
       packages/compliance-agent/tests/test_partner_rbac.py
git commit -m "feat: enforce partner_users RBAC on portal routes"
```

---

### Task 3: Client SSO Module — OIDC Authorize + Callback

**Files:**
- Create: `mcp-server/central-command/backend/client_sso.py`
- Test: `packages/compliance-agent/tests/test_client_sso.py`

- [ ] **Step 1: Write SSO flow tests**

Create `packages/compliance-agent/tests/test_client_sso.py` with tests for:
- `test_authorize_returns_auth_url` — valid email with SSO config returns redirect URL with PKCE + state + nonce
- `test_authorize_unknown_email_404` — email not in client_users returns 404
- `test_authorize_no_sso_config_404` — email exists but org has no SSO config returns 404
- `test_callback_creates_session` — valid code exchange creates client_sessions row + sets cookie
- `test_callback_auto_provisions_user` — unknown email (but valid domain) creates client_users with role=viewer
- `test_callback_domain_mismatch_403` — email domain not in allowed_domains returns 403
- `test_callback_expired_state_400` — expired state token returns 400
- `test_callback_replayed_state_400` — reused state token returns 400
- `test_callback_nonce_mismatch_400` — ID token nonce doesn't match stored nonce returns 400
- `test_cleanup_expired_states` — authorize call deletes expired state rows

Mock `httpx.AsyncClient` for OIDC discovery + token exchange. Mock the ID token as a JWT with email + nonce claims.

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_client_sso.py -v --tb=short
```

- [ ] **Step 3: Create `client_sso.py`**

```python
"""Client portal OIDC SSO — authorize, callback, config CRUD."""
import hashlib, hmac, json, logging, os, secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Response, Cookie
from pydantic import BaseModel

import httpx

from .fleet import get_pool
from .tenant_middleware import admin_connection
from .oauth_login import encrypt_secret, decrypt_secret
from .client_portal import (
    SESSION_COOKIE_NAME, SESSION_DURATION_DAYS,
    generate_token, hash_token,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/client/auth/sso", tags=["client-sso"])

PKCE_VERIFIER_LENGTH = 64
STATE_TTL_SECONDS = 600  # 10 minutes
SESSION_TOKEN_SECRET = os.environ.get("SESSION_TOKEN_SECRET", "dev-secret")
```

Implement:
- `POST /authorize` — email → lookup user → get org SSO config → OIDC discovery → PKCE + state + nonce → return auth_url
- `GET /callback` — validate state → exchange code → extract email/nonce from ID token → validate domain → find/create user → create session → redirect

Key functions:
- `_discover_oidc(issuer_url)` — fetch `.well-known/openid-configuration`, cache briefly
- `_exchange_code(token_endpoint, code, code_verifier, redirect_uri, client_id, client_secret)` — POST to token endpoint
- `_decode_id_token(id_token)` — base64 decode payload (no signature verification needed — token came directly from IdP over HTTPS)

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_client_sso.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add mcp-server/central-command/backend/client_sso.py \
       packages/compliance-agent/tests/test_client_sso.py
git commit -m "feat: client portal OIDC SSO authorize + callback"
```

---

### Task 4: SSO Config CRUD + Enforcement

**Files:**
- Modify: `mcp-server/central-command/backend/client_sso.py` (add config endpoints)
- Modify: `mcp-server/central-command/backend/client_portal.py:360,236` (enforcement checks)
- Modify: `mcp-server/central-command/backend/main.py:1212` (register SSO router)

- [ ] **Step 1: Add SSO config endpoints to `client_sso.py`**

Partner-facing CRUD (requires partner auth):

```python
config_router = APIRouter(prefix="/api/partners/me/orgs", tags=["partner-sso-config"])

# GET /{org_id}/sso — read SSO config
# PUT /{org_id}/sso — create/update (validates issuer discovery)
# DELETE /{org_id}/sso — remove SSO config
```

PUT validates `issuer_url` by fetching `/.well-known/openid-configuration`. Returns 400 if unreachable or missing required fields.

Encrypts `client_secret` via `encrypt_secret()` before storage.

- [ ] **Step 2: Add SSO enforcement to client_portal.py**

In `login_with_password()` (line 360), after finding the user, check:

```python
# Check SSO enforcement
sso_row = await conn.fetchrow(
    "SELECT sso_enforced FROM client_org_sso WHERE client_org_id = $1",
    user_row["client_org_id"]
)
if sso_row and sso_row["sso_enforced"]:
    raise HTTPException(403, "This organization requires SSO login")
```

Same check in `request_magic_link()` (line 236).

- [ ] **Step 3: Register routers in main.py**

After line 1212:
```python
from dashboard_api.client_sso import router as client_sso_router, config_router as client_sso_config_router
app.include_router(client_sso_router, prefix="/api")
app.include_router(client_sso_config_router)
```

- [ ] **Step 4: Add enforcement tests**

In `test_client_sso.py`:
- `test_enforced_password_login_403` — org with sso_enforced=true rejects password login
- `test_enforced_magic_link_403` — same for magic link
- `test_non_enforced_allows_password` — org with sso_enforced=false still allows password
- `test_config_put_validates_issuer` — PUT with invalid issuer returns 400
- `test_config_put_stores_encrypted_secret` — secret is Fernet-encrypted in DB
- `test_config_delete_removes_sso` — DELETE removes config, SSO login stops working

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/test_client_sso.py tests/test_partner_rbac.py -v --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add mcp-server/central-command/backend/client_sso.py \
       mcp-server/central-command/backend/client_portal.py \
       mcp-server/central-command/backend/main.py \
       packages/compliance-agent/tests/test_client_sso.py
git commit -m "feat: SSO config CRUD + enforcement on password/magic-link login"
```

---

### Task 5: Frontend — SSO Button + Partner Config UI

**Files:**
- Modify: `mcp-server/central-command/frontend/src/pages/ClientLogin.tsx`
- Create: `mcp-server/central-command/frontend/src/pages/partner/SSOConfig.tsx`

- [ ] **Step 1: Add SSO button to ClientLogin.tsx**

Add "Sign in with SSO" button below the existing login form. On click:
1. Prompt for email (or use email already entered in the form)
2. POST to `/api/client/auth/sso/authorize` with `{ email }`
3. If 200: redirect to `auth_url` from response
4. If 404: show "SSO is not configured for this organization"

When `sso_enforced` is true (check via a new endpoint or embed in login page load), hide password/magic-link tabs.

- [ ] **Step 2: Create partner SSO config page**

`SSOConfig.tsx` — accessible from partner portal org detail page:
- Form with: issuer URL, client ID, client secret, allowed domains (comma-separated), SSO enforced toggle
- GET loads existing config (or shows empty form)
- Save button PUTs config
- Delete button removes config
- Validation feedback from backend (issuer discovery check)

- [ ] **Step 3: Run frontend checks**

```bash
cd mcp-server/central-command/frontend
npx tsc --noEmit && npx eslint src/ --max-warnings 0
```

- [ ] **Step 4: Commit**

```bash
git add mcp-server/central-command/frontend/src/pages/ClientLogin.tsx \
       mcp-server/central-command/frontend/src/pages/partner/SSOConfig.tsx
git commit -m "feat: SSO login button + partner SSO config UI"
```

---

### Task 6: Integration Test + Deploy

- [ ] **Step 1: Run full test suite**

```bash
cd packages/compliance-agent && source venv/bin/activate
python -m pytest tests/ -v --tb=short -q
```

- [ ] **Step 2: Run frontend checks**

```bash
cd mcp-server/central-command/frontend && npx tsc --noEmit && npx eslint src/ --max-warnings 0
```

- [ ] **Step 3: Push and verify CI**

```bash
git push
# Watch: gh run list --limit 1
```

- [ ] **Step 4: Run migration on VPS**

```bash
ssh root@178.156.162.116 "docker exec mcp-server python3 -c \"...migration runner...\""
```

- [ ] **Step 5: Verify endpoints**

```bash
# Test SSO config CRUD via partner API
curl -s https://api.osiriscare.net/api/partners/me/orgs/{org_id}/sso -H "Cookie: ..."
```
