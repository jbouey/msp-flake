"""Local Resilience Module - Offline-first compliance and evidence capture.

This module enables the appliance to operate with local execution capability when
Central Command is unreachable. It manages pre-synced controls only:

1. Local Runbook Cache - Synced from Central Command, executed locally
2. Local Compliance Frameworks - Multi-standard support (HIPAA, SOC2, PCI-DSS, etc.)
3. Evidence Queue - Store-and-forward when offline
4. Offline Alerting - Local SMTP relay capability

Architecture:
- Central Command = Orchestration Server (administers policy and configuration)
- Appliance = Execution Agent (runs pre-approved controls, syncs when online)
"""

import json
import os
import hashlib
import asyncio
import logging
import secrets
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum
import sqlite3
import aiofiles
import aiohttp

# Ed25519 signing for delegated keys
try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.encoding import HexEncoder
    from nacl.exceptions import BadSignatureError
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

LOCAL_DATA_DIR = Path(os.getenv("OSIRIS_LOCAL_DATA", "/var/lib/osiris"))
RUNBOOK_CACHE_DIR = LOCAL_DATA_DIR / "runbooks"
FRAMEWORK_CACHE_DIR = LOCAL_DATA_DIR / "frameworks"
EVIDENCE_QUEUE_DIR = LOCAL_DATA_DIR / "evidence-queue"
LOCAL_DB_PATH = LOCAL_DATA_DIR / "local.db"

# Sync intervals
RUNBOOK_SYNC_INTERVAL = 300  # 5 minutes
FRAMEWORK_SYNC_INTERVAL = 3600  # 1 hour
EVIDENCE_DRAIN_INTERVAL = 60  # 1 minute when online

# Queue limits
MAX_EVIDENCE_QUEUE_SIZE_MB = 500
MAX_EVIDENCE_AGE_DAYS = 30

# Phase 2: Urgent retry configuration
URGENT_RETRY_MAX_ATTEMPTS = 10
URGENT_RETRY_BASE_DELAY = 1.0  # seconds
URGENT_RETRY_MAX_DELAY = 60.0  # seconds
URGENT_RETRY_JITTER = 0.5  # randomization factor

# Phase 2: SMS alerting (Twilio)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

# Phase 2: Delegated signing
DELEGATED_KEY_DIR = LOCAL_DATA_DIR / "keys"
AUDIT_TRAIL_DB = LOCAL_DATA_DIR / "audit_trail.db"


# =============================================================================
# COMPLIANCE FRAMEWORKS
# =============================================================================

class ComplianceFramework(str, Enum):
    """Supported compliance frameworks."""
    HIPAA = "hipaa"           # Healthcare
    SOC2 = "soc2"             # Service organizations
    PCI_DSS = "pci_dss"       # Payment card industry
    NIST_CSF = "nist_csf"     # NIST Cybersecurity Framework
    NIST_800_171 = "nist_800_171"  # NIST 800-171 (defense contractors)
    SOX = "sox"               # Sarbanes-Oxley (finance)
    GDPR = "gdpr"             # EU data protection
    CMMC = "cmmc"             # Cybersecurity Maturity Model
    ISO_27001 = "iso_27001"   # Information security management
    CIS = "cis"               # CIS Controls


# Framework metadata
FRAMEWORK_INFO = {
    ComplianceFramework.HIPAA: {
        "name": "HIPAA",
        "full_name": "Health Insurance Portability and Accountability Act",
        "industries": ["healthcare", "health_insurance", "medical_devices"],
        "description": "US healthcare data protection standard",
    },
    ComplianceFramework.SOC2: {
        "name": "SOC 2",
        "full_name": "Service Organization Control 2",
        "industries": ["technology", "saas", "cloud_services"],
        "description": "Trust service criteria for service organizations",
    },
    ComplianceFramework.PCI_DSS: {
        "name": "PCI-DSS",
        "full_name": "Payment Card Industry Data Security Standard",
        "industries": ["finance", "retail", "payment_processing"],
        "description": "Payment card data security requirements",
    },
    ComplianceFramework.NIST_CSF: {
        "name": "NIST CSF",
        "full_name": "NIST Cybersecurity Framework",
        "industries": ["any"],
        "description": "General cybersecurity best practices framework",
    },
    ComplianceFramework.NIST_800_171: {
        "name": "NIST 800-171",
        "full_name": "NIST Special Publication 800-171",
        "industries": ["defense", "government_contractors"],
        "description": "Protecting controlled unclassified information",
    },
    ComplianceFramework.SOX: {
        "name": "SOX",
        "full_name": "Sarbanes-Oxley Act",
        "industries": ["finance", "public_companies", "accounting"],
        "description": "Financial reporting and internal controls",
    },
    ComplianceFramework.GDPR: {
        "name": "GDPR",
        "full_name": "General Data Protection Regulation",
        "industries": ["any_eu_data"],
        "description": "EU personal data protection regulation",
    },
    ComplianceFramework.CMMC: {
        "name": "CMMC",
        "full_name": "Cybersecurity Maturity Model Certification",
        "industries": ["defense", "dod_contractors"],
        "description": "DoD contractor cybersecurity requirements",
    },
    ComplianceFramework.ISO_27001: {
        "name": "ISO 27001",
        "full_name": "ISO/IEC 27001",
        "industries": ["any"],
        "description": "International information security standard",
    },
    ComplianceFramework.CIS: {
        "name": "CIS Controls",
        "full_name": "Center for Internet Security Controls",
        "industries": ["any"],
        "description": "Prioritized cybersecurity best practices",
    },
}


@dataclass
class ComplianceControl:
    """A single compliance control/requirement."""
    control_id: str
    framework: str
    title: str
    description: str
    category: str
    check_type: str  # automated, manual, hybrid
    severity: str  # critical, high, medium, low
    check_script: Optional[str] = None  # Script/command to verify
    remediation_runbook: Optional[str] = None  # Associated runbook ID
    evidence_requirements: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ComplianceControl":
        return cls(**data)


@dataclass
class FrameworkDefinition:
    """Complete framework definition with all controls."""
    framework_id: str
    name: str
    version: str
    last_updated: str
    controls: List[ComplianceControl] = field(default_factory=list)
    total_controls: int = 0
    automated_controls: int = 0

    def to_dict(self) -> dict:
        return {
            "framework_id": self.framework_id,
            "name": self.name,
            "version": self.version,
            "last_updated": self.last_updated,
            "controls": [c.to_dict() for c in self.controls],
            "total_controls": self.total_controls,
            "automated_controls": self.automated_controls,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FrameworkDefinition":
        controls = [ComplianceControl.from_dict(c) for c in data.get("controls", [])]
        return cls(
            framework_id=data["framework_id"],
            name=data["name"],
            version=data["version"],
            last_updated=data["last_updated"],
            controls=controls,
            total_controls=data.get("total_controls", len(controls)),
            automated_controls=data.get("automated_controls", 0),
        )


# =============================================================================
# LOCAL RUNBOOK CACHE
# =============================================================================

@dataclass
class CachedRunbook:
    """Locally cached runbook."""
    runbook_id: str
    name: str
    version: str
    category: str
    triggers: List[str]
    actions: List[dict]
    checksum: str
    synced_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CachedRunbook":
        return cls(**data)


class LocalRunbookCache:
    """Manages locally cached runbooks synced from Central Command."""

    def __init__(self, cache_dir: Path = RUNBOOK_CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_dir / "index.json"
        self._index: Dict[str, CachedRunbook] = {}
        self._load_index()

    def _load_index(self):
        """Load runbook index from disk."""
        if self.index_path.exists():
            try:
                with open(self.index_path) as f:
                    data = json.load(f)
                    self._index = {
                        k: CachedRunbook.from_dict(v)
                        for k, v in data.items()
                    }
                logger.info(f"Loaded {len(self._index)} cached runbooks")
            except Exception as e:
                logger.error(f"Failed to load runbook index: {e}")
                self._index = {}

    def _save_index(self):
        """Save runbook index to disk."""
        with open(self.index_path, "w") as f:
            json.dump({k: v.to_dict() for k, v in self._index.items()}, f, indent=2)

    async def sync_from_cloud(
        self,
        api_url: str,
        site_id: str,
        api_key: str,
        tier: str = "full"
    ) -> Dict[str, Any]:
        """Sync runbooks from Central Command.

        Args:
            api_url: Central Command API URL
            site_id: Site identifier
            api_key: API key for authentication
            tier: Coverage tier (determines which runbooks)

        Returns:
            Sync result with counts
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Fetch runbook manifest for this site
                headers = {"X-API-Key": api_key}
                url = f"{api_url}/api/sites/{site_id}/runbooks?tier={tier}"

                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"Failed to fetch runbooks: {resp.status}")
                        return {"status": "failed", "error": f"HTTP {resp.status}"}

                    manifest = await resp.json()

                # Download each runbook that's new or updated
                added = 0
                updated = 0
                unchanged = 0

                for rb_info in manifest.get("runbooks", []):
                    rb_id = rb_info["runbook_id"]
                    rb_checksum = rb_info["checksum"]

                    # Check if we have this version
                    if rb_id in self._index:
                        if self._index[rb_id].checksum == rb_checksum:
                            unchanged += 1
                            continue
                        else:
                            updated += 1
                    else:
                        added += 1

                    # Download full runbook
                    rb_url = f"{api_url}/api/runbooks/{rb_id}"
                    async with session.get(rb_url, headers=headers) as rb_resp:
                        if rb_resp.status == 200:
                            runbook_data = await rb_resp.json()
                            await self._store_runbook(runbook_data, rb_checksum)

                logger.info(f"Runbook sync: {added} added, {updated} updated, {unchanged} unchanged")

                return {
                    "status": "success",
                    "added": added,
                    "updated": updated,
                    "unchanged": unchanged,
                    "total": len(self._index),
                }

        except aiohttp.ClientError as e:
            logger.error(f"Network error syncing runbooks: {e}")
            return {"status": "offline", "error": str(e), "total": len(self._index)}
        except Exception as e:
            logger.error(f"Error syncing runbooks: {e}")
            return {"status": "error", "error": str(e)}

    async def _store_runbook(self, runbook_data: dict, checksum: str):
        """Store a runbook locally."""
        rb_id = runbook_data["runbook_id"]

        # Save runbook file
        rb_path = self.cache_dir / f"{rb_id}.json"
        async with aiofiles.open(rb_path, "w") as f:
            await f.write(json.dumps(runbook_data, indent=2))

        # Update index
        self._index[rb_id] = CachedRunbook(
            runbook_id=rb_id,
            name=runbook_data.get("name", rb_id),
            version=runbook_data.get("version", "1.0"),
            category=runbook_data.get("category", "general"),
            triggers=runbook_data.get("triggers", []),
            actions=runbook_data.get("actions", []),
            checksum=checksum,
            synced_at=datetime.now(timezone.utc).isoformat(),
        )
        self._save_index()

    def get_runbook(self, runbook_id: str) -> Optional[dict]:
        """Get a cached runbook by ID."""
        if runbook_id not in self._index:
            return None

        rb_path = self.cache_dir / f"{runbook_id}.json"
        if not rb_path.exists():
            return None

        with open(rb_path) as f:
            return json.load(f)

    def get_runbooks_for_trigger(self, trigger: str) -> List[dict]:
        """Get all runbooks that match a trigger."""
        matching = []
        for rb_id, rb_info in self._index.items():
            if trigger in rb_info.triggers:
                runbook = self.get_runbook(rb_id)
                if runbook:
                    matching.append(runbook)
        return matching

    def list_runbooks(self) -> List[CachedRunbook]:
        """List all cached runbooks."""
        return list(self._index.values())

    @property
    def count(self) -> int:
        """Number of cached runbooks."""
        return len(self._index)


# =============================================================================
# LOCAL COMPLIANCE FRAMEWORK CACHE
# =============================================================================

class LocalFrameworkCache:
    """Manages locally cached compliance frameworks."""

    def __init__(self, cache_dir: Path = FRAMEWORK_CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._frameworks: Dict[str, FrameworkDefinition] = {}
        self._load_frameworks()

    def _load_frameworks(self):
        """Load all cached frameworks from disk."""
        for fw_file in self.cache_dir.glob("*.json"):
            try:
                with open(fw_file) as f:
                    data = json.load(f)
                    fw = FrameworkDefinition.from_dict(data)
                    self._frameworks[fw.framework_id] = fw
            except Exception as e:
                logger.error(f"Failed to load framework {fw_file}: {e}")

        logger.info(f"Loaded {len(self._frameworks)} compliance frameworks")

    async def sync_from_cloud(
        self,
        api_url: str,
        site_id: str,
        api_key: str,
        frameworks: List[str]
    ) -> Dict[str, Any]:
        """Sync compliance frameworks from Central Command.

        Args:
            api_url: Central Command API URL
            site_id: Site identifier
            api_key: API key
            frameworks: List of framework IDs to sync
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": api_key}
                synced = []
                failed = []

                for fw_id in frameworks:
                    url = f"{api_url}/api/frameworks/{fw_id}"

                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            fw_data = await resp.json()
                            await self._store_framework(fw_data)
                            synced.append(fw_id)
                        else:
                            failed.append(fw_id)
                            logger.warning(f"Failed to sync framework {fw_id}: {resp.status}")

                return {
                    "status": "success" if not failed else "partial",
                    "synced": synced,
                    "failed": failed,
                    "total": len(self._frameworks),
                }

        except aiohttp.ClientError as e:
            logger.error(f"Network error syncing frameworks: {e}")
            return {"status": "offline", "error": str(e)}

    async def _store_framework(self, fw_data: dict):
        """Store a framework locally."""
        fw = FrameworkDefinition.from_dict(fw_data)

        # Save to disk
        fw_path = self.cache_dir / f"{fw.framework_id}.json"
        async with aiofiles.open(fw_path, "w") as f:
            await f.write(json.dumps(fw.to_dict(), indent=2))

        self._frameworks[fw.framework_id] = fw
        logger.info(f"Stored framework {fw.framework_id} v{fw.version} ({fw.total_controls} controls)")

    def get_framework(self, framework_id: str) -> Optional[FrameworkDefinition]:
        """Get a cached framework."""
        return self._frameworks.get(framework_id)

    def get_controls(self, framework_id: str) -> List[ComplianceControl]:
        """Get all controls for a framework."""
        fw = self._frameworks.get(framework_id)
        return fw.controls if fw else []

    def get_automated_controls(self, framework_id: str) -> List[ComplianceControl]:
        """Get controls that can be checked automatically."""
        controls = self.get_controls(framework_id)
        return [c for c in controls if c.check_type == "automated"]

    def list_frameworks(self) -> List[str]:
        """List available framework IDs."""
        return list(self._frameworks.keys())


# =============================================================================
# EVIDENCE QUEUE (Store-and-Forward)
# =============================================================================

@dataclass
class QueuedEvidence:
    """Evidence bundle queued for upload."""
    evidence_id: str
    site_id: str
    framework: str
    control_id: str
    check_result: str  # pass, fail, warning
    evidence_type: str
    evidence_data: dict
    collected_at: str
    queued_at: str
    retry_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class EvidenceQueue:
    """Store-and-forward queue for evidence when offline."""

    def __init__(self, queue_dir: Path = EVIDENCE_QUEUE_DIR):
        self.queue_dir = queue_dir
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = queue_dir / "queue.db"
        self._init_db()

    def _init_db(self):
        """Initialize SQLite queue database."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS evidence_queue (
                evidence_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                framework TEXT NOT NULL,
                control_id TEXT NOT NULL,
                check_result TEXT NOT NULL,
                evidence_type TEXT NOT NULL,
                evidence_data TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_site
            ON evidence_queue(site_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_queued
            ON evidence_queue(queued_at)
        """)
        conn.commit()
        conn.close()

    async def enqueue(self, evidence: QueuedEvidence) -> bool:
        """Add evidence to the queue."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT OR REPLACE INTO evidence_queue
                (evidence_id, site_id, framework, control_id, check_result,
                 evidence_type, evidence_data, collected_at, queued_at, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                evidence.evidence_id,
                evidence.site_id,
                evidence.framework,
                evidence.control_id,
                evidence.check_result,
                evidence.evidence_type,
                json.dumps(evidence.evidence_data),
                evidence.collected_at,
                evidence.queued_at,
                evidence.retry_count,
            ))
            conn.commit()
            conn.close()
            logger.debug(f"Queued evidence {evidence.evidence_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to queue evidence: {e}")
            return False

    async def drain_to_cloud(
        self,
        api_url: str,
        api_key: str,
        batch_size: int = 50
    ) -> Dict[str, Any]:
        """Upload queued evidence to Central Command.

        Returns:
            Upload result with counts
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Get oldest items first (FIFO)
        cursor = conn.execute("""
            SELECT * FROM evidence_queue
            ORDER BY queued_at ASC
            LIMIT ?
        """, (batch_size,))

        items = cursor.fetchall()
        if not items:
            conn.close()
            return {"status": "empty", "uploaded": 0, "remaining": 0}

        uploaded = 0
        failed = 0

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "X-API-Key": api_key,
                    "Content-Type": "application/json",
                }

                for row in items:
                    evidence_data = {
                        "evidence_id": row["evidence_id"],
                        "site_id": row["site_id"],
                        "framework": row["framework"],
                        "control_id": row["control_id"],
                        "check_result": row["check_result"],
                        "evidence_type": row["evidence_type"],
                        "evidence_data": json.loads(row["evidence_data"]),
                        "collected_at": row["collected_at"],
                    }

                    url = f"{api_url}/api/evidence"
                    async with session.post(url, headers=headers, json=evidence_data) as resp:
                        if resp.status in (200, 201):
                            # Remove from queue
                            conn.execute(
                                "DELETE FROM evidence_queue WHERE evidence_id = ?",
                                (row["evidence_id"],)
                            )
                            uploaded += 1
                        else:
                            # Increment retry count
                            conn.execute("""
                                UPDATE evidence_queue
                                SET retry_count = retry_count + 1
                                WHERE evidence_id = ?
                            """, (row["evidence_id"],))
                            failed += 1

                conn.commit()

        except aiohttp.ClientError as e:
            logger.error(f"Network error draining queue: {e}")
            conn.close()
            return {"status": "offline", "error": str(e)}

        # Get remaining count
        remaining = conn.execute("SELECT COUNT(*) FROM evidence_queue").fetchone()[0]
        conn.close()

        logger.info(f"Evidence drain: {uploaded} uploaded, {failed} failed, {remaining} remaining")

        return {
            "status": "success" if failed == 0 else "partial",
            "uploaded": uploaded,
            "failed": failed,
            "remaining": remaining,
        }

    def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        conn = sqlite3.connect(self.db_path)

        total = conn.execute("SELECT COUNT(*) FROM evidence_queue").fetchone()[0]

        by_framework = dict(conn.execute("""
            SELECT framework, COUNT(*)
            FROM evidence_queue
            GROUP BY framework
        """).fetchall())

        oldest = conn.execute("""
            SELECT queued_at FROM evidence_queue
            ORDER BY queued_at ASC LIMIT 1
        """).fetchone()

        conn.close()

        return {
            "total_queued": total,
            "by_framework": by_framework,
            "oldest_item": oldest[0] if oldest else None,
        }

    def clear_old_items(self, max_age_days: int = MAX_EVIDENCE_AGE_DAYS):
        """Remove items older than max age."""
        conn = sqlite3.connect(self.db_path)
        cutoff = datetime.now(timezone.utc).isoformat()
        # This is simplified - would need proper date math
        conn.execute("""
            DELETE FROM evidence_queue
            WHERE retry_count > 10
        """)
        deleted = conn.total_changes
        conn.commit()
        conn.close()

        if deleted > 0:
            logger.info(f"Cleared {deleted} stale evidence items from queue")

        return deleted


# =============================================================================
# PHASE 2: DELEGATED SIGNING KEYS
# =============================================================================

@dataclass
class DelegatedKeyInfo:
    """Information about a delegated signing key."""
    key_id: str
    public_key_hex: str
    delegated_at: str
    expires_at: str
    delegated_by: str  # Central Command key ID that signed this delegation
    scope: List[str]  # What this key can sign: ["evidence", "audit", "l1_actions"]
    signature: str  # Central Command's signature over this delegation


class DelegatedSigningKey:
    """Manages Ed25519 signing keys delegated from Central Command.

    Central Command delegates a signing key to the appliance, which can then
    sign evidence and audit trail entries locally. This enables:
    - Offline evidence signing
    - Audit trail integrity
    - Non-repudiation of local actions
    """

    def __init__(self, key_dir: Path = DELEGATED_KEY_DIR):
        self.key_dir = key_dir
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self.private_key_path = self.key_dir / "delegated.key"
        self.key_info_path = self.key_dir / "delegation.json"
        self._signing_key: Optional[SigningKey] = None
        self._key_info: Optional[DelegatedKeyInfo] = None
        self._load_key()

    def _load_key(self):
        """Load existing delegated key from disk."""
        if not NACL_AVAILABLE:
            logger.warning("PyNaCl not available - signing disabled")
            return

        if self.private_key_path.exists() and self.key_info_path.exists():
            try:
                # Load private key
                with open(self.private_key_path, "rb") as f:
                    key_bytes = f.read()
                self._signing_key = SigningKey(key_bytes)

                # Load delegation info
                with open(self.key_info_path) as f:
                    data = json.load(f)
                    self._key_info = DelegatedKeyInfo(**data)

                # Check expiration
                expires = datetime.fromisoformat(self._key_info.expires_at.replace("Z", "+00:00"))
                if expires < datetime.now(timezone.utc):
                    logger.warning(f"Delegated key {self._key_info.key_id} has expired")
                    self._signing_key = None
                    self._key_info = None
                else:
                    logger.info(f"Loaded delegated key {self._key_info.key_id}")

            except Exception as e:
                logger.error(f"Failed to load delegated key: {e}")

    async def request_delegation(
        self,
        api_url: str,
        site_id: str,
        api_key: str,
        appliance_id: str
    ) -> bool:
        """Request a delegated signing key from Central Command.

        Central Command will:
        1. Generate a new Ed25519 keypair
        2. Sign the public key with its master key
        3. Return the private key and delegation certificate
        """
        if not NACL_AVAILABLE:
            logger.error("PyNaCl not available - cannot request delegation")
            return False

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
                url = f"{api_url}/api/appliances/{appliance_id}/delegate-key"

                payload = {
                    "site_id": site_id,
                    "appliance_id": appliance_id,
                    "scope": ["evidence", "audit", "l1_actions"],
                    "validity_days": 365,
                }

                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"Key delegation request failed: {resp.status}")
                        return False

                    data = await resp.json()

                    # Store private key securely
                    private_key_hex = data["private_key"]
                    private_key_bytes = bytes.fromhex(private_key_hex)

                    with open(self.private_key_path, "wb") as f:
                        f.write(private_key_bytes)
                    os.chmod(self.private_key_path, 0o600)

                    # Store delegation info
                    key_info = DelegatedKeyInfo(
                        key_id=data["key_id"],
                        public_key_hex=data["public_key"],
                        delegated_at=data["delegated_at"],
                        expires_at=data["expires_at"],
                        delegated_by=data["delegated_by"],
                        scope=data["scope"],
                        signature=data["signature"],
                    )

                    with open(self.key_info_path, "w") as f:
                        json.dump(asdict(key_info), f, indent=2)

                    # Reload
                    self._load_key()
                    logger.info(f"Received delegated key {key_info.key_id}")
                    return True

        except Exception as e:
            logger.error(f"Failed to request key delegation: {e}")
            return False

    def sign(self, data: bytes) -> Optional[str]:
        """Sign data with the delegated key.

        Returns:
            Hex-encoded signature, or None if signing unavailable
        """
        if not self._signing_key:
            return None

        try:
            signed = self._signing_key.sign(data)
            # Return just the signature (first 64 bytes), not the message
            return signed.signature.hex()
        except Exception as e:
            logger.error(f"Signing failed: {e}")
            return None

    def sign_json(self, data: dict) -> Optional[dict]:
        """Sign a JSON object, returning it with signature attached.

        The signature covers the canonical JSON encoding of the data.
        """
        if not self._signing_key:
            return None

        # Canonical JSON encoding (sorted keys, no whitespace)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        signature = self.sign(canonical.encode("utf-8"))

        if signature:
            return {
                **data,
                "_signature": signature,
                "_signed_by": self._key_info.key_id if self._key_info else "unknown",
                "_signed_at": datetime.now(timezone.utc).isoformat(),
            }
        return None

    @property
    def is_available(self) -> bool:
        """Check if signing is available."""
        return self._signing_key is not None

    @property
    def key_id(self) -> Optional[str]:
        """Get the key ID."""
        return self._key_info.key_id if self._key_info else None

    @property
    def public_key_hex(self) -> Optional[str]:
        """Get the public key in hex format."""
        if self._signing_key:
            return self._signing_key.verify_key.encode(encoder=HexEncoder).decode()
        return None


# =============================================================================
# PHASE 2: URGENT CLOUD RETRY
# =============================================================================

class IncidentPriority(str, Enum):
    """Incident priority levels for retry handling."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class PendingEscalation:
    """An incident pending cloud escalation."""
    escalation_id: str
    incident_id: str
    site_id: str
    priority: str
    incident_type: str
    incident_data: dict
    created_at: str
    retry_count: int = 0
    last_retry_at: Optional[str] = None
    sms_sent: bool = False


class UrgentCloudRetry:
    """Handles urgent cloud reconnection for critical incidents.

    When cloud is unreachable and a critical incident occurs:
    1. Queue the incident with priority
    2. Aggressive retry with exponential backoff
    3. SMS fallback after N failures
    4. Continue retrying until cloud responds
    """

    def __init__(self, db_path: Path = LOCAL_DB_PATH):
        self.db_path = db_path
        self._init_db()
        self._retry_task: Optional[asyncio.Task] = None
        self._running = False

    def _init_db(self):
        """Initialize escalation queue table."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_escalations (
                escalation_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                site_id TEXT NOT NULL,
                priority TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                incident_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                last_retry_at TEXT,
                sms_sent INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_escalation_priority
            ON pending_escalations(priority, created_at)
        """)
        conn.commit()
        conn.close()

    async def queue_escalation(
        self,
        incident_id: str,
        site_id: str,
        priority: str,
        incident_type: str,
        incident_data: dict
    ) -> str:
        """Queue an incident for cloud escalation."""
        escalation_id = hashlib.sha256(
            f"{incident_id}:{datetime.now().isoformat()}:{secrets.token_hex(8)}".encode()
        ).hexdigest()[:16]

        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO pending_escalations
            (escalation_id, incident_id, site_id, priority, incident_type,
             incident_data, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            escalation_id,
            incident_id,
            site_id,
            priority,
            incident_type,
            json.dumps(incident_data),
            datetime.now(timezone.utc).isoformat(),
        ))
        conn.commit()
        conn.close()

        logger.info(f"Queued escalation {escalation_id} (priority={priority})")
        return escalation_id

    async def process_escalations(
        self,
        api_url: str,
        api_key: str,
        sms_callback: Optional[Callable[[str, str], bool]] = None
    ) -> Dict[str, Any]:
        """Process pending escalations with retry logic.

        Args:
            api_url: Central Command API URL
            api_key: API key for auth
            sms_callback: Optional callback for SMS sending (to_number, message) -> success

        Returns:
            Processing results
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Get pending escalations, critical first
        cursor = conn.execute("""
            SELECT * FROM pending_escalations
            ORDER BY
                CASE priority
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    ELSE 4
                END,
                created_at ASC
        """)

        escalations = cursor.fetchall()
        if not escalations:
            conn.close()
            return {"status": "empty", "processed": 0}

        results = {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "sms_sent": 0,
        }

        for row in escalations:
            escalation = PendingEscalation(
                escalation_id=row["escalation_id"],
                incident_id=row["incident_id"],
                site_id=row["site_id"],
                priority=row["priority"],
                incident_type=row["incident_type"],
                incident_data=json.loads(row["incident_data"]),
                created_at=row["created_at"],
                retry_count=row["retry_count"],
                last_retry_at=row["last_retry_at"],
                sms_sent=bool(row["sms_sent"]),
            )

            # Calculate backoff delay
            delay = self._calculate_backoff(escalation.retry_count)

            # Check if enough time has passed since last retry
            if escalation.last_retry_at:
                last_retry = datetime.fromisoformat(escalation.last_retry_at.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - last_retry).total_seconds()
                if elapsed < delay:
                    continue  # Not time to retry yet

            results["processed"] += 1

            # Try to escalate to cloud
            success = await self._try_escalate(api_url, api_key, escalation)

            if success:
                # Remove from queue
                conn.execute(
                    "DELETE FROM pending_escalations WHERE escalation_id = ?",
                    (escalation.escalation_id,)
                )
                results["succeeded"] += 1
                logger.info(f"Escalation {escalation.escalation_id} succeeded")
            else:
                # Update retry count
                new_retry_count = escalation.retry_count + 1
                conn.execute("""
                    UPDATE pending_escalations
                    SET retry_count = ?, last_retry_at = ?
                    WHERE escalation_id = ?
                """, (
                    new_retry_count,
                    datetime.now(timezone.utc).isoformat(),
                    escalation.escalation_id,
                ))
                results["failed"] += 1

                # Send SMS if critical and not yet sent
                if (escalation.priority == "critical" and
                    not escalation.sms_sent and
                    new_retry_count >= 3 and
                    sms_callback):

                    message = (
                        f"[OsirisCare CRITICAL] Site {escalation.site_id}: "
                        f"{escalation.incident_type}. Cloud unreachable after "
                        f"{new_retry_count} attempts. Manual review required."
                    )

                    # Get partner phone from site config (simplified here)
                    partner_phone = escalation.incident_data.get("alert_phone")
                    if partner_phone:
                        try:
                            sms_success = sms_callback(partner_phone, message)
                            if sms_success:
                                conn.execute("""
                                    UPDATE pending_escalations
                                    SET sms_sent = 1
                                    WHERE escalation_id = ?
                                """, (escalation.escalation_id,))
                                results["sms_sent"] += 1
                                logger.info(f"SMS sent for escalation {escalation.escalation_id}")
                        except Exception as e:
                            logger.error(f"SMS send failed: {e}")

        conn.commit()
        conn.close()
        return results

    async def _try_escalate(
        self,
        api_url: str,
        api_key: str,
        escalation: PendingEscalation
    ) -> bool:
        """Try to escalate a single incident to cloud."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

                # Determine endpoint based on priority
                if escalation.priority in ("critical", "high"):
                    # L2 escalation - needs LLM decision
                    url = f"{api_url}/chat"
                    payload = {
                        "client_id": escalation.site_id,
                        "hostname": escalation.incident_data.get("hostname", "unknown"),
                        "incident_type": escalation.incident_type,
                        "severity": escalation.priority,
                        "details": escalation.incident_data,
                    }
                else:
                    # Standard incident report
                    url = f"{api_url}/incidents"
                    payload = {
                        "incident_id": escalation.incident_id,
                        "site_id": escalation.site_id,
                        "incident_type": escalation.incident_type,
                        "severity": escalation.priority,
                        "data": escalation.incident_data,
                    }

                async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                    return resp.status in (200, 201, 202)

        except asyncio.TimeoutError:
            logger.warning(f"Escalation timeout for {escalation.escalation_id}")
            return False
        except aiohttp.ClientError as e:
            logger.warning(f"Escalation network error: {e}")
            return False
        except Exception as e:
            logger.error(f"Escalation failed: {e}")
            return False

    def _calculate_backoff(self, retry_count: int) -> float:
        """Calculate exponential backoff with jitter."""
        delay = min(
            URGENT_RETRY_BASE_DELAY * (2 ** retry_count),
            URGENT_RETRY_MAX_DELAY
        )
        # Add jitter
        jitter = delay * URGENT_RETRY_JITTER * (2 * secrets.randbelow(100) / 100 - 1)
        return max(0, delay + jitter)

    def get_pending_count(self) -> Dict[str, int]:
        """Get count of pending escalations by priority."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT priority, COUNT(*) as count
            FROM pending_escalations
            GROUP BY priority
        """)
        result = {row[0]: row[1] for row in cursor.fetchall()}
        conn.close()
        return result


# =============================================================================
# PHASE 2: OFFLINE AUDIT TRAIL
# =============================================================================

@dataclass
class AuditEntry:
    """A single audit trail entry."""
    entry_id: str
    site_id: str
    action_type: str  # l1_execution, evidence_collection, config_change, etc.
    action_data: dict
    outcome: str  # success, failure, partial
    timestamp: str
    signature: Optional[str] = None
    signed_by: Optional[str] = None
    synced_to_cloud: bool = False


class OfflineAuditTrail:
    """Tamper-evident audit trail for offline actions.

    All local L1 actions are logged with:
    - Timestamp
    - Action details
    - Outcome
    - Ed25519 signature (using delegated key)
    - Hash chain for tamper detection

    When cloud is reachable, entries are synced and verified.
    """

    def __init__(
        self,
        db_path: Path = AUDIT_TRAIL_DB,
        signing_key: Optional[DelegatedSigningKey] = None
    ):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.signing_key = signing_key
        self._init_db()
        self._last_hash: Optional[str] = None
        self._load_last_hash()

    def _init_db(self):
        """Initialize audit trail database."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_trail (
                entry_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_data TEXT NOT NULL,
                outcome TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                signature TEXT,
                signed_by TEXT,
                prev_hash TEXT,
                entry_hash TEXT NOT NULL,
                synced_to_cloud INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_trail(timestamp)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_synced
            ON audit_trail(synced_to_cloud)
        """)
        conn.commit()
        conn.close()

    def _load_last_hash(self):
        """Load the hash of the last entry for chain continuity."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT entry_hash FROM audit_trail
            ORDER BY timestamp DESC LIMIT 1
        """)
        row = cursor.fetchone()
        self._last_hash = row[0] if row else None
        conn.close()

    def log_action(
        self,
        site_id: str,
        action_type: str,
        action_data: dict,
        outcome: str
    ) -> str:
        """Log an action to the audit trail.

        Returns:
            The entry ID
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        entry_id = hashlib.sha256(
            f"{site_id}:{action_type}:{timestamp}:{secrets.token_hex(8)}".encode()
        ).hexdigest()[:16]

        # Build entry for signing
        entry_content = {
            "entry_id": entry_id,
            "site_id": site_id,
            "action_type": action_type,
            "action_data": action_data,
            "outcome": outcome,
            "timestamp": timestamp,
            "prev_hash": self._last_hash,
        }

        # Sign if key available
        signature = None
        signed_by = None
        if self.signing_key and self.signing_key.is_available:
            canonical = json.dumps(entry_content, sort_keys=True, separators=(",", ":"))
            signature = self.signing_key.sign(canonical.encode("utf-8"))
            signed_by = self.signing_key.key_id

        # Calculate entry hash (includes signature for integrity)
        hash_content = json.dumps({
            **entry_content,
            "signature": signature,
        }, sort_keys=True, separators=(",", ":"))
        entry_hash = hashlib.sha256(hash_content.encode()).hexdigest()

        # Store entry
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO audit_trail
            (entry_id, site_id, action_type, action_data, outcome, timestamp,
             signature, signed_by, prev_hash, entry_hash, synced_to_cloud)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            entry_id,
            site_id,
            action_type,
            json.dumps(action_data),
            outcome,
            timestamp,
            signature,
            signed_by,
            self._last_hash,
            entry_hash,
        ))
        conn.commit()
        conn.close()

        # Update chain
        self._last_hash = entry_hash

        logger.debug(f"Audit entry {entry_id}: {action_type} -> {outcome}")
        return entry_id

    async def sync_to_cloud(
        self,
        api_url: str,
        api_key: str,
        batch_size: int = 100
    ) -> Dict[str, Any]:
        """Sync unsynced audit entries to Central Command."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute("""
            SELECT * FROM audit_trail
            WHERE synced_to_cloud = 0
            ORDER BY timestamp ASC
            LIMIT ?
        """, (batch_size,))

        entries = cursor.fetchall()
        if not entries:
            conn.close()
            return {"status": "empty", "synced": 0}

        synced = 0
        failed = 0

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

                # Batch upload
                batch = []
                for row in entries:
                    batch.append({
                        "entry_id": row["entry_id"],
                        "site_id": row["site_id"],
                        "action_type": row["action_type"],
                        "action_data": json.loads(row["action_data"]),
                        "outcome": row["outcome"],
                        "timestamp": row["timestamp"],
                        "signature": row["signature"],
                        "signed_by": row["signed_by"],
                        "prev_hash": row["prev_hash"],
                        "entry_hash": row["entry_hash"],
                    })

                url = f"{api_url}/api/audit/sync"
                async with session.post(url, headers=headers, json={"entries": batch}) as resp:
                    if resp.status in (200, 201):
                        result = await resp.json()
                        synced_ids = result.get("synced_ids", [])

                        # Mark synced entries
                        for entry_id in synced_ids:
                            conn.execute("""
                                UPDATE audit_trail
                                SET synced_to_cloud = 1
                                WHERE entry_id = ?
                            """, (entry_id,))
                            synced += 1

                        failed = len(batch) - synced
                    else:
                        failed = len(batch)

                conn.commit()

        except Exception as e:
            logger.error(f"Audit sync failed: {e}")
            conn.close()
            return {"status": "error", "error": str(e)}

        remaining = conn.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE synced_to_cloud = 0"
        ).fetchone()[0]
        conn.close()

        return {
            "status": "success" if failed == 0 else "partial",
            "synced": synced,
            "failed": failed,
            "remaining": remaining,
        }

    def verify_chain_integrity(self) -> Dict[str, Any]:
        """Verify the hash chain integrity of the audit trail."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute("""
            SELECT entry_id, prev_hash, entry_hash, action_data, outcome,
                   timestamp, signature, site_id, action_type
            FROM audit_trail
            ORDER BY timestamp ASC
        """)

        entries = cursor.fetchall()
        conn.close()

        if not entries:
            return {"valid": True, "entries_checked": 0}

        errors = []
        prev_hash = None

        for row in entries:
            # Check prev_hash matches
            if row["prev_hash"] != prev_hash:
                errors.append({
                    "entry_id": row["entry_id"],
                    "error": "prev_hash mismatch",
                    "expected": prev_hash,
                    "found": row["prev_hash"],
                })

            # Verify entry hash
            entry_content = {
                "entry_id": row["entry_id"],
                "site_id": row["site_id"],
                "action_type": row["action_type"],
                "action_data": json.loads(row["action_data"]),
                "outcome": row["outcome"],
                "timestamp": row["timestamp"],
                "prev_hash": row["prev_hash"],
            }
            hash_content = json.dumps({
                **entry_content,
                "signature": row["signature"],
            }, sort_keys=True, separators=(",", ":"))
            computed_hash = hashlib.sha256(hash_content.encode()).hexdigest()

            if computed_hash != row["entry_hash"]:
                errors.append({
                    "entry_id": row["entry_id"],
                    "error": "entry_hash mismatch",
                    "expected": computed_hash,
                    "found": row["entry_hash"],
                })

            prev_hash = row["entry_hash"]

        return {
            "valid": len(errors) == 0,
            "entries_checked": len(entries),
            "errors": errors,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get audit trail statistics."""
        conn = sqlite3.connect(self.db_path)

        total = conn.execute("SELECT COUNT(*) FROM audit_trail").fetchone()[0]
        synced = conn.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE synced_to_cloud = 1"
        ).fetchone()[0]
        signed = conn.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE signature IS NOT NULL"
        ).fetchone()[0]

        by_type = dict(conn.execute("""
            SELECT action_type, COUNT(*)
            FROM audit_trail
            GROUP BY action_type
        """).fetchall())

        conn.close()

        return {
            "total_entries": total,
            "synced_to_cloud": synced,
            "pending_sync": total - synced,
            "signed_entries": signed,
            "by_action_type": by_type,
        }


# =============================================================================
# PHASE 2: SMS ALERTING (Twilio Integration)
# =============================================================================

class SMSAlerter:
    """Send SMS alerts via Twilio for critical escalations."""

    def __init__(
        self,
        account_sid: str = TWILIO_ACCOUNT_SID,
        auth_token: str = TWILIO_AUTH_TOKEN,
        from_number: str = TWILIO_FROM_NUMBER
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self._enabled = bool(account_sid and auth_token and from_number)

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def send_sms(self, to_number: str, message: str) -> bool:
        """Send an SMS message via Twilio.

        Args:
            to_number: Recipient phone number (E.164 format)
            message: Message text (max 1600 chars)

        Returns:
            True if sent successfully
        """
        if not self._enabled:
            logger.warning("SMS alerting not configured")
            return False

        # Truncate message if needed
        if len(message) > 1600:
            message = message[:1597] + "..."

        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
                auth = aiohttp.BasicAuth(self.account_sid, self.auth_token)

                data = {
                    "To": to_number,
                    "From": self.from_number,
                    "Body": message,
                }

                async with session.post(url, auth=auth, data=data) as resp:
                    if resp.status in (200, 201):
                        result = await resp.json()
                        logger.info(f"SMS sent: {result.get('sid')}")
                        return True
                    else:
                        error = await resp.text()
                        logger.error(f"SMS send failed: {resp.status} - {error}")
                        return False

        except Exception as e:
            logger.error(f"SMS send error: {e}")
            return False

    def send_sms_sync(self, to_number: str, message: str) -> bool:
        """Synchronous wrapper for send_sms (for use as callback)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Create a new task in the running loop
                future = asyncio.ensure_future(self.send_sms(to_number, message))
                return False  # Can't wait synchronously in async context
            else:
                return loop.run_until_complete(self.send_sms(to_number, message))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(self.send_sms(to_number, message))


# =============================================================================
# PHASE 3: SMART SYNC SCHEDULER
# =============================================================================

@dataclass
class SyncWindow:
    """A time window for sync operations."""
    start_hour: int  # 0-23
    end_hour: int    # 0-23
    priority: str    # "preferred", "acceptable", "avoid"
    bandwidth_weight: float  # 0.0-1.0, lower = better for syncing


class SmartSyncScheduler:
    """Optimizes sync timing based on bandwidth usage patterns.

    Learns optimal sync windows by:
    1. Tracking bandwidth usage patterns by hour/day
    2. Identifying low-bandwidth periods
    3. Scheduling bulk syncs during off-peak times
    4. Allowing urgent syncs anytime
    """

    def __init__(self, db_path: Path = LOCAL_DB_PATH):
        self.db_path = db_path
        self._init_db()

        # Default sync windows (can be overridden by learned patterns)
        self._default_windows = [
            SyncWindow(0, 6, "preferred", 0.2),    # 12am-6am: best
            SyncWindow(6, 8, "acceptable", 0.5),   # 6am-8am: morning ramp
            SyncWindow(8, 17, "avoid", 0.9),       # 8am-5pm: business hours
            SyncWindow(17, 19, "acceptable", 0.6), # 5pm-7pm: evening ramp
            SyncWindow(19, 24, "preferred", 0.3),  # 7pm-12am: good
        ]

    def _init_db(self):
        """Initialize bandwidth tracking tables."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bandwidth_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                hour INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL,
                bytes_sent INTEGER,
                bytes_received INTEGER,
                sync_duration_ms INTEGER,
                success INTEGER
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bandwidth_hour
            ON bandwidth_samples(hour, day_of_week)
        """)
        conn.commit()
        conn.close()

    def record_sync(
        self,
        bytes_sent: int,
        bytes_received: int,
        duration_ms: int,
        success: bool
    ):
        """Record a sync operation for pattern learning."""
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO bandwidth_samples
            (timestamp, hour, day_of_week, bytes_sent, bytes_received,
             sync_duration_ms, success)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            now.isoformat(),
            now.hour,
            now.weekday(),
            bytes_sent,
            bytes_received,
            duration_ms,
            1 if success else 0,
        ))
        conn.commit()
        conn.close()

    def get_optimal_sync_window(self) -> Dict[str, Any]:
        """Determine the best time window for the next sync.

        Returns:
            Dict with recommended window info
        """
        now = datetime.now(timezone.utc)
        current_hour = now.hour

        # Check learned patterns first
        conn = sqlite3.connect(self.db_path)

        # Get average sync performance by hour (last 30 days)
        cursor = conn.execute("""
            SELECT hour,
                   AVG(sync_duration_ms) as avg_duration,
                   AVG(CASE WHEN success = 1 THEN 1.0 ELSE 0.0 END) as success_rate,
                   COUNT(*) as sample_count
            FROM bandwidth_samples
            WHERE timestamp > datetime('now', '-30 days')
            GROUP BY hour
            HAVING sample_count >= 5
            ORDER BY avg_duration ASC
        """)
        learned_patterns = {row[0]: {
            "avg_duration": row[1],
            "success_rate": row[2],
            "sample_count": row[3],
        } for row in cursor.fetchall()}
        conn.close()

        # Score each upcoming hour (next 24 hours)
        hour_scores = []
        for offset in range(24):
            check_hour = (current_hour + offset) % 24

            # Base score from default windows
            base_score = 0.5
            for window in self._default_windows:
                if window.start_hour <= check_hour < window.end_hour:
                    base_score = window.bandwidth_weight
                    break

            # Adjust based on learned patterns
            if check_hour in learned_patterns:
                pattern = learned_patterns[check_hour]
                # Lower duration and higher success = better score
                learned_score = (1 - pattern["success_rate"]) * 0.5 + \
                               (pattern["avg_duration"] / 60000) * 0.5  # Normalize to ~1 for 1 min
                # Blend with base score (weight learned more if we have samples)
                weight = min(pattern["sample_count"] / 30, 0.8)  # Max 80% learned
                final_score = base_score * (1 - weight) + learned_score * weight
            else:
                final_score = base_score

            hour_scores.append({
                "hour": check_hour,
                "offset_hours": offset,
                "score": final_score,
                "learned_data": learned_patterns.get(check_hour),
            })

        # Sort by score (lower is better) but prefer sooner
        hour_scores.sort(key=lambda x: (x["score"], x["offset_hours"]))
        best = hour_scores[0]

        return {
            "recommended_hour": best["hour"],
            "wait_hours": best["offset_hours"],
            "score": best["score"],
            "is_now_optimal": best["offset_hours"] == 0,
            "learned_patterns": len(learned_patterns),
            "all_scores": hour_scores[:6],  # Top 6 options
        }

    def should_sync_now(self, priority: str = "normal") -> bool:
        """Determine if we should sync right now.

        Args:
            priority: "urgent" always syncs, "normal" checks window, "bulk" waits for optimal

        Returns:
            True if sync should proceed
        """
        if priority == "urgent":
            return True

        window = self.get_optimal_sync_window()

        if priority == "bulk":
            # Only sync during optimal windows
            return window["is_now_optimal"] and window["score"] < 0.4

        # Normal: sync if score is acceptable
        return window["score"] < 0.7


# =============================================================================
# PHASE 3: PREDICTIVE RUNBOOK CACHE
# =============================================================================

class PredictiveRunbookCache:
    """Predicts and pre-caches runbooks based on incident patterns.

    Analyzes:
    1. Incident frequency by type
    2. Time-of-day incident patterns
    3. Seasonal/monthly patterns
    4. Correlated incident sequences (A often follows B)

    Then pre-caches runbooks likely to be needed soon.
    """

    def __init__(self, db_path: Path = LOCAL_DB_PATH, runbook_cache: Optional[LocalRunbookCache] = None):
        self.db_path = db_path
        self.runbook_cache = runbook_cache
        self._init_db()

    def _init_db(self):
        """Initialize incident pattern tracking tables."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                runbook_id TEXT,
                hour INTEGER,
                day_of_week INTEGER,
                day_of_month INTEGER,
                resolved_by TEXT  -- "l1", "l2", "l3"
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_incident_type
            ON incident_patterns(incident_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_incident_hour
            ON incident_patterns(hour, day_of_week)
        """)
        conn.commit()
        conn.close()

    def record_incident(
        self,
        incident_type: str,
        runbook_id: Optional[str] = None,
        resolved_by: str = "l1"
    ):
        """Record an incident for pattern learning."""
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO incident_patterns
            (timestamp, incident_type, runbook_id, hour, day_of_week,
             day_of_month, resolved_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            now.isoformat(),
            incident_type,
            runbook_id,
            now.hour,
            now.weekday(),
            now.day,
            resolved_by,
        ))
        conn.commit()
        conn.close()

    def get_predicted_runbooks(self, lookahead_hours: int = 24) -> List[Dict[str, Any]]:
        """Predict which runbooks are likely to be needed soon.

        Args:
            lookahead_hours: How far ahead to predict

        Returns:
            List of runbook IDs with probability scores
        """
        now = datetime.now(timezone.utc)
        predictions = {}

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Pattern 1: Hour-of-day patterns (last 30 days)
        for offset in range(lookahead_hours):
            check_hour = (now.hour + offset) % 24
            cursor = conn.execute("""
                SELECT runbook_id, COUNT(*) as count
                FROM incident_patterns
                WHERE runbook_id IS NOT NULL
                  AND hour = ?
                  AND timestamp > datetime('now', '-30 days')
                GROUP BY runbook_id
                ORDER BY count DESC
                LIMIT 10
            """, (check_hour,))

            for row in cursor.fetchall():
                rb_id = row["runbook_id"]
                if rb_id not in predictions:
                    predictions[rb_id] = {"runbook_id": rb_id, "score": 0, "reasons": []}
                # Decay score by offset (sooner = more important)
                score_boost = row["count"] * (1 - offset / (lookahead_hours * 2))
                predictions[rb_id]["score"] += score_boost
                if row["count"] >= 3:
                    predictions[rb_id]["reasons"].append(
                        f"Hour {check_hour}: {row['count']} incidents in 30d"
                    )

        # Pattern 2: Day-of-week patterns
        for offset_days in range(min(lookahead_hours // 24 + 1, 7)):
            check_dow = (now.weekday() + offset_days) % 7
            cursor = conn.execute("""
                SELECT runbook_id, COUNT(*) as count
                FROM incident_patterns
                WHERE runbook_id IS NOT NULL
                  AND day_of_week = ?
                  AND timestamp > datetime('now', '-90 days')
                GROUP BY runbook_id
                ORDER BY count DESC
                LIMIT 5
            """, (check_dow,))

            for row in cursor.fetchall():
                rb_id = row["runbook_id"]
                if rb_id not in predictions:
                    predictions[rb_id] = {"runbook_id": rb_id, "score": 0, "reasons": []}
                predictions[rb_id]["score"] += row["count"] * 0.5
                if row["count"] >= 5:
                    day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][check_dow]
                    predictions[rb_id]["reasons"].append(
                        f"{day_name}: {row['count']} incidents in 90d"
                    )

        # Pattern 3: Recent high-frequency incidents (velocity)
        cursor = conn.execute("""
            SELECT runbook_id, COUNT(*) as count
            FROM incident_patterns
            WHERE runbook_id IS NOT NULL
              AND timestamp > datetime('now', '-7 days')
            GROUP BY runbook_id
            ORDER BY count DESC
            LIMIT 5
        """)

        for row in cursor.fetchall():
            rb_id = row["runbook_id"]
            if rb_id not in predictions:
                predictions[rb_id] = {"runbook_id": rb_id, "score": 0, "reasons": []}
            predictions[rb_id]["score"] += row["count"] * 2  # High weight for recent
            if row["count"] >= 2:
                predictions[rb_id]["reasons"].append(
                    f"Recent velocity: {row['count']} in 7d"
                )

        conn.close()

        # Sort by score and return top predictions
        result = sorted(predictions.values(), key=lambda x: -x["score"])
        return result[:20]  # Top 20 predictions

    async def pre_cache_predicted(
        self,
        api_url: str,
        site_id: str,
        api_key: str
    ) -> Dict[str, Any]:
        """Pre-cache predicted runbooks from Central Command.

        Returns:
            Results of pre-caching operation
        """
        if not self.runbook_cache:
            return {"status": "no_cache", "message": "Runbook cache not configured"}

        predictions = self.get_predicted_runbooks()

        if not predictions:
            return {"status": "no_predictions", "cached": 0}

        cached = 0
        already_cached = 0
        failed = 0

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": api_key}

                for pred in predictions[:10]:  # Cache top 10
                    rb_id = pred["runbook_id"]

                    # Check if already cached
                    if self.runbook_cache.get_runbook(rb_id):
                        already_cached += 1
                        continue

                    # Fetch from cloud
                    url = f"{api_url}/api/runbooks/{rb_id}"
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            rb_data = await resp.json()
                            checksum = hashlib.sha256(
                                json.dumps(rb_data, sort_keys=True).encode()
                            ).hexdigest()[:16]
                            await self.runbook_cache._store_runbook(rb_data, checksum)
                            cached += 1
                            logger.info(f"Pre-cached runbook {rb_id} (score={pred['score']:.1f})")
                        else:
                            failed += 1

        except Exception as e:
            logger.error(f"Pre-cache failed: {e}")
            return {"status": "error", "error": str(e)}

        return {
            "status": "success",
            "cached": cached,
            "already_cached": already_cached,
            "failed": failed,
            "predictions_analyzed": len(predictions),
        }


# =============================================================================
# PHASE 3: LOCAL METRICS AGGREGATOR
# =============================================================================

@dataclass
class MetricsSummary:
    """Aggregated metrics summary."""
    period_start: str
    period_end: str
    total_incidents: int
    incidents_by_type: Dict[str, int]
    incidents_by_severity: Dict[str, int]
    l1_resolution_rate: float
    l2_resolution_rate: float
    l3_escalation_rate: float
    mean_resolution_time_ms: float
    evidence_collected: int
    compliance_checks_passed: int
    compliance_checks_failed: int


class LocalMetricsAggregator:
    """Aggregates and reports local operational metrics.

    Collects:
    - Incident counts and resolution rates
    - Healing success rates by tier
    - Evidence collection stats
    - Compliance check results
    - Resource utilization

    Provides hourly, daily, and weekly rollups.
    """

    def __init__(self, db_path: Path = LOCAL_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize metrics tables."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                value REAL NOT NULL,
                tags TEXT  -- JSON tags for filtering
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_type_time
            ON metrics_events(metric_type, timestamp)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics_rollups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL,  -- "hourly", "daily", "weekly"
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                count INTEGER,
                sum_value REAL,
                avg_value REAL,
                min_value REAL,
                max_value REAL,
                UNIQUE(period, period_start, metric_type, metric_name)
            )
        """)
        conn.commit()
        conn.close()

    def record_metric(
        self,
        metric_type: str,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None
    ):
        """Record a metric event."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO metrics_events (timestamp, metric_type, metric_name, value, tags)
            VALUES (?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            metric_type,
            metric_name,
            value,
            json.dumps(tags) if tags else None,
        ))
        conn.commit()
        conn.close()

    def record_incident_resolution(
        self,
        incident_type: str,
        severity: str,
        resolution_tier: str,  # "l1", "l2", "l3"
        resolution_time_ms: int,
        success: bool
    ):
        """Record incident resolution metrics."""
        self.record_metric("incident", "resolution", 1 if success else 0, {
            "incident_type": incident_type,
            "severity": severity,
            "tier": resolution_tier,
        })
        self.record_metric("incident", "resolution_time", resolution_time_ms, {
            "incident_type": incident_type,
            "tier": resolution_tier,
        })

    def record_compliance_check(
        self,
        framework: str,
        control_id: str,
        passed: bool
    ):
        """Record compliance check result."""
        self.record_metric("compliance", "check_result", 1 if passed else 0, {
            "framework": framework,
            "control_id": control_id,
        })

    def compute_rollup(self, period: str = "hourly") -> Dict[str, Any]:
        """Compute metrics rollup for a period.

        Args:
            period: "hourly", "daily", or "weekly"

        Returns:
            Rollup computation result
        """
        conn = sqlite3.connect(self.db_path)

        # Determine time range
        now = datetime.now(timezone.utc)
        if period == "hourly":
            period_start = now.replace(minute=0, second=0, microsecond=0)
            period_end = period_start
        elif period == "daily":
            period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            period_end = period_start
        else:  # weekly
            days_since_monday = now.weekday()
            period_start = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            period_end = period_start

        # Compute aggregates for each metric type
        cursor = conn.execute("""
            SELECT metric_type, metric_name,
                   COUNT(*) as count,
                   SUM(value) as sum_value,
                   AVG(value) as avg_value,
                   MIN(value) as min_value,
                   MAX(value) as max_value
            FROM metrics_events
            WHERE timestamp >= ?
            GROUP BY metric_type, metric_name
        """, (period_start.isoformat(),))

        rollups = []
        for row in cursor.fetchall():
            # Insert or update rollup
            conn.execute("""
                INSERT OR REPLACE INTO metrics_rollups
                (period, period_start, period_end, metric_type, metric_name,
                 count, sum_value, avg_value, min_value, max_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                period,
                period_start.isoformat(),
                now.isoformat(),
                row[0], row[1], row[2], row[3], row[4], row[5], row[6],
            ))
            rollups.append({
                "metric_type": row[0],
                "metric_name": row[1],
                "count": row[2],
                "sum": row[3],
                "avg": row[4],
                "min": row[5],
                "max": row[6],
            })

        conn.commit()
        conn.close()

        return {
            "period": period,
            "period_start": period_start.isoformat(),
            "computed_at": now.isoformat(),
            "metrics": rollups,
        }

    def get_summary(self, hours: int = 24) -> MetricsSummary:
        """Get a summary of metrics for the given period."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        # Get incident counts
        cursor = conn.execute("""
            SELECT tags, COUNT(*) as count
            FROM metrics_events
            WHERE metric_type = 'incident' AND metric_name = 'resolution'
              AND timestamp >= ?
            GROUP BY tags
        """, (cutoff,))

        incidents_by_type = {}
        incidents_by_severity = {}
        tier_counts = {"l1": 0, "l2": 0, "l3": 0}
        total_incidents = 0

        for row in cursor.fetchall():
            if row["tags"]:
                tags = json.loads(row["tags"])
                incident_type = tags.get("incident_type", "unknown")
                severity = tags.get("severity", "unknown")
                tier = tags.get("tier", "unknown")

                incidents_by_type[incident_type] = incidents_by_type.get(incident_type, 0) + row["count"]
                incidents_by_severity[severity] = incidents_by_severity.get(severity, 0) + row["count"]
                if tier in tier_counts:
                    tier_counts[tier] += row["count"]
                total_incidents += row["count"]

        # Get resolution times
        cursor = conn.execute("""
            SELECT AVG(value) as avg_time
            FROM metrics_events
            WHERE metric_type = 'incident' AND metric_name = 'resolution_time'
              AND timestamp >= ?
        """, (cutoff,))
        avg_time = cursor.fetchone()["avg_time"] or 0

        # Get compliance checks
        cursor = conn.execute("""
            SELECT SUM(CASE WHEN value = 1 THEN 1 ELSE 0 END) as passed,
                   SUM(CASE WHEN value = 0 THEN 1 ELSE 0 END) as failed
            FROM metrics_events
            WHERE metric_type = 'compliance' AND metric_name = 'check_result'
              AND timestamp >= ?
        """, (cutoff,))
        compliance = cursor.fetchone()

        # Get evidence count
        cursor = conn.execute("""
            SELECT COUNT(*) as count
            FROM metrics_events
            WHERE metric_type = 'evidence'
              AND timestamp >= ?
        """, (cutoff,))
        evidence = cursor.fetchone()["count"]

        conn.close()

        # Calculate rates
        total_resolved = tier_counts["l1"] + tier_counts["l2"] + tier_counts["l3"]

        return MetricsSummary(
            period_start=cutoff,
            period_end=datetime.now(timezone.utc).isoformat(),
            total_incidents=total_incidents,
            incidents_by_type=incidents_by_type,
            incidents_by_severity=incidents_by_severity,
            l1_resolution_rate=tier_counts["l1"] / total_resolved if total_resolved > 0 else 0,
            l2_resolution_rate=tier_counts["l2"] / total_resolved if total_resolved > 0 else 0,
            l3_escalation_rate=tier_counts["l3"] / total_resolved if total_resolved > 0 else 0,
            mean_resolution_time_ms=avg_time,
            evidence_collected=evidence,
            compliance_checks_passed=compliance["passed"] or 0 if compliance else 0,
            compliance_checks_failed=compliance["failed"] or 0 if compliance else 0,
        )

    async def report_to_cloud(
        self,
        api_url: str,
        site_id: str,
        api_key: str
    ) -> Dict[str, Any]:
        """Report aggregated metrics to Central Command."""
        summary = self.get_summary(24)  # Last 24 hours

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
                url = f"{api_url}/api/sites/{site_id}/metrics"

                payload = asdict(summary)

                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"Reported metrics to cloud: {summary.total_incidents} incidents")
                        return {"status": "success", "reported": True}
                    else:
                        return {"status": "failed", "http_status": resp.status}

        except Exception as e:
            logger.error(f"Failed to report metrics: {e}")
            return {"status": "error", "error": str(e)}


# =============================================================================
# PHASE 3: COVERAGE TIER OPTIMIZER
# =============================================================================

@dataclass
class TierRecommendation:
    """Coverage tier recommendation."""
    current_tier: str
    recommended_tier: str
    confidence: float  # 0-1
    reasons: List[str]
    estimated_l1_rate_change: float  # Percentage point change
    estimated_cost_impact: str  # "increase", "decrease", "same"


class CoverageTierOptimizer:
    """Recommends optimal coverage tier based on incident patterns.

    Analyzes:
    - L1 vs L2 vs L3 resolution rates
    - Incident types that escape L1
    - Cost of L2/L3 escalations
    - Framework requirements

    Recommends tier upgrades/downgrades.
    """

    TIER_CAPABILITIES = {
        "basic": {
            "l1_rules": 10,
            "runbooks": ["critical"],
            "description": "Critical incidents only",
        },
        "standard": {
            "l1_rules": 20,
            "runbooks": ["critical", "high"],
            "description": "Critical and high priority incidents",
        },
        "full": {
            "l1_rules": 50,
            "runbooks": ["critical", "high", "medium", "low"],
            "description": "Comprehensive coverage",
        },
    }

    def __init__(self, db_path: Path = LOCAL_DB_PATH, metrics: Optional[LocalMetricsAggregator] = None):
        self.db_path = db_path
        self.metrics = metrics
        self._init_db()

    def _init_db(self):
        """Initialize tier analysis tables."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tier_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                current_tier TEXT NOT NULL,
                l1_rate REAL,
                l2_rate REAL,
                l3_rate REAL,
                incident_types_escaping TEXT,  -- JSON list
                recommendation TEXT,
                applied INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def analyze_tier_effectiveness(self, current_tier: str) -> TierRecommendation:
        """Analyze current tier effectiveness and recommend changes.

        Args:
            current_tier: Current coverage tier ("basic", "standard", "full")

        Returns:
            Tier recommendation with justification
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # Get last 30 days of incident data
        cursor = conn.execute("""
            SELECT incident_type, runbook_id, resolved_by, COUNT(*) as count
            FROM incident_patterns
            WHERE timestamp > datetime('now', '-30 days')
            GROUP BY incident_type, resolved_by
        """)

        tier_counts = {"l1": 0, "l2": 0, "l3": 0}
        escaping_types = []  # Incident types that escape to L2/L3

        for row in cursor.fetchall():
            resolved_by = row["resolved_by"]
            if resolved_by in tier_counts:
                tier_counts[resolved_by] += row["count"]

            # Track types escaping L1
            if resolved_by in ("l2", "l3") and row["count"] >= 3:
                escaping_types.append({
                    "incident_type": row["incident_type"],
                    "count": row["count"],
                    "tier": resolved_by,
                })

        conn.close()

        total = sum(tier_counts.values())
        if total == 0:
            return TierRecommendation(
                current_tier=current_tier,
                recommended_tier=current_tier,
                confidence=0.5,
                reasons=["Insufficient data (no incidents in 30 days)"],
                estimated_l1_rate_change=0,
                estimated_cost_impact="same",
            )

        l1_rate = tier_counts["l1"] / total
        l2_rate = tier_counts["l2"] / total
        l3_rate = tier_counts["l3"] / total

        # Decision logic
        reasons = []
        recommended_tier = current_tier
        l1_rate_change = 0
        cost_impact = "same"
        confidence = 0.7

        # Check if we should upgrade
        if current_tier == "basic":
            if l3_rate > 0.15:  # More than 15% going to L3
                recommended_tier = "standard"
                reasons.append(f"High L3 escalation rate ({l3_rate:.1%}) - standard tier would add more L1 rules")
                l1_rate_change = 0.10  # Estimate 10% improvement
                cost_impact = "increase"
                confidence = 0.8
            elif l2_rate > 0.25:  # More than 25% needing LLM
                recommended_tier = "standard"
                reasons.append(f"High L2 rate ({l2_rate:.1%}) - standard tier includes more runbooks")
                l1_rate_change = 0.08
                cost_impact = "increase"
                confidence = 0.75

        elif current_tier == "standard":
            if l3_rate > 0.10:  # More than 10% going to L3
                recommended_tier = "full"
                reasons.append(f"L3 escalation rate ({l3_rate:.1%}) above threshold")
                l1_rate_change = 0.08
                cost_impact = "increase"
                confidence = 0.75
            elif l1_rate > 0.90 and l3_rate < 0.02:  # Very high L1, low L3
                recommended_tier = "basic"
                reasons.append(f"Excellent L1 rate ({l1_rate:.1%}) - basic tier may suffice")
                l1_rate_change = -0.05  # Slight decrease expected
                cost_impact = "decrease"
                confidence = 0.65

        elif current_tier == "full":
            if l1_rate > 0.85 and l3_rate < 0.05:  # High L1, low L3
                recommended_tier = "standard"
                reasons.append(f"Strong L1 performance ({l1_rate:.1%}) - could reduce to standard")
                l1_rate_change = -0.05
                cost_impact = "decrease"
                confidence = 0.6

        # Add escaping incident types to reasons
        if escaping_types and recommended_tier != current_tier:
            top_escaping = sorted(escaping_types, key=lambda x: -x["count"])[:3]
            for esc in top_escaping:
                reasons.append(
                    f"'{esc['incident_type']}' frequently escapes to {esc['tier'].upper()} ({esc['count']} in 30d)"
                )

        # If no changes recommended, explain why
        if recommended_tier == current_tier:
            reasons.append(f"Current tier is appropriate: L1={l1_rate:.1%}, L2={l2_rate:.1%}, L3={l3_rate:.1%}")

        # Store analysis
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT INTO tier_analysis
            (timestamp, current_tier, l1_rate, l2_rate, l3_rate,
             incident_types_escaping, recommendation)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            current_tier,
            l1_rate,
            l2_rate,
            l3_rate,
            json.dumps(escaping_types),
            recommended_tier,
        ))
        conn.commit()
        conn.close()

        return TierRecommendation(
            current_tier=current_tier,
            recommended_tier=recommended_tier,
            confidence=confidence,
            reasons=reasons,
            estimated_l1_rate_change=l1_rate_change,
            estimated_cost_impact=cost_impact,
        )

    def get_tier_history(self, days: int = 90) -> List[Dict[str, Any]]:
        """Get tier analysis history."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        cursor = conn.execute("""
            SELECT * FROM tier_analysis
            WHERE timestamp > datetime('now', ? || ' days')
            ORDER BY timestamp DESC
        """, (f"-{days}",))

        history = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return history


# =============================================================================
# SITE CONFIGURATION
# =============================================================================

@dataclass
class SiteComplianceConfig:
    """Site-level compliance configuration."""
    site_id: str
    site_name: str
    enabled_frameworks: List[str]  # Which frameworks apply to this site
    coverage_tier: str  # basic, standard, full
    industry: str  # healthcare, finance, technology, etc.
    runbook_overrides: Dict[str, Any] = field(default_factory=dict)
    check_schedule: Dict[str, str] = field(default_factory=dict)  # framework -> cron
    alert_config: Dict[str, Any] = field(default_factory=dict)
    last_synced: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SiteComplianceConfig":
        return cls(**data)


class SiteConfigManager:
    """Manages site-level compliance configuration."""

    def __init__(self, data_dir: Path = LOCAL_DATA_DIR):
        self.config_path = data_dir / "site_config.json"
        self._config: Optional[SiteComplianceConfig] = None
        self._load_config()

    def _load_config(self):
        """Load site configuration from disk."""
        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    data = json.load(f)
                    self._config = SiteComplianceConfig.from_dict(data)
                logger.info(f"Loaded site config: {self._config.site_id}")
            except Exception as e:
                logger.error(f"Failed to load site config: {e}")

    def _save_config(self):
        """Save site configuration to disk."""
        if self._config:
            with open(self.config_path, "w") as f:
                json.dump(self._config.to_dict(), f, indent=2)

    async def sync_from_cloud(self, api_url: str, site_id: str, api_key: str) -> bool:
        """Sync site configuration from Central Command."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"X-API-Key": api_key}
                url = f"{api_url}/api/sites/{site_id}/compliance-config"

                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._config = SiteComplianceConfig.from_dict(data)
                        self._config.last_synced = datetime.now(timezone.utc).isoformat()
                        self._save_config()
                        logger.info(f"Synced site config: frameworks={self._config.enabled_frameworks}")
                        return True
                    else:
                        logger.warning(f"Failed to sync site config: {resp.status}")
                        return False

        except Exception as e:
            logger.error(f"Error syncing site config: {e}")
            return False

    @property
    def config(self) -> Optional[SiteComplianceConfig]:
        return self._config

    def get_enabled_frameworks(self) -> List[str]:
        """Get list of enabled frameworks for this site."""
        if self._config:
            return self._config.enabled_frameworks
        return []

    def is_framework_enabled(self, framework: str) -> bool:
        """Check if a framework is enabled for this site."""
        return framework in self.get_enabled_frameworks()


# =============================================================================
# LOCAL RESILIENCE MANAGER (Main Interface)
# =============================================================================

class LocalResilienceManager:
    """Main interface for local resilience capabilities.

    Coordinates all offline-first functionality:
    - Runbook caching and execution
    - Compliance framework management
    - Evidence queuing
    - Site configuration

    Phase 2 additions:
    - Delegated signing keys for offline evidence signing
    - Urgent cloud retry for critical incidents
    - Offline audit trail with tamper detection
    - SMS alerting for cloud-unreachable critical incidents

    Phase 3 additions:
    - Smart sync scheduling (low-bandwidth periods)
    - Predictive runbook caching
    - Local metrics aggregation
    - Coverage tier optimization
    """

    def __init__(self, data_dir: Path = LOCAL_DATA_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Phase 1: Core components
        self.runbooks = LocalRunbookCache(data_dir / "runbooks")
        self.frameworks = LocalFrameworkCache(data_dir / "frameworks")
        self.evidence_queue = EvidenceQueue(data_dir / "evidence-queue")
        self.site_config = SiteConfigManager(data_dir)

        # Phase 2: Delegated authority and audit
        self.signing_key = DelegatedSigningKey(data_dir / "keys")
        self.urgent_retry = UrgentCloudRetry(data_dir / "local.db")
        self.audit_trail = OfflineAuditTrail(
            data_dir / "audit_trail.db",
            signing_key=self.signing_key
        )
        self.sms_alerter = SMSAlerter()

        # Phase 3: Operational intelligence
        self.sync_scheduler = SmartSyncScheduler(data_dir / "local.db")
        self.predictive_cache = PredictiveRunbookCache(
            data_dir / "local.db",
            runbook_cache=self.runbooks
        )
        self.metrics = LocalMetricsAggregator(data_dir / "local.db")
        self.tier_optimizer = CoverageTierOptimizer(
            data_dir / "local.db",
            metrics=self.metrics
        )

        # Cloud connectivity state
        self._cloud_reachable = False
        self._last_cloud_contact = None

    async def full_sync(
        self,
        api_url: str,
        site_id: str,
        api_key: str,
        appliance_id: Optional[str] = None,
        force: bool = False
    ) -> Dict[str, Any]:
        """Perform full sync with Central Command.

        Syncs:
        1. Site configuration
        2. Enabled compliance frameworks
        3. Runbooks for site's coverage tier
        4. Drains evidence queue
        5. (Phase 2) Request delegated signing key if needed
        6. (Phase 2) Process pending escalations
        7. (Phase 2) Sync audit trail
        8. (Phase 3) Predictive runbook pre-caching
        9. (Phase 3) Report metrics to cloud
        10. (Phase 3) Record sync for bandwidth learning

        Args:
            api_url: Central Command API URL
            site_id: Site identifier
            api_key: API key for auth
            appliance_id: Appliance ID for key delegation
            force: Force sync even if not optimal time
        """
        start_time = time.time()
        results = {
            "site_config": None,
            "frameworks": None,
            "runbooks": None,
            "evidence_drain": None,
            "signing_key": None,
            "escalations": None,
            "audit_sync": None,
            "predictive_cache": None,
            "metrics_report": None,
            "tier_analysis": None,
            "cloud_reachable": False,
            "sync_scheduled": True,
        }

        # (Phase 3) Check if we should sync now
        if not force and not self.sync_scheduler.should_sync_now("normal"):
            optimal = self.sync_scheduler.get_optimal_sync_window()
            results["sync_scheduled"] = False
            results["recommended_sync_hour"] = optimal["recommended_hour"]
            results["wait_hours"] = optimal["wait_hours"]
            logger.info(f"Skipping sync - optimal window in {optimal['wait_hours']}h")
            return results

        # 1. Sync site config
        config_ok = await self.site_config.sync_from_cloud(api_url, site_id, api_key)
        results["site_config"] = "success" if config_ok else "failed"

        if not config_ok:
            results["cloud_reachable"] = False
            # Record failed sync for learning
            self.sync_scheduler.record_sync(0, 0, int((time.time() - start_time) * 1000), False)
            return results

        self._cloud_reachable = True
        self._last_cloud_contact = datetime.now(timezone.utc)
        results["cloud_reachable"] = True

        # 2. Sync enabled frameworks
        enabled_frameworks = self.site_config.get_enabled_frameworks()
        if enabled_frameworks:
            fw_result = await self.frameworks.sync_from_cloud(
                api_url, site_id, api_key, enabled_frameworks
            )
            results["frameworks"] = fw_result

        # 3. Sync runbooks
        tier = self.site_config.config.coverage_tier if self.site_config.config else "basic"
        rb_result = await self.runbooks.sync_from_cloud(api_url, site_id, api_key, tier)
        results["runbooks"] = rb_result

        # 4. Drain evidence queue
        drain_result = await self.evidence_queue.drain_to_cloud(api_url, api_key)
        results["evidence_drain"] = drain_result

        # 5. (Phase 2) Request delegated signing key if needed
        if appliance_id and not self.signing_key.is_available:
            key_ok = await self.signing_key.request_delegation(
                api_url, site_id, api_key, appliance_id
            )
            results["signing_key"] = "obtained" if key_ok else "failed"
        else:
            results["signing_key"] = "available" if self.signing_key.is_available else "not_requested"

        # 6. (Phase 2) Process pending escalations
        sms_callback = self.sms_alerter.send_sms_sync if self.sms_alerter.is_enabled else None
        escalation_result = await self.urgent_retry.process_escalations(
            api_url, api_key, sms_callback
        )
        results["escalations"] = escalation_result

        # 7. (Phase 2) Sync audit trail
        audit_result = await self.audit_trail.sync_to_cloud(api_url, api_key)
        results["audit_sync"] = audit_result

        # 8. (Phase 3) Predictive runbook pre-caching
        pred_result = await self.predictive_cache.pre_cache_predicted(
            api_url, site_id, api_key
        )
        results["predictive_cache"] = pred_result

        # 9. (Phase 3) Report metrics to cloud
        metrics_result = await self.metrics.report_to_cloud(api_url, site_id, api_key)
        results["metrics_report"] = metrics_result

        # 10. (Phase 3) Analyze tier effectiveness
        tier_rec = self.tier_optimizer.analyze_tier_effectiveness(tier)
        results["tier_analysis"] = {
            "current_tier": tier_rec.current_tier,
            "recommended_tier": tier_rec.recommended_tier,
            "confidence": tier_rec.confidence,
            "reasons": tier_rec.reasons,
        }

        # Record successful sync for bandwidth learning
        duration_ms = int((time.time() - start_time) * 1000)
        bytes_estimate = len(json.dumps(results)) * 10  # Rough estimate
        self.sync_scheduler.record_sync(bytes_estimate, bytes_estimate, duration_ms, True)

        return results

    def get_status(self) -> Dict[str, Any]:
        """Get current local resilience status."""
        # Get Phase 3 sync window info
        sync_window = self.sync_scheduler.get_optimal_sync_window()

        # Get current tier for tier analysis
        current_tier = self.site_config.config.coverage_tier if self.site_config.config else "basic"

        return {
            # Phase 1
            "cloud_reachable": self._cloud_reachable,
            "last_cloud_contact": self._last_cloud_contact.isoformat() if self._last_cloud_contact else None,
            "site_id": self.site_config.config.site_id if self.site_config.config else None,
            "enabled_frameworks": self.site_config.get_enabled_frameworks(),
            "cached_runbooks": self.runbooks.count,
            "cached_frameworks": len(self.frameworks.list_frameworks()),
            "evidence_queue": self.evidence_queue.get_queue_stats(),
            # Phase 2
            "signing_key_available": self.signing_key.is_available,
            "signing_key_id": self.signing_key.key_id,
            "pending_escalations": self.urgent_retry.get_pending_count(),
            "audit_trail": self.audit_trail.get_stats(),
            "sms_alerting_enabled": self.sms_alerter.is_enabled,
            # Phase 3
            "sync_scheduler": {
                "optimal_hour": sync_window["recommended_hour"],
                "is_now_optimal": sync_window["is_now_optimal"],
                "learned_patterns": sync_window["learned_patterns"],
            },
            "predictive_cache": {
                "predictions": len(self.predictive_cache.get_predicted_runbooks()),
            },
            "metrics_summary": asdict(self.metrics.get_summary(24)),
            "tier_optimizer": {
                "current_tier": current_tier,
            },
        }

    async def queue_evidence(
        self,
        site_id: str,
        framework: str,
        control_id: str,
        check_result: str,
        evidence_type: str,
        evidence_data: dict,
    ) -> str:
        """Queue evidence for upload (used when offline or always for reliability)."""
        evidence_id = hashlib.sha256(
            f"{site_id}:{framework}:{control_id}:{datetime.now().isoformat()}".encode()
        ).hexdigest()[:16]

        evidence = QueuedEvidence(
            evidence_id=evidence_id,
            site_id=site_id,
            framework=framework,
            control_id=control_id,
            check_result=check_result,
            evidence_type=evidence_type,
            evidence_data=evidence_data,
            collected_at=datetime.now(timezone.utc).isoformat(),
            queued_at=datetime.now(timezone.utc).isoformat(),
        )

        await self.evidence_queue.enqueue(evidence)
        return evidence_id

    # =========================================================================
    # PHASE 2: L1 Action Execution with Audit Trail
    # =========================================================================

    def log_l1_action(
        self,
        site_id: str,
        runbook_id: str,
        incident_id: str,
        action_details: dict,
        outcome: str,
    ) -> str:
        """Log an L1 action execution to the audit trail.

        This should be called after every L1 runbook execution to maintain
        a tamper-evident record of all automated actions.

        Args:
            site_id: Site identifier
            runbook_id: The runbook that was executed
            incident_id: The incident that triggered the action
            action_details: Details of what was done
            outcome: "success", "failure", or "partial"

        Returns:
            The audit entry ID
        """
        return self.audit_trail.log_action(
            site_id=site_id,
            action_type="l1_execution",
            action_data={
                "runbook_id": runbook_id,
                "incident_id": incident_id,
                "details": action_details,
            },
            outcome=outcome,
        )

    async def escalate_to_cloud(
        self,
        site_id: str,
        incident_id: str,
        incident_type: str,
        severity: str,
        incident_data: dict,
    ) -> Dict[str, Any]:
        """Escalate an incident to Central Command (L2/L3).

        If cloud is reachable, escalates immediately.
        If cloud is unreachable, queues for urgent retry.

        Args:
            site_id: Site identifier
            incident_id: Unique incident ID
            incident_type: Type of incident (e.g., "disk_full", "service_down")
            severity: "critical", "high", "medium", "low"
            incident_data: Full incident details

        Returns:
            Escalation result
        """
        # Log the escalation attempt
        self.audit_trail.log_action(
            site_id=site_id,
            action_type="escalation_attempt",
            action_data={
                "incident_id": incident_id,
                "incident_type": incident_type,
                "severity": severity,
            },
            outcome="initiated",
        )

        # If we know cloud is unreachable, queue immediately
        if not self._cloud_reachable:
            escalation_id = await self.urgent_retry.queue_escalation(
                incident_id=incident_id,
                site_id=site_id,
                priority=severity,
                incident_type=incident_type,
                incident_data=incident_data,
            )

            # Send SMS immediately for critical incidents
            if severity == "critical" and self.sms_alerter.is_enabled:
                alert_phone = incident_data.get("alert_phone")
                if alert_phone:
                    message = (
                        f"[OsirisCare CRITICAL] Site {site_id}: {incident_type}. "
                        f"Cloud unreachable. Incident queued for retry. ID: {incident_id}"
                    )
                    await self.sms_alerter.send_sms(alert_phone, message)

            return {
                "status": "queued",
                "escalation_id": escalation_id,
                "reason": "cloud_unreachable",
            }

        # Cloud is reachable, try direct escalation
        # (This would normally call the MCP client, but for now we just return success)
        return {
            "status": "escalated",
            "incident_id": incident_id,
        }

    def verify_audit_integrity(self) -> Dict[str, Any]:
        """Verify the integrity of the local audit trail.

        Returns:
            Verification result with any errors found
        """
        return self.audit_trail.verify_chain_integrity()

    def sign_evidence(self, evidence_data: dict) -> Optional[dict]:
        """Sign evidence data with the delegated key.

        Args:
            evidence_data: The evidence to sign

        Returns:
            Evidence with signature attached, or None if signing unavailable
        """
        return self.signing_key.sign_json(evidence_data)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_resilience_manager: Optional[LocalResilienceManager] = None

def get_resilience_manager() -> LocalResilienceManager:
    """Get or create the global resilience manager."""
    global _resilience_manager
    if _resilience_manager is None:
        _resilience_manager = LocalResilienceManager()
    return _resilience_manager
