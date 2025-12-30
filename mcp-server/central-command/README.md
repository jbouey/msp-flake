# Central Command Dashboard

Web-based dashboard for Malachor MSP compliance platform. Provides fleet overview, incident tracking, runbook management, learning loop visibility, and onboarding pipeline.

## Design Language

iOS/Apple glassmorphism: white backgrounds, frosted glass cards, subtle shadows, SF Pro-inspired typography.

## Directory Structure

```
central-command/
├── README.md                     # This file
├── CHANGELOG.md                  # Track all changes
│
├── backend/
│   ├── __init__.py
│   ├── models.py                 # Pydantic models for API
│   ├── metrics.py                # Health/compliance scoring
│   ├── fleet.py                  # Multi-tenant fleet aggregation
│   ├── runbooks.py               # Runbook library management
│   ├── learning.py               # L2→L1 promotion status
│   ├── onboarding.py             # Onboarding pipeline logic
│   ├── chat.py                   # Command interface handler
│   └── routes.py                 # FastAPI routes
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── tokens/               # Design system
│       ├── components/           # UI components
│       ├── pages/                # Route pages
│       ├── hooks/                # React hooks
│       ├── utils/                # Utilities
│       └── types/                # TypeScript types
│
└── docs/
    ├── metrics-spec.md           # Scoring model documentation
    ├── api-spec.md               # API endpoint docs
    └── design-system.md          # Component documentation
```

## Health Scoring Model

### Overall Health
```
overall = (connectivity.score * 0.4) + (compliance.score * 0.6)
```

### Connectivity Score (40% weight)
- Check-in freshness (0-100 based on last_checkin age)
- Healing success rate (successful_heals / total_incidents * 100)
- Order execution rate (executed_orders / total_orders * 100)

### Compliance Score (60% weight)
- Patching (0 or 100)
- Antivirus (0 or 100)
- Backup (0 or 100)
- Logging (0 or 100)
- Firewall (0 or 100)
- Encryption (0 or 100)

### Health Thresholds
| Score | Color | Label |
|-------|-------|-------|
| 0-39 | Red | Critical |
| 40-79 | Yellow | Warning |
| 80-100 | Green | Healthy |

## API Endpoints

### Fleet
- `GET /api/dashboard/fleet` - All clients with health scores
- `GET /api/dashboard/fleet/{site_id}` - Client detail
- `GET /api/dashboard/fleet/{site_id}/appliances` - Client appliances

### Incidents
- `GET /api/dashboard/incidents` - Recent incidents
- `GET /api/dashboard/incidents/{incident_id}` - Incident detail

### Runbooks
- `GET /api/dashboard/runbooks` - All runbooks
- `GET /api/dashboard/runbooks/{runbook_id}` - Runbook detail
- `GET /api/dashboard/runbooks/{runbook_id}/executions` - Execution history

### Learning Loop
- `GET /api/dashboard/learning/status` - Learning loop status
- `GET /api/dashboard/learning/candidates` - Promotion candidates
- `GET /api/dashboard/learning/history` - Promotion history
- `POST /api/dashboard/learning/promote/{pattern_id}` - Promote pattern

### Onboarding
- `GET /api/dashboard/onboarding` - Pipeline overview
- `GET /api/dashboard/onboarding/metrics` - Pipeline metrics
- `GET /api/dashboard/onboarding/{client_id}` - Client detail
- `POST /api/dashboard/onboarding` - Create prospect
- `PATCH /api/dashboard/onboarding/{client_id}/stage` - Advance stage

### Stats & Command
- `GET /api/dashboard/stats` - Global statistics
- `POST /api/dashboard/command` - Command interface

## Development

```bash
# Backend (runs with existing MCP server)
cd mcp-server
python server.py

# Frontend
cd central-command/frontend
npm install
npm run dev
```

## Build Phases

1. Backend Foundation - Models, metrics, routes
2. Frontend Foundation - React + Vite + Tailwind + design tokens
3. Fleet Dashboard - Client grid with health gauges
4. Runbook Library - HIPAA mappings, execution stats
5. Learning Loop Dashboard - L2→L1 promotion UI
6. Command Bar - Natural language interface
7. Client Detail & Polish - Deep dive views
8. Onboarding Pipeline - Prospect tracking
