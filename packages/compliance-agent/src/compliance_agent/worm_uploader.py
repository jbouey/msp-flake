"""
WORM Storage Uploader - Syncs local evidence to immutable cloud storage.

Uploads signed evidence bundles to AWS S3 with Object Lock (WORM mode).
Supports both direct S3 upload and proxy through MCP server.

HIPAA Controls:
- §164.310(d)(2)(iv) - Data Backup and Storage
- §164.312(c)(1) - Integrity Controls

Architecture:
  Local Evidence (Ed25519 signed)
           ↓
  WORM Uploader (this module)
           ↓
  [Direct: S3 with Object Lock]
  [Proxy: MCP Server → S3]
           ↓
  WORM Storage (90+ day retention)
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    """Result of a WORM upload operation."""
    bundle_id: str
    success: bool
    s3_uri: Optional[str] = None
    signature_uri: Optional[str] = None
    upload_timestamp: Optional[str] = None
    retention_days: Optional[int] = None
    error: Optional[str] = None
    retry_count: int = 0


@dataclass
class WormConfig:
    """WORM storage configuration."""
    enabled: bool = False
    mode: str = "proxy"  # "proxy" (via MCP) or "direct" (S3)

    # Proxy mode (via MCP server)
    mcp_upload_endpoint: Optional[str] = None

    # Direct mode (S3)
    s3_bucket: Optional[str] = None
    s3_region: str = "us-east-1"
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None

    # Common settings
    retention_days: int = 90
    max_retries: int = 3
    retry_delay_seconds: int = 5
    upload_batch_size: int = 10
    auto_upload: bool = True  # Upload immediately on evidence creation


class WormUploader:
    """
    Uploads evidence bundles to WORM storage.

    Supports two modes:
    1. Proxy mode: Upload via MCP server (recommended for multi-tenant)
    2. Direct mode: Upload directly to S3 (for single-tenant or offline)

    Usage:
        uploader = WormUploader(config, evidence_dir)
        result = await uploader.upload_bundle(bundle_path, signature_path)
    """

    def __init__(
        self,
        config: WormConfig,
        evidence_dir: Path,
        client_id: str,
        mcp_api_key: Optional[str] = None,
        client_cert: Optional[Path] = None,
        client_key: Optional[Path] = None
    ):
        """
        Initialize WORM uploader.

        Args:
            config: WORM storage configuration
            evidence_dir: Local evidence directory
            client_id: Client/site identifier for S3 key prefix
            mcp_api_key: API key for MCP proxy mode
            client_cert: mTLS client certificate
            client_key: mTLS client key
        """
        self.config = config
        self.evidence_dir = Path(evidence_dir)
        self.client_id = client_id
        self.mcp_api_key = mcp_api_key
        self.client_cert = client_cert
        self.client_key = client_key

        # Track upload state
        self._upload_registry_path = self.evidence_dir / ".upload_registry.json"
        self._upload_registry: Dict[str, Dict] = self._load_registry()

        # S3 client for direct mode (lazy init)
        self._s3_client = None

        logger.info(
            f"WormUploader initialized: mode={config.mode}, "
            f"enabled={config.enabled}, retention={config.retention_days}d"
        )

    def _load_registry(self) -> Dict[str, Dict]:
        """Load upload registry from disk."""
        if self._upload_registry_path.exists():
            try:
                with open(self._upload_registry_path) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load upload registry: {e}")
        return {}

    def _save_registry(self) -> None:
        """Save upload registry to disk."""
        try:
            with open(self._upload_registry_path, 'w') as f:
                json.dump(self._upload_registry, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save upload registry: {e}")

    async def upload_bundle(
        self,
        bundle_path: Path,
        signature_path: Optional[Path] = None
    ) -> UploadResult:
        """
        Upload evidence bundle and signature to WORM storage.

        Args:
            bundle_path: Path to evidence bundle JSON
            signature_path: Path to signature file (defaults to bundle_path.parent/bundle.sig)

        Returns:
            UploadResult with success status and URIs
        """
        if not self.config.enabled:
            return UploadResult(
                bundle_id=bundle_path.stem,
                success=False,
                error="WORM upload disabled"
            )

        bundle_path = Path(bundle_path)

        # Find signature file
        if signature_path is None:
            signature_path = bundle_path.parent / "bundle.sig"

        # Extract bundle ID
        bundle_id = self._extract_bundle_id(bundle_path)

        # Check if already uploaded
        if bundle_id in self._upload_registry:
            existing = self._upload_registry[bundle_id]
            if existing.get("success"):
                logger.debug(f"Bundle {bundle_id} already uploaded")
                return UploadResult(
                    bundle_id=bundle_id,
                    success=True,
                    s3_uri=existing.get("s3_uri"),
                    signature_uri=existing.get("signature_uri"),
                    upload_timestamp=existing.get("upload_timestamp")
                )

        # Upload based on mode
        if self.config.mode == "proxy":
            result = await self._upload_via_proxy(bundle_path, signature_path, bundle_id)
        else:
            result = await self._upload_direct(bundle_path, signature_path, bundle_id)

        # Update registry
        if result.success:
            self._upload_registry[bundle_id] = {
                "success": True,
                "s3_uri": result.s3_uri,
                "signature_uri": result.signature_uri,
                "upload_timestamp": result.upload_timestamp,
                "retention_days": result.retention_days
            }
            self._save_registry()
            logger.info(f"Uploaded evidence bundle {bundle_id} to WORM storage")
        else:
            logger.error(f"Failed to upload {bundle_id}: {result.error}")

        return result

    async def _upload_via_proxy(
        self,
        bundle_path: Path,
        signature_path: Path,
        bundle_id: str
    ) -> UploadResult:
        """Upload via MCP server proxy endpoint."""

        if not self.config.mcp_upload_endpoint:
            return UploadResult(
                bundle_id=bundle_id,
                success=False,
                error="MCP upload endpoint not configured"
            )

        # Prepare multipart form data
        bundle_data = bundle_path.read_bytes()
        sig_data = signature_path.read_bytes() if signature_path.exists() else b""

        # Compute checksums
        bundle_hash = hashlib.sha256(bundle_data).hexdigest()
        sig_hash = hashlib.sha256(sig_data).hexdigest() if sig_data else None

        # Build request
        url = f"{self.config.mcp_upload_endpoint}/evidence/upload"

        headers = {
            "X-Client-ID": self.client_id,
            "X-Bundle-ID": bundle_id,
            "X-Bundle-Hash": f"sha256:{bundle_hash}",
        }

        if self.mcp_api_key:
            headers["Authorization"] = f"Bearer {self.mcp_api_key}"

        if sig_hash:
            headers["X-Signature-Hash"] = f"sha256:{sig_hash}"

        # Setup SSL context for mTLS
        ssl_context = None
        if self.client_cert and self.client_key:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.load_cert_chain(self.client_cert, self.client_key)

        # Upload with retry
        for attempt in range(1, self.config.max_retries + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    form = aiohttp.FormData()
                    form.add_field(
                        "bundle",
                        bundle_data,
                        filename=bundle_path.name,
                        content_type="application/json"
                    )
                    if sig_data:
                        form.add_field(
                            "signature",
                            sig_data,
                            filename=signature_path.name,
                            content_type="application/octet-stream"
                        )

                    async with session.post(
                        url,
                        data=form,
                        headers=headers,
                        ssl=ssl_context,
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:

                        if response.status == 200:
                            result_data = await response.json()
                            return UploadResult(
                                bundle_id=bundle_id,
                                success=True,
                                s3_uri=result_data.get("bundle_uri"),
                                signature_uri=result_data.get("signature_uri"),
                                upload_timestamp=datetime.now(timezone.utc).isoformat(),
                                retention_days=self.config.retention_days,
                                retry_count=attempt - 1
                            )
                        else:
                            error_text = await response.text()
                            raise Exception(f"HTTP {response.status}: {error_text}")

            except Exception as e:
                logger.warning(
                    f"Upload attempt {attempt}/{self.config.max_retries} failed: {e}"
                )
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self.config.retry_delay_seconds)

        return UploadResult(
            bundle_id=bundle_id,
            success=False,
            error=f"Upload failed after {self.config.max_retries} attempts",
            retry_count=self.config.max_retries
        )

    async def _upload_direct(
        self,
        bundle_path: Path,
        signature_path: Path,
        bundle_id: str
    ) -> UploadResult:
        """Upload directly to S3 with Object Lock."""

        if not self.config.s3_bucket:
            return UploadResult(
                bundle_id=bundle_id,
                success=False,
                error="S3 bucket not configured"
            )

        try:
            # Lazy import boto3
            import boto3
            from botocore.exceptions import ClientError

            # Initialize S3 client if needed
            if self._s3_client is None:
                session_kwargs = {"region_name": self.config.s3_region}
                if self.config.aws_access_key_id:
                    session_kwargs["aws_access_key_id"] = self.config.aws_access_key_id
                    session_kwargs["aws_secret_access_key"] = self.config.aws_secret_access_key

                self._s3_client = boto3.client("s3", **session_kwargs)

            # Generate S3 keys
            date = datetime.now(timezone.utc)
            prefix = f"evidence/{self.client_id}/{date.year}/{date.month:02d}"
            bundle_key = f"{prefix}/{bundle_path.name}"
            sig_key = f"{prefix}/{signature_path.name}" if signature_path.exists() else None

            # Calculate retention date
            retention_until = datetime.now(timezone.utc) + timedelta(days=self.config.retention_days)

            # Upload bundle with Object Lock
            for attempt in range(1, self.config.max_retries + 1):
                try:
                    self._s3_client.put_object(
                        Bucket=self.config.s3_bucket,
                        Key=bundle_key,
                        Body=bundle_path.read_bytes(),
                        ObjectLockMode="COMPLIANCE",
                        ObjectLockRetainUntilDate=retention_until,
                        ContentType="application/json",
                        Metadata={
                            "bundle_id": bundle_id,
                            "client_id": self.client_id,
                            "uploaded_at": datetime.now(timezone.utc).isoformat()
                        }
                    )

                    # Upload signature if exists
                    if sig_key and signature_path.exists():
                        self._s3_client.put_object(
                            Bucket=self.config.s3_bucket,
                            Key=sig_key,
                            Body=signature_path.read_bytes(),
                            ObjectLockMode="COMPLIANCE",
                            ObjectLockRetainUntilDate=retention_until,
                            ContentType="application/octet-stream"
                        )

                    bundle_uri = f"s3://{self.config.s3_bucket}/{bundle_key}"
                    sig_uri = f"s3://{self.config.s3_bucket}/{sig_key}" if sig_key else None

                    return UploadResult(
                        bundle_id=bundle_id,
                        success=True,
                        s3_uri=bundle_uri,
                        signature_uri=sig_uri,
                        upload_timestamp=datetime.now(timezone.utc).isoformat(),
                        retention_days=self.config.retention_days,
                        retry_count=attempt - 1
                    )

                except ClientError as e:
                    logger.warning(f"S3 upload attempt {attempt} failed: {e}")
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(self.config.retry_delay_seconds)

            return UploadResult(
                bundle_id=bundle_id,
                success=False,
                error=f"S3 upload failed after {self.config.max_retries} attempts"
            )

        except ImportError:
            return UploadResult(
                bundle_id=bundle_id,
                success=False,
                error="boto3 not installed - required for direct S3 upload"
            )
        except Exception as e:
            return UploadResult(
                bundle_id=bundle_id,
                success=False,
                error=str(e)
            )

    async def sync_pending(self) -> List[UploadResult]:
        """
        Sync all pending (not yet uploaded) evidence bundles.

        Scans local evidence directory and uploads any bundles
        not in the upload registry.

        Returns:
            List of UploadResult for each attempted upload
        """
        if not self.config.enabled:
            return []

        results = []
        pending = self._find_pending_bundles()

        logger.info(f"Found {len(pending)} pending bundles to upload")

        # Process in batches
        for i in range(0, len(pending), self.config.upload_batch_size):
            batch = pending[i:i + self.config.upload_batch_size]

            # Upload batch concurrently
            tasks = [
                self.upload_bundle(bundle_path, sig_path)
                for bundle_path, sig_path in batch
            ]

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    results.append(UploadResult(
                        bundle_id="unknown",
                        success=False,
                        error=str(result)
                    ))
                else:
                    results.append(result)

        # Summary
        success_count = sum(1 for r in results if r.success)
        logger.info(f"WORM sync complete: {success_count}/{len(results)} successful")

        return results

    def _find_pending_bundles(self) -> List[Tuple[Path, Optional[Path]]]:
        """Find bundles not yet uploaded to WORM storage."""
        pending = []

        # Walk evidence directory
        for bundle_json in self.evidence_dir.rglob("bundle.json"):
            bundle_id = self._extract_bundle_id(bundle_json)

            # Skip if already uploaded
            if bundle_id in self._upload_registry:
                if self._upload_registry[bundle_id].get("success"):
                    continue

            # Find signature file
            sig_path = bundle_json.parent / "bundle.sig"
            if not sig_path.exists():
                sig_path = None

            pending.append((bundle_json, sig_path))

        # Sort by date (oldest first)
        pending.sort(key=lambda x: x[0].parent.name)

        return pending

    def _extract_bundle_id(self, bundle_path: Path) -> str:
        """Extract bundle ID from path or content."""
        # Try to get from parent directory name (our storage format)
        parent_name = bundle_path.parent.name
        if parent_name.startswith("EB-") or len(parent_name) == 36:  # UUID
            return parent_name

        # Fall back to reading from bundle content
        try:
            with open(bundle_path) as f:
                data = json.load(f)
                return data.get("bundle_id", bundle_path.stem)
        except Exception:
            return bundle_path.stem

    def get_upload_status(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Get upload status for a bundle."""
        return self._upload_registry.get(bundle_id)

    def get_pending_count(self) -> int:
        """Get count of bundles pending upload."""
        return len(self._find_pending_bundles())

    def get_stats(self) -> Dict[str, Any]:
        """Get upload statistics."""
        uploaded = [r for r in self._upload_registry.values() if r.get("success")]

        return {
            "enabled": self.config.enabled,
            "mode": self.config.mode,
            "total_uploaded": len(uploaded),
            "pending_count": self.get_pending_count(),
            "retention_days": self.config.retention_days,
            "last_upload": max(
                (r.get("upload_timestamp") for r in uploaded),
                default=None
            )
        }


def load_worm_config_from_env() -> WormConfig:
    """Load WORM configuration from environment variables."""
    import os

    return WormConfig(
        enabled=os.environ.get("WORM_ENABLED", "false").lower() == "true",
        mode=os.environ.get("WORM_MODE", "proxy"),
        mcp_upload_endpoint=os.environ.get("MCP_URL"),
        s3_bucket=os.environ.get("WORM_S3_BUCKET"),
        s3_region=os.environ.get("WORM_S3_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        retention_days=int(os.environ.get("WORM_RETENTION_DAYS", "90")),
        max_retries=int(os.environ.get("WORM_MAX_RETRIES", "3")),
        retry_delay_seconds=int(os.environ.get("WORM_RETRY_DELAY", "5")),
        upload_batch_size=int(os.environ.get("WORM_BATCH_SIZE", "10")),
        auto_upload=os.environ.get("WORM_AUTO_UPLOAD", "true").lower() == "true"
    )
