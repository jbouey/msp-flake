# Changelog

All notable changes to Central Command Dashboard.

## [Unreleased]

### Phase 1: Backend Foundation - 2025-12-30

#### Added
- **Directory Structure**: Created `central-command/` feature directory with backend, frontend, and docs subdirectories
- **README.md**: Feature documentation with architecture overview, API endpoints, and development instructions
- **Pydantic Models** (`backend/models.py`):
  - Health metrics: `HealthMetrics`, `ConnectivityMetrics`, `ComplianceMetrics`
  - Fleet: `Appliance`, `ClientOverview`, `ClientDetail`
  - Incidents: `Incident`, `IncidentDetail`
  - Runbooks: `Runbook`, `RunbookDetail`, `RunbookExecution`
  - Learning Loop: `LearningStatus`, `PromotionCandidate`, `PromotionHistory`
  - Onboarding: `OnboardingClient`, `OnboardingMetrics`, `ComplianceChecks`
  - Stats: `GlobalStats`, `ClientStats`
  - Command: `CommandRequest`, `CommandResponse`
  - Enums: `HealthStatus`, `ResolutionLevel`, `Severity`, `CheckType`, `OnboardingStage`, `CheckinStatus`

- **Metrics Engine** (`backend/metrics.py`):
  - `calculate_checkin_freshness()`: Score based on check-in age (100/75/50/25/0)
  - `calculate_connectivity_score()`: Combined check-in, healing, and order execution rates
  - `calculate_compliance_score()`: Binary pass/fail for 6 HIPAA checks
  - `calculate_overall_health()`: Weighted formula (40% connectivity + 60% compliance)
  - `get_health_status()`: Threshold-based status (critical/warning/healthy)
  - `aggregate_health_scores()`: Multi-appliance aggregation

- **Fleet Queries** (`backend/fleet.py`):
  - `get_pool()`: Async PostgreSQL connection pool management
  - `get_fleet_overview()`: All clients with aggregated health
  - `get_client_detail()`: Single client deep dive
  - `get_client_appliances()`: Appliances with health for a client
  - Mock data functions for development/testing

- **API Routes** (`backend/routes.py`):
  - Fleet: `GET /fleet`, `GET /fleet/{site_id}`, `GET /fleet/{site_id}/appliances`
  - Incidents: `GET /incidents`, `GET /incidents/{incident_id}`
  - Runbooks: `GET /runbooks`, `GET /runbooks/{id}`, `GET /runbooks/{id}/executions`
  - Learning: `GET /learning/status`, `GET /learning/candidates`, `GET /learning/history`, `POST /learning/promote/{id}`
  - Onboarding: `GET /onboarding`, `GET /onboarding/metrics`, `GET /onboarding/{id}`, `POST /onboarding`, `PATCH /onboarding/{id}/stage`, etc.
  - Stats: `GET /stats`, `GET /stats/{site_id}`
  - Command: `POST /command`

- **Documentation** (`docs/metrics-spec.md`):
  - Complete health scoring model specification
  - Connectivity and compliance score formulas
  - Data sources and aggregation rules
  - API response format examples

#### Notes
- All endpoints return mock data for Phase 1
- Database queries are implemented but not yet wired to live data
- Frontend foundation pending Phase 2

---

### Phase 2: Frontend Foundation - 2025-12-30

#### Added
- **Vite + React + TypeScript Setup** (`frontend/`):
  - `package.json` with React 18, React Router 6, TanStack Query 5
  - `vite.config.ts` with API proxy to MCP server at 178.156.162.116:8000
  - `tsconfig.json` with path aliases (@/components, @/tokens, etc.)
  - `tailwind.config.js` with iOS color palette and glassmorphism utilities
  - `postcss.config.js` for Tailwind processing

- **Design System** (`src/tokens/style-tokens.ts`):
  - iOS System Colors (red, orange, yellow, green, blue, purple, etc.)
  - Background colors (primary #F2F2F7, secondary white, tertiary frosted glass)
  - Text colors (primary #1C1C1E, secondary, tertiary)
  - Health status colors (critical, warning, healthy)
  - Resolution level colors (L1 blue, L2 purple, L3 orange)
  - Glass effects (blur 20px, saturate 180%, white border)
  - Spacing, border radius, typography tokens
  - Helper functions: `getHealthStatus()`, `getHealthColor()`, `getLevelColor()`

- **Shared Components** (`src/components/shared/`):
  - `GlassCard.tsx`: Frosted glass card with hover effects
  - `Badge.tsx`: Status badges for health, level, severity
  - `Button.tsx`: Primary, secondary, ghost, danger variants
  - `Spinner.tsx`: Loading spinner, skeleton loaders, loading screens

- **Layout Components** (`src/components/layout/`):
  - `Sidebar.tsx`: Client switcher with health dots, navigation menu, user info
  - `Header.tsx`: Page title, search, refresh indicator, user dropdown

- **Fleet Components** (`src/components/fleet/`):
  - `HealthGauge.tsx`: Circular progress indicator with color-coded status
  - `HealthRing.tsx`: Mini ring indicator for compact displays
  - `HealthBar.tsx`: Horizontal progress bar variant

- **TypeScript Types** (`src/types/index.ts`):
  - Mirrors all backend Pydantic models
  - Enums: HealthStatus, ResolutionLevel, Severity, CheckType, OnboardingStage

- **API Client** (`src/utils/api.ts`):
  - Type-safe fetch wrapper with error handling
  - API modules: fleetApi, incidentApi, runbookApi, learningApi, onboardingApi, statsApi, commandApi

- **React Hooks** (`src/hooks/`):
  - `useFleet()`: Fleet data with 30-second polling
  - `useClient()`: Single client details
  - `useIncidents()`: Incident list with filters
  - `useGlobalStats()`: Dashboard statistics
  - `useRefreshFleet()`: Manual refresh trigger

- **Page Stubs** (`src/pages/`):
  - `Dashboard.tsx`: Fleet overview with sample data, stats cards
  - `Runbooks.tsx`: Library placeholder with sample runbook cards
  - `Learning.tsx`: L2→L1 promotion dashboard placeholder
  - `Onboarding.tsx`: Two-phase pipeline visualization placeholder
  - `ClientDetail.tsx`: Client deep-dive page placeholder

- **App Structure** (`src/`):
  - `App.tsx`: React Router setup, QueryClient provider, main layout
  - `main.tsx`: React 18 root render
  - `index.css`: Tailwind directives, glass effects, badge/button styles

#### Notes
- All pages render with mock data from sidebar
- API client ready but not yet fetching live data (pending Phase 3)
- Vite dev server proxies /api to MCP server at 178.156.162.116:8000
- Run `npm install && npm run dev` in frontend/ to start

---

### Phase 3: Fleet Dashboard - 2025-12-30

#### Added
- **Fleet Components** (`src/components/fleet/`):
  - `FleetOverview.tsx`: Client grid with health cards, loading/error states
  - `ClientCard.tsx`: Individual client card with health gauge and stats

- **Incident Components** (`src/components/incidents/`):
  - `IncidentFeed.tsx`: Real-time incident list with compact/expanded modes
  - `IncidentRow.tsx`: Single incident display with level badges and status icons

- **Dashboard Updates** (`src/pages/Dashboard.tsx`):
  - Integrated FleetOverview with live API data
  - Added IncidentFeed with latest incidents
  - Wired up useGlobalStats and useLearningStatus hooks
  - Stats cards display real-time data

- **API Integration**:
  - All hooks now fetch from live /api/dashboard/* endpoints
  - 30-second automatic polling with React Query
  - Manual refresh via header refresh button

- **Production Deployment**:
  - Frontend deployed to Hetzner VPS at 178.156.162.116:3000
  - Nginx container serves static assets and proxies /api to MCP server
  - Dashboard API routes integrated into MCP server main.py
  - Docker Compose updated with frontend service

#### Notes
- Dashboard is live at http://178.156.162.116:3000
- Fleet data displays 3 mock clients with real health scoring
- Incidents feed shows recent activity with resolution levels
- Auto-refresh every 30 seconds with "Just now" timestamp

---

### Phase 4: Runbook Library - 2025-12-30

#### Added
- **Runbook Components** (`src/components/runbooks/`):
  - `RunbookCard.tsx`: Card displaying runbook info with ID, name, level badge, HIPAA controls, and execution stats
  - `RunbookDetail.tsx`: Modal showing detailed runbook view with steps, configuration, and execution history
  - `index.ts`: Component barrel exports

- **Runbooks Page** (`src/pages/Runbooks.tsx`):
  - Full runbook library grid layout
  - Search functionality by name, ID, or HIPAA control
  - Filter by resolution level (All, L1, L2, L3)
  - Click-to-view detail modal
  - Empty state handling

- **API Hooks** (`src/hooks/useFleet.ts`):
  - `useRunbooks()`: Fetch all runbooks with 30s caching
  - `useRunbook(id)`: Fetch single runbook details
  - `useRunbookExecutions(id, limit)`: Fetch execution history

- **Authentication System** (`src/contexts/AuthContext.tsx`):
  - React Context-based authentication
  - Default administrator account (admin/admin)
  - Role-based access control (admin/operator)
  - LocalStorage session persistence
  - Login/logout functions

- **Login Page** (`src/pages/Login.tsx`):
  - Glassmorphism login form
  - Error handling for invalid credentials
  - Default credentials hint
  - Auto-focus on username field

- **Audit Logs** (`src/pages/AuditLogs.tsx`):
  - Comprehensive action logging
  - Timestamp, user, action, target, details columns
  - Filter by action type and user
  - Search by target or details
  - CSV export for compliance audits
  - Admin-only access

- **Layout Updates**:
  - `Sidebar.tsx`: Added Audit Logs nav item (admin only), user info display, logout button
  - `Header.tsx`: Added user prop for displaying logged-in user info
  - `App.tsx`: Wrapped with AuthProvider, added login gate

- **User Documentation** (`docs/USER_GUIDE.md`):
  - Getting started guide with login instructions
  - Dashboard overview and health scoring explanation
  - Fleet management documentation
  - Runbook library usage guide
  - Audit logs access and export instructions
  - User roles and permissions
  - Keyboard shortcuts
  - Troubleshooting section

#### Notes
- Authentication uses client-side state with localStorage persistence
- Audit logs track VIEW, REFRESH, LOGIN, LOGOUT, and other actions
- Admin role required for audit log access
- All features deployed to http://178.156.162.116:3000

---

### Phase 5: Learning Loop Dashboard - 2025-12-30

#### Added
- **Learning Components** (`src/components/learning/`):
  - `PatternCard.tsx`: Card displaying L2 pattern candidates with approval/rejection actions
  - `PromotionTimeline.tsx`: Timeline view of recently promoted patterns with post-promotion stats
  - `index.ts`: Component barrel exports

- **Learning Page** (`src/pages/Learning.tsx`):
  - Four-stat overview: L1 Rules, L2 Decisions, L1 Resolution Rate, Promotion Success Rate
  - Awaiting Promotion section with pattern cards
  - "Approve All" batch action for administrators
  - Recently Promoted timeline with execution stats
  - Educational info section explaining the Learning Loop concept

- **API Hooks** (`src/hooks/useFleet.ts`):
  - `usePromotionCandidates()`: Fetch patterns awaiting promotion
  - `usePromotionHistory(limit)`: Fetch promotion timeline
  - `usePromotePattern()`: Mutation for promoting L2 patterns to L1

#### Notes
- Patterns are promoted from L2 (LLM-assisted, ~$0.001/call) to L1 (deterministic, $0, <100ms)
- Success thresholds and occurrence counts drive promotion eligibility
- All features deployed to http://178.156.162.116:3000/learning

---

### Phase 6: Command Bar - 2025-12-30

#### Added
- **Command Components** (`src/components/command/`):
  - `CommandBar.tsx`: Modal command palette with search, navigation, and command execution
  - `index.ts`: Component barrel exports

- **Keyboard Shortcuts** (`src/hooks/useKeyboardShortcuts.ts`):
  - `useKeyboardShortcuts()`: Generic hook for registering keyboard shortcuts
  - `useCommandPalette()`: Specialized hook for Cmd+K / Ctrl+K to open command bar

- **Command Bar Features**:
  - **Quick Navigation:** Jump to Dashboard, Runbooks, Learning, Onboarding, Audit Logs
  - **Quick Actions:** Refresh data, open documentation PDFs
  - **Command Execution:** Start commands with `/` or `!` for API commands
  - **Keyboard Navigation:** Arrow keys, Enter to select, Escape to close
  - **Search Filtering:** Filter suggestions by typing
  - **Category Grouping:** Navigation and Actions separated
  - **Command Results:** Success/error display with data output

- **App Integration** (`src/App.tsx`):
  - CommandBar component integrated into main layout
  - Cmd+K / Ctrl+K shortcut active globally

#### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Cmd+K / Ctrl+K | Open command bar |
| Arrow Up/Down | Navigate suggestions |
| Enter | Select/Execute |
| Escape | Close command bar |

#### Notes
- Command bar accessible from any page after login
- PDF documentation links open in new tab
- Custom commands can be sent to `/api/dashboard/command` endpoint
- All features deployed to http://178.156.162.116:3000

---

### Phase 7: Client Detail & Polish - 2025-12-30

#### Added
- **ClientDetail Page** (`src/pages/ClientDetail.tsx`):
  - Full redesign with live API data integration
  - Overall health gauge with visual indicator
  - Connectivity metrics with progress bars (check-in, healing, order execution)
  - Compliance breakdown with PASS/WARN/FAIL status
  - Appliance list with online status, IP, version, last check-in
  - Recent incidents table with resolution levels
  - Quick stats: Total appliances, online count, open incidents, L1 resolved

- **Error Handling**:
  - Loading spinner while fetching client data
  - Error state with "Client Not Found" message
  - Return to Dashboard button for navigation

- **Visual Polish**:
  - Progress bars for connectivity metrics
  - Color-coded compliance status indicators
  - Online/offline indicators for appliances
  - Relative time formatting for check-ins

#### Notes
- Client detail accessible by clicking client cards in sidebar
- Back button returns to dashboard
- Auto-refreshes with 30-second polling
- All features deployed to http://178.156.162.116:3000/client/{site_id}

---

### Phase 8: Onboarding Pipeline - 2025-12-30

#### Added
- **Onboarding Components** (`src/components/onboarding/`):
  - `OnboardingCard.tsx`: Prospect card with phase indicator, progress bar, blockers, days in stage
  - `PipelineStages.tsx`: Two-phase funnel visualization with bar charts
  - `index.ts`: Component barrel exports

- **Onboarding Page** (`src/pages/Onboarding.tsx`):
  - Two-phase pipeline visualization (Acquisition & Activation)
  - Stats: At Risk, Stalled, Connectivity Issues, Recently Activated
  - Blocker alert banner for prospects with blockers
  - Phase filter (All, Phase 1, Phase 2)
  - Sorted prospect list (at-risk first)
  - Recently Activated section for completed clients

- **API Hooks** (`src/hooks/useFleet.ts`):
  - `useOnboardingPipeline()`: Fetch all prospects
  - `useOnboardingMetrics()`: Fetch pipeline metrics

- **Pipeline Features**:
  - Phase 1 (Acquisition): Lead, Discovery, Proposal, Contract, Intake, Credentials, Shipped
  - Phase 2 (Activation): Received, Connectivity, Scanning, Baseline, Compliant, Active
  - Visual bar charts showing counts per stage
  - Average days to ship/active metrics
  - At-risk highlighting (>7 days in stage)
  - Blocker tags on prospect cards
  - Progress percentage visualization

#### Notes
- Prospects sorted by days in stage to surface at-risk clients
- Empty state with CTA to add new prospects
- All features deployed to http://178.156.162.116:3000/onboarding

---

### Phase 9: Learning Loop Infrastructure - 2025-12-30

#### Added
- **Database Module** (`mcp-server/database/`):
  - `models.py`: SQLAlchemy models for centralized storage
    - `ClientRecord`: Registered clients with health scores
    - `ApplianceRecord`: Individual appliances with check-in tracking
    - `IncidentRecord`: All incidents from all agents
    - `ExecutionRecord`: Runbook execution results (feeds learning)
    - `PatternRecord`: Detected patterns from L2 decisions
    - `RuleRecord`: Promoted L1 deterministic rules
    - `AuditLog`: Compliance audit trail
  - `store.py`: IncidentStore class with full CRUD operations
    - Client/appliance registration and health tracking
    - Incident creation, resolution, and querying
    - Execution recording with pattern aggregation
    - Promotion candidate detection (5+ occurrences, 90%+ success)
    - Pattern-to-rule promotion workflow
    - Global and per-client statistics

- **Server Integration** (`mcp-server/server.py`):
  - Database initialization on startup
  - Incident storage when `/chat` receives incidents
  - L1 rule lookup before LLM (cost savings)
  - Execution recording when `/evidence` receives bundles
  - Automatic incident resolution on successful execution
  - Pattern aggregation for learning loop

- **Learning Loop Endpoints**:
  - `GET /rules`: All active L1 rules for agents
  - `GET /learning/status`: Learning loop statistics
  - `GET /learning/candidates`: Patterns eligible for promotion
  - `POST /learning/promote/{pattern_id}`: Promote to L1 rule
  - `GET /learning/history`: Recently promoted patterns

- **Agent Sync System** (`mcp-server/agent_sync.py`):
  - `GET /agent/sync`: Full sync for agents (rules, config, timestamp)
  - `POST /agent/checkin`: Health reporting from agents
  - Rules versioning for efficient change detection
  - Configurable check intervals

- **Dashboard Real Data** (`central-command/backend/routes.py`):
  - Fleet endpoints now query database (fallback to mock)
  - Learning status from real pattern data
  - Stats from actual incident counts
  - Client detail with live appliances and incidents

- **Docker Updates** (`mcp-server/Dockerfile`):
  - Added SQLAlchemy dependency
  - Added aiohttp for async HTTP
  - Changed default CMD to full server
  - Created /var/lib/mcp-server for database

#### Architecture
```
Agent                    MCP Server                    Dashboard
  |                          |                             |
  |---(1) Report incident--->|                             |
  |                          |---(2) Store in DB           |
  |                          |---(3) Check L1 rules        |
  |                          |   (if match, skip LLM)      |
  |                          |---(4) Select runbook        |
  |<--(5) Remediation order--|                             |
  |                          |                             |
  |---(6) Execute runbook    |                             |
  |                          |                             |
  |---(7) Submit evidence--->|                             |
  |                          |---(8) Record execution      |
  |                          |---(9) Update patterns       |
  |                          |---(10) Resolve incident     |
  |                          |                             |
  |                          |<---(11) Query stats---------|
  |                          |<---(12) View learning-------|
  |                          |<---(13) Promote pattern-----|
  |                          |                             |
  |<--(14) Sync L1 rules-----|                             |
```

#### Learning Loop Flow
1. **Pattern Detection**: Every L2 decision creates/updates a pattern
2. **Aggregation**: Patterns track occurrences, success rate, resolution time
3. **Candidate Surfacing**: Patterns with 5+ occurrences and 90%+ success
4. **Promotion**: Admin approves pattern → creates L1 rule
5. **Distribution**: Agents sync L1 rules periodically
6. **Local Execution**: Future incidents match L1 rules → no LLM cost

#### Notes
- SQLite database stored at `/var/lib/mcp-server/mcp.db`
- Dashboard falls back to mock data if DB is empty
- L1 rules reduce LLM costs from ~$0.001/call to $0/call
- Pattern signatures use MD5 hash of incident_type:runbook_id

---

## All Phases Complete

Central Command Dashboard v1.1.0 is now fully implemented with all 9 phases:

1. **Backend Foundation** - Pydantic models, metrics engine, API routes
2. **Frontend Foundation** - Vite/React/TypeScript, design system, shared components
3. **Fleet Dashboard** - Client overview, health gauges, incident feed
4. **Runbook Library** - Runbook cards, detail modal, execution history
5. **Learning Loop** - Pattern promotion, L2→L1 flywheel
6. **Command Bar** - Cmd+K palette, navigation, quick actions
7. **Client Detail** - Deep-dive view, appliances, compliance breakdown
8. **Onboarding Pipeline** - Two-phase funnel, prospect management
9. **Learning Loop Infrastructure** - Database, agent sync, real data

### Additional Features
- **Authentication** - admin/admin login, role-based access
- **Audit Logs** - Action tracking, CSV export (admin only)
- **User Documentation** - PDF guide deployed to /USER_GUIDE.pdf
- **Standards & Procedures** - PDF deployed to /STANDARDS_AND_PROCEDURES.pdf
- **Centralized Database** - SQLite with SQLAlchemy for all fleet data
- **Agent Sync API** - L1 rule distribution and health reporting
