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

## gRPC API

### Service Definition
```protobuf
syntax = "proto3";
package compliance;

service ComplianceAgent {
  // Agent registration
  rpc Register(RegisterRequest) returns (RegisterResponse);

  // Streaming drift events
  rpc ReportDrift(stream DriftEvent) returns (stream DriftAck);

  // Healing results
  rpc ReportHealing(HealingResult) returns (HealingAck);

  // Heartbeat
  rpc Heartbeat(HeartbeatRequest) returns (HeartbeatResponse);
}

message DriftEvent {
  string agent_id = 1;
  string hostname = 2;
  string check_type = 3;
  bool passed = 4;
  string expected = 5;
  string actual = 6;
  string hipaa_control = 7;
  int64 timestamp = 8;
  map<string, string> metadata = 9;
}

enum CapabilityTier {
  MONITOR_ONLY = 0;
  SELF_HEAL = 1;
  FULL_REMEDIATION = 2;
}
```

### Go Client Usage
```go
conn, _ := grpc.Dial("appliance:50051", grpc.WithInsecure())
client := pb.NewComplianceAgentClient(conn)

// Register
resp, _ := client.Register(ctx, &pb.RegisterRequest{
    Hostname: hostname,
    AgentVersion: "1.0.44",
})

// Stream drift events
stream, _ := client.ReportDrift(ctx)
stream.Send(&pb.DriftEvent{
    AgentId: resp.AgentId,
    CheckType: "firewall",
    Passed: false,
})
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
- `proto/compliance.proto` - gRPC definitions
- `frontend/src/utils/api.ts` - TypeScript client
