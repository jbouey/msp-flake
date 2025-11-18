# MCP Server - Self-Learning Runbook System

## Overview

This is the MCP (Model Context Protocol) server with integrated self-learning capabilities. It executes runbooks, learns from failures, and automatically generates improved versions using LLMs.

**Key Features:**
- 🧠 **Self-Learning**: Automatically improves runbooks from execution failures
- 🛡️ **Human-in-Loop**: All LLM-generated runbooks require approval
- 📊 **Rich Telemetry**: Captures complete execution context for learning
- 🔍 **Root Cause Analysis**: LLM categorizes why runbooks fail
- 📈 **Continuous Improvement**: Success rates improve over time

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Learning Pipeline                      │
└─────────────────────────────────────────────────────────┘

Incident → Runbook Execution → ExecutionResult
                                      ↓
                              Learning Engine
                                      ↓
                         ┌───────────┴──────────┐
                         ↓                      ↓
                   Success Pattern        Failure Analysis
                   (Future Phase)               ↓
                                      Improvable Type?
                                            ↓ Yes
                                   Generate Improved Runbook
                                            ↓
                                      Review Queue
                                            ↓
                                   Human Approval
                                            ↓
                                   Production Deployment
```

## Directory Structure

```
mcp-server/
├── schemas/                    # Data models
│   └── execution_result.py    # Rich execution telemetry
│
├── learning/                   # Learning engine
│   └── learning_engine.py     # LLM-based improvement
│
├── review/                     # Human approval workflow
│   └── review_queue.py        # Review queue management
│
├── api/                        # REST API
│   └── review_endpoints.py    # Review API endpoints
│
├── examples/                   # Integration examples
│   └── executor_integration.py # How to integrate with your executor
│
└── templates/                  # UI templates
    └── review_dashboard.html  # Web-based review interface
```

## Quick Start

### 1. Install Dependencies

```bash
pip install pydantic openai pyyaml motor fastapi uvicorn
```

### 2. Configure Environment

```bash
# .env
OPENAI_API_KEY=your_api_key
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=msp_platform
```

### 3. Initialize Learning System

```python
from motor.motor_asyncio import AsyncIOMotorClient
from learning.learning_engine import LearningEngine
from review.review_queue import ReviewQueue

# Database
client = AsyncIOMotorClient(os.getenv("MONGODB_URL"))
db = client[os.getenv("DATABASE_NAME")]

# LLM Client (your implementation)
from your_llm_client import LLMClient
llm_client = LLMClient(api_key=os.getenv("OPENAI_API_KEY"))

# Runbook Repository (your implementation)
from your_runbook_repo import RunbookRepository
runbook_repo = RunbookRepository(db)

# Initialize learning system
review_queue = ReviewQueue(db)
learning_engine = LearningEngine(
    llm_client=llm_client,
    runbook_repo=runbook_repo,
    review_queue=review_queue,
    db=db
)
```

### 4. Integrate with Your Executor

See `examples/executor_integration.py` for complete integration pattern.

**Key integration points:**
1. Capture state before/after execution
2. Build ExecutionResult with rich telemetry
3. Trigger learning engine analysis

```python
from schemas.execution_result import ExecutionResult, ExecutionStatus

async def execute_runbook(runbook, incident, params):
    # 1. Capture state BEFORE
    state_before = await capture_state(params["hostname"])

    # 2. Execute runbook
    success, error = await run_steps(runbook)

    # 3. Capture state AFTER
    state_after = await capture_state(params["hostname"])

    # 4. Build telemetry
    result = ExecutionResult(
        execution_id=generate_id(),
        runbook_id=runbook["id"],
        incident_id=incident["id"],
        incident_type=incident["type"],
        state_before=state_before,
        state_after=state_after,
        success=success,
        error_message=error,
        # ... other fields
    )

    # 5. TRIGGER LEARNING
    await learning_engine.analyze_execution(result)

    return result
```

### 5. Start Review Dashboard

```bash
uvicorn main:app --reload
```

Then open: http://localhost:8000/review

## API Endpoints

### Review Queue

#### `GET /api/review/pending`
Get pending reviews

**Query params:**
- `priority`: high/medium/low
- `limit`: max results (default 50)

```bash
curl http://localhost:8000/api/review/pending?priority=high
```

#### `GET /api/review/runbook/{id}`
Get runbook details for review

```bash
curl http://localhost:8000/api/review/runbook/RB-WIN-SERVICE-001-v2
```

#### `POST /api/review/approve/{id}`
Approve a runbook

```bash
curl -X POST http://localhost:8000/api/review/approve/RB-WIN-SERVICE-001-v2 \
  -H "Content-Type: application/json" \
  -d '{
    "reviewer": "you@example.com",
    "notes": "Tested in staging, looks good"
  }'
```

#### `POST /api/review/reject/{id}`
Reject a runbook

```bash
curl -X POST http://localhost:8000/api/review/reject/RB-WIN-SERVICE-001-v2 \
  -H "Content-Type: application/json" \
  -d '{
    "reviewer": "you@example.com",
    "reason": "Doesn't handle dependency issue"
  }'
```

#### `GET /api/review/stats`
Get queue statistics

```bash
curl http://localhost:8000/api/review/stats
```

## Database Collections

### execution_results
Complete telemetry from runbook executions
- Used for learning and analysis
- Retention: 90 days minimum

### review_queue
Pending/approved/rejected runbooks
- Status: pending_review, in_review, approved, rejected
- Priority: high, medium, low

### runbooks
Runbook definitions with versioning
- Status: active, pending_review, rejected
- Metadata tracks lineage (parent_runbook, generated_from_failure)

### learning_analyses
Learning engine analysis results
- Links execution results to improvements
- Tracks categorization and generation

## Configuration

### LLM Models

**Classification (cheap):**
- Model: GPT-4o-mini
- Temperature: 0.1 (consistent)
- Max tokens: 200
- Cost: ~$0.001 per classification

**Generation (expensive but worth it):**
- Model: GPT-4o
- Temperature: 0.2 (consistent but creative)
- Max tokens: 2500
- Cost: ~$0.05 per generation

### Failure Types

**Improvable** (triggers runbook generation):
- `runbook_insufficient`: Runbook incomplete/buggy
- `environment_difference`: Environment-specific issue

**Not Improvable** (no generation):
- `wrong_diagnosis`: Incident misclassified
- `wrong_runbook`: Wrong solution chosen
- `external_dependency`: External service down
- `permission_denied`: Access issue

## Monitoring

### Key Metrics

```python
# Learning rate
improvements_per_week = await db.runbooks.count_documents({
    "metadata.generated_by": "learning_engine",
    "metadata.generated_at": {"$gte": one_week_ago}
})

# Approval rate
approval_rate = (approved / total_generated) * 100

# Success rate by version
for version in [v1, v2, v3]:
    executions = await db.execution_results.find({
        "runbook_id": version
    })
    success_rate = (successful / total) * 100
```

### Alerts

1. **Review Backlog**: pending_review > 10
2. **Low Approval**: approval_rate < 50%
3. **High-Priority Aging**: high_priority > 24h old
4. **Success Rate Drop**: v2 worse than v1

## Testing

### Test Learning Engine

```python
from schemas.execution_result import ExecutionResult, FailureType

# Create test failure
test_result = ExecutionResult(
    execution_id="test-001",
    runbook_id="RB-TEST-001",
    incident_id="inc-test-001",
    incident_type="service_crash",
    success=False,
    error_message="Service failed to start: dependency missing",
    state_before={"service": "stopped", "dependency": "stopped"},
    state_after={"service": "stopped", "dependency": "stopped"},
    # ... other required fields
)

# Trigger learning
await learning_engine.analyze_execution(test_result)

# Check if improved runbook generated
analysis = await db.learning_analyses.find_one({
    "execution_id": "test-001"
})

assert analysis["failure_analysis"]["improved_runbook_generated"] == True
```

### Test Review Flow

```python
# Add to queue
await review_queue.add(
    runbook_id="RB-TEST-001-v2",
    reason="Test runbook",
    priority=ReviewPriority.MEDIUM
)

# Get pending
pending = await review_queue.get_pending()
assert len(pending) > 0

# Approve
await review_queue.approve(
    runbook_id="RB-TEST-001-v2",
    reviewer="test@example.com",
    notes="Test approval"
)

# Verify activated
runbook = await db.runbooks.find_one({"id": "RB-TEST-001-v2"})
assert runbook["status"] == "active"
```

## Documentation

**Full Documentation:** See `../docs/LEARNING_SYSTEM.md`

**Quick Start:** See `../docs/LEARNING_SYSTEM_QUICKSTART.md`

**Integration Examples:** See `examples/executor_integration.py`

## Development

### Adding New Verification Methods

Edit `examples/executor_integration.py`:

```python
class FixVerifier:
    async def verify(self, incident_type, state_before, state_after):
        if incident_type == "your_new_type":
            return await self._verify_your_type(state_before, state_after)

    async def _verify_your_type(self, before, after):
        # Your verification logic
        passed = some_condition
        confidence = 0.0 to 1.0
        return (passed, "your_verification_method", confidence)
```

### Customizing LLM Prompts

Edit `learning/learning_engine.py`:

```python
def _build_improvement_prompt(self, result, original):
    # Customize the prompt sent to LLM
    # Add domain-specific instructions
    # Include examples
    # Adjust output format
```

### Adding Review Workflows

Edit `review/review_queue.py`:

```python
class ReviewQueue:
    async def your_custom_workflow(self, runbook_id, params):
        # Add custom review steps
        # e.g., automated testing, multi-stage approval, etc.
```

## Troubleshooting

### No Improvements Generated

**Check:**
1. Are failures captured? (`execution_results` collection)
2. Are failures improvable? (check `failure_type`)
3. LLM API key configured?

**Debug:**
```python
# Test categorization
failure_type = await learning_engine._categorize_failure(test_result)
print(f"Categorized as: {failure_type}")

# Should be "runbook_insufficient" or "environment_difference"
```

### Invalid Runbooks Generated

**Check:**
1. Validation errors in logs
2. LLM prompt clarity
3. Temperature setting

**Fix:**
- Add more structure to prompt
- Include example YAML
- Lower temperature (0.1)

### Verification Always Fails

**Check:**
1. State capture working?
2. Thresholds too strict?
3. Correct verification for incident type?

**Debug:**
```python
# Test verification directly
verifier = FixVerifier()
passed, method, conf = await verifier.verify(
    "service_crash",
    {"service": "stopped"},
    {"service": "running"}
)
print(f"Passed: {passed}, Confidence: {conf}")
```

## Contributing

**Add verification for new incident type:**
1. Edit `examples/executor_integration.py`
2. Add `_verify_your_type()` method
3. Add case to `verify()` switch
4. Test with real execution

**Improve LLM prompts:**
1. Edit `learning/learning_engine.py`
2. Modify `_build_improvement_prompt()`
3. Test with historical failures
4. Measure approval rate

**Enhance dashboard:**
1. Edit `templates/review_dashboard.html`
2. Add metrics, filters, visualizations
3. Test with production data

## License

See top-level LICENSE file.

## Support

**Issues:** Open GitHub issue with:
- Execution result JSON
- Generated runbook YAML
- Error logs

**Questions:** See main project documentation in `../CLAUDE.md`

---

**Status:** ✅ Production Ready

**Version:** 1.0.0

**Last Updated:** 2025-11-11
