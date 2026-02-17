# API Patterns

## REST API (FastAPI)

### Base Configuration
- **Framework:** FastAPI
- **Base URL:** `/api`
- **Auth:** Bearer token (session-based)
- **Validation:** Pydantic models

### Endpoint Categories

| Router | Prefix | Purpose |
|--------|--------|---------|
| auth | `/api/auth` | Login, logout, session |
| sites | `/api/sites` | Site CRUD |
| fleet | `/api/fleet` | Fleet overview |
| incidents | `/api/incidents` | Incident management |
| runbooks | `/api/runbooks` | Runbook library |
| learning | `/api/learning` | Pattern promotion |
| integrations | `/api/integrations` | Cloud connectors |
| escalation | `/api/escalation` | L3 notifications |

### Request/Response Models

```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class SiteCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    partner_id: Optional[str] = None

class Site(BaseModel):
    id: str
    name: str
    partner_id: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

class IncidentList(BaseModel):
    items: List[Incident]
    total: int
    limit: int
    offset: int
```

### Query Parameters
```python
@router.get("/incidents")
async def list_incidents(
    site_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    resolved: Optional[bool] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    # Build filtered query
    pass
```

## gRPC API (Go Agent v0.3.0)

### Service Definition
```protobuf
syntax = "proto3";
package compliance;

service ComplianceAgent {
  rpc Register(RegisterRequest) returns (RegisterResponse);
  rpc ReportDrift(stream DriftEvent) returns (stream DriftAck);
  rpc ReportHealing(HealingResult) returns (HealingAck);
  rpc Heartbeat(HeartbeatRequest) returns (HeartbeatResponse);
  rpc ReportRMMStatus(RMMStatusReport) returns (RMMAck);
}

message RegisterRequest {
  string hostname = 1;
  string os_version = 2;
  string agent_version = 3;
  string mac_address = 4;
  CapabilityTier capability = 5;
  bool needs_certificates = 6;  // mTLS auto-enrollment
}

message RegisterResponse {
  string agent_id = 1;
  bytes ca_cert_pem = 6;      // CA cert for TLS verification
  bytes agent_cert_pem = 7;   // Signed client cert
  bytes agent_key_pem = 8;    // Client private key
}
```

### Certificate Auto-Enrollment (mTLS)
1. Agent boots with no certs → connects insecure, sets `needs_certificates=true`
2. Server issues ECDSA P-256 cert via `AgentCA.issue_agent_cert()`
3. Agent saves certs to disk, reconnects with mTLS
4. All subsequent connections use mutual TLS

### Agent Discovery
- DNS SRV: `_osiris-grpc._tcp.<domain>` → appliance IP:50051
- Fallback: `OSIRIS_APPLIANCE_ADDR` env or config file
- Domain detection: `USERDNSDOMAIN` env (Windows) or WMI fallback

### GPO Deployment
- Agent binary uploaded to `\\domain\SYSVOL\domain\OsirisCare\`
- Idempotent startup script checks hash, installs/updates as needed
- `sc.exe failure` configures restart backoff (1min/2min/5min)

### Go Client Usage
```go
// Agent auto-discovers appliance and connects with mTLS
client, _ := transport.NewGRPCClient(ctx, cfg)
resp, _ := client.Register(ctx)  // Auto-enrolls certs if needed

// Bidirectional drift stream with heal commands
client.StartDriftStream(ctx)
client.SendDrift(ctx, &pb.DriftEvent{CheckType: "firewall", Passed: false})
// HealCommands arrive on client.HealCmds channel
```

## Authentication

### Login Flow
```python
@router.post("/auth/login")
async def login(
    credentials: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    user = await authenticate_user(credentials.username, credentials.password, db)
    if not user:
        raise HTTPException(401, "Invalid credentials")

    token = secrets.token_urlsafe(32)
    await create_session(user.id, token, db)

    response = JSONResponse({"user": user.dict()})
    response.set_cookie(
        "session_token",
        token,
        httponly=True,
        secure=True,
        samesite="lax"
    )
    return response
```

### Auth Dependency
```python
async def require_auth(request: Request) -> Dict:
    token = request.cookies.get("session_token")
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        raise HTTPException(401, "Not authenticated")

    user = await validate_session(token)
    if not user:
        raise HTTPException(401, "Invalid session")

    return user
```

## TypeScript API Client

### Fetch Wrapper
```typescript
class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const token = localStorage.getItem('auth_token');

  const response = await fetch(endpoint, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token && { 'Authorization': `Bearer ${token}` }),
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new ApiError(response.status, error.detail);
  }

  return response.json();
}
```

### API Module Pattern
```typescript
export const sitesApi = {
  list: (status?: string) =>
    fetchApi<Site[]>(`/api/sites${status ? `?status=${status}` : ''}`),

  get: (id: string) =>
    fetchApi<SiteDetail>(`/api/sites/${id}`),

  create: (data: CreateSiteRequest) =>
    fetchApi<Site>('/api/sites', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: string, data: UpdateSiteRequest) =>
    fetchApi<Site>(`/api/sites/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  delete: (id: string) =>
    fetchApi<void>(`/api/sites/${id}`, { method: 'DELETE' }),
};
```

## Error Handling

### HTTP Status Codes
| Code | Meaning | Usage |
|------|---------|-------|
| 200 | OK | Successful GET/PUT |
| 201 | Created | Successful POST |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Validation failure |
| 401 | Unauthorized | Missing/invalid auth |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 429 | Too Many Requests | Rate limited |
| 500 | Server Error | Unhandled exception |

### Error Response Format
```json
{
  "detail": "Site not found",
  "status": "error",
  "code": "SITE_NOT_FOUND"
}
```

## OAuth Integration

### Flow
```
1. POST /api/integrations/sites/{site_id}
   → Returns { auth_url, state }

2. Redirect user to auth_url

3. Provider callback to /api/integrations/oauth/callback?code=...&state=...

4. Exchange code for tokens, store encrypted
```

### Supported Providers
- Google Workspace
- Azure AD / Microsoft Entra
- Okta
- AWS (IAM role assumption)

## Key Files
- `backend/auth.py` - Authentication endpoints
- `backend/sites.py` - Site management
- `backend/integrations/api.py` - OAuth + cloud
- `agent/proto/compliance.proto` - gRPC service definition (Go agent)
- `packages/compliance-agent/src/compliance_agent/grpc_server.py` - Python gRPC server
- `packages/compliance-agent/src/compliance_agent/agent_ca.py` - ECDSA P-256 CA for agent mTLS
- `packages/compliance-agent/src/compliance_agent/dns_registration.py` - DNS SRV record registration
- `packages/compliance-agent/src/compliance_agent/gpo_deployment.py` - GPO-based agent deployment
- `frontend/src/utils/api.ts` - TypeScript client
