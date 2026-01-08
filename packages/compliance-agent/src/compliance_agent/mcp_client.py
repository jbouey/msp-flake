"""
MCP client for communicating with central MCP server.

Provides HTTP client with mTLS for:
- Health checks
- Order submission
- Order status polling
- Evidence bundle upload
"""

import aiohttp
import asyncio
import ssl
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

from .models import MCPOrder
from .config import AgentConfig

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    """Base exception for MCP client errors."""
    pass


class MCPConnectionError(MCPClientError):
    """Connection to MCP server failed."""
    pass


class MCPAuthenticationError(MCPClientError):
    """Authentication with MCP server failed."""
    pass


class MCPOrderError(MCPClientError):
    """Order submission or processing failed."""
    pass


class MCPClient:
    """
    HTTP client for MCP server communication.

    Features:
    - mTLS (mutual TLS) authentication
    - Connection pooling
    - Automatic retries with exponential backoff
    - Health checking
    - Order submission and status polling
    - Evidence bundle upload
    """

    def __init__(
        self,
        config: AgentConfig,
        max_retries: int = 3,
        timeout: int = 30,
        pool_size: int = 10,
        ssl_context: Optional[ssl.SSLContext] = None
    ):
        """
        Initialize MCP client.

        Args:
            config: Agent configuration
            max_retries: Maximum retry attempts (default: 3)
            timeout: Request timeout in seconds (default: 30)
            pool_size: Connection pool size (default: 10)
            ssl_context: Custom SSL context (default: create from config)
        """
        self.config = config
        self.max_retries = max_retries
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.pool_size = pool_size

        # SSL context for mTLS
        if ssl_context is not None:
            self.ssl_context = ssl_context
        else:
            self.ssl_context = self._create_ssl_context()

        # Session will be created when needed
        self._session: Optional[aiohttp.ClientSession] = None

    def _create_ssl_context(self) -> ssl.SSLContext:
        """
        Create SSL context for mTLS.

        Returns:
            SSL context configured with client certificates
        """
        ssl_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)

        # Load client certificate and key
        ssl_context.load_cert_chain(
            certfile=str(self.config.client_cert_file),
            keyfile=str(self.config.client_key_file)
        )

        # Require server certificate validation
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED

        logger.debug("Created SSL context for mTLS")

        return ssl_context

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Get or create aiohttp session.

        Returns:
            Active client session
        """
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                ssl=self.ssl_context,
                limit=self.pool_size,
                limit_per_host=self.pool_size
            )

            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout,
                headers={
                    'User-Agent': f'compliance-agent/{self.config.site_id}',
                    'X-Site-ID': self.config.site_id,
                    'X-Host-ID': self.config.host_id,
                    'X-Deployment-Mode': self.config.deployment_mode
                }
            )

            if self.config.is_reseller_mode:
                self._session.headers['X-Reseller-ID'] = self.config.reseller_id

            logger.debug("Created new aiohttp session")

        return self._session

    async def close(self):
        """Close the client session."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("Closed aiohttp session")

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            **kwargs: Additional arguments for aiohttp request

        Returns:
            Tuple of (status_code, response_json)

        Raises:
            MCPConnectionError: If all retries fail
            MCPAuthenticationError: If authentication fails
        """
        url = f"{self.config.mcp_url}{endpoint}"
        session = await self._get_session()

        last_error = None

        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Attempting {method} {url} (attempt {attempt + 1}/{self.max_retries})")

                async with session.request(method, url, **kwargs) as response:
                    # Check for authentication errors
                    if response.status == 401 or response.status == 403:
                        raise MCPAuthenticationError(
                            f"Authentication failed: {response.status}"
                        )

                    # Try to parse JSON response
                    try:
                        response_data = await response.json()
                    except Exception:
                        response_data = {"error": await response.text()}

                    logger.debug(
                        f"{method} {url} returned {response.status}"
                    )

                    return response.status, response_data

            except aiohttp.ClientError as e:
                last_error = e
                logger.warning(
                    f"Request failed (attempt {attempt + 1}/{self.max_retries}): {e}"
                )

                # Exponential backoff
                if attempt < self.max_retries - 1:
                    backoff = 2 ** attempt
                    logger.debug(f"Backing off {backoff} seconds")
                    await asyncio.sleep(backoff)

            except MCPAuthenticationError:
                # Don't retry authentication errors
                raise

        # All retries failed
        raise MCPConnectionError(
            f"Failed to connect to MCP server after {self.max_retries} attempts: {last_error}"
        )

    async def health_check(self) -> bool:
        """
        Check if MCP server is reachable and healthy.

        Returns:
            True if server is healthy, False otherwise
        """
        try:
            status, response = await self._request_with_retry(
                'GET',
                '/health'
            )

            if status == 200:
                logger.info("MCP server health check: OK")
                return True
            else:
                logger.warning(f"MCP server health check failed: {status}")
                return False

        except (MCPConnectionError, MCPAuthenticationError) as e:
            logger.error(f"MCP server health check failed: {e}")
            return False

    async def submit_order(self, order: MCPOrder) -> str:
        """
        Submit remediation order to MCP server.

        Args:
            order: MCP order to submit

        Returns:
            Order ID assigned by server

        Raises:
            MCPOrderError: If order submission fails
        """
        try:
            # Serialize order to JSON
            order_json = order.model_dump(mode='json')

            status, response = await self._request_with_retry(
                'POST',
                '/api/orders',
                json=order_json
            )

            if status == 200 or status == 201:
                order_id = response.get('order_id')
                if not order_id:
                    raise MCPOrderError("Server did not return order_id")

                logger.info(f"Submitted order {order_id}")
                return order_id

            else:
                error_msg = response.get('error', 'Unknown error')
                raise MCPOrderError(
                    f"Order submission failed: {status} - {error_msg}"
                )

        except (MCPConnectionError, MCPAuthenticationError) as e:
            raise MCPOrderError(f"Failed to submit order: {e}")

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """
        Poll order execution status.

        Args:
            order_id: Order ID to check

        Returns:
            Order status dictionary with keys:
            - status: pending, executing, completed, failed
            - result: execution result (if completed)
            - error: error message (if failed)
            - updated_at: last status update timestamp

        Raises:
            MCPOrderError: If status check fails
        """
        try:
            status, response = await self._request_with_retry(
                'GET',
                f'/api/orders/{order_id}/status'
            )

            if status == 200:
                logger.debug(f"Order {order_id} status: {response.get('status')}")
                return response

            elif status == 404:
                raise MCPOrderError(f"Order {order_id} not found")

            else:
                error_msg = response.get('error', 'Unknown error')
                raise MCPOrderError(
                    f"Status check failed: {status} - {error_msg}"
                )

        except (MCPConnectionError, MCPAuthenticationError) as e:
            raise MCPOrderError(f"Failed to check order status: {e}")

    async def wait_for_order_completion(
        self,
        order_id: str,
        timeout_sec: int = 300,
        poll_interval: int = 5
    ) -> Dict[str, Any]:
        """
        Wait for order to complete (blocking).

        Args:
            order_id: Order ID to wait for
            timeout_sec: Maximum time to wait (default: 300)
            poll_interval: Seconds between polls (default: 5)

        Returns:
            Final order status

        Raises:
            MCPOrderError: If order fails or timeout occurs
        """
        start_time = datetime.now(timezone.utc)
        timeout_dt = start_time + timedelta(seconds=timeout_sec)

        while datetime.now(timezone.utc) < timeout_dt:
            status = await self.get_order_status(order_id)

            order_status = status.get('status')

            if order_status == 'completed':
                logger.info(f"Order {order_id} completed successfully")
                return status

            elif order_status == 'failed':
                error_msg = status.get('error', 'Unknown error')
                raise MCPOrderError(f"Order {order_id} failed: {error_msg}")

            # Still pending or executing
            logger.debug(f"Order {order_id} status: {order_status}, waiting...")
            await asyncio.sleep(poll_interval)

        # Timeout
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        raise MCPOrderError(
            f"Order {order_id} timed out after {elapsed:.1f} seconds"
        )

    async def upload_evidence(
        self,
        bundle_path: Path,
        signature_path: Optional[Path] = None
    ) -> bool:
        """
        Upload evidence bundle to MCP server.

        Args:
            bundle_path: Path to bundle.json
            signature_path: Path to bundle.sig (optional)

        Returns:
            True if upload successful, False otherwise

        Raises:
            MCPOrderError: If upload fails
        """
        try:
            # Read files into memory (FormData needs to read them)
            with open(bundle_path, 'rb') as f:
                bundle_data = f.read()

            signature_data = None
            if signature_path and signature_path.exists():
                with open(signature_path, 'rb') as f:
                    signature_data = f.read()

            # Prepare multipart form data
            data = aiohttp.FormData()

            # Add bundle JSON
            data.add_field(
                'bundle',
                bundle_data,
                filename=bundle_path.name,
                content_type='application/json'
            )

            # Add signature if provided
            if signature_data is not None:
                data.add_field(
                    'signature',
                    signature_data,
                    filename=signature_path.name,
                    content_type='application/octet-stream'
                )

            status, response = await self._request_with_retry(
                'POST',
                '/api/evidence',
                data=data
            )

            if status == 200 or status == 201:
                bundle_id = response.get('bundle_id')
                logger.info(f"Uploaded evidence bundle {bundle_id}")
                return True

            else:
                error_msg = response.get('error', 'Unknown error')
                logger.error(
                    f"Evidence upload failed: {status} - {error_msg}"
                )
                return False

        except (MCPConnectionError, MCPAuthenticationError) as e:
            logger.error(f"Failed to upload evidence: {e}")
            return False

    async def poll_commands(self) -> list:
        """
        Poll for pending commands from the server.

        Returns:
            List of command dictionaries, each containing:
            - id: Command ID
            - type: Command type (run_check, remediate, phone_home, etc.)
            - params: Command parameters
            - created_at: When command was created

        Raises:
            MCPConnectionError: If connection fails
        """
        try:
            status, response = await self._request_with_retry(
                'GET',
                '/api/commands/pending'
            )

            if status == 200:
                commands = response.get('commands', [])
                if commands:
                    logger.debug(f"Received {len(commands)} pending command(s)")
                return commands

            elif status == 204:
                # No pending commands
                return []

            else:
                error_msg = response.get('error', 'Unknown error')
                logger.warning(f"Command poll failed: {status} - {error_msg}")
                return []

        except (MCPConnectionError, MCPAuthenticationError) as e:
            logger.error(f"Failed to poll commands: {e}")
            raise

    async def acknowledge_command(
        self,
        command_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> bool:
        """
        Acknowledge command execution to server.

        Args:
            command_id: ID of the command being acknowledged
            status: Execution status ('completed', 'failed', 'rejected')
            result: Execution result (if successful)
            error: Error message (if failed)

        Returns:
            True if acknowledgment was successful
        """
        try:
            payload = {
                "command_id": command_id,
                "status": status,
                "acknowledged_at": datetime.now(timezone.utc).isoformat()
            }

            if result is not None:
                payload["result"] = result

            if error is not None:
                payload["error"] = error

            resp_status, response = await self._request_with_retry(
                'POST',
                f'/api/commands/{command_id}/acknowledge',
                json=payload
            )

            if resp_status == 200:
                logger.debug(f"Command {command_id} acknowledged")
                return True
            else:
                error_msg = response.get('error', 'Unknown error')
                logger.warning(f"Command ack failed: {resp_status} - {error_msg}")
                return False

        except (MCPConnectionError, MCPAuthenticationError) as e:
            logger.error(f"Failed to acknowledge command {command_id}: {e}")
            return False

    async def __aenter__(self):
        """Async context manager entry."""
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
