# Testing Patterns

## Overview
- **1037+ tests**: 994 agent tests (39 files) + 55 backend tests (3 files)
- **595+ fixtures** for isolation
- **pytest-asyncio** for async testing

### Backend Tests (mcp-server/central-command/backend/tests/)
- `test_auth.py` — 25 tests: password complexity, bcrypt/SHA-256 hashing, session tokens
- `test_evidence_chain.py` — 30 tests: Ed25519 signatures, OTS file construction/parsing, timestamp replay
- `test_production_security.py` — 46 tests: CSRF tokens (requires starlette installed)
- **Note:** Backend tests stub FastAPI/SQLAlchemy at module level to test pure functions without full server deps
- Run: `python3.13 -m pytest tests/ -v --tb=short` (from backend dir)

## Async Test Pattern

```python
@pytest.mark.asyncio
async def test_drift_detection(detector):
    """All async tests use this decorator."""
    result = await detector.check_patching()
    assert result.drifted is False
```

## Fixture Patterns

### Configuration Fixture
```python
@pytest.fixture
def test_config(tmp_path):
    """Create isolated test configuration."""
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text("baseline_generation: 1000\n")

    signing_key = tmp_path / "signing.key"
    private_key_bytes, _ = generate_keypair()
    signing_key.write_bytes(private_key_bytes)

    return AgentConfig(
        site_id="test-site",
        baseline_path=str(baseline),
        signing_key_path=str(signing_key),
    )
```

### Database Fixture
```python
@pytest.fixture
def mock_incident_db():
    """Mock database for unit tests."""
    db = Mock()
    db.get_promotion_candidates.return_value = []
    db.get_stats_summary.return_value = {
        "total_incidents": 0,
        "l1_percentage": 0,
        "success_rate": 0,
    }
    return db
```

## Mock Patterns

### AsyncMock for Commands
```python
with patch('compliance_agent.drift.run_command') as mock_run:
    mock_run.side_effect = [
        AsyncMock(stdout="/nix/store/hash-nixos-system"),
        AsyncMock(stdout="123   2025-01-15 10:23:45"),
    ]
    result = await detector.check_patching()
```

### MagicMock with Spec
```python
config = MagicMock(spec=AgentConfig)
config.baseline_path = str(baseline_path)
config.site_id = "test-site"
# Prevents accessing non-existent attributes
```

### Patching File System
```python
with patch("compliance_agent.health_gate.Path") as mock_path:
    mock_path.return_value.read_text.return_value = "ab.partition=A"
    result = get_active_partition_from_cmdline()
    assert result == "A"
```

## Test Organization

### Class-Based Grouping
```python
class TestExtractActionParams:
    """Tests for _extract_action_params method."""

    def test_extract_params_empty(self, learning_system):
        params = learning_system._extract_action_params([], "restart")
        assert params == {}

class TestPromotionCriteria:
    """Tests for promotion decision logic."""
    pass
```

## Integration Tests

### Real VM Testing (Optional)
```python
class VMConfig:
    def __init__(self):
        self.use_real_vms = os.environ.get('USE_REAL_VMS', '').lower() == 'true'

# Run with: USE_REAL_VMS=1 pytest tests/test_integration.py
```

### Disable Expensive Operations
```python
config = AutoHealerConfig(
    db_path=temp_db,
    enable_level1=True,
    enable_level2=False,  # Disable LLM calls
    dry_run=True          # No real actions
)
```

## Running Tests

```bash
# All tests
python -m pytest tests/ -v --tb=short

# Single file
python -m pytest tests/test_agent.py -v

# With coverage
pytest --cov=compliance_agent tests/

# Integration only
USE_REAL_VMS=1 pytest tests/test_auto_healer_integration.py -v
```

## Test Categories

| Type | Files | Strategy |
|------|-------|----------|
| Unit | test_utils.py, test_crypto.py | Synchronous, fully mocked |
| Service | test_drift.py, test_healing.py | AsyncMock for I/O |
| Integration | test_auto_healer_integration.py | Full pipeline, optional VMs |
| API | test_web_ui.py, test_partner_api.py | Mock HTTP |

## Key Test Files
- `tests/conftest.py` - Shared fixtures
- `tests/test_auto_healer_integration.py` - Full healing pipeline
- `tests/test_drift.py` - 6 compliance checks
- `tests/test_learning_loop.py` - Pattern promotion
- `tests/test_health_gate.py` - A/B partition rollback
- `tests/test_agent_ca.py` - CA generation, cert issuance, server certs (real crypto)
- `tests/test_gpo_deployment.py` - GPO pipeline, rollback, verify
- `tests/test_dns_registration.py` - DNS SRV record registration
- `tests/test_agent_deployment.py` - WinRM deployment pipeline, concurrent
