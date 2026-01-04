"""Tests for the provisioning module."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from compliance_agent.provisioning import (
    needs_provisioning,
    get_mac_address,
    get_hostname,
    generate_api_key,
    claim_provision_code,
    create_config,
    run_provisioning_auto,
    CONFIG_PATH,
)


class TestNeedsProvisioning:
    """Tests for needs_provisioning function."""

    def test_needs_provisioning_no_config(self, tmp_path, monkeypatch):
        """Returns True when config file doesn't exist."""
        fake_path = tmp_path / "nonexistent" / "config.yaml"
        monkeypatch.setattr(
            "compliance_agent.provisioning.CONFIG_PATH",
            fake_path
        )
        assert needs_provisioning() is True

    def test_needs_provisioning_has_config(self, tmp_path, monkeypatch):
        """Returns False when config file exists."""
        fake_path = tmp_path / "config.yaml"
        fake_path.write_text("site_id: test")
        monkeypatch.setattr(
            "compliance_agent.provisioning.CONFIG_PATH",
            fake_path
        )
        assert needs_provisioning() is False


class TestGetMacAddress:
    """Tests for get_mac_address function."""

    def test_mac_address_format(self):
        """MAC address should be properly formatted."""
        mac = get_mac_address()
        # Should be uppercase with colons
        assert ":" in mac or mac.startswith("02:")
        # Should be valid hex characters
        cleaned = mac.replace(":", "")
        assert all(c in "0123456789ABCDEF" for c in cleaned)

    def test_mac_address_fallback(self, tmp_path, monkeypatch):
        """Uses hostname-based fallback when no interfaces found."""
        # Point to non-existent network path
        monkeypatch.setattr(
            "compliance_agent.provisioning.Path",
            lambda x: tmp_path / "nonexistent" if x == "/sys/class/net" else Path(x)
        )
        mac = get_mac_address()
        # Fallback format starts with "02:"
        assert mac.startswith("02:")


class TestGetHostname:
    """Tests for get_hostname function."""

    def test_hostname_returns_string(self):
        """Hostname should be a non-empty string."""
        hostname = get_hostname()
        assert isinstance(hostname, str)
        assert len(hostname) > 0

    @patch("socket.gethostname")
    def test_hostname_fallback_on_error(self, mock_gethostname):
        """Returns default hostname on error."""
        mock_gethostname.side_effect = Exception("Test error")
        hostname = get_hostname()
        assert hostname == "osiriscare-appliance"


class TestGenerateApiKey:
    """Tests for generate_api_key function."""

    def test_api_key_length(self):
        """API key should have expected length."""
        key = generate_api_key(32)
        # URL-safe base64 encoding: ~4/3 * length
        assert len(key) >= 32

    def test_api_key_uniqueness(self):
        """Each API key should be unique."""
        keys = [generate_api_key() for _ in range(100)]
        assert len(set(keys)) == 100

    def test_api_key_url_safe(self):
        """API key should be URL-safe."""
        key = generate_api_key()
        # Should not contain problematic characters
        assert "+" not in key
        assert "/" not in key


class TestClaimProvisionCode:
    """Tests for claim_provision_code function."""

    def test_invalid_code_format_short(self):
        """Rejects codes that are too short."""
        success, data = claim_provision_code("ABC123")
        assert success is False
        assert "Invalid provision code format" in data["error"]

    def test_invalid_code_format_long(self):
        """Rejects codes that are too long."""
        success, data = claim_provision_code("ABC1234567890123456789")
        assert success is False
        assert "Invalid provision code format" in data["error"]

    def test_invalid_code_format_special_chars(self):
        """Rejects codes with special characters."""
        success, data = claim_provision_code("ABCD!@#$EFGH5678")
        assert success is False
        assert "Invalid provision code format" in data["error"]

    def test_valid_code_format_accepted(self):
        """Accepts properly formatted codes."""
        # Mock the HTTP request
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({
                "status": "claimed",
                "site_id": "test-site",
                "appliance_id": "test-appliance"
            }).encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            success, data = claim_provision_code("ABCD1234EFGH5678")
            assert success is True
            assert data["site_id"] == "test-site"

    def test_code_normalization(self):
        """Codes are normalized (uppercase, no dashes/spaces)."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({
                "status": "claimed",
                "site_id": "test-site",
                "appliance_id": "test-appliance"
            }).encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            # Should work with lowercase and dashes
            success, data = claim_provision_code("abcd-1234-efgh-5678")
            assert success is True

    @patch("urllib.request.urlopen")
    def test_connection_timeout(self, mock_urlopen):
        """Handles connection timeout gracefully."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("timed out")

        success, data = claim_provision_code("ABCD1234EFGH5678")
        assert success is False
        assert "timed out" in data["error"].lower()


class TestCreateConfig:
    """Tests for create_config function."""

    def test_config_file_created(self, tmp_path, monkeypatch):
        """Config file is created at expected path."""
        config_dir = tmp_path / "msp"
        config_path = config_dir / "config.yaml"
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_DIR", config_dir)
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_PATH", config_path)

        result = create_config(
            site_id="test-site",
            appliance_id="test-appliance"
        )

        assert result == config_path
        assert config_path.exists()

    def test_config_contains_required_fields(self, tmp_path, monkeypatch):
        """Config file contains all required fields."""
        config_dir = tmp_path / "msp"
        config_path = config_dir / "config.yaml"
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_DIR", config_dir)
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_PATH", config_path)

        create_config(
            site_id="test-site",
            appliance_id="test-appliance",
            api_endpoint="https://test.api"
        )

        content = config_path.read_text()
        assert "site_id: test-site" in content
        assert "appliance_id: test-appliance" in content
        assert "api_endpoint: https://test.api" in content
        assert "api_key:" in content
        assert "poll_interval:" in content

    def test_config_includes_partner_info(self, tmp_path, monkeypatch):
        """Config file includes partner info when provided."""
        config_dir = tmp_path / "msp"
        config_path = config_dir / "config.yaml"
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_DIR", config_dir)
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_PATH", config_path)

        create_config(
            site_id="test-site",
            appliance_id="test-appliance",
            partner_info={
                "slug": "nepa-it",
                "brand_name": "NEPA IT Solutions",
                "primary_color": "#2563EB"
            }
        )

        content = config_path.read_text()
        assert "partner:" in content
        assert "slug: nepa-it" in content
        assert "brand_name: NEPA IT Solutions" in content
        assert "#2563EB" in content

    def test_config_has_secure_permissions(self, tmp_path, monkeypatch):
        """Config file has 0600 permissions."""
        config_dir = tmp_path / "msp"
        config_path = config_dir / "config.yaml"
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_DIR", config_dir)
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_PATH", config_path)

        create_config(
            site_id="test-site",
            appliance_id="test-appliance"
        )

        # Check permissions (octal 0600 = 384 decimal)
        mode = config_path.stat().st_mode & 0o777
        assert mode == 0o600


class TestRunProvisioningAuto:
    """Tests for run_provisioning_auto function."""

    def test_auto_provision_success(self, tmp_path, monkeypatch, capsys):
        """Auto provisioning succeeds with valid code."""
        config_dir = tmp_path / "msp"
        config_path = config_dir / "config.yaml"
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_DIR", config_dir)
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_PATH", config_path)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({
                "status": "claimed",
                "site_id": "test-site",
                "appliance_id": "test-appliance"
            }).encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = run_provisioning_auto("ABCD1234EFGH5678")

        assert result is True
        assert config_path.exists()
        captured = capsys.readouterr()
        assert "Provisioning complete" in captured.out

    def test_auto_provision_failure(self, tmp_path, monkeypatch, capsys):
        """Auto provisioning fails gracefully with invalid code."""
        config_dir = tmp_path / "msp"
        config_path = config_dir / "config.yaml"
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_DIR", config_dir)
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_PATH", config_path)

        result = run_provisioning_auto("BADCODE")  # Too short

        assert result is False
        assert not config_path.exists()
        captured = capsys.readouterr()
        assert "failed" in captured.out.lower()


class TestProvisioningIntegration:
    """Integration tests for the provisioning flow."""

    def test_full_provisioning_flow(self, tmp_path, monkeypatch):
        """Complete provisioning flow works end-to-end."""
        config_dir = tmp_path / "msp"
        config_path = config_dir / "config.yaml"
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_DIR", config_dir)
        monkeypatch.setattr("compliance_agent.provisioning.CONFIG_PATH", config_path)

        # Initially needs provisioning
        assert needs_provisioning() is True

        # Mock successful API claim
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = json.dumps({
                "status": "claimed",
                "site_id": "partner-clinic-123",
                "appliance_id": "partner-clinic-123-MAC",
                "partner": {
                    "slug": "nepa-it",
                    "brand_name": "NEPA IT"
                }
            }).encode()
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = run_provisioning_auto("TESTCODE12345678")

        # Should succeed
        assert result is True

        # No longer needs provisioning
        assert needs_provisioning() is False

        # Config contains expected values
        content = config_path.read_text()
        assert "partner-clinic-123" in content
        assert "nepa-it" in content
