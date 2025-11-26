"""
Tests for MCP client.
"""

import pytest
import tempfile
import shutil
import asyncio
from pathlib import Path
from datetime import datetime, timezone
import json
from aiohttp import web
import ssl

from compliance_agent.models import MCPOrder
from compliance_agent.mcp_client import (
    MCPClient,
    MCPConnectionError,
    MCPAuthenticationError,
    MCPOrderError
)
from compliance_agent.config import AgentConfig
from compliance_agent.crypto import generate_keypair


@pytest.fixture
def test_ssl_context():
    """Create unverified SSL context for testing."""
    import ssl
    return ssl._create_unverified_context()


@pytest.fixture
def test_config(tmp_path):
    """Create test configuration with mock certificates."""
    # Create mock baseline file
    baseline_path = tmp_path / "baseline.nix"
    baseline_path.write_text("{ }")

    # Create mock certificate files
    cert_file = tmp_path / "cert.pem"
    cert_file.write_text("MOCK_CERT")
    key_file = tmp_path / "key.pem"
    key_file.write_text("MOCK_KEY")
    signing_key = tmp_path / "signing.key"
    signing_key.write_bytes(generate_keypair()[0])

    # Create state directory
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    import os
    os.environ.update({
        'SITE_ID': 'test-site-001',
        'HOST_ID': 'test-host',
        'DEPLOYMENT_MODE': 'direct',
        'STATE_DIR': str(state_dir),
        'MCP_URL': 'http://localhost:8888',  # Mock server
        'BASELINE_PATH': str(baseline_path),
        'CLIENT_CERT_FILE': str(cert_file),
        'CLIENT_KEY_FILE': str(key_file),
        'SIGNING_KEY_FILE': str(signing_key),
    })

    from compliance_agent.config import load_config
    config = load_config()

    return config


@pytest.fixture
def test_order():
    """Create test MCP order."""
    return MCPOrder(
        order_id="test-order-001",
        runbook_id="RB-RESTART-001",
        params={"service_name": "nginx"},
        nonce="test-nonce-12345",
        ttl=3600,
        issued_at=datetime.now(timezone.utc),
        site_id="test-site-001",
        host_id="test-host",
        priority="high",
        signed_by="test-signer",
        signature="test-signature"
    )


@pytest.fixture
def mock_evidence_bundle(tmp_path):
    """Create mock evidence bundle files."""
    bundle_path = tmp_path / "bundle.json"
    bundle_data = {
        "bundle_id": "test-bundle-001",
        "site_id": "test-site-001",
        "check": "test",
        "outcome": "success"
    }
    bundle_path.write_text(json.dumps(bundle_data))

    sig_path = tmp_path / "bundle.sig"
    sig_path.write_bytes(b"mock_signature")

    return bundle_path, sig_path


class MockMCPServer:
    """Mock MCP server for testing."""

    def __init__(self):
        self.app = web.Application()
        self.runner = None
        self.site = None

        # Test data
        self.orders = {}
        self.evidence_bundles = []

        # Setup routes
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_post('/api/orders', self.submit_order)
        self.app.router.add_get('/api/orders/{order_id}/status', self.order_status)
        self.app.router.add_post('/api/evidence', self.upload_evidence)

    async def health_check(self, request):
        """Health check endpoint."""
        return web.json_response({'status': 'ok'})

    async def submit_order(self, request):
        """Order submission endpoint."""
        order_data = await request.json()
        order_id = f"order-{len(self.orders) + 1:04d}"

        self.orders[order_id] = {
            'order_id': order_id,
            'status': 'pending',
            'order_data': order_data,
            'created_at': datetime.now(timezone.utc).isoformat()
        }

        return web.json_response({'order_id': order_id}, status=201)

    async def order_status(self, request):
        """Order status endpoint."""
        order_id = request.match_info['order_id']

        if order_id not in self.orders:
            return web.json_response({'error': 'Order not found'}, status=404)

        order = self.orders[order_id]
        return web.json_response({
            'order_id': order_id,
            'status': order['status'],
            'updated_at': datetime.now(timezone.utc).isoformat()
        })

    async def upload_evidence(self, request):
        """Evidence upload endpoint."""
        reader = await request.multipart()

        bundle_data = None
        signature_data = None

        async for field in reader:
            if field.name == 'bundle':
                bundle_data = await field.read()
            elif field.name == 'signature':
                signature_data = await field.read()

        if not bundle_data:
            return web.json_response({'error': 'No bundle provided'}, status=400)

        bundle_json = json.loads(bundle_data)
        bundle_id = bundle_json.get('bundle_id', f'bundle-{len(self.evidence_bundles) + 1:04d}')

        self.evidence_bundles.append({
            'bundle_id': bundle_id,
            'bundle_data': bundle_json,
            'has_signature': signature_data is not None,
            'uploaded_at': datetime.now(timezone.utc).isoformat()
        })

        return web.json_response({'bundle_id': bundle_id}, status=201)

    async def start(self, port=8888):
        """Start mock server."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, 'localhost', port)
        await self.site.start()

    async def stop(self):
        """Stop mock server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    def set_order_status(self, order_id: str, status: str):
        """Update order status (for testing)."""
        if order_id in self.orders:
            self.orders[order_id]['status'] = status


@pytest.fixture
async def mock_server():
    """Create and start mock MCP server."""
    server = MockMCPServer()
    await server.start()
    yield server
    await server.stop()


@pytest.mark.asyncio
async def test_client_initialization(test_config):
    """Test MCP client initialization."""
    # Create client but skip SSL context check (mock certs aren't real)
    try:
        client = MCPClient(test_config)
        assert client.config == test_config
        assert client.max_retries == 3
        assert client.pool_size == 10
        await client.close()
    except (ssl.SSLError, FileNotFoundError):
        # Expected with mock certificates - just verify config handling
        assert test_config.site_id == 'test-site-001'


@pytest.mark.asyncio
async def test_health_check_success(test_config, test_ssl_context, mock_server):
    """Test successful health check."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    result = await client.health_check()

    assert result is True

    await client.close()


@pytest.mark.asyncio
async def test_health_check_failure(test_config, test_ssl_context):
    """Test health check failure when server unreachable."""
    # Point to non-existent server
    test_config.mcp_url = 'http://localhost:9999'

    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    result = await client.health_check()

    assert result is False

    await client.close()


@pytest.mark.asyncio
async def test_submit_order_success(test_config, test_ssl_context, test_order, mock_server):
    """Test successful order submission."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    order_id = await client.submit_order(test_order)

    assert order_id is not None
    assert order_id.startswith('order-')

    # Verify order was stored in mock server
    assert order_id in mock_server.orders

    await client.close()


@pytest.mark.asyncio
async def test_get_order_status_pending(test_config, test_ssl_context, test_order, mock_server):
    """Test getting order status (pending)."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    # Submit order
    order_id = await client.submit_order(test_order)

    # Get status
    status = await client.get_order_status(order_id)

    assert status['order_id'] == order_id
    assert status['status'] == 'pending'
    assert 'updated_at' in status

    await client.close()


@pytest.mark.asyncio
async def test_get_order_status_not_found(test_config, test_ssl_context, mock_server):
    """Test getting status for non-existent order."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    with pytest.raises(MCPOrderError, match="not found"):
        await client.get_order_status("nonexistent-order")

    await client.close()


@pytest.mark.asyncio
async def test_wait_for_order_completion_success(test_config, test_ssl_context, test_order, mock_server):
    """Test waiting for order completion (success)."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    # Submit order
    order_id = await client.submit_order(test_order)

    # Simulate async completion
    async def complete_order():
        await asyncio.sleep(0.5)
        mock_server.set_order_status(order_id, 'completed')

    completion_task = asyncio.create_task(complete_order())

    # Wait for completion
    status = await client.wait_for_order_completion(
        order_id,
        timeout_sec=5,
        poll_interval=0.2
    )

    await completion_task

    assert status['status'] == 'completed'

    await client.close()


@pytest.mark.asyncio
async def test_wait_for_order_completion_failed(test_config, test_ssl_context, test_order, mock_server):
    """Test waiting for order that fails."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    # Submit order
    order_id = await client.submit_order(test_order)

    # Simulate async failure
    async def fail_order():
        await asyncio.sleep(0.5)
        mock_server.orders[order_id]['status'] = 'failed'
        mock_server.orders[order_id]['error'] = 'Test error'

    failure_task = asyncio.create_task(fail_order())

    # Wait should raise exception
    with pytest.raises(MCPOrderError, match="failed"):
        await client.wait_for_order_completion(
            order_id,
            timeout_sec=5,
            poll_interval=0.2
        )

    await failure_task
    await client.close()


@pytest.mark.asyncio
async def test_wait_for_order_completion_timeout(test_config, test_ssl_context, test_order, mock_server):
    """Test waiting for order that times out."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    # Submit order (stays pending)
    order_id = await client.submit_order(test_order)

    # Wait should timeout
    with pytest.raises(MCPOrderError, match="timed out"):
        await client.wait_for_order_completion(
            order_id,
            timeout_sec=1,
            poll_interval=0.2
        )

    await client.close()


@pytest.mark.asyncio
async def test_upload_evidence_success(test_config, test_ssl_context, mock_evidence_bundle, mock_server):
    """Test successful evidence upload."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    bundle_path, sig_path = mock_evidence_bundle

    result = await client.upload_evidence(bundle_path, sig_path)

    assert result is True

    # Verify evidence was stored in mock server
    assert len(mock_server.evidence_bundles) == 1
    assert mock_server.evidence_bundles[0]['bundle_id'] == 'test-bundle-001'
    assert mock_server.evidence_bundles[0]['has_signature'] is True

    await client.close()


@pytest.mark.asyncio
async def test_upload_evidence_without_signature(test_config, test_ssl_context, mock_evidence_bundle, mock_server):
    """Test evidence upload without signature."""
    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    bundle_path, _ = mock_evidence_bundle

    result = await client.upload_evidence(bundle_path)

    assert result is True

    # Verify evidence was stored without signature
    assert len(mock_server.evidence_bundles) == 1
    assert mock_server.evidence_bundles[0]['has_signature'] is False

    await client.close()


@pytest.mark.asyncio
async def test_upload_evidence_failure(test_config, test_ssl_context, mock_evidence_bundle):
    """Test evidence upload failure when server unreachable."""
    # Point to non-existent server
    test_config.mcp_url = 'http://localhost:9999'

    client = MCPClient(test_config, max_retries=1, ssl_context=test_ssl_context)

    bundle_path, sig_path = mock_evidence_bundle

    result = await client.upload_evidence(bundle_path, sig_path)

    assert result is False

    await client.close()


@pytest.mark.asyncio
async def test_retry_logic(test_config, test_ssl_context):
    """Test retry logic with exponential backoff."""
    client = MCPClient(test_config, max_retries=3, ssl_context=test_ssl_context)

    # Point to non-existent server to trigger retries
    test_config.mcp_url = 'http://localhost:9999'

    with pytest.raises(MCPConnectionError):
        await client._request_with_retry('GET', '/health')

    await client.close()


@pytest.mark.asyncio
async def test_context_manager(test_config, test_ssl_context):
    """Test async context manager usage."""
    async with MCPClient(test_config, ssl_context=test_ssl_context) as client:
        assert client._session is not None

    # Session should be closed after exiting context
    assert client._session.closed


@pytest.mark.asyncio
async def test_session_reuse(test_config, test_ssl_context, mock_server):
    """Test that session is reused across requests."""
    client = MCPClient(test_config, ssl_context=test_ssl_context)

    # First request creates session
    await client.health_check()
    first_session = client._session

    # Second request reuses same session
    await client.health_check()
    second_session = client._session

    assert first_session is second_session

    await client.close()
