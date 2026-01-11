"""
Evidence bundle generation and storage.

Generates signed evidence bundles for every compliance action.
Stores in /var/lib/compliance-agent/evidence/YYYY/MM/DD/<uuid>/

Optionally uploads to WORM storage (S3 with Object Lock) for
immutable, HIPAA-compliant evidence retention.

HIPAA Controls:
- §164.310(d)(2)(iv) - Data Backup and Storage
- §164.312(b) - Audit Controls
- §164.312(c)(1) - Integrity Controls
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
import logging
import asyncio

from .models import EvidenceBundle, ActionTaken
from .crypto import Ed25519Signer
from .config import AgentConfig

logger = logging.getLogger(__name__)


class EvidenceGenerator:
    """
    Generate and store evidence bundles.

    Each bundle includes:
    - bundle.json: Full evidence data
    - bundle.sig: Ed25519 detached signature
    - bundle.ots: OpenTimestamps proof (if OTS enabled)

    Optionally uploads to WORM storage for immutable backup.
    """

    def __init__(self, config: AgentConfig, signer: Ed25519Signer):
        """
        Initialize evidence generator.

        Args:
            config: Agent configuration
            signer: Ed25519 signer for bundle signatures
        """
        self.config = config
        self.signer = signer
        self.evidence_dir = config.evidence_dir

        # Initialize WORM uploader if enabled
        self._worm_uploader = None
        if config.worm_enabled:
            self._init_worm_uploader()

        # Initialize OTS client if enabled
        self._ots_client = None
        if config.ots_enabled:
            self._init_ots_client()

    def _init_worm_uploader(self):
        """Initialize WORM uploader with config."""
        try:
            from .worm_uploader import WormUploader, WormConfig

            worm_config = WormConfig(
                enabled=self.config.worm_enabled,
                mode=self.config.worm_mode,
                mcp_upload_endpoint=self.config.mcp_url,
                s3_bucket=self.config.worm_s3_bucket,
                s3_region=self.config.worm_s3_region,
                retention_days=self.config.worm_retention_days,
                auto_upload=self.config.worm_auto_upload
            )

            # Read API key if available
            mcp_api_key = None
            if self.config.mcp_api_key_file and self.config.mcp_api_key_file.exists():
                mcp_api_key = self.config.mcp_api_key_file.read_text().strip()

            self._worm_uploader = WormUploader(
                config=worm_config,
                evidence_dir=self.evidence_dir,
                client_id=self.config.site_id,
                mcp_api_key=mcp_api_key,
                client_cert=self.config.client_cert_file,
                client_key=self.config.client_key_file
            )
            logger.info("WORM uploader initialized")

        except Exception as e:
            logger.error(f"Failed to initialize WORM uploader: {e}")
            self._worm_uploader = None

    def _init_ots_client(self):
        """Initialize OpenTimestamps client with config."""
        try:
            from .opentimestamps import OTSClient, OTSConfig

            ots_config = OTSConfig(
                enabled=self.config.ots_enabled,
                calendars=self.config.ots_calendars,
                timeout_seconds=self.config.ots_timeout_seconds,
                proof_dir=self.evidence_dir / "ots_proofs",
                auto_upgrade=self.config.ots_auto_upgrade,
            )

            self._ots_client = OTSClient(ots_config)
            logger.info("OTS client initialized")

        except Exception as e:
            logger.error(f"Failed to initialize OTS client: {e}")
            self._ots_client = None

    async def create_evidence(
        self,
        check: str,
        outcome: str,
        pre_state: Dict[str, Any],
        post_state: Optional[Dict[str, Any]] = None,
        actions: Optional[List[ActionTaken]] = None,
        error: Optional[str] = None,
        timestamp_start: Optional[datetime] = None,
        timestamp_end: Optional[datetime] = None,
        hipaa_controls: Optional[List[str]] = None,
        rollback_available: bool = False,
        rollback_generation: Optional[int] = None,
        order_id: Optional[str] = None,
        runbook_id: Optional[str] = None,
        ntp_offset_ms: Optional[int] = None,
        ntp_verification: Optional[Dict[str, Any]] = None,
        nixos_revision: Optional[str] = None,
        derivation_digest: Optional[str] = None,
        ruleset_hash: Optional[str] = None
    ) -> EvidenceBundle:
        """
        Create an evidence bundle.

        Args:
            check: Check type (patching, backup, etc.)
            outcome: Outcome (success, failed, reverted, deferred, alert)
            pre_state: System state before action
            post_state: System state after action
            actions: List of actions taken
            error: Error message if outcome != success
            timestamp_start: Action start time (default: now)
            timestamp_end: Action end time (default: now)
            hipaa_controls: HIPAA control citations
            rollback_available: Whether rollback is possible
            rollback_generation: NixOS generation to rollback to
            order_id: MCP order ID that triggered action
            runbook_id: Runbook ID executed
            ntp_offset_ms: NTP offset at check time
            ntp_verification: Multi-source NTP verification result
            nixos_revision: NixOS flake revision
            derivation_digest: NixOS derivation hash
            ruleset_hash: Compliance ruleset hash

        Returns:
            EvidenceBundle ready for signing and storage
        """
        if timestamp_start is None:
            timestamp_start = datetime.now(timezone.utc)
        if timestamp_end is None:
            timestamp_end = datetime.now(timezone.utc)
        if post_state is None:
            post_state = {}
        if actions is None:
            actions = []

        bundle = EvidenceBundle(
            # Metadata
            site_id=self.config.site_id,
            host_id=self.config.host_id,
            deployment_mode=self.config.deployment_mode,
            reseller_id=self.config.reseller_id,

            # Timestamps
            timestamp_start=timestamp_start,
            timestamp_end=timestamp_end,

            # Policy
            policy_version=self.config.policy_version,
            ruleset_hash=ruleset_hash,
            nixos_revision=nixos_revision,
            derivation_digest=derivation_digest,
            ntp_offset_ms=ntp_offset_ms,
            ntp_verification=ntp_verification,

            # Check
            check=check,
            hipaa_controls=hipaa_controls,

            # State
            pre_state=pre_state,
            post_state=post_state,

            # Actions
            action_taken=actions,

            # Rollback
            rollback_available=rollback_available,
            rollback_generation=rollback_generation,

            # Outcome
            outcome=outcome,
            error=error,

            # Order
            order_id=order_id,
            runbook_id=runbook_id
        )

        logger.info(
            f"Created evidence bundle {bundle.bundle_id} "
            f"for check={check}, outcome={outcome}"
        )

        return bundle

    async def store_evidence_batch(
        self,
        bundles: List[EvidenceBundle],
        sign: bool = True,
        upload_to_worm: bool = True,
        submit_to_ots: bool = True,
        max_concurrency: int = 5
    ) -> List[tuple[Path, Optional[Path], Optional[str], Optional[str]]]:
        """
        Store multiple evidence bundles concurrently.

        Processes bundles in parallel for improved throughput when
        handling multiple compliance checks or incident resolutions.

        Args:
            bundles: List of evidence bundles to store
            sign: Whether to sign each bundle (default: True)
            upload_to_worm: Whether to upload to WORM storage (default: True)
            submit_to_ots: Whether to submit hashes to OTS (default: True)
            max_concurrency: Maximum concurrent uploads (default: 5)

        Returns:
            List of tuples: (bundle_path, signature_path, worm_uri, ots_status) for each bundle
        """
        if not bundles:
            return []

        results = []

        # Process in batches to limit concurrency
        for i in range(0, len(bundles), max_concurrency):
            batch = bundles[i:i + max_concurrency]

            # Create coroutines for each bundle
            tasks = [
                self.store_evidence(
                    bundle, sign=sign, upload_to_worm=upload_to_worm, submit_to_ots=submit_to_ots
                )
                for bundle in batch
            ]

            # Execute batch concurrently
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(batch_results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Failed to store evidence bundle {batch[j].bundle_id}: {result}"
                    )
                    # Return empty tuple for failed bundles
                    results.append((None, None, None, None))
                else:
                    results.append(result)

        logger.info(
            f"Batch stored {len([r for r in results if r[0]])} of {len(bundles)} bundles"
        )

        return results

    async def store_evidence(
        self,
        bundle: EvidenceBundle,
        sign: bool = True,
        upload_to_worm: bool = True,
        submit_to_ots: bool = True
    ) -> tuple[Path, Optional[Path], Optional[str], Optional[str]]:
        """
        Store evidence bundle to disk and optionally upload to WORM storage.

        Storage structure:
        /var/lib/compliance-agent/evidence/YYYY/MM/DD/<bundle_id>/
        ├── bundle.json
        ├── bundle.sig (if signed)
        └── bundle.ots (if OTS enabled)

        Args:
            bundle: Evidence bundle to store
            sign: Whether to sign the bundle (default: True)
            upload_to_worm: Whether to upload to WORM storage (default: True)
            submit_to_ots: Whether to submit hash to OTS (default: True)

        Returns:
            Tuple of (bundle_path, signature_path, worm_uri, ots_status)
        """
        # Create date-based directory structure
        date = bundle.timestamp_start
        bundle_dir = (
            self.evidence_dir /
            f"{date.year:04d}" /
            f"{date.month:02d}" /
            f"{date.day:02d}" /
            bundle.bundle_id
        )
        bundle_dir.mkdir(parents=True, exist_ok=True)

        # Write bundle.json
        bundle_path = bundle_dir / "bundle.json"
        bundle_json = bundle.model_dump_json(indent=2)

        with open(bundle_path, 'w') as f:
            f.write(bundle_json)

        logger.debug(f"Wrote bundle to {bundle_path}")

        # Sign bundle
        signature_path = None
        if sign:
            signature = self.signer.sign(bundle_json)
            signature_path = bundle_dir / "bundle.sig"

            with open(signature_path, 'wb') as f:
                f.write(signature)

            logger.debug(f"Wrote signature to {signature_path}")

        # Submit to OpenTimestamps
        ots_status = None
        if submit_to_ots and self._ots_client and self.config.ots_enabled:
            try:
                from .opentimestamps import compute_bundle_hash

                bundle_hash = compute_bundle_hash(bundle_json)
                ots_proof = await self._ots_client.submit_hash(
                    bundle_hash, bundle.bundle_id
                )

                if ots_proof:
                    # Save OTS proof to bundle directory
                    ots_path = bundle_dir / "bundle.ots.json"
                    with open(ots_path, 'w') as f:
                        json.dump(ots_proof.to_dict(), f, indent=2)

                    ots_status = ots_proof.status
                    logger.info(
                        f"OTS submitted for {bundle.bundle_id}: {ots_status}"
                    )
                else:
                    ots_status = "failed"
                    logger.warning(f"OTS submission failed for {bundle.bundle_id}")

            except Exception as e:
                ots_status = "error"
                logger.error(f"OTS error for {bundle.bundle_id}: {e}")

        logger.info(
            f"Stored evidence bundle {bundle.bundle_id} at {bundle_dir}"
        )

        # Upload to WORM storage if enabled
        worm_uri = None
        if upload_to_worm and self._worm_uploader and self.config.worm_auto_upload:
            try:
                result = await self._worm_uploader.upload_bundle(
                    bundle_path, signature_path
                )
                if result.success:
                    worm_uri = result.s3_uri
                    logger.info(f"Uploaded to WORM: {worm_uri}")
                else:
                    logger.warning(f"WORM upload failed: {result.error}")
            except Exception as e:
                logger.error(f"WORM upload error: {e}")

        return bundle_path, signature_path, worm_uri, ots_status

    async def load_evidence(self, bundle_id: str) -> Optional[EvidenceBundle]:
        """
        Load an evidence bundle from disk.

        Searches all date directories for the bundle_id.

        Args:
            bundle_id: Bundle ID to load

        Returns:
            EvidenceBundle if found, None otherwise
        """
        # Search for bundle_id in evidence directory
        for bundle_dir in self.evidence_dir.rglob(bundle_id):
            if bundle_dir.is_dir():
                bundle_path = bundle_dir / "bundle.json"
                if bundle_path.exists():
                    with open(bundle_path, 'r') as f:
                        data = json.load(f)
                    return EvidenceBundle(**data)

        logger.warning(f"Evidence bundle {bundle_id} not found")
        return None

    async def verify_evidence(
        self,
        bundle_path: Path,
        signature_path: Optional[Path] = None
    ) -> bool:
        """
        Verify evidence bundle signature.

        Args:
            bundle_path: Path to bundle.json
            signature_path: Path to bundle.sig (default: same dir as bundle)

        Returns:
            True if signature valid, False otherwise
        """
        if signature_path is None:
            signature_path = bundle_path.parent / "bundle.sig"

        if not signature_path.exists():
            logger.warning(f"Signature file not found: {signature_path}")
            return False

        try:
            # Load bundle
            with open(bundle_path, 'rb') as f:
                bundle_data = f.read()

            # Load signature
            with open(signature_path, 'rb') as f:
                signature = f.read()

            # Verify using signer's public key
            from .crypto import Ed25519Verifier
            public_key = self.signer.get_public_key_bytes()
            verifier = Ed25519Verifier(public_key)

            is_valid = verifier.verify(bundle_data, signature)

            if is_valid:
                logger.debug(f"Signature valid for {bundle_path}")
            else:
                logger.warning(f"Signature INVALID for {bundle_path}")

            return is_valid

        except Exception as e:
            logger.error(f"Failed to verify evidence signature: {e}")
            return False

    async def list_evidence(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        check_type: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[EvidenceBundle]:
        """
        List evidence bundles matching criteria.

        Args:
            start_date: Filter by start date (inclusive)
            end_date: Filter by end date (inclusive)
            check_type: Filter by check type
            outcome: Filter by outcome
            limit: Maximum number of results

        Returns:
            List of matching evidence bundles (sorted by timestamp desc)
        """
        bundles = []

        # Walk evidence directory
        for bundle_json in self.evidence_dir.rglob("bundle.json"):
            try:
                with open(bundle_json, 'r') as f:
                    data = json.load(f)

                bundle = EvidenceBundle(**data)

                # Apply filters
                if start_date and bundle.timestamp_start < start_date:
                    continue
                if end_date and bundle.timestamp_start > end_date:
                    continue
                if check_type and bundle.check != check_type:
                    continue
                if outcome and bundle.outcome != outcome:
                    continue

                bundles.append(bundle)

            except Exception as e:
                logger.warning(f"Failed to load bundle {bundle_json}: {e}")
                continue

        # Sort by timestamp (newest first)
        bundles.sort(key=lambda b: b.timestamp_start, reverse=True)

        # Apply limit
        if limit:
            bundles = bundles[:limit]

        return bundles

    async def prune_old_evidence(
        self,
        retention_count: int,
        retention_days: int
    ) -> int:
        """
        Prune old evidence bundles.

        Keeps last N bundles and never deletes bundles < retention_days old.

        Args:
            retention_count: Keep at least this many bundles
            retention_days: Never delete bundles younger than this

        Returns:
            Number of bundles deleted
        """
        # Get all bundles sorted by timestamp
        all_bundles = await self.list_evidence()

        # Identify bundles to delete
        to_delete = []
        now = datetime.now(timezone.utc)

        for i, bundle in enumerate(all_bundles):
            # Keep last N bundles
            if i < retention_count:
                continue

            # Never delete recent bundles
            age_days = (now - bundle.timestamp_start).days
            if age_days < retention_days:
                continue

            to_delete.append(bundle)

        # Delete bundles
        deleted_count = 0
        for bundle in to_delete:
            try:
                # Find bundle directory
                date = bundle.timestamp_start
                bundle_dir = (
                    self.evidence_dir /
                    f"{date.year:04d}" /
                    f"{date.month:02d}" /
                    f"{date.day:02d}" /
                    bundle.bundle_id
                )

                if bundle_dir.exists():
                    # Delete directory and contents
                    import shutil
                    shutil.rmtree(bundle_dir)
                    deleted_count += 1
                    logger.debug(f"Deleted evidence bundle {bundle.bundle_id}")

            except Exception as e:
                logger.error(f"Failed to delete bundle {bundle.bundle_id}: {e}")

        logger.info(
            f"Pruned {deleted_count} evidence bundles "
            f"(retention: {retention_count} count, {retention_days} days)"
        )

        return deleted_count

    async def get_evidence_stats(self) -> Dict[str, Any]:
        """
        Get statistics about stored evidence.

        Returns:
            Dictionary with stats (total_count, by_outcome, by_check, etc.)
        """
        bundles = await self.list_evidence()

        stats = {
            "total_count": len(bundles),
            "by_outcome": {},
            "by_check": {},
            "oldest": None,
            "newest": None,
            "total_size_bytes": 0
        }

        if not bundles:
            return stats

        # Count by outcome
        for bundle in bundles:
            stats["by_outcome"][bundle.outcome] = \
                stats["by_outcome"].get(bundle.outcome, 0) + 1

        # Count by check type
        for bundle in bundles:
            stats["by_check"][bundle.check] = \
                stats["by_check"].get(bundle.check, 0) + 1

        # Oldest and newest
        stats["oldest"] = bundles[-1].timestamp_start.isoformat()
        stats["newest"] = bundles[0].timestamp_start.isoformat()

        # Calculate total size
        for bundle_json in self.evidence_dir.rglob("bundle.json"):
            stats["total_size_bytes"] += bundle_json.stat().st_size

        for sig_file in self.evidence_dir.rglob("bundle.sig"):
            stats["total_size_bytes"] += sig_file.stat().st_size

        return stats

    async def sync_to_worm(
        self,
        max_concurrency: Optional[int] = None,
        batch_size: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Sync all pending evidence bundles to WORM storage.

        Use this to catch up on bundles that weren't uploaded
        (e.g., due to network issues or disabled auto-upload).

        Args:
            max_concurrency: Maximum parallel uploads (default: from config)
            batch_size: Number of bundles per batch (default: from config)

        Returns:
            Dict with sync results (uploaded, failed, pending)
        """
        if not self._worm_uploader:
            return {
                "enabled": False,
                "uploaded": 0,
                "failed": 0,
                "pending": 0,
                "error": "WORM uploader not initialized"
            }

        try:
            # Override batch size if specified
            original_batch_size = None
            if batch_size is not None:
                original_batch_size = self._worm_uploader.config.upload_batch_size
                self._worm_uploader.config.upload_batch_size = batch_size

            results = await self._worm_uploader.sync_pending()

            # Restore original batch size
            if original_batch_size is not None:
                self._worm_uploader.config.upload_batch_size = original_batch_size

            uploaded = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)
            pending = self._worm_uploader.get_pending_count()

            return {
                "enabled": True,
                "uploaded": uploaded,
                "failed": failed,
                "pending": pending,
                "batch_size": batch_size or self._worm_uploader.config.upload_batch_size,
                "results": [
                    {
                        "bundle_id": r.bundle_id,
                        "success": r.success,
                        "s3_uri": r.s3_uri,
                        "error": r.error
                    }
                    for r in results
                ]
            }

        except Exception as e:
            logger.error(f"WORM sync failed: {e}")
            return {
                "enabled": True,
                "uploaded": 0,
                "failed": 0,
                "pending": 0,
                "error": str(e)
            }

    async def sync_to_worm_parallel(
        self,
        max_workers: int = 5,
        progress_callback: Optional[callable] = None
    ) -> Dict[str, Any]:
        """
        Sync pending evidence bundles with explicit parallelism control.

        This method provides finer control over parallel uploads compared
        to sync_to_worm(), which uses the config's batch_size setting.

        Args:
            max_workers: Number of parallel upload workers (default: 5)
            progress_callback: Optional callback(uploaded, total) for progress

        Returns:
            Dict with detailed sync results
        """
        if not self._worm_uploader:
            return {
                "enabled": False,
                "uploaded": 0,
                "failed": 0,
                "pending": 0,
                "error": "WORM uploader not initialized"
            }

        try:
            # Find pending bundles
            pending_bundles = self._worm_uploader._find_pending_bundles()
            total = len(pending_bundles)

            if total == 0:
                return {
                    "enabled": True,
                    "uploaded": 0,
                    "failed": 0,
                    "pending": 0,
                    "results": []
                }

            logger.info(f"Starting parallel WORM sync: {total} bundles, {max_workers} workers")

            results = []
            uploaded_count = 0

            # Use semaphore to limit concurrency
            semaphore = asyncio.Semaphore(max_workers)

            async def upload_with_semaphore(bundle_path, sig_path):
                async with semaphore:
                    return await self._worm_uploader.upload_bundle(bundle_path, sig_path)

            # Create all tasks
            tasks = [
                upload_with_semaphore(bundle_path, sig_path)
                for bundle_path, sig_path in pending_bundles
            ]

            # Execute with progress tracking
            for i, coro in enumerate(asyncio.as_completed(tasks)):
                result = await coro
                results.append(result)

                if result.success:
                    uploaded_count += 1

                if progress_callback:
                    try:
                        progress_callback(i + 1, total)
                    except Exception:
                        pass  # Don't let callback errors break sync

            uploaded = sum(1 for r in results if r.success)
            failed = sum(1 for r in results if not r.success)
            pending = self._worm_uploader.get_pending_count()

            logger.info(f"Parallel WORM sync complete: {uploaded}/{total} succeeded")

            return {
                "enabled": True,
                "uploaded": uploaded,
                "failed": failed,
                "pending": pending,
                "total_processed": total,
                "max_workers": max_workers,
                "results": [
                    {
                        "bundle_id": r.bundle_id,
                        "success": r.success,
                        "s3_uri": r.s3_uri,
                        "error": r.error,
                        "retry_count": r.retry_count
                    }
                    for r in results
                ]
            }

        except Exception as e:
            logger.error(f"Parallel WORM sync failed: {e}")
            return {
                "enabled": True,
                "uploaded": 0,
                "failed": 0,
                "pending": 0,
                "error": str(e)
            }

    def get_worm_stats(self) -> Dict[str, Any]:
        """
        Get WORM storage statistics.

        Returns:
            Dict with WORM storage status and stats
        """
        if not self._worm_uploader:
            return {
                "enabled": False,
                "mode": None,
                "total_uploaded": 0,
                "pending_count": 0
            }

        return self._worm_uploader.get_stats()
