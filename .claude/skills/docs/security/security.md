# Security Patterns

## Authentication

### Password Hashing
- **Primary:** bcrypt with 12-round cost factor
- **Fallback:** SHA-256 for legacy compatibility
- **Complexity:** 12+ chars, uppercase, lowercase, digit, special char

### Session Tokens
```python
# Generate secure token
token = secrets.token_urlsafe(32)
# Hash before storage
token_hash = hmac.new(SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
```

### Account Security
- Max 5 failed attempts → 15-minute lockout
- 24-hour session duration
- Audit logging: LOGIN_SUCCESS, LOGIN_FAILED, LOGIN_BLOCKED

## OAuth 2.0 + PKCE

### State Token Pattern
```python
# Generate PKCE verifier (64 bytes)
code_verifier = secrets.token_urlsafe(64)
# Create challenge
code_challenge = base64.urlsafe_b64encode(
    hashlib.sha256(code_verifier.encode()).digest()
).rstrip(b'=').decode()
```

### Supported Providers
- Google Workspace
- Azure AD / Microsoft Entra
- Okta

### State Management
- Single-use tokens via Redis (atomic get-delete)
- 10-minute TTL
- Site-bound validation

## Input Validation (Guardrails)

### Dangerous Pattern Detection
```python
DANGEROUS_PATTERNS = [
    r'rm\s+-rf',           # Destructive deletion
    r'/dev/(sd|hd|nvme)',  # Device manipulation
    r'/etc/passwd',        # Sensitive files
]
```

### Shell Metacharacter Filtering
Block: `;`, `|`, `` ` ``, `$`, `()`, `{}`

### Rate Limiting
- Per-client-hostname-tool: 5-minute cooldown
- Per-client hourly: 100 requests
- Global hourly: 1000 requests

## Evidence Chain Integrity

### Hash-Chaining Pattern
```python
# Each entry contains hash of previous
new_hash = hashlib.sha256(
    f"{prev_hash}{json.dumps(entry)}".encode()
).hexdigest()
```

### Ed25519 Signing
```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
private_key = Ed25519PrivateKey.generate()
signature = private_key.sign(bundle_json.encode())
```

## Secrets Management (SOPS/age)

### Configuration
```nix
services.msp-secrets = {
  sopsFile = /etc/secrets/secrets.yaml;
  ageKeyFile = /var/lib/sops-age/keys.txt;
  secrets.api-key = {
    owner = "mcp-server";
    mode = "0400";
  };
};
```

### Rotation
- Weekly rotation reminders
- 60-day age warnings
- Audit logging to `/var/log/secrets-audit.log`

## Security Headers

### Production Headers (nginx.conf + backend)
```
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
Content-Security-Policy: default-src 'self'
Referrer-Policy: strict-origin-when-cross-origin
```

## Production Hardening (Session 99)
- **CORS:** Environment-aware — only `dashboard.osiriscare.net` + `portal.osiriscare.net` in production; localhost origins added only when `ENVIRONMENT=development`
- **Docker ports:** All 5 services bound to `127.0.0.1` (postgres, redis, minio, grafana, prometheus)
- **Redis:** `requirepass` enabled via `REDIS_PASSWORD` env var
- **Source maps:** Disabled in production (`sourcemap: false` in vite.config.ts)
- **Auth tokens:** HTTP-only cookies only (dead localStorage code removed)
- **CSRF:** Double-submit cookie pattern via `csrf.py` middleware

## Key Files
- `backend/auth.py` - Authentication logic (bcrypt, session tokens, lockout)
- `backend/csrf.py` - CSRF double-submit pattern middleware
- `backend/security_headers.py` - CSP and headers
- `mcp-server/guardrails.py` - Input validation
- `flake/Modules/secrets.nix` - SOPS configuration
- `docker-compose.yml` - Port binding, Redis auth, Grafana password
