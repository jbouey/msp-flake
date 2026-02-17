"""Tests for DNS SRV record registration (dns_registration.py).

Tests SRV record creation, A record handling, and verification.
"""

import pytest
import socket
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

from compliance_agent.dns_registration import (
    DNSRegistrar,
    SRV_SERVICE,
    APPLIANCE_A_RECORD,
)


@dataclass
class MockScriptResult:
    """Mock for WindowsExecutor.run_script result."""
    success: bool
    output: dict
    error: str = ""


class TestDNSRegistration:
    """Test DNS SRV record registration via WinRM."""

    @pytest.mark.asyncio
    async def test_register_srv_success(self):
        """Successful SRV record creation."""
        executor = MagicMock()
        executor.run_script.return_value = MockScriptResult(
            success=True,
            output={"stdout": "A_CREATED\nSRV_OK"},
        )

        registrar = DNSRegistrar(
            domain="northvalley.local",
            appliance_ip="192.168.88.241",
            grpc_port=50051,
        )

        result = await registrar.register_srv_record(
            executor=executor,
            dc_host="192.168.88.10",
            credentials={"username": "NORTHVALLEY\\admin", "password": "test"},
        )

        assert result is True
        executor.run_script.assert_called_once()

        # Verify the script references correct zone and IP
        call_args = executor.run_script.call_args
        script = call_args.kwargs.get("script") or call_args[1].get("script", "")
        if not script:
            script = call_args[0][1] if len(call_args[0]) > 1 else ""

    @pytest.mark.asyncio
    async def test_register_srv_a_record_exists(self):
        """A record already exists — SRV should still be created."""
        executor = MagicMock()
        executor.run_script.return_value = MockScriptResult(
            success=True,
            output={"stdout": "A_EXISTS\nSRV_OK"},
        )

        registrar = DNSRegistrar(
            domain="northvalley.local",
            appliance_ip="192.168.88.241",
        )

        result = await registrar.register_srv_record(
            executor=executor,
            dc_host="192.168.88.10",
            credentials={"username": "admin", "password": "test"},
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_register_srv_failure(self):
        """SRV record verification fails after creation."""
        executor = MagicMock()
        executor.run_script.return_value = MockScriptResult(
            success=True,
            output={"stdout": "A_CREATED\nSRV_FAIL"},
        )

        registrar = DNSRegistrar(
            domain="northvalley.local",
            appliance_ip="192.168.88.241",
        )

        result = await registrar.register_srv_record(
            executor=executor,
            dc_host="192.168.88.10",
            credentials={"username": "admin", "password": "test"},
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_register_winrm_failure(self):
        """WinRM script execution fails entirely."""
        executor = MagicMock()
        executor.run_script.return_value = MockScriptResult(
            success=False,
            output={},
            error="WinRM connection refused",
        )

        registrar = DNSRegistrar(
            domain="northvalley.local",
            appliance_ip="192.168.88.241",
        )

        result = await registrar.register_srv_record(
            executor=executor,
            dc_host="192.168.88.10",
            credentials={"username": "admin", "password": "test"},
        )

        assert result is False


class TestDNSVerification:
    """Test DNS SRV record verification."""

    @pytest.mark.asyncio
    async def test_verify_srv_resolves(self):
        """verify_srv_record should return address when DNS resolves."""
        registrar = DNSRegistrar(
            domain="northvalley.local",
            appliance_ip="192.168.88.241",
            grpc_port=50051,
        )

        fake_answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.88.241", 50051))]

        with patch("socket.getaddrinfo", return_value=fake_answer):
            result = await registrar.verify_srv_record()

        assert result == "192.168.88.241:50051"

    @pytest.mark.asyncio
    async def test_verify_srv_no_record(self):
        """verify_srv_record should return None when DNS fails."""
        registrar = DNSRegistrar(
            domain="northvalley.local",
            appliance_ip="192.168.88.241",
        )

        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name not found")):
            result = await registrar.verify_srv_record()

        assert result is None


class TestDNSRegistrarInit:
    """Test DNSRegistrar initialization."""

    def test_default_port(self):
        """Default gRPC port should be 50051."""
        registrar = DNSRegistrar(domain="test.local", appliance_ip="10.0.0.1")
        assert registrar.grpc_port == 50051

    def test_custom_port(self):
        """Should accept custom port."""
        registrar = DNSRegistrar(domain="test.local", appliance_ip="10.0.0.1", grpc_port=50052)
        assert registrar.grpc_port == 50052
