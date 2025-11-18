# Self-Learning System - Quick Start Guide

## What You Just Built

A system that automatically improves runbooks by learning from failures using LLMs. Every failure becomes an opportunity to get better.

**Key Principle:** NO LLM-generated runbook executes without human approval.

---

## File Structure

```
mcp-server/
├── schemas/
│   └── execution_result.py          ✅ Rich telemetry schema
│
├── learning/
│   └── learning_engine.py           ✅ LLM-based improvement engine
│
├── review/
│   └── review_queue.py              ✅ Human approval workflow
│
├── api/
│   └── review_endpoints.py          ✅ REST API for reviews
│
├── examples/
│   └── executor_integration.py      ✅ Integration template
│
└── templates/
    └── review_dashboard.html        ✅ Review UI

docs/
└── LEARNING_SYSTEM.md               ✅ Complete documentation
```

---

## 3-Minute Integration

### 1. Install Dependencies

```bash
pip install pydantic openai pyyaml
```

### 2. Update Your Executor

In your existing `executor.py`:

```python
from mcp_server.schemas.execution_result import ExecutionResult, ExecutionStatus
from mcp_server.learning.learning_engine import LearningEngine

async def execute_runbook(runbook, incident, params):
    started_at = datetime.utcnow()

    # Capture state BEFORE
    state_before = await capture_state(params["hostname"])

    # Execute runbook
    success, error = await run_runbook_steps(runbook, params)

    # Capture state AFTER
    state_after = await capture_state(params["hostname"])

    # Verify fix worked
    verified = await verify_fix(incident["type"], state_before, state_after)

    # Build telemetry
    result = ExecutionResult(
        execution_id=generate_id(),
        runbook_id=runbook["id"],
        incident_id=incident["id"],
        incident_type=incident["type"],
        client_id=params["client_id"],
        hostname=params["hostname"],
        platform=runbook["platform"],
        started_at=started_at,
        completed_at=datetime.utcnow(),
        duration_seconds=(datetime.utcnow() - started_at).total_seconds(),
        status=ExecutionStatus.SUCCESS if success else ExecutionStatus.FAILURE,
        success=success,
        verification_passed=verified,
        state_before=state_before,
        state_after=state_after,
        error_message=error,
        evidence_bundle_id=generate_id()
    )

    # Store in database
    await db.execution_results.insert_one(result.to_dict())

    # TRIGGER LEARNING (this is the magic)
    await learning_engine.analyze_execution(result)

    return result
```

### 3. Initialize Learning Engine

In your `main.py`:

```python
from mcp_server.learning.learning_engine import LearningEngine
from mcp_server.review.review_queue import ReviewQueue

# Initialize
review_queue = ReviewQueue(db)
learning_engine = LearningEngine(
    llm_client=your_llm_client,
    runbook_repo=your_runbook_repo,
    review_queue=review_queue,
    db=db
)
```

### 4. Start Review Dashboard

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mcp_server.api.review_endpoints import router, init_review_api

app = FastAPI()

# Initialize review API
init_review_api(db)

# Mount review endpoints
app.include_router(router)

# Serve dashboard
app.mount("/static", StaticFiles(directory="mcp-server/templates"), name="static")

@app.get("/review")
async def review_dashboard():
    return FileResponse("mcp-server/templates/review_dashboard.html")
```

---

## First Run Checklist

### Week 1: Foundation
- [x] ExecutionResult schema created
- [x] Learning engine implemented
- [x] Review queue implemented
- [ ] Update your executor to capture telemetry
- [ ] Initialize learning engine in your app

### Week 2: Integration
- [ ] Add state capture functions (services, disk, CPU, memory)
- [ ] Add verification logic for top 5 incident types
- [ ] Test: Trigger a failure, verify ExecutionResult is created
- [ ] Test: Verify learning engine categorizes failure

### Week 3: Review System
- [ ] Deploy review dashboard
- [ ] Configure LLM API keys (GPT-4o for generation, GPT-4o-mini for classification)
- [ ] Test: Trigger improvable failure, verify improved runbook generated
- [ ] Test: Review and approve improved runbook

### Week 4: Production
- [ ] Monitor: Check review queue daily
- [ ] Track: Success rates before/after improvements
- [ ] Iterate: Refine verification logic based on results
- [ ] Document: Team review process

---

## Testing the System

### Test 1: Trigger a Failure

```python
# Create a test incident
incident = {
    "id": "inc-test-001",
    "type": "service_crash"
}

# Execute a runbook that will fail
runbook = {
    "id": "RB-WIN-SERVICE-001",
    "platform": "windows",
    "steps": [
        {"action": "start_service", "params": {"name": "nonexistent"}}
    ]
}

# Execute (will fail)
result = await executor.execute_runbook(runbook, incident, {
    "client_id": "test-client",
    "hostname": "test-host"
})

# Verify telemetry captured
assert result.success == False
assert result.error_message is not None
assert result.state_before is not None
assert result.state_after is not None
```

### Test 2: Verify Learning Triggered

```python
# Check learning engine analyzed it
analysis = await db.learning_analyses.find_one({
    "execution_id": result.execution_id
})

assert analysis is not None
assert "failure_analysis" in analysis

# If improvable, check runbook generated
if analysis["failure_analysis"]["improved_runbook_generated"]:
    improved_id = analysis["failure_analysis"]["improved_runbook_id"]

    # Check it's in review queue
    queue_item = await db.review_queue.find_one({
        "runbook_id": improved_id
    })

    assert queue_item is not None
    assert queue_item["status"] == "pending_review"
```

### Test 3: Review Flow

```bash
# 1. Open review dashboard
open http://localhost:8000/review

# 2. Click pending review
# 3. Compare parent vs improved runbook
# 4. Review failure context
# 5. Approve or reject

# OR via API:
curl -X POST http://localhost:8000/api/review/approve/RB-WIN-SERVICE-001-v2 \
  -H "Content-Type: application/json" \
  -d '{
    "reviewer": "you@example.com",
    "notes": "Looks good, tested in staging"
  }'
```

---

## Monitoring

### Key Metrics Dashboard

```python
# metrics.py
async def get_learning_metrics():
    # Improvements generated this week
    week_ago = datetime.utcnow() - timedelta(days=7)
    improvements_this_week = await db.runbooks.count_documents({
        "metadata.generated_by": "learning_engine",
        "metadata.generated_at": {"$gte": week_ago.isoformat()}
    })

    # Approval rate
    total_generated = await db.runbooks.count_documents({
        "metadata.generated_by": "learning_engine"
    })
    approved = await db.review_queue.count_documents({
        "status": "approved"
    })
    approval_rate = (approved / total_generated * 100) if total_generated > 0 else 0

    # Success rate trend
    # (Compare v1 vs v2 vs v3 success rates)

    return {
        "improvements_this_week": improvements_this_week,
        "approval_rate": round(approval_rate, 1),
        "total_generated": total_generated,
        "approved": approved
    }
```

### Alerts to Set Up

1. **Review Backlog Growing**
   - Alert if pending_review > 10

2. **Low Approval Rate**
   - Alert if approval_rate < 50%

3. **High-Priority Aging**
   - Alert if high-priority review > 24 hours old

4. **Success Rate Declining**
   - Alert if runbook success rate drops after "improvement"

---

## Common Issues

### Issue: "No improved runbooks being generated"

**Check:**
1. Are failures being captured? (Check `execution_results` collection)
2. Are failures classified as improvable? (Check `failure_type` field)
3. Is LLM API key configured correctly?
4. Check learning engine logs for errors

**Solution:**
```python
# Test learning engine directly
from mcp_server.learning.learning_engine import LearningEngine

# Create test failure
test_result = ExecutionResult(
    # ... fill in required fields with failure scenario
    failure_type="runbook_insufficient"  # Improvable type
)

# Trigger learning
await learning_engine.analyze_execution(test_result)
```

### Issue: "LLM generates invalid runbooks"

**Check:**
1. Review `_validate_runbook()` validation errors
2. Check LLM prompt clarity
3. Review LLM response (might be malformed)

**Solution:**
- Add more structure to prompt
- Include example YAML in prompt
- Lower temperature for more deterministic output

### Issue: "Verification always fails"

**Check:**
1. Is state capture working? (Check `state_before` and `state_after`)
2. Are thresholds too strict?
3. Is verification logic correct for incident type?

**Solution:**
```python
# Test verification directly
from mcp_server.examples.executor_integration import FixVerifier

verifier = FixVerifier()

state_before = {"services": {"nginx": "stopped"}}
state_after = {"services": {"nginx": "running"}}

passed, method, confidence = await verifier.verify(
    "service_crash",
    state_before,
    state_after
)

print(f"Passed: {passed}, Confidence: {confidence}")
```

---

## Next Steps

### Week 1-2: Get Basic Learning Working
- [ ] Integrate executor with telemetry capture
- [ ] Verify ExecutionResult is populated correctly
- [ ] Trigger first improved runbook generation
- [ ] Review and approve first improvement

### Week 3-4: Expand Coverage
- [ ] Add verification for 10 most common incident types
- [ ] Refine state capture (add more checks)
- [ ] Set up review dashboard monitoring
- [ ] Train team on review process

### Week 5-8: Measure Impact
- [ ] Track success rates over time
- [ ] Compare v1 vs v2 performance
- [ ] Identify runbooks needing improvement
- [ ] Iterate on prompts and verification

### Week 9-12: Optimize
- [ ] Reduce false improvements (refine categorization)
- [ ] Speed up review process (automated testing)
- [ ] Extract patterns from successes
- [ ] Cross-client learning

---

## Getting Help

**Documentation:** See `docs/LEARNING_SYSTEM.md` for complete details

**Examples:** See `mcp-server/examples/executor_integration.py` for integration patterns

**API Reference:** See `mcp-server/api/review_endpoints.py` for endpoint documentation

**Issues:** Check logs in:
- Learning engine: `learning_analyses` collection
- Review queue: `review_queue` collection
- Executions: `execution_results` collection

---

## Success Metrics

After 6 months, you should see:

✅ **50+ improved runbooks** approved and active
✅ **Success rates** improving (v1 → v2 → v3)
✅ **MTTR** decreasing for common incidents
✅ **Manual escalations** down by 30%+
✅ **Approval rate** >80% (high-quality improvements)

**This is the moat:** A runbook library that gets smarter every week, informed by thousands of real executions across dozens of clients. Traditional MSPs can't compete with this continuous improvement cycle.

---

## Philosophy

> "The system that beat the world champion at Go didn't start perfect. It played millions of games, analyzed what worked, and got better. Your runbook system does the same thing - but learns from real production failures, not simulations. Within 6 months, your runbook library will be better than anything a human could write." - Original spec

The key is:
1. **Capture everything** (rich telemetry)
2. **Learn from failures** (LLM analysis)
3. **Human oversight** (safety gate)
4. **Continuous improvement** (every week, better)

---

**You're ready to deploy.** Start with integration (Week 1-2), then expand coverage (Week 3-4), then measure impact (Week 5-8). The system will improve itself from there.
