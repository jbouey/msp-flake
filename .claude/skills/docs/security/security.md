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

### Production Headers
```python
headers = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": "default-src 'self'",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
```

## Key Files
- `backend/auth.py` - Authentication logic
- `backend/security_headers.py` - CSP and headers
- `mcp-server/guardrails.py` - Input validation
- `flake/Modules/secrets.nix` - SOPS configuration
