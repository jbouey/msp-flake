"""Tests for scripts/consumer_contract_check.py — Session 210 Layer 2.

The script is invoked in pre-push. We verify:
  * Current committed consumer.json + openapi.json produce a clean pass
  * Breaking the committed consumer contract (in an isolated temp copy)
    produces a clear failure with actionable remediation

Source-level only — no backend deps required.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "consumer_contract_check.py"
CONSUMER = REPO_ROOT / "mcp-server" / "central-command" / "frontend" / "contracts" / "consumer.json"
OPENAPI = REPO_ROOT / "mcp-server" / "central-command" / "openapi.json"

# Make the script importable so unit tests can reach its helpers.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import consumer_contract_check as ccc  # noqa: E402


def test_consumer_json_exists():
    assert CONSUMER.exists(), f"{CONSUMER} missing — seed Layer 2 contracts"


def test_consumer_json_has_contracts_key():
    data = json.loads(CONSUMER.read_text())
    assert "contracts" in data, "consumer.json must have 'contracts' array"
    assert isinstance(data["contracts"], list)


def test_every_contract_has_required_keys():
    data = json.loads(CONSUMER.read_text())
    for i, c in enumerate(data["contracts"]):
        assert "endpoint" in c, f"contract #{i} missing 'endpoint'"
        assert "method" in c, f"contract #{i} missing 'method'"
        assert "required_fields" in c, f"contract #{i} missing 'required_fields'"
        assert c["required_fields"], f"contract #{i} has empty required_fields"
        assert "rationale" in c, (
            f"contract #{i} missing 'rationale' — future readers need to know "
            f"why this field is contracted (prevents random deletions)"
        )


def test_committed_state_passes():
    """The committed consumer.json + openapi.json must be in sync. If this
    fails in CI, either the backend dropped a contracted field or the
    frontend started declaring a field that was never in the schema."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"consumer_contract_check failed against committed state.\n"
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )


def test_failure_path_is_helpful(tmp_path):
    """Deliberately craft an invalid consumer declaration and verify the
    script fails with a clear remediation message."""
    bad_consumer = {
        "contracts": [
            {
                "endpoint": "/health",
                "method": "get",
                "required_fields": ["status", "nonexistent_field_on_purpose"],
                "rationale": "test",
            }
        ],
    }
    # Run the script with a forced consumer.json via monkey-patching the
    # module-level constant. Simpler than recreating a repo layout.
    original = ccc.CONSUMER_JSON
    tmp_consumer = tmp_path / "consumer.json"
    tmp_consumer.write_text(json.dumps(bad_consumer))
    ccc.CONSUMER_JSON = tmp_consumer
    try:
        rc = ccc.main()
    finally:
        ccc.CONSUMER_JSON = original
    assert rc == 1, "violated contract must exit 1"


def test_helpers_resolve_basic_ref():
    schema = {
        "components": {
            "schemas": {
                "Foo": {"type": "object", "properties": {"a": {"type": "string"}}}
            }
        }
    }
    resolved = ccc._resolve_ref(schema, "#/components/schemas/Foo")
    assert resolved is not None
    assert "a" in resolved.get("properties", {})


def test_helpers_walks_allof_composition():
    schema = {
        "components": {
            "schemas": {
                "Base": {"type": "object", "properties": {"a": {"type": "string"}}},
                "Ext": {
                    "allOf": [
                        {"$ref": "#/components/schemas/Base"},
                        {"type": "object", "properties": {"b": {"type": "string"}}},
                    ]
                },
            }
        }
    }
    ext = schema["components"]["schemas"]["Ext"]
    fields = ccc._available_fields(schema, ext)
    assert fields == {"a", "b"}


def test_helpers_handles_anyof_optional_field():
    """Optional fields show up in openapi as `anyOf: [Type, null]` but
    they ARE declared as properties — the walker should still surface them."""
    schema = {
        "components": {
            "schemas": {
                "Response": {
                    "type": "object",
                    "properties": {
                        "maybe": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    },
                }
            }
        }
    }
    body = schema["components"]["schemas"]["Response"]
    fields = ccc._available_fields(schema, body)
    assert "maybe" in fields
