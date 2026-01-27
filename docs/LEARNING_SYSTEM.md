# Self-Learning Runbook Improvement System

## Implementation Status: COMPLETE ✅

**Verified 2026-01-27**

| Component | Status | Notes |
|-----------|--------|-------|
| Pattern Aggregation | ✅ | 8 patterns tracked, 6 promotion-eligible |
| Partner Dashboard API | ✅ | `/api/partners/me/learning/*` endpoints |
| Partner Dashboard UI | ✅ | Learning tab with approve/reject/disable |
| Rule YAML Generation | ✅ | Generates correct conditions and runbook mappings |
| Sync Endpoint | ✅ | `/api/agent/sync/promoted-rules` |
| Agent Fetch & Deploy | ✅ | LearningSyncService syncs every 4 hours |
| L1 Engine Loading | ✅ | Loads from `rules/promoted/` directory |

---

## Executive Summary

This system automatically improves runbooks by learning from execution failures. When a remediation fails, an LLM analyzes what went wrong and generates an improved runbook. Every LLM-generated runbook requires human review before production use.

**Business Value:**
- Runbooks get better every week without manual effort
- System learns from real production failures
- 6-month runbook library will outperform human-written alternatives
- This is the actual moat - not just the tech, but continuously improving knowledge

**Safety Model:**
- NO LLM-generated runbook executes without human approval
- All improvements tracked with full audit trail
- Humans always have final say

---

## Critical: Resolution Recording Requirement

**IMPORTANT (Session 62 Fix):** The learning data flywheel requires that `resolve_incident()` is called after every healing attempt. Without resolution recording:
- `pattern_stats` table shows 0 L1/L2/L3 resolutions
- L2→L1 promotion criteria cannot be evaluated
- The learning system cannot function

**Required in auto_healer.py:**
```python
from .incident_db import IncidentDatabase, Incident, ResolutionLevel, IncidentOutcome

# After L1 healing:
self.incident_db.resolve_incident(
    incident_id=incident.id,
    resolution_level=ResolutionLevel.LEVEL1_DETERMINISTIC,
    resolution_action=match.action,
    outcome=IncidentOutcome.SUCCESS if success else IncidentOutcome.FAILURE,
    resolution_time_ms=duration_ms
)

# After L2 healing:
self.incident_db.resolve_incident(
    incident_id=incident.id,
    resolution_level=ResolutionLevel.LEVEL2_LLM,
    resolution_action=action_taken,
    outcome=IncidentOutcome.SUCCESS if success else IncidentOutcome.FAILURE,
    resolution_time_ms=duration_ms
)

# After L3 escalation:
self.incident_db.resolve_incident(
    incident_id=incident.id,
    resolution_level=ResolutionLevel.LEVEL3_HUMAN,
    resolution_action="Escalated to human operator",
    outcome=IncidentOutcome.ESCALATED
)
```

This ensures the `pattern_stats` table tracks:
- `total_occurrences` - How many times pattern seen
- `l1_resolutions` - Successful L1 heals
- `l2_resolutions` - Successful L2 heals
- `l3_resolutions` - Escalations to human
- `success_count` - Total successful resolutions

---

## Bidirectional Sync (Session 73)

The learning system now supports full bidirectional synchronization between agents (SQLite) and Central Command (PostgreSQL).

### Agent → Server Sync

**Pattern Stats Push (every 4 hours):**
```
POST /api/agent/sync/pattern-stats
{
  "site_id": "north-valley-dental",
  "appliance_id": "north-valley-dental-00:11:22:33:44:55",
  "synced_at": "2026-01-27T04:00:00Z",
  "pattern_stats": [
    {
      "pattern_signature": "abc12345...",
      "total_occurrences": 15,
      "l1_resolutions": 12,
      "l2_resolutions": 2,
      "l3_resolutions": 1,
      "success_count": 14,
      "total_resolution_time_ms": 45000.0,
      "recommended_action": "RB-WIN-FIREWALL-001",
      "promotion_eligible": true
    }
  ]
}
```

**Execution Telemetry (after each healing):**
```
POST /api/agent/executions
{
  "site_id": "north-valley-dental",
  "execution": {
    "execution_id": "uuid",
    "runbook_id": "RB-WIN-FIREWALL-001",
    "incident_id": "INC-123",
    "hostname": "NVDC01",
    "success": true,
    "state_before": {"firewall_enabled": false},
    "state_after": {"firewall_enabled": true},
    "state_diff": {"changed_keys": ["firewall_enabled"]},
    "duration_seconds": 2.5
  }
}
```

### Server → Agent Sync

**Promoted Rules Pull:**
```
GET /api/agent/sync/promoted-rules?site_id=north-valley-dental&since=2026-01-26T00:00:00Z

Response:
{
  "rules": [
    {
      "rule_id": "L1-PROMOTED-ABC12345",
      "pattern_signature": "abc12345...",
      "rule_yaml": "id: L1-PROMOTED-ABC12345\nname: ...",
      "promoted_at": "2026-01-27T00:00:00Z",
      "promoted_by": "admin@site.com"
    }
  ]
}
```

**Server-Pushed Rule (via command):**
```python
# Server sends command via poll_commands()
{
  "command_type": "sync_promoted_rule",
  "params": {
    "rule_id": "L1-PROMOTED-ABC12345",
    "rule_yaml": "...",
    "promoted_at": "...",
    "promoted_by": "..."
  }
}
```

### Database Tables (PostgreSQL)

| Table | Purpose |
|-------|---------|
| `aggregated_pattern_stats` | Cross-appliance pattern aggregation |
| `appliance_pattern_sync` | Track last sync per appliance |
| `promoted_rule_deployments` | Audit trail of rule deployments |
| `execution_telemetry` | Rich execution data for learning engine |

### Offline Queue

When Central Command is unreachable, operations are queued locally:
- SQLite WAL mode for durability
- Exponential backoff (2^n minutes, max 60)
- Automatic replay when connectivity returns

---

## Partner Promotion Workflow (Session 74)

The learning system now includes a complete partner-facing workflow for reviewing and approving L2→L1 pattern promotions.

### Partner Learning Dashboard

Partners access the Learning tab in their dashboard to:
1. View promotion-eligible patterns across their sites
2. Approve patterns to generate new L1 rules
3. Reject patterns with documented reasons
4. Manage active promoted rules (enable/disable)
5. View execution history and resolution statistics

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/partners/me/learning/stats` | GET | Dashboard statistics (pending, active, rates) |
| `/api/partners/me/learning/candidates` | GET | Promotion-eligible patterns |
| `/api/partners/me/learning/candidates/{id}` | GET | Pattern details with history |
| `/api/partners/me/learning/candidates/{id}/approve` | POST | Approve and generate L1 rule |
| `/api/partners/me/learning/candidates/{id}/reject` | POST | Reject with reason |
| `/api/partners/me/learning/promoted-rules` | GET | Active promoted rules list |
| `/api/partners/me/learning/promoted-rules/{id}/status` | PATCH | Toggle rule status |
| `/api/partners/me/learning/execution-history` | GET | Recent healing executions |

### Approval Request Body

```json
{
  "custom_rule_name": "Print Spooler Auto-Restart",
  "notes": "Approved after 15 successful L2 resolutions"
}
```

### Generated Rule Format

```yaml
id: L1-PROMOTED-PRINT-SP
name: Print Spooler Auto-Restart
description: Auto-generated from pattern promotion
check_type: windows_service
status: ["warning", "fail", "error"]
action: run_runbook:RB-WIN-SERVICES-001
priority: 5
```

### Database Tables (032_learning_promotion.sql)

| Table | Purpose |
|-------|---------|
| `promoted_rules` | Stores generated L1 rules with YAML content |
| `v_partner_promotion_candidates` | Partner-scoped candidates view |
| `v_partner_learning_stats` | Dashboard statistics aggregation |

### Rule Generation Flow

```
1. Pattern meets promotion criteria (5+ occurrences, 90%+ success)
   ↓
2. Appears in Partner Learning Dashboard as candidate
   ↓
3. Partner reviews pattern details and execution history
   ↓
4. Partner approves with custom name and notes
   ↓
5. System generates L1 rule YAML with appropriate action
   ↓
6. Rule stored in promoted_rules table
   ↓
7. Rule deployed to agents via sync mechanism
   ↓
8. Future incidents match L1 rule (instant, free)
```

### VPS Deployment Note

**Critical:** The VPS uses Docker compose volume mounts that override built images:
- Backend: `/opt/mcp-server/dashboard_api_mount/` → `/app/dashboard_api`
- Frontend: `/opt/mcp-server/frontend_dist/` → `/usr/share/nginx/html`

Deploy files to host mount paths, not to image build paths.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     EXECUTION FLOW                              │
└─────────────────────────────────────────────────────────────────┘

1. Incident Detected
   ↓
2. Runbook Executed (with telemetry capture)
   ↓
3. ExecutionResult Created (rich telemetry)
   ↓
4. Learning Engine Analyzes
   ↓
   ├─ Success → Extract patterns
   │
   └─ Failure → Categorize cause
      ↓
      └─ If improvable → Generate improved runbook
         ↓
         └─ Queue for human review
            ↓
            ├─ Approved → Activate for production
            │
            └─ Rejected → Archive with reason
```

---

## Components

### 1. ExecutionResult Schema

**Purpose:** Capture everything about a runbook execution for learning.

**Location:** `mcp-server/schemas/execution_result.py`

**Key Fields:**
- Identity: execution_id, runbook_id, incident_id
- Timing: started_at, completed_at, duration_seconds
- Success metrics: status, verification_passed, confidence
- **State capture**: state_before, state_after, state_diff (CRITICAL FOR LEARNING)
- Execution trace: executed_steps (each step with timing, output, errors)
- Error details: error_message, error_step, error_traceback
- Learning signals: was_correct_runbook, failure_type, human_feedback

**Example:**
```python
execution_result = ExecutionResult(
    execution_id="exec-20251110-0001",
    runbook_id="RB-WIN-SERVICE-001",
    incident_id="inc-20251110-0042",
    incident_type="service_crash",
    state_before={"service_status": "stopped", "cpu_usage": 12},
    state_after={"service_status": "stopped", "cpu_usage": 12},
    error_message="Service failed to start: dependency missing",
    error_step=2,
    failure_type="runbook_insufficient"
)
```

---

### 2. Learning Engine

**Purpose:** Analyzes execution results and triggers runbook improvement.

**Location:** `mcp-server/learning/learning_engine.py`

**Key Methods:**

#### `analyze_execution(result: ExecutionResult)`
Main entry point called after every execution.

**Process:**
1. If success → Extract patterns (future enhancement)
2. If failure → Categorize failure type
3. If improvable → Generate improved runbook
4. Store analysis in database

#### `_categorize_failure(result: ExecutionResult) → FailureType`
Uses LLM (GPT-4o-mini, cheap) to categorize WHY runbook failed.

**Failure Types:**
- `wrong_diagnosis`: Misclassified incident type
- `wrong_runbook`: Right diagnosis, wrong solution
- `runbook_insufficient`: Runbook incomplete or buggy (IMPROVABLE)
- `environment_difference`: Environment-specific issue (IMPROVABLE)
- `external_dependency`: External service unavailable
- `permission_denied`: Access/auth issue

**Only IMPROVABLE types trigger runbook generation.**

#### `_generate_improved_runbook(result: ExecutionResult) → str`
**THIS IS THE MAGIC**

Uses LLM (GPT-4o, expensive but worth it) to generate improved runbook.

**Process:**
1. Get original runbook
2. Build rich context prompt (failure details, state, executed steps)
3. Call GPT-4o to generate improved YAML
4. Validate structure
5. Generate version ID (RB-WIN-SERVICE-001-v2)
6. Store with metadata tracking lineage
7. **Queue for human review** (SAFETY CRITICAL)

**Example Prompt Structure:**
```
You are a Senior SRE analyzing a failed remediation.

ORIGINAL RUNBOOK:
[YAML of parent runbook]

EXECUTION CONTEXT:
- Incident: service_crash
- Platform: windows
- Error: "Service failed to start: dependency missing"
- Failed at step: 2

STATE BEFORE:
{"service_status": "stopped", "dependency_service": "stopped"}

STATE AFTER:
{"service_status": "stopped", "dependency_service": "stopped"}

YOUR TASK:
Generate an IMPROVED runbook that handles this case.

Add step to check/start dependency service first.
Include comments explaining changes.
```

---

### 3. Review Queue

**Purpose:** Human approval workflow - the safety gate.

**Location:** `mcp-server/review/review_queue.py`

**Key Concepts:**

#### ReviewStatus
- `pending_review`: Waiting for human
- `in_review`: Assigned to reviewer
- `approved`: Green light for production
- `rejected`: Archived, won't be used
- `needs_changes`: Requires modifications

#### ReviewPriority
- `high`: Critical incident type, blocking production
- `medium`: Standard improvement
- `low`: Optimization, nice-to-have

**Key Methods:**

#### `add(runbook_id, reason, failure_context)`
Add runbook to review queue and notify human.

#### `approve(runbook_id, reviewer, notes)`
Approve runbook for production:
1. Update queue status
2. Activate runbook (make available for selection)
3. Notify stakeholders

#### `reject(runbook_id, reviewer, reason)`
Reject runbook:
1. Update queue status
2. Archive runbook (not deleted, for learning)
3. Store rejection reason

---

### 4. Review API

**Purpose:** REST API for human reviewers.

**Location:** `mcp-server/api/review_endpoints.py`

**Endpoints:**

#### `GET /api/review/pending`
List all pending reviews, sorted by priority then age.

**Query params:**
- `priority`: Filter by high/medium/low
- `limit`: Max results (default 50)

**Response:**
```json
{
  "count": 3,
  "pending_reviews": [
    {
      "runbook_id": "RB-WIN-SERVICE-001-v2",
      "priority": "high",
      "reason": "Generated from execution failure",
      "created_at": "2025-11-10T14:35:00Z"
    }
  ]
}
```

#### `GET /api/review/runbook/{id}`
Get comprehensive review details:
- Runbook content
- Parent runbook (for comparison)
- Failure context that triggered generation
- Execution history
- Test results

**Response:**
```json
{
  "runbook": {...},
  "parent_runbook": {...},
  "failure_context": {
    "error_message": "Service failed to start",
    "error_step": 2,
    "state_before": {...}
  },
  "parent_execution_history": [...]
}
```

#### `POST /api/review/approve/{id}`
Approve runbook for production.

**Body:**
```json
{
  "reviewer": "john@example.com",
  "notes": "Tested on staging, looks good"
}
```

#### `POST /api/review/reject/{id}`
Reject runbook.

**Body:**
```json
{
  "reviewer": "john@example.com",
  "reason": "Doesn't handle edge case X"
}
```

#### `POST /api/review/test/{id}`
Add test result.

**Body:**
```json
{
  "test_name": "staging_deployment",
  "passed": true,
  "details": {"duration": "12s", "incidents_fixed": 3}
}
```

#### `GET /api/review/stats`
Get queue statistics for dashboard.

**Response:**
```json
{
  "queue_stats": {
    "pending_review": 5,
    "in_review": 2,
    "approved": 47,
    "rejected": 3,
    "high_priority_pending": 1
  }
}
```

---

### 5. Review Dashboard

**Purpose:** Web UI for reviewing runbooks.

**Location:** `mcp-server/templates/review_dashboard.html`

**Features:**
- Real-time stats display
- Pending reviews sorted by priority
- Side-by-side runbook comparison
- Failure context display
- One-click approve/reject
- Auto-refresh every 30 seconds

**Usage:**
1. Open dashboard: `http://localhost:8000/review`
2. Click review item to open details
3. Compare parent vs improved runbook
4. Review failure context
5. Approve or reject with notes

---

## Integration Guide

### Step 1: Update Your Executor

Modify your runbook executor to capture rich telemetry:

```python
from mcp_server.schemas.execution_result import ExecutionResult, ExecutionStatus
from mcp_server.learning.learning_engine import LearningEngine

async def execute_runbook(runbook, incident, params):
    # Generate IDs
    execution_id = generate_execution_id()
    started_at = datetime.utcnow()

    # STEP 1: Capture state BEFORE
    state_before = await capture_system_state(
        hostname=params["hostname"],
        checks=["services", "disk", "cpu", "memory"]
    )

    # STEP 2: Execute runbook steps
    executed_steps = []
    success = True
    error_message = None

    for i, step in enumerate(runbook["steps"]):
        step_result = await execute_step(step, params, i + 1)
        executed_steps.append(step_result)

        if not step_result.success:
            success = False
            error_message = step_result.error
            break

    # STEP 3: Capture state AFTER
    state_after = await capture_system_state(
        hostname=params["hostname"],
        checks=["services", "disk", "cpu", "memory"]
    )

    # STEP 4: Compute diff
    state_diff = compute_state_diff(state_before, state_after)

    # STEP 5: Verify fix worked
    verification_passed, verification_method, confidence = await verify_fix(
        incident_type=incident["type"],
        state_before=state_before,
        state_after=state_after
    )

    # STEP 6: Build ExecutionResult
    execution_result = ExecutionResult(
        execution_id=execution_id,
        runbook_id=runbook["id"],
        incident_id=incident["id"],
        incident_type=incident["type"],
        client_id=params["client_id"],
        hostname=params["hostname"],
        platform=runbook["platform"],
        started_at=started_at,
        completed_at=datetime.utcnow(),
        duration_seconds=...,
        status=ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILURE,
        success=success,
        verification_passed=verification_passed,
        verification_method=verification_method,
        confidence=confidence,
        state_before=state_before,
        state_after=state_after,
        state_diff=state_diff,
        executed_steps=executed_steps,
        error_message=error_message,
        evidence_bundle_id=generate_evidence_id()
    )

    # STEP 7: Store in database
    await db.execution_results.insert_one(execution_result.to_dict())

    # STEP 8: TRIGGER LEARNING ENGINE
    await learning_engine.analyze_execution(execution_result)

    return execution_result
```

### Step 2: Implement State Capture

Create functions to capture system state:

```python
async def capture_system_state(hostname, checks):
    """Capture current system state"""
    state = {}

    if "services" in checks:
        state["services"] = await check_service_status(hostname)

    if "disk" in checks:
        state["disk"] = await check_disk_usage(hostname)

    if "cpu" in checks:
        state["cpu"] = await check_cpu_usage(hostname)

    if "memory" in checks:
        state["memory"] = await check_memory_usage(hostname)

    return state

async def check_service_status(hostname):
    """Get status of key services"""
    # Via SSH/WinRM
    # Return: {"nginx": "running", "postgresql": "stopped", ...}

async def check_disk_usage(hostname):
    """Get disk usage metrics"""
    # Return: {"usage_percent": 75, "free_gb": 128.5, ...}
```

### Step 3: Implement Fix Verification

Create verification logic for each incident type:

```python
async def verify_fix(incident_type, state_before, state_after):
    """Verify the fix actually worked"""

    if incident_type == "service_crash":
        # Check if stopped service is now running
        services_before = state_before.get("services", {})
        services_after = state_after.get("services", {})

        for service, status_before in services_before.items():
            status_after = services_after.get(service)
            if status_before == "stopped" and status_after == "running":
                return (True, "service_status_check", 0.95)

        return (False, "service_status_check", 0.5)

    elif incident_type == "disk_full":
        # Check if disk usage decreased
        usage_before = state_before.get("disk", {}).get("usage_percent", 100)
        usage_after = state_after.get("disk", {}).get("usage_percent", 100)

        if usage_before - usage_after >= 5:
            return (True, "disk_usage_check", 0.9)

        return (False, "disk_usage_check", 0.3)

    # Add more incident types...

    return (None, "no_verification", 0.0)
```

### Step 4: Initialize Learning System

In your main application:

```python
from mcp_server.learning.learning_engine import LearningEngine
from mcp_server.review.review_queue import ReviewQueue
from your_llm_client import LLMClient
from your_runbook_repo import RunbookRepository

# Initialize components
llm_client = LLMClient(api_key=...)
runbook_repo = RunbookRepository(db)
review_queue = ReviewQueue(db)

learning_engine = LearningEngine(
    llm_client=llm_client,
    runbook_repo=runbook_repo,
    review_queue=review_queue,
    db=db
)

# Make available to executor
app.state.learning_engine = learning_engine
```

---

## Database Schema

### execution_results
```json
{
  "_id": ObjectId,
  "execution_id": "exec-20251110-0001",
  "runbook_id": "RB-WIN-SERVICE-001",
  "incident_id": "inc-20251110-0042",
  "incident_type": "service_crash",
  "client_id": "clinic-001",
  "hostname": "srv-dc01",
  "platform": "windows",
  "started_at": "2025-11-10T14:32:01Z",
  "completed_at": "2025-11-10T14:35:23Z",
  "duration_seconds": 202,
  "status": "failure",
  "success": false,
  "verification_passed": false,
  "verification_method": "service_status_check",
  "confidence": 0.0,
  "state_before": {...},
  "state_after": {...},
  "state_diff": {...},
  "executed_steps": [...],
  "error_message": "Service failed to start: dependency missing",
  "error_step": 2,
  "failure_type": "runbook_insufficient",
  "evidence_bundle_id": "EB-20251110-0001"
}
```

### review_queue
```json
{
  "_id": ObjectId,
  "runbook_id": "RB-WIN-SERVICE-001-v2",
  "status": "pending_review",
  "priority": "high",
  "reason": "Generated from execution failure",
  "failure_execution_id": "exec-20251110-0001",
  "created_at": "2025-11-10T14:35:30Z",
  "reviewed_by": null,
  "reviewed_at": null,
  "approval_notes": null,
  "test_results": []
}
```

### runbooks (with learning metadata)
```json
{
  "_id": ObjectId,
  "id": "RB-WIN-SERVICE-001-v2",
  "name": "Windows Service Restart with Dependency Check",
  "platform": "windows",
  "incident_types": ["service_crash"],
  "steps": [...],
  "status": "pending_review",
  "metadata": {
    "parent_runbook": "RB-WIN-SERVICE-001",
    "generated_from_failure": "exec-20251110-0001",
    "generated_at": "2025-11-10T14:35:25Z",
    "generated_by": "learning_engine",
    "generation_model": "gpt-4o",
    "requires_human_review": true,
    "failure_type": "runbook_insufficient"
  }
}
```

---

## Monitoring & Metrics

### Key Metrics to Track

1. **Learning Rate**
   - Improved runbooks generated per week
   - Approval rate (% approved vs rejected)
   - Time to review (median, p95)

2. **Improvement Impact**
   - Success rate: v1 vs v2 vs v3
   - MTTR reduction over time
   - Verification confidence trend

3. **Queue Health**
   - Pending review backlog
   - High priority aging
   - Review throughput (reviews/day)

### Dashboard Queries

```python
# Learning rate
improved_runbooks_per_week = await db.runbooks.count_documents({
    "metadata.generated_by": "learning_engine",
    "metadata.generated_at": {"$gte": one_week_ago}
})

# Success rate by version
async def get_version_success_rates(base_runbook_id):
    versions = await db.runbooks.find({
        "id": {"$regex": f"^{base_runbook_id}"}
    }).to_list()

    stats = []
    for version in versions:
        executions = await db.execution_results.find({
            "runbook_id": version["id"]
        }).to_list()

        success_count = sum(1 for e in executions if e["success"])
        success_rate = (success_count / len(executions)) * 100 if executions else 0

        stats.append({
            "version": version["id"],
            "executions": len(executions),
            "success_rate": success_rate
        })

    return stats
```

---

## Best Practices

### 1. Telemetry Capture

**DO:**
- Capture state before AND after every execution
- Record every step with timing and output
- Include verification results
- Tag with client_id, platform, incident_type for filtering

**DON'T:**
- Skip state capture for "simple" runbooks
- Forget to compute state_diff
- Omit error tracebacks
- Leave verification_method as null

### 2. Verification Logic

**DO:**
- Implement verification for top 10 incident types first
- Return confidence scores (0.0-1.0)
- Check multiple signals (not just one metric)
- Use thresholds that make sense (e.g., 5% disk reduction = success)

**DON'T:**
- Assume success just because no error occurred
- Use overly strict verification (100% = success)
- Skip verification entirely

### 3. Review Process

**DO:**
- Review within 24 hours of generation
- Test improved runbooks in staging first
- Document why approved or rejected
- Look for edge cases LLM might have missed

**DON'T:**
- Approve blindly
- Reject without explanation
- Let high-priority reviews age

### 4. Prompt Engineering

**DO:**
- Include complete failure context in prompts
- Show state before/after/diff
- Ask LLM to explain changes in comments
- Specify exact output format (YAML structure)

**DON'T:**
- Send minimal context to LLM
- Forget to include error messages
- Allow free-form output (parse errors)

---

## Troubleshooting

### Problem: LLM generates invalid YAML

**Solution:**
- Check `_validate_runbook()` logic
- Review LLM prompt - is output format clear?
- Add more examples to prompt
- Lower temperature (more deterministic)

### Problem: Too many false improvements

**Solution:**
- Refine failure categorization prompt
- Only generate for specific failure types
- Increase verification confidence thresholds

### Problem: Review backlog growing

**Solution:**
- Lower priority for non-critical improvements
- Batch review similar runbooks
- Add more reviewers
- Implement automated testing before review

### Problem: Success rates not improving

**Solution:**
- Check if improvements are being approved
- Verify state capture is comprehensive
- Review rejection reasons - common patterns?
- LLM might need better context in prompts

---

## Future Enhancements

### Phase 2: Pattern Extraction (Week 12+)

Learn from successes, not just failures:
- Identify optimal execution paths
- Detect common success patterns
- Optimize step ordering
- Reduce execution time

### Phase 3: Automated Testing (Week 16+)

Auto-test improved runbooks before review:
- Spin up staging environment
- Execute improved runbook against synthetic incidents
- Only queue for review if tests pass

### Phase 4: Cross-Client Learning (Week 20+)

Learn from one client's fixes, apply to others:
- Detect similar environments
- Suggest improvements to other clients
- Build confidence through multi-client validation

### Phase 5: Incident Classification Improvement (Week 24+)

Use execution results to improve incident classification:
- Track which classifications lead to successful fixes
- Retrain classifier based on verified outcomes
- Suggest reclassification for chronic failures

---

## FAQ

### Q: Can LLM-generated runbooks execute immediately?

**A: NO.** Every LLM-generated runbook requires human review and explicit approval before production use. This is a safety-critical requirement.

### Q: What if the LLM generates something dangerous?

**A:** Multiple safety layers:
1. Structural validation (rejects malformed runbooks)
2. Human review (catches dangerous logic)
3. Audit trail (every generation tracked)
4. Rejection with reason (stored for learning)

### Q: How often are runbooks improved?

**A:** Depends on failure rate. High-traffic runbooks might get 2-3 improvements per month. Low-traffic ones might go months without improvement.

### Q: Can humans manually create improved runbooks?

**A:** Yes! The review system works for both LLM-generated and human-written runbooks. Just add to review queue with appropriate metadata.

### Q: What's the cost per improvement?

**A:** ~$0.05 per generation (GPT-4o tokens). Categorization is ~$0.001 (GPT-4o-mini). Budget ~$20/month for 100+ client platform.

### Q: How do I know if learning is working?

**A:** Track metrics:
- Success rate trending up per runbook
- MTTR trending down
- Fewer manual escalations
- Approval rate >80%

---

## Support

**Issues:** Open GitHub issue with:
- Execution result JSON
- Generated runbook YAML
- Error logs

**Questions:** See main project CLAUDE.md file for architecture context.

**Contributing:** PRs welcome for:
- New verification methods
- Better prompts
- Dashboard improvements
- Metric calculations
