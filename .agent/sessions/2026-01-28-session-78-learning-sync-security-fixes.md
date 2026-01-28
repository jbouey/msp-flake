# Session 78 - Learning Sync & Security Fixes

**Date:** 2026-01-28
**Duration:** ~2 hours
**Status:** COMPLETE

---

## Session Goals

1. Fix Central Command learning sync (500/422 errors)
2. Audit Linux healing system
3. Audit learning storage system
4. Fix all identified critical/high priority issues

---

## Accomplishments

### 1. Central Command Learning Sync Fix

**Problem:** `/api/agent/sync/pattern-stats` returning 500 errors

**Root Causes:**
- Transaction rollback not happening after SQL exceptions (InFailedSQLTransactionError)
- asyncpg requires datetime objects, received ISO strings (DataError)

**Fixes Applied (VPS `main.py`):**
```python
# Added rollback after exceptions
except Exception as e:
    await db.rollback()
    logger.error(f"Error: {e}")

# Added datetime parsing
def parse_iso_timestamp(iso_string):
    try:
        return datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)
```

**Result:** Learning sync fully operational
- Pattern sync: 26 completed
- Execution report: 152 completed

### 2. SQL Injection Vulnerability Fix

**File:** `packages/compliance-agent/src/compliance_agent/incident_db.py`

**Problem:** f-string column interpolation in UPDATE statement
```python
# BEFORE (vulnerable)
f"UPDATE pattern_stats SET {level_column} = {level_column} + 1..."
```

**Fix:** Parameterized CASE statements
```python
# AFTER (secure)
conn.execute("""
    UPDATE pattern_stats SET
        l1_resolutions = l1_resolutions + CASE WHEN ? = 1 THEN 1 ELSE 0 END,
        l2_resolutions = l2_resolutions + CASE WHEN ? = 2 THEN 1 ELSE 0 END,
        l3_resolutions = l3_resolutions + CASE WHEN ? = 3 THEN 1 ELSE 0 END,
        ...
""", (level_code, level_code, level_code, ...))
```

### 3. UNIQUE Constraint on promoted_rules

**File:** `incident_db.py`

Added UNIQUE constraint to prevent duplicate pattern entries:
```sql
CREATE TABLE IF NOT EXISTS promoted_rules (
    ...
    pattern_signature TEXT NOT NULL UNIQUE,
    ...
)
```

### 4. SSH Exception Handling

**File:** `packages/compliance-agent/src/compliance_agent/runbooks/linux/executor.py`

Added specific asyncssh exception types:
```python
except asyncssh.PermissionDenied as e:
    last_error = f"SSH authentication failed: {e}"
    self.invalidate_connection(target.hostname)
    break  # Don't retry auth failures

except asyncssh.ConnectionLost as e:
    last_error = f"SSH connection lost: {e}"
    self.invalidate_connection(target.hostname)

except asyncssh.Error as e:
    last_error = f"SSH error: {e}"
    self.invalidate_connection(target.hostname)
```

### 5. Post-Promotion Stats Query Fix

**Files:** `level1_deterministic.py`, `learning_loop.py`

**Problem:** Fragile LIKE pattern matching `%{rule_id}%` could match wrong rules

**Fix:** Changed resolution_action format to include rule_id suffix:
```python
# level1_deterministic.py
resolution_action = f"{match.action}:{match.rule.id}"

# learning_loop.py
WHERE resolution_action LIKE ? OR resolution_action = ?
# With params: (f'%:{rule_id}', rule_id)
```

---

## Linux Healing Audit Results

- **20 Linux runbooks total** (15 L1 auto-heal, 5 escalate-only)
- Good SSH-based async execution model
- Connection pooling with cache invalidation
- All runbooks have detect/remediate/verify scripts

---

## Files Modified

| File | Change |
|------|--------|
| `mcp-server/main.py` (VPS) | Learning sync rollback + datetime parsing |
| `incident_db.py` | SQL injection fix + UNIQUE constraint |
| `runbooks/linux/executor.py` | Specific SSH exception handling |
| `level1_deterministic.py` | resolution_action format with rule_id |
| `learning_loop.py` | Post-promotion query fix |

---

## Tests

All 95 tests pass for modified modules.

---

## Next Session Priorities

1. Deploy ISO v49 to physical appliance
2. Evidence bundles uploading to MinIO verification
3. First compliance packet generation
4. 30-day monitoring period

---

## Technical Notes

- asyncpg requires datetime objects, not ISO strings
- SQLAlchemy transaction must be rolled back after exceptions before new queries
- Learning data flywheel now fully operational with proper rule-specific tracking
