# Compliance Agent Testing Guide

## Overview

The compliance agent test suite validates the core functionality for HIPAA compliance monitoring and self-healing across NixOS and Windows Server targets.

## Test Structure

```
tests/
├── test_agent.py          # Core agent lifecycle tests (15 tests)
├── test_healing.py        # Self-healing engine tests (22 tests)
├── test_drift.py          # Drift detection tests (25 tests, 2 skipped)
├── test_queue.py          # Offline evidence queue tests
├── test_crypto.py         # Ed25519 signing tests
├── test_evidence.py       # Evidence bundle tests (needs fixture updates)
├── test_mcp_client.py     # MCP client tests (needs queue import fix)
└── conftest.py            # Shared pytest fixtures
```

## Running Tests

### Full Suite
```bash
source venv/bin/activate
python -m pytest tests/ -v --tb=short
```

### Core Tests Only (Recommended)
```bash
# Agent + Healing + Drift (all passing)
python -m pytest tests/test_agent.py tests/test_healing.py tests/test_drift.py -v
```

### Individual Test Files
```bash
python -m pytest tests/test_agent.py -v --tb=short
python -m pytest tests/test_healing.py -v --tb=short
python -m pytest tests/test_drift.py -v --tb=short
```

## Test Categories

### 1. Agent Tests (`test_agent.py`)

Tests the core compliance agent lifecycle:

| Test | Description | HIPAA Control |
|------|-------------|---------------|
| `test_agent_initialization` | Agent starts with valid config | - |
| `test_agent_initialization_without_mcp` | Agent handles missing MCP | - |
| `test_run_iteration_no_drift` | No action when compliant | 164.308(a)(1)(ii)(D) |
| `test_run_iteration_with_drift` | Detects and heals drift | 164.308(a)(5)(ii)(B) |
| `test_maintenance_window_check` | Respects maintenance windows | - |
| `test_evidence_creation` | Creates evidence bundles | 164.312(b) |
| `test_evidence_signing` | Signs evidence with Ed25519 | 164.312(b) |

**Key Fixtures:**
- `test_config(tmp_path)` - Creates test AgentConfig with temp directories
- `agent(test_config)` - Creates agent instance with mocked dependencies

### 2. Healing Tests (`test_healing.py`)

Tests self-healing remediation actions:

| Test | Description | HIPAA Control |
|------|-------------|---------------|
| `test_update_to_baseline_generation_*` | NixOS generation updates | 164.308(a)(5)(ii)(B) |
| `test_restart_av_service_*` | AV/EDR service healing | 164.308(a)(5)(ii)(B) |
| `test_run_backup_job_*` | Backup remediation | 164.308(a)(7)(ii)(A) |
| `test_restart_logging_services_*` | Logging continuity | 164.312(b) |
| `test_restore_firewall_baseline_*` | Firewall remediation | 164.312(e)(1) |
| `test_enable_volume_encryption_*` | Encryption alerts | 164.312(a)(2)(iv) |

**Key Fixtures:**
- `healing_engine(test_config)` - Creates HealingEngine instance
- `*_drift` fixtures - Create mock DriftResult objects for each check type

### 3. Drift Detection Tests (`test_drift.py`)

Tests the 6 compliance drift checks:

| Check | Tests | HIPAA Control |
|-------|-------|---------------|
| Patching | `test_patching_*` (4 tests) | 164.308(a)(5)(ii)(B) |
| AV/EDR | `test_av_edr_*` (3 tests, 2 skipped) | 164.308(a)(5)(ii)(B) |
| Backup | `test_backup_*` (3 tests) | 164.308(a)(7)(ii)(A) |
| Logging | `test_logging_*` (3 tests) | 164.312(b) |
| Firewall | `test_firewall_*` (3 tests) | 164.312(a)(1) |
| Encryption | `test_encryption_*` (3 tests) | 164.312(a)(2)(iv) |

**Key Fixtures:**
- `mock_config(tmp_path)` - Creates mock config with baseline YAML
- `detector(mock_config)` - Creates DriftDetector instance

## Mocking Patterns

### AsyncMock for Async Functions
```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_example():
    with patch('module.async_function') as mock:
        mock.return_value = AsyncMock(stdout="result")
        # or for side_effect:
        async def side_effect(*args):
            return AsyncMock(stdout="result")
        mock.side_effect = side_effect
```

### Datetime Mocking
```python
from datetime import datetime

# Use recent dates to avoid age-based drift detection
recent_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

with patch('module.datetime') as mock_dt:
    mock_dt.utcnow.return_value = datetime(2025, 11, 7, 14, 30, 0)
```

### Path/File Mocking
```python
with patch('module.Path') as mock_path:
    mock_path.return_value.exists.return_value = True
```

## Known Issues

### Skipped Tests
- `test_av_edr_no_drift` - Complex Path/hashlib mocking causes hang
- `test_av_edr_hash_mismatch` - Same issue
- `test_drift_detection.py` - Deprecated test file (uses old module structure)

### Resolved Issues (2025-11-23)
- ✅ `test_evidence.py` - Fixed `state_dir`/`evidence_dir` config handling
- ✅ `test_mcp_client.py` - Fixed by renaming `queue.py` to `offline_queue.py`
- ✅ `test_windows_integration.py` - Fixed dict hashing error in `list_runbooks()`

## Test Dependencies

```
pytest>=8.0
pytest-asyncio>=1.0
pytest-cov>=7.0
pyyaml
```

## Coverage

Run with coverage:
```bash
python -m pytest tests/test_agent.py tests/test_healing.py tests/test_drift.py \
  --cov=compliance_agent --cov-report=html
```

## Adding New Tests

1. **For new drift checks**: Add to `test_drift.py` with matching `*_drift` fixture
2. **For new healing actions**: Add to `test_healing.py` with mocked `run_command`
3. **For new evidence types**: Add to `test_evidence.py` after fixing config fixture

## Windows Runbook Tests

Windows runbook tests require:
- `pywinrm` library
- Test Windows Server or mock WinRM responses

```bash
pip install pywinrm
python -m pytest tests/test_windows_runbooks.py -v
```
