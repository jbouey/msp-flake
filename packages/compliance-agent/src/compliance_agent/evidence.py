"""
Evidence bundle generation and storage.

Generates signed evidence bundles for every compliance action.
Stores in /var/lib/compliance-agent/evidence/YYYY/MM/DD/<uuid>/
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import logging

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
            nixos_revision: NixOS flake revision
            derivation_digest: NixOS derivation hash
            ruleset_hash: Compliance ruleset hash

        Returns:
            EvidenceBundle ready for signing and storage
        """
        if timestamp_start is None:
            timestamp_start = datetime.utcnow()
        if timestamp_end is None:
            timestamp_end = datetime.utcnow()
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

    async def store_evidence(
        self,
        bundle: EvidenceBundle,
        sign: bool = True
    ) -> tuple[Path, Optional[Path]]:
        """
        Store evidence bundle to disk.

        Storage structure:
        /var/lib/compliance-agent/evidence/YYYY/MM/DD/<bundle_id>/
        ├── bundle.json
        └── bundle.sig (if signed)

        Args:
            bundle: Evidence bundle to store
            sign: Whether to sign the bundle (default: True)

        Returns:
            Tuple of (bundle_path, signature_path)
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

        logger.info(
            f"Stored evidence bundle {bundle.bundle_id} at {bundle_dir}"
        )

        return bundle_path, signature_path

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
        now = datetime.utcnow()

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
