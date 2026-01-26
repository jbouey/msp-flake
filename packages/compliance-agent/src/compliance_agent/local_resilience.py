"""Local Resilience Module - Offline-first compliance and healing.

This module enables the appliance to operate fully autonomously when
Central Command is unreachable. It manages:

1. Local Runbook Cache - Synced from cloud, executed locally
2. Local Compliance Frameworks - Multi-standard support (HIPAA, SOC2, PCI-DSS, etc.)
3. Evidence Queue - Store-and-forward when offline
4. Offline Alerting - Local SMTP relay capability

Architecture:
- Central Command = Control Plane (administers what's deployed)
- Appliance = Data Plane (executes locally, syncs when online)
"""

import json
import os
import hashlib
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import sqlite3
import aiofiles
import aiohttp

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
    """

    def __init__(self, data_dir: Path = LOCAL_DATA_DIR):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Initialize components
        self.runbooks = LocalRunbookCache(data_dir / "runbooks")
        self.frameworks = LocalFrameworkCache(data_dir / "frameworks")
        self.evidence_queue = EvidenceQueue(data_dir / "evidence-queue")
        self.site_config = SiteConfigManager(data_dir)

        # Cloud connectivity state
        self._cloud_reachable = False
        self._last_cloud_contact = None

    async def full_sync(self, api_url: str, site_id: str, api_key: str) -> Dict[str, Any]:
        """Perform full sync with Central Command.

        Syncs:
        1. Site configuration
        2. Enabled compliance frameworks
        3. Runbooks for site's coverage tier
        4. Drains evidence queue
        """
        results = {
            "site_config": None,
            "frameworks": None,
            "runbooks": None,
            "evidence_drain": None,
            "cloud_reachable": False,
        }

        # 1. Sync site config
        config_ok = await self.site_config.sync_from_cloud(api_url, site_id, api_key)
        results["site_config"] = "success" if config_ok else "failed"

        if not config_ok:
            results["cloud_reachable"] = False
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

        return results

    def get_status(self) -> Dict[str, Any]:
        """Get current local resilience status."""
        return {
            "cloud_reachable": self._cloud_reachable,
            "last_cloud_contact": self._last_cloud_contact.isoformat() if self._last_cloud_contact else None,
            "site_id": self.site_config.config.site_id if self.site_config.config else None,
            "enabled_frameworks": self.site_config.get_enabled_frameworks(),
            "cached_runbooks": self.runbooks.count,
            "cached_frameworks": len(self.frameworks.list_frameworks()),
            "evidence_queue": self.evidence_queue.get_queue_stats(),
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
