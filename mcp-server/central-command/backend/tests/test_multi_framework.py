"""Tests for multi-framework compliance support.

Verifies that get_control_for_check() correctly routes HIPAA lookups
through CHECK_TYPE_HIPAA_MAP and non-HIPAA frameworks through
framework_mapper crosswalk.
"""

import sys
import types
import os

import pytest

# ---------------------------------------------------------------------------
# Path setup — backend dir must be on sys.path for direct imports
# ---------------------------------------------------------------------------

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# ---------------------------------------------------------------------------
# Stub heavy dependencies so compliance_packet can be imported without
# a real database or the full FastAPI app stack.
# ---------------------------------------------------------------------------

# Stub jinja2
if "jinja2" not in sys.modules:
    jinja2_stub = types.ModuleType("jinja2")
    jinja2_stub.Template = object
    sys.modules["jinja2"] = jinja2_stub

# Stub sqlalchemy
for _mod in ["sqlalchemy", "sqlalchemy.ext", "sqlalchemy.ext.asyncio", "sqlalchemy.sql", "sqlalchemy.sql.expression"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if "sqlalchemy" in sys.modules and not hasattr(sys.modules["sqlalchemy"], "text"):
    sys.modules["sqlalchemy"].text = lambda x: x

if "sqlalchemy.ext.asyncio" in sys.modules and not hasattr(sys.modules["sqlalchemy.ext.asyncio"], "AsyncSession"):
    sys.modules["sqlalchemy.ext.asyncio"].AsyncSession = object

# ---------------------------------------------------------------------------
# Stub framework_mapper as a sibling package member so relative import resolves
# ---------------------------------------------------------------------------

# Create a minimal 'dashboard_api' package if it doesn't exist, pointing at backend_dir
if "dashboard_api" not in sys.modules:
    pkg = types.ModuleType("dashboard_api")
    pkg.__path__ = [backend_dir]
    pkg.__package__ = "dashboard_api"
    sys.modules["dashboard_api"] = pkg

# Stub dashboard_api.framework_mapper with a real get_controls_for_check that
# returns SOC2-style data for known HIPAA controls.
_SOC2_CROSSWALK = {
    "164.312(e)(1)": [
        {"framework": "soc2", "control_id": "CC6.6", "control_name": "Transmission Security", "category": "Logical Access", "required": True},
    ],
}

def _fake_get_controls_for_check(check_type, hipaa_control_id, enabled_frameworks):
    results = []
    if "hipaa" in enabled_frameworks and hipaa_control_id:
        results.append({"framework": "hipaa", "control_id": hipaa_control_id, "control_name": ""})
    for fw in enabled_frameworks:
        if fw == "hipaa":
            continue
        for ctrl in _SOC2_CROSSWALK.get(hipaa_control_id, []):
            if ctrl["framework"] == fw:
                results.append(ctrl)
    return results

fw_mapper_stub = types.ModuleType("dashboard_api.framework_mapper")
fw_mapper_stub.get_controls_for_check = _fake_get_controls_for_check
fw_mapper_stub.resolve_control_id = lambda ct, fw: ct
fw_mapper_stub.resolve_control_description = lambda ct, fw: ct.replace("_", " ").title()
sys.modules["dashboard_api.framework_mapper"] = fw_mapper_stub

# Also register as plain 'framework_mapper' for any direct imports
sys.modules["framework_mapper"] = fw_mapper_stub

# Now register compliance_packet under the dashboard_api package namespace so
# relative imports inside it resolve correctly
import compliance_packet as _cp_module  # noqa: E402
_cp_module.__package__ = "dashboard_api"
_cp_module.__name__ = "dashboard_api.compliance_packet"
sys.modules["dashboard_api.compliance_packet"] = _cp_module

from compliance_packet import get_control_for_check  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_get_control_hipaa_default():
    """Default (no framework arg) returns the HIPAA control for firewall_status."""
    result = get_control_for_check("firewall_status")
    assert result["control"] == "164.312(e)(1)"
    assert "Transmission" in result["description"]


def test_get_control_hipaa_explicit():
    """Explicit framework='hipaa' also returns HIPAA control."""
    result = get_control_for_check("firewall_status", "hipaa")
    assert result["control"] == "164.312(e)(1)"


def test_get_control_soc2():
    """SOC2 framework returns crosswalk control for firewall_status."""
    result = get_control_for_check("firewall_status", "soc2")
    assert result["control"] == "CC6.6"
    assert "Transmission" in result["description"]


def test_get_control_unknown_check():
    """Unknown check_type returns N/A sentinel."""
    result = get_control_for_check("nonexistent_check_xyz")
    assert result["control"] == "N/A"
    assert result["description"] == "Unmapped check"


def test_get_control_unknown_framework_falls_back():
    """Unknown framework with no crosswalk entry falls back to HIPAA mapping."""
    result = get_control_for_check("firewall_status", "unknown_framework")
    # No crosswalk entry for unknown_framework → should return HIPAA control
    assert result["control"] == "164.312(e)(1)"
    assert "Transmission" in result["description"]


def test_get_control_audit_logging_hipaa():
    """audit_logging maps to 164.312(b) Audit Controls."""
    result = get_control_for_check("audit_logging")
    assert result["control"] == "164.312(b)"
    assert "Audit" in result["description"]


def test_get_control_bitlocker_hipaa():
    """bitlocker_status maps to 164.312(a)(2)(iv) Encryption."""
    result = get_control_for_check("bitlocker_status")
    assert result["control"] == "164.312(a)(2)(iv)"
    assert "Encryption" in result["description"]
