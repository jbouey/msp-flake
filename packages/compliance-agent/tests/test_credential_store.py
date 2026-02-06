"""
Tests for local encrypted credential storage.

Tests Fernet encryption, key derivation, TTL, and atomic writes.
"""

import json
import pytest
import tempfile
import time
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from compliance_agent.credential_store import CredentialStore, _derive_key, _get_machine_id


class TestKeyDerivation:
    """Test encryption key derivation."""

    def test_derive_key_deterministic(self):
        """Same inputs produce same key."""
        key1 = _derive_key("api-key-123", "machine-id-456")
        key2 = _derive_key("api-key-123", "machine-id-456")
        assert key1 == key2

    def test_derive_key_different_api_key(self):
        """Different API keys produce different keys."""
        key1 = _derive_key("api-key-123", "machine-id-456")
        key2 = _derive_key("api-key-789", "machine-id-456")
        assert key1 != key2

    def test_derive_key_different_machine(self):
        """Different machine IDs produce different keys."""
        key1 = _derive_key("api-key-123", "machine-id-456")
        key2 = _derive_key("api-key-123", "machine-id-789")
        assert key1 != key2

    def test_derive_key_length(self):
        """Key should be valid Fernet key length (44 bytes base64)."""
        key = _derive_key("test", "test")
        assert len(key) == 44  # base64url-encoded 32 bytes


class TestCredentialStore:
    """Test credential store operations."""

    @pytest.fixture
    def store(self, tmp_path):
        """Create a credential store in a temp directory."""
        return CredentialStore(state_dir=tmp_path, api_key="test-api-key-12345")

    @pytest.fixture
    def sample_windows_creds(self):
        """Sample Windows credentials."""
        return [
            {
                "hostname": "dc1.northvalley.local",
                "username": "NORTHVALLEY\\Administrator",
                "password": "TestPassword123!",
                "use_ssl": False,
            },
            {
                "hostname": "ws1.northvalley.local",
                "username": "localadmin",
                "password": "WorkstationPass!",
                "use_ssl": False,
            },
        ]

    def test_store_and_load(self, store, sample_windows_creds):
        """Test basic store and load roundtrip."""
        store.store_credentials("windows", sample_windows_creds)
        loaded = store.load_credentials("windows")

        assert len(loaded) == 2
        assert loaded[0]["hostname"] == "dc1.northvalley.local"
        assert loaded[0]["password"] == "TestPassword123!"
        assert loaded[1]["hostname"] == "ws1.northvalley.local"

    def test_load_empty(self, store):
        """Test loading when no credentials stored."""
        loaded = store.load_credentials("windows")
        assert loaded == []

    def test_has_credentials(self, store, sample_windows_creds):
        """Test has_credentials check."""
        assert store.has_credentials("windows") is False
        store.store_credentials("windows", sample_windows_creds)
        assert store.has_credentials("windows") is True

    def test_clear_credentials(self, store, sample_windows_creds):
        """Test clearing credentials."""
        store.store_credentials("windows", sample_windows_creds)
        assert store.has_credentials("windows") is True

        store.clear_credentials("windows")
        assert store.has_credentials("windows") is False
        assert store.load_credentials("windows") == []

    def test_multiple_types(self, store, sample_windows_creds):
        """Test storing different credential types."""
        linux_creds = [{"hostname": "linux1", "username": "root", "password": "pass"}]

        store.store_credentials("windows", sample_windows_creds)
        store.store_credentials("linux", linux_creds)

        assert len(store.load_credentials("windows")) == 2
        assert len(store.load_credentials("linux")) == 1

        # Clear one type doesn't affect the other
        store.clear_credentials("linux")
        assert store.has_credentials("windows") is True
        assert store.has_credentials("linux") is False

    def test_credentials_age(self, store, sample_windows_creds):
        """Test credentials age tracking."""
        # No credentials = age -1
        assert store.credentials_age_seconds("windows") == -1

        store.store_credentials("windows", sample_windows_creds)
        age = store.credentials_age_seconds("windows")

        # Should be very recent (< 2 seconds)
        assert 0 <= age < 2

    def test_needs_refresh_no_creds(self, store):
        """Test needs_refresh when no credentials exist."""
        assert store.needs_refresh("windows") is True

    def test_needs_refresh_fresh_creds(self, store, sample_windows_creds):
        """Test needs_refresh with fresh credentials."""
        store.store_credentials("windows", sample_windows_creds)
        assert store.needs_refresh("windows", ttl_seconds=3600) is False

    def test_needs_refresh_expired(self, store, sample_windows_creds):
        """Test needs_refresh with expired TTL."""
        store.store_credentials("windows", sample_windows_creds)
        # Set TTL to 0 seconds so credentials are immediately "expired"
        assert store.needs_refresh("windows", ttl_seconds=0) is True

    def test_credentials_hash(self, store, sample_windows_creds):
        """Test credential hash for change detection."""
        assert store.credentials_hash("windows") is None

        store.store_credentials("windows", sample_windows_creds)
        hash1 = store.credentials_hash("windows")
        assert hash1 is not None

        # Same credentials = same hash
        store.store_credentials("windows", sample_windows_creds)
        hash2 = store.credentials_hash("windows")
        assert hash1 == hash2

        # Different credentials = different hash
        store.store_credentials("windows", [{"hostname": "other", "password": "diff"}])
        hash3 = store.credentials_hash("windows")
        assert hash3 != hash1

    def test_encryption_at_rest(self, store, sample_windows_creds, tmp_path):
        """Test that stored file is actually encrypted."""
        store.store_credentials("windows", sample_windows_creds)

        # Read raw file contents
        store_path = tmp_path / "credentials.enc"
        assert store_path.exists()

        raw_bytes = store_path.read_bytes()
        raw_str = raw_bytes.decode('utf-8', errors='replace')

        # Plaintext credentials should NOT appear in the file
        assert "TestPassword123!" not in raw_str
        assert "NORTHVALLEY" not in raw_str

    def test_file_permissions(self, store, sample_windows_creds, tmp_path):
        """Test that credential files have restricted permissions."""
        store.store_credentials("windows", sample_windows_creds)

        store_path = tmp_path / "credentials.enc"
        meta_path = tmp_path / "credentials.meta"

        if store_path.exists():
            mode = oct(store_path.stat().st_mode)[-3:]
            assert mode == "600", f"Expected 600, got {mode}"

    def test_different_api_key_cant_decrypt(self, tmp_path, sample_windows_creds):
        """Test that different API key can't decrypt."""
        store1 = CredentialStore(state_dir=tmp_path, api_key="key-1")
        store1.store_credentials("windows", sample_windows_creds)

        # Different API key should fail to decrypt
        store2 = CredentialStore(state_dir=tmp_path, api_key="key-2")
        loaded = store2.load_credentials("windows")
        # Should return empty (decrypt failure handled gracefully)
        assert loaded == []

    def test_overwrite_existing(self, store, sample_windows_creds):
        """Test overwriting existing credentials."""
        store.store_credentials("windows", sample_windows_creds)
        assert len(store.load_credentials("windows")) == 2

        new_creds = [{"hostname": "new-host", "password": "new-pass"}]
        store.store_credentials("windows", new_creds)
        loaded = store.load_credentials("windows")
        assert len(loaded) == 1
        assert loaded[0]["hostname"] == "new-host"


class TestOutboundScrubGateway:
    """Test the PHI scrub gateway in appliance_client."""

    def test_scrub_outbound_basic(self):
        """Test that outbound scrubbing catches PHI in payloads."""
        from compliance_agent.phi_scrubber import PHIScrubber

        scrubber = PHIScrubber(hash_redacted=True, exclude_categories={'ip_address'})
        data = {
            "site_id": "site-123",
            "hostname": "dc1.local",
            "ip_addresses": ["192.168.88.250"],
            "details": {
                "output": "Error: Patient SSN 123-45-6789 not found in database",
                "server": "192.168.88.250",
            }
        }
        scrubbed, result = scrubber.scrub_dict(data)

        # Infrastructure preserved
        assert scrubbed["ip_addresses"] == ["192.168.88.250"]
        assert scrubbed["details"]["server"] == "192.168.88.250"

        # PHI scrubbed
        assert "123-45-6789" not in scrubbed["details"]["output"]
        assert result.phi_scrubbed is True

    def test_evidence_payload_scrubbing(self):
        """Test scrubbing a realistic evidence payload."""
        from compliance_agent.phi_scrubber import PHIScrubber

        scrubber = PHIScrubber(hash_redacted=True, exclude_categories={'ip_address'})
        evidence_payload = {
            "site_id": "site-abc",
            "checked_at": "2025-02-06T12:00:00Z",
            "checks": [
                {
                    "check": "windows_audit_policy",
                    "status": "fail",
                    "host": "dc1.northvalley.local",
                    "details": {
                        "output": "Audit log shows MRN: 12345678 access by user john.doe@clinic.com",
                        "computer_name": "DC1",
                    },
                    "hipaa_control": "164.312(b)"
                }
            ],
            "summary": {
                "total_checks": 1,
                "non_compliant": 1,
            }
        }

        scrubbed, result = scrubber.scrub_dict(evidence_payload)

        # MRN and email should be scrubbed from output
        output = scrubbed["checks"][0]["details"]["output"]
        assert "12345678" not in output
        assert "john.doe@clinic.com" not in output

        # Structure preserved
        assert scrubbed["checks"][0]["host"] == "dc1.northvalley.local"
        assert scrubbed["checks"][0]["hipaa_control"] == "164.312(b)"
        assert scrubbed["summary"]["total_checks"] == 1
