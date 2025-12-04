"""
MSP Compliance Agent - Web UI

Provides a local web interface for:
- Compliance dashboard with real-time status
- Evidence browser with search/filter
- Report downloads (PDF/HTML)
- Audit log viewer
- Hash chain verification status

Based on CLAUDE.md section 14: Executive Dashboards & Audit-Ready Outputs
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ============================================================================
# Data Models
# ============================================================================

class ComplianceStatus(BaseModel):
    """Overall compliance status."""
    status: str  # healthy, warning, critical
    score: float  # 0-100
    last_check: str
    checks_passed: int
    checks_failed: int
    checks_warning: int


class ControlStatus(BaseModel):
    """Individual HIPAA control status."""
    control_id: str
    name: str
    status: str  # pass, warn, fail
    last_checked: str
    evidence_count: int
    auto_fixed: int
    hipaa_citation: str


class IncidentSummary(BaseModel):
    """Incident summary for dashboard."""
    total_24h: int
    auto_resolved: int
    escalated: int
    l1_handled: int
    l2_handled: int
    l3_handled: int
    avg_mttr_seconds: float


class FlywheelMetrics(BaseModel):
    """Data flywheel metrics."""
    status: str  # excellent, good, developing, needs_attention
    l1_percentage: float
    l2_percentage: float
    l3_percentage: float
    patterns_tracked: int
    promotion_candidates: int
    rules_promoted: int


# ============================================================================
# Web UI Application
# ============================================================================

class ComplianceWebUI:
    """
    FastAPI-based Web UI for Compliance Agent.

    Runs on the local appliance, providing:
    - Real-time compliance dashboard
    - Evidence browser
    - Report downloads
    - Audit log viewer
    """

    def __init__(
        self,
        evidence_dir: Path = Path("/var/lib/msp-compliance-agent/evidence"),
        incident_db_path: str = "/var/lib/msp-compliance-agent/incidents.db",
        hash_chain_path: Path = Path("/var/lib/msp-compliance-agent/hash-chain"),
        templates_dir: Optional[Path] = None,
        static_dir: Optional[Path] = None,
        site_id: str = "unknown",
        host_id: str = "unknown",
        windows_targets: Optional[List[Dict]] = None
    ):
        self.evidence_dir = evidence_dir
        self.incident_db_path = incident_db_path
        self.hash_chain_path = hash_chain_path
        self.site_id = site_id
        self.host_id = host_id
        self.windows_targets = windows_targets or []
        self._windows_collector = None

        # Set up paths
        module_dir = Path(__file__).parent
        self.templates_dir = templates_dir or module_dir / "web_templates"
        self.static_dir = static_dir or module_dir / "web_static"

        # Create FastAPI app
        self.app = FastAPI(
            title="MSP Compliance Dashboard",
            description="HIPAA Compliance Monitoring & Evidence Browser",
            version="1.0.0"
        )

        # Set up templates
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.static_dir.mkdir(parents=True, exist_ok=True)

        self.templates = Jinja2Templates(directory=str(self.templates_dir))

        # Mount static files
        if self.static_dir.exists():
            self.app.mount("/static", StaticFiles(directory=str(self.static_dir)), name="static")

        # Register routes
        self._register_routes()

    def _register_routes(self):
        """Register all routes."""

        # ====================================================================
        # Dashboard Routes
        # ====================================================================

        @self.app.get("/", response_class=HTMLResponse)
        async def dashboard(request: Request):
            """Main compliance dashboard."""
            status = await self._get_compliance_status()
            controls = await self._get_control_statuses()
            incidents = await self._get_incident_summary()
            flywheel = await self._get_flywheel_metrics()

            return self.templates.TemplateResponse(
                request,
                "dashboard.html",
                {
                    "site_id": self.site_id,
                    "host_id": self.host_id,
                    "status": status,
                    "controls": controls,
                    "incidents": incidents,
                    "flywheel": flywheel,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )

        @self.app.get("/api/status")
        async def api_status():
            """API: Get compliance status."""
            return await self._get_compliance_status()

        @self.app.get("/api/controls")
        async def api_controls():
            """API: Get control statuses."""
            return await self._get_control_statuses()

        @self.app.get("/api/incidents")
        async def api_incidents(hours: int = 24):
            """API: Get incident summary."""
            return await self._get_incident_summary(hours)

        @self.app.get("/api/flywheel")
        async def api_flywheel():
            """API: Get flywheel metrics."""
            return await self._get_flywheel_metrics()

        # ====================================================================
        # Evidence Browser Routes
        # ====================================================================

        @self.app.get("/evidence", response_class=HTMLResponse)
        async def evidence_browser(
            request: Request,
            page: int = 1,
            per_page: int = 20,
            check_type: Optional[str] = None,
            outcome: Optional[str] = None,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None
        ):
            """Evidence browser page."""
            bundles = await self._list_evidence(
                page=page,
                per_page=per_page,
                check_type=check_type,
                outcome=outcome,
                start_date=start_date,
                end_date=end_date
            )
            stats = await self._get_evidence_stats()

            return self.templates.TemplateResponse(
                request,
                "evidence.html",
                {
                    "bundles": bundles["items"],
                    "pagination": bundles["pagination"],
                    "stats": stats,
                    "filters": {
                        "check_type": check_type,
                        "outcome": outcome,
                        "start_date": start_date,
                        "end_date": end_date
                    }
                }
            )

        @self.app.get("/api/evidence")
        async def api_evidence(
            page: int = 1,
            per_page: int = 20,
            check_type: Optional[str] = None,
            outcome: Optional[str] = None
        ):
            """API: List evidence bundles."""
            return await self._list_evidence(page, per_page, check_type, outcome)

        @self.app.get("/api/evidence/{bundle_id}")
        async def api_evidence_detail(bundle_id: str):
            """API: Get evidence bundle details."""
            bundle = await self._get_evidence_bundle(bundle_id)
            if not bundle:
                raise HTTPException(status_code=404, detail="Bundle not found")
            return bundle

        @self.app.get("/evidence/{bundle_id}/download")
        async def download_evidence(bundle_id: str):
            """Download evidence bundle as JSON."""
            bundle_path = await self._find_evidence_path(bundle_id)
            if not bundle_path:
                raise HTTPException(status_code=404, detail="Bundle not found")
            return FileResponse(
                bundle_path,
                filename=f"{bundle_id}.json",
                media_type="application/json"
            )

        # ====================================================================
        # Reports Routes
        # ====================================================================

        @self.app.get("/reports", response_class=HTMLResponse)
        async def reports_page(request: Request):
            """Reports download page."""
            available_reports = await self._get_available_reports()
            return self.templates.TemplateResponse(
                request,
                "reports.html",
                {
                    "reports": available_reports
                }
            )

        @self.app.get("/api/reports/generate")
        async def generate_report(
            report_type: str = "daily",
            format: str = "html",
            start_date: Optional[str] = None,
            end_date: Optional[str] = None
        ):
            """Generate compliance report."""
            report = await self._generate_report(report_type, format, start_date, end_date)
            return report

        # ====================================================================
        # Audit Log Routes
        # ====================================================================

        @self.app.get("/audit", response_class=HTMLResponse)
        async def audit_log(
            request: Request,
            page: int = 1,
            per_page: int = 50,
            search: Optional[str] = None
        ):
            """Audit log viewer."""
            logs = await self._get_audit_logs(page, per_page, search)
            return self.templates.TemplateResponse(
                request,
                "audit.html",
                {
                    "logs": logs["items"],
                    "pagination": logs["pagination"],
                    "search": search
                }
            )

        @self.app.get("/api/audit")
        async def api_audit_logs(
            page: int = 1,
            per_page: int = 50,
            search: Optional[str] = None
        ):
            """API: Get audit logs."""
            return await self._get_audit_logs(page, per_page, search)

        # ====================================================================
        # Hash Chain Verification
        # ====================================================================

        @self.app.get("/api/hash-chain/status")
        async def hash_chain_status():
            """Get hash chain verification status."""
            return await self._verify_hash_chain()

        # ====================================================================
        # Health Check
        # ====================================================================

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {
                "status": "healthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "site_id": self.site_id,
                "host_id": self.host_id
            }

        # ====================================================================
        # Windows Integration Routes
        # ====================================================================

        @self.app.get("/api/windows/targets")
        async def get_windows_targets():
            """Get configured Windows targets."""
            return {
                "targets": [
                    {"hostname": t.get("hostname"), "port": t.get("port", 5985)}
                    for t in self.windows_targets
                ]
            }

        @self.app.post("/api/windows/collect")
        async def collect_windows_data():
            """Trigger Windows compliance collection."""
            if not self.windows_targets:
                raise HTTPException(status_code=400, detail="No Windows targets configured")

            try:
                from .windows_collector import WindowsCollector, WindowsTarget as WinTarget

                targets = [
                    WinTarget(
                        hostname=t["hostname"],
                        port=t.get("port", 5985),
                        username=t["username"],
                        password=t["password"],
                        use_ssl=t.get("use_ssl", False)
                    )
                    for t in self.windows_targets
                ]

                collector = WindowsCollector(
                    targets=targets,
                    incident_db_path=self.incident_db_path,
                    evidence_dir=str(self.evidence_dir),
                    site_id=self.site_id
                )

                summary = await collector.get_compliance_summary()
                return summary

            except ImportError:
                raise HTTPException(status_code=500, detail="Windows collector not available (pywinrm required)")
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/windows/health/{hostname}")
        async def check_windows_health(hostname: str):
            """Check health of a specific Windows target."""
            target_config = next(
                (t for t in self.windows_targets if t.get("hostname") == hostname),
                None
            )

            if not target_config:
                raise HTTPException(status_code=404, detail=f"Target not found: {hostname}")

            try:
                from .windows_collector import WindowsCollector, WindowsTarget as WinTarget

                target = WinTarget(
                    hostname=target_config["hostname"],
                    port=target_config.get("port", 5985),
                    username=target_config["username"],
                    password=target_config["password"],
                    use_ssl=target_config.get("use_ssl", False)
                )

                from .runbooks.windows.executor import WindowsExecutor
                executor = WindowsExecutor([target])
                health = await executor.check_target_health(target)
                return health

            except ImportError:
                raise HTTPException(status_code=500, detail="Windows executor not available (pywinrm required)")
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        # ====================================================================
        # Approval Queue Routes
        # ====================================================================

        @self.app.get("/approvals", response_class=HTMLResponse)
        async def approvals_page(request: Request):
            """Approval queue page."""
            pending = await self._get_pending_approvals()
            stats = await self._get_approval_stats()

            return self.templates.TemplateResponse(
                request,
                "approvals.html",
                {
                    "site_id": self.site_id,
                    "pending": pending,
                    "stats": stats,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )

        @self.app.get("/api/approvals")
        async def api_approvals(status: Optional[str] = None, limit: int = 100):
            """API: Get approval requests."""
            return await self._get_approvals(status=status, limit=limit)

        @self.app.get("/api/approvals/pending")
        async def api_pending_approvals():
            """API: Get pending approval requests."""
            return await self._get_pending_approvals()

        @self.app.get("/api/approvals/stats")
        async def api_approval_stats():
            """API: Get approval statistics."""
            return await self._get_approval_stats()

        @self.app.get("/api/approvals/{request_id}")
        async def api_approval_detail(request_id: str):
            """API: Get approval request details."""
            approval = await self._get_approval_detail(request_id)
            if not approval:
                raise HTTPException(status_code=404, detail="Approval request not found")
            return approval

        @self.app.post("/api/approvals/{request_id}/approve")
        async def api_approve_request(request_id: str, approved_by: str = "web_ui_user"):
            """API: Approve a pending request."""
            result = await self._approve_request(request_id, approved_by)
            if not result:
                raise HTTPException(status_code=404, detail="Request not found or not pending")
            return {"status": "approved", "request_id": request_id}

        @self.app.post("/api/approvals/{request_id}/reject")
        async def api_reject_request(request_id: str, reason: str = "Rejected via web UI", rejected_by: str = "web_ui_user"):
            """API: Reject a pending request."""
            result = await self._reject_request(request_id, rejected_by, reason)
            if not result:
                raise HTTPException(status_code=404, detail="Request not found or not pending")
            return {"status": "rejected", "request_id": request_id, "reason": reason}

        @self.app.get("/api/approvals/{request_id}/audit")
        async def api_approval_audit(request_id: str):
            """API: Get audit log for approval request."""
            return await self._get_approval_audit(request_id)

        # ====================================================================
        # Regulatory Monitoring Routes
        # ====================================================================

        @self.app.get("/regulatory", response_class=HTMLResponse)
        async def regulatory_page(request: Request):
            """Regulatory monitoring page."""
            alerts = await self._get_regulatory_alerts()
            updates = await self._get_regulatory_updates()

            return self.templates.TemplateResponse(
                request,
                "regulatory.html",
                {
                    "site_id": self.site_id,
                    "alerts": alerts,
                    "updates": updates,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )

        @self.app.get("/api/regulatory")
        async def api_regulatory():
            """API: Get regulatory monitoring data."""
            return await self._get_regulatory_alerts()

        @self.app.get("/api/regulatory/updates")
        async def api_regulatory_updates(limit: int = 10):
            """API: Get latest regulatory updates."""
            return await self._get_regulatory_updates(limit=limit)

        @self.app.get("/api/regulatory/comments")
        async def api_active_comments():
            """API: Get active comment periods."""
            return await self._get_active_comment_periods()

        @self.app.post("/api/regulatory/check")
        async def api_trigger_regulatory_check():
            """API: Trigger a regulatory check."""
            return await self._trigger_regulatory_check()

        # ====================================================================
        # Rollback Tracking Routes
        # ====================================================================

        @self.app.get("/api/rollback/stats")
        async def api_rollback_stats():
            """API: Get rollback statistics."""
            return await self._get_rollback_stats()

        @self.app.get("/api/rollback/history")
        async def api_rollback_history():
            """API: Get rollback history."""
            return await self._get_rollback_history()

        @self.app.get("/api/rollback/monitoring")
        async def api_rollback_monitoring():
            """API: Get current rule monitoring status."""
            return await self._get_rule_monitoring_status()

    # ========================================================================
    # Data Methods
    # ========================================================================

    async def _get_compliance_status(self) -> Dict[str, Any]:
        """Get overall compliance status."""
        controls = await self._get_control_statuses()

        passed = sum(1 for c in controls if c["status"] == "pass")
        failed = sum(1 for c in controls if c["status"] == "fail")
        warning = sum(1 for c in controls if c["status"] == "warn")
        total = len(controls)

        score = (passed / total * 100) if total > 0 else 0

        if failed > 0:
            status = "critical"
        elif warning > 0:
            status = "warning"
        else:
            status = "healthy"

        return {
            "status": status,
            "score": round(score, 1),
            "last_check": datetime.now(timezone.utc).isoformat(),
            "checks_passed": passed,
            "checks_failed": failed,
            "checks_warning": warning
        }

    async def _get_control_statuses(self) -> List[Dict[str, Any]]:
        """Get individual HIPAA control statuses."""
        # HIPAA controls we monitor (from CLAUDE.md)
        controls = [
            {
                "control_id": "patching",
                "name": "Critical Patch Timeliness",
                "hipaa_citation": "164.308(a)(5)(ii)(B)",
                "description": "Critical patches remediated within 7 days"
            },
            {
                "control_id": "backup",
                "name": "Backup Success & Restore Testing",
                "hipaa_citation": "164.308(a)(7)(ii)(A)",
                "description": "Successful backup in last 24h, restore test within 30 days"
            },
            {
                "control_id": "logging",
                "name": "Audit Logging",
                "hipaa_citation": "164.312(b)",
                "description": "Audit logging enabled and forwarding"
            },
            {
                "control_id": "encryption",
                "name": "Encryption at Rest",
                "hipaa_citation": "164.312(a)(2)(iv)",
                "description": "Full disk encryption enabled"
            },
            {
                "control_id": "firewall",
                "name": "Firewall Configuration",
                "hipaa_citation": "164.312(a)(1)",
                "description": "Firewall enabled with proper rules"
            },
            {
                "control_id": "av_edr",
                "name": "Antivirus/EDR Protection",
                "hipaa_citation": "164.308(a)(5)(ii)(B)",
                "description": "AV/EDR active with current definitions"
            },
            {
                "control_id": "mfa",
                "name": "MFA Coverage",
                "hipaa_citation": "164.312(a)(2)(i)",
                "description": "100% MFA for human accounts"
            },
            {
                "control_id": "access_review",
                "name": "Privileged Access Review",
                "hipaa_citation": "164.308(a)(3)(ii)(B)",
                "description": "Privileged users approved in last 90 days"
            }
        ]

        # Check evidence for each control
        for control in controls:
            evidence = await self._get_latest_evidence_for_check(control["control_id"])
            if evidence:
                control["status"] = "pass" if evidence.get("outcome") == "success" else "fail"
                control["last_checked"] = evidence.get("timestamp_end", "")
                control["evidence_count"] = await self._count_evidence_for_check(control["control_id"])
                control["auto_fixed"] = await self._count_auto_fixes_for_check(control["control_id"])
            else:
                control["status"] = "warn"
                control["last_checked"] = "Never"
                control["evidence_count"] = 0
                control["auto_fixed"] = 0

        return controls

    async def _get_incident_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get incident summary for dashboard."""
        try:
            import sqlite3
            if not Path(self.incident_db_path).exists():
                return self._empty_incident_summary()

            conn = sqlite3.connect(self.incident_db_path)
            conn.row_factory = sqlite3.Row

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

            # Total incidents
            total = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ?",
                (cutoff,)
            ).fetchone()[0]

            # By resolution level
            l1 = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ? AND resolution_level = 'L1'",
                (cutoff,)
            ).fetchone()[0]

            l2 = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ? AND resolution_level = 'L2'",
                (cutoff,)
            ).fetchone()[0]

            l3 = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ? AND resolution_level = 'L3'",
                (cutoff,)
            ).fetchone()[0]

            # Auto-resolved (L1 + L2)
            auto_resolved = l1 + l2

            # Average MTTR
            avg_mttr = conn.execute(
                "SELECT AVG(resolution_time_ms) FROM incidents WHERE created_at >= ? AND resolution_time_ms IS NOT NULL",
                (cutoff,)
            ).fetchone()[0] or 0

            conn.close()

            return {
                "total_24h": total,
                "auto_resolved": auto_resolved,
                "escalated": l3,
                "l1_handled": l1,
                "l2_handled": l2,
                "l3_handled": l3,
                "avg_mttr_seconds": avg_mttr / 1000 if avg_mttr else 0
            }
        except Exception as e:
            logger.error(f"Failed to get incident summary: {e}")
            return self._empty_incident_summary()

    def _empty_incident_summary(self) -> Dict[str, Any]:
        """Return empty incident summary."""
        return {
            "total_24h": 0,
            "auto_resolved": 0,
            "escalated": 0,
            "l1_handled": 0,
            "l2_handled": 0,
            "l3_handled": 0,
            "avg_mttr_seconds": 0
        }

    async def _get_flywheel_metrics(self) -> Dict[str, Any]:
        """Get data flywheel metrics."""
        try:
            import sqlite3
            if not Path(self.incident_db_path).exists():
                return self._empty_flywheel_metrics()

            conn = sqlite3.connect(self.incident_db_path)

            # Get 30-day stats
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

            total = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ?",
                (cutoff,)
            ).fetchone()[0] or 1  # Avoid division by zero

            l1 = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ? AND resolution_level = 'L1'",
                (cutoff,)
            ).fetchone()[0]

            l2 = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ? AND resolution_level = 'L2'",
                (cutoff,)
            ).fetchone()[0]

            l3 = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE created_at >= ? AND resolution_level = 'L3'",
                (cutoff,)
            ).fetchone()[0]

            # Pattern stats
            patterns = conn.execute(
                "SELECT COUNT(DISTINCT pattern_signature) FROM incidents"
            ).fetchone()[0]

            # Check if pattern_stats table exists
            try:
                promotion_candidates = conn.execute(
                    "SELECT COUNT(*) FROM pattern_stats WHERE promotion_eligible = 1"
                ).fetchone()[0]

                promoted = conn.execute(
                    "SELECT COUNT(*) FROM incidents WHERE promoted_to_l1 = 1"
                ).fetchone()[0]
            except:
                promotion_candidates = 0
                promoted = 0

            conn.close()

            l1_pct = (l1 / total) * 100
            l2_pct = (l2 / total) * 100
            l3_pct = (l3 / total) * 100

            # Determine flywheel status
            if l1_pct >= 70 and (l1_pct + l2_pct) >= 95:
                status = "excellent"
            elif l1_pct >= 50 and (l1_pct + l2_pct) >= 85:
                status = "good"
            elif l1_pct >= 30:
                status = "developing"
            else:
                status = "needs_attention"

            return {
                "status": status,
                "l1_percentage": round(l1_pct, 1),
                "l2_percentage": round(l2_pct, 1),
                "l3_percentage": round(l3_pct, 1),
                "patterns_tracked": patterns,
                "promotion_candidates": promotion_candidates,
                "rules_promoted": promoted
            }
        except Exception as e:
            logger.error(f"Failed to get flywheel metrics: {e}")
            return self._empty_flywheel_metrics()

    def _empty_flywheel_metrics(self) -> Dict[str, Any]:
        """Return empty flywheel metrics."""
        return {
            "status": "developing",
            "l1_percentage": 0,
            "l2_percentage": 0,
            "l3_percentage": 0,
            "patterns_tracked": 0,
            "promotion_candidates": 0,
            "rules_promoted": 0
        }

    def _get_evidence_cache(self) -> List[Dict[str, Any]]:
        """Get cached evidence bundle list, refreshing if stale."""
        # Cache for 60 seconds to avoid repeated directory scans
        cache_ttl = 60

        # Initialize cache if not exists
        if not hasattr(self, '_evidence_cache'):
            self._evidence_cache = None
            self._evidence_cache_time = 0

        current_time = datetime.now(timezone.utc).timestamp()

        # Check if cache is valid
        if (self._evidence_cache is not None and
            (current_time - self._evidence_cache_time) < cache_ttl):
            return self._evidence_cache

        # Rebuild cache
        bundles = []

        if not self.evidence_dir.exists():
            self._evidence_cache = []
            self._evidence_cache_time = current_time
            return []

        # Walk evidence directory
        for bundle_json in self.evidence_dir.rglob("bundle.json"):
            try:
                with open(bundle_json, 'r') as f:
                    data = json.load(f)

                bundles.append({
                    "bundle_id": data.get("bundle_id"),
                    "check": data.get("check"),
                    "outcome": data.get("outcome"),
                    "timestamp": data.get("timestamp_start"),
                    "hipaa_controls": data.get("hipaa_controls", []),
                    "site_id": data.get("site_id"),
                    "host_id": data.get("host_id"),
                    "_path": str(bundle_json)  # Store path for quick lookup
                })
            except Exception as e:
                logger.warning(f"Failed to load bundle {bundle_json}: {e}")

        # Sort by timestamp (newest first)
        bundles.sort(key=lambda b: b.get("timestamp", ""), reverse=True)

        self._evidence_cache = bundles
        self._evidence_cache_time = current_time

        return bundles

    def invalidate_evidence_cache(self):
        """Invalidate the evidence cache. Call after storing new bundles."""
        self._evidence_cache = None
        self._evidence_cache_time = 0

    async def _list_evidence(
        self,
        page: int = 1,
        per_page: int = 20,
        check_type: Optional[str] = None,
        outcome: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """List evidence bundles with pagination and caching."""
        # Get cached bundle list
        all_bundles = self._get_evidence_cache()

        if not all_bundles:
            return {"items": [], "pagination": {"page": 1, "per_page": per_page, "total": 0, "pages": 0}}

        # Apply filters
        bundles = []
        for data in all_bundles:
            if check_type and data.get("check") != check_type:
                continue
            if outcome and data.get("outcome") != outcome:
                continue
            if start_date:
                bundle_date = data.get("timestamp", "")
                if bundle_date < start_date:
                    continue
            if end_date:
                bundle_date = data.get("timestamp", "")
                if bundle_date > end_date:
                    continue

            # Remove internal fields from response
            bundle_data = {k: v for k, v in data.items() if not k.startswith('_')}
            bundles.append(bundle_data)

        # Paginate
        total = len(bundles)
        pages = (total + per_page - 1) // per_page if per_page > 0 else 0
        start = (page - 1) * per_page
        end = start + per_page

        return {
            "items": bundles[start:end],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages
            }
        }

    async def _get_evidence_bundle(self, bundle_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific evidence bundle using cache for path lookup."""
        # Try cache first for fast path lookup
        cached_bundles = self._get_evidence_cache()
        for cached in cached_bundles:
            if cached.get("bundle_id") == bundle_id:
                # Found in cache, load full data from file
                try:
                    with open(cached["_path"], 'r') as f:
                        return json.load(f)
                except Exception:
                    pass

        # Fallback to full scan if not in cache
        for bundle_json in self.evidence_dir.rglob("bundle.json"):
            try:
                with open(bundle_json, 'r') as f:
                    data = json.load(f)
                if data.get("bundle_id") == bundle_id:
                    return data
            except:
                continue
        return None

    async def _find_evidence_path(self, bundle_id: str) -> Optional[Path]:
        """Find path to evidence bundle using cache."""
        # Try cache first
        cached_bundles = self._get_evidence_cache()
        for cached in cached_bundles:
            if cached.get("bundle_id") == bundle_id:
                return Path(cached["_path"])

        # Fallback to full scan if not in cache
        for bundle_json in self.evidence_dir.rglob("bundle.json"):
            try:
                with open(bundle_json, 'r') as f:
                    data = json.load(f)
                if data.get("bundle_id") == bundle_id:
                    return bundle_json
            except:
                continue
        return None

    async def _get_evidence_stats(self) -> Dict[str, Any]:
        """Get evidence statistics."""
        bundles = (await self._list_evidence(per_page=10000))["items"]

        by_outcome = {}
        by_check = {}

        for b in bundles:
            outcome = b.get("outcome", "unknown")
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1

            check = b.get("check", "unknown")
            by_check[check] = by_check.get(check, 0) + 1

        return {
            "total": len(bundles),
            "by_outcome": by_outcome,
            "by_check": by_check
        }

    async def _get_latest_evidence_for_check(self, check_type: str) -> Optional[Dict[str, Any]]:
        """Get most recent evidence for a check type."""
        bundles = (await self._list_evidence(check_type=check_type, per_page=1))["items"]
        return bundles[0] if bundles else None

    async def _count_evidence_for_check(self, check_type: str) -> int:
        """Count evidence bundles for a check type."""
        return (await self._list_evidence(check_type=check_type, per_page=10000))["pagination"]["total"]

    async def _count_auto_fixes_for_check(self, check_type: str) -> int:
        """Count auto-fixed incidents for a check type."""
        try:
            import sqlite3
            if not Path(self.incident_db_path).exists():
                return 0

            conn = sqlite3.connect(self.incident_db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE check_type = ? AND resolution_level IN ('L1', 'L2')",
                (check_type,)
            ).fetchone()[0]
            conn.close()
            return count
        except:
            return 0

    async def _get_available_reports(self) -> List[Dict[str, Any]]:
        """Get list of available reports."""
        return [
            {
                "type": "daily",
                "name": "Daily Compliance Report",
                "description": "24-hour compliance summary",
                "formats": ["html", "pdf"]
            },
            {
                "type": "weekly",
                "name": "Weekly Executive Summary",
                "description": "7-day compliance trends and incidents",
                "formats": ["html", "pdf"]
            },
            {
                "type": "monthly",
                "name": "Monthly Compliance Packet",
                "description": "Full HIPAA compliance packet for auditors",
                "formats": ["html", "pdf"]
            }
        ]

    async def _generate_report(
        self,
        report_type: str,
        format: str,
        start_date: Optional[str],
        end_date: Optional[str]
    ) -> Dict[str, Any]:
        """Generate a compliance report."""
        # Calculate date range
        now = datetime.now(timezone.utc)
        if report_type == "daily":
            start = now - timedelta(days=1)
        elif report_type == "weekly":
            start = now - timedelta(days=7)
        else:  # monthly
            start = now - timedelta(days=30)

        if start_date:
            start = datetime.fromisoformat(start_date)
        if end_date:
            end = datetime.fromisoformat(end_date)
        else:
            end = now

        # Gather data
        status = await self._get_compliance_status()
        controls = await self._get_control_statuses()
        incidents = await self._get_incident_summary()
        flywheel = await self._get_flywheel_metrics()
        evidence = await self._list_evidence(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            per_page=1000
        )

        return {
            "report_type": report_type,
            "format": format,
            "generated_at": now.isoformat(),
            "period": {
                "start": start.isoformat(),
                "end": end.isoformat()
            },
            "site_id": self.site_id,
            "host_id": self.host_id,
            "compliance_status": status,
            "controls": controls,
            "incidents": incidents,
            "flywheel": flywheel,
            "evidence_count": evidence["pagination"]["total"]
        }

    async def _get_audit_logs(
        self,
        page: int = 1,
        per_page: int = 50,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get audit logs."""
        logs = []

        # Read from hash chain if available
        chain_file = self.hash_chain_path / "chain.jsonl"
        if chain_file.exists():
            with open(chain_file, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if search and search.lower() not in json.dumps(entry).lower():
                            continue
                        logs.append(entry)
                    except:
                        continue

        # Sort by timestamp (newest first)
        logs.sort(key=lambda l: l.get("timestamp", ""), reverse=True)

        # Paginate
        total = len(logs)
        pages = (total + per_page - 1) // per_page
        start = (page - 1) * per_page
        end = start + per_page

        return {
            "items": logs[start:end],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages
            }
        }

    async def _verify_hash_chain(self) -> Dict[str, Any]:
        """Verify hash chain integrity."""
        chain_file = self.hash_chain_path / "chain.jsonl"

        if not chain_file.exists():
            return {
                "status": "no_chain",
                "message": "Hash chain not initialized"
            }

        try:
            links = []
            with open(chain_file, 'r') as f:
                for line in f:
                    links.append(json.loads(line))

            if not links:
                return {
                    "status": "empty",
                    "message": "Hash chain is empty"
                }

            # Verify genesis
            if links[0].get("prev_hash") != "0" * 64:
                return {
                    "status": "tampered",
                    "message": "Invalid genesis block"
                }

            # Verify chain continuity
            for i in range(1, len(links)):
                if links[i].get("prev_hash") != links[i-1].get("hash"):
                    return {
                        "status": "tampered",
                        "message": f"Chain broken at link {i}"
                    }

            return {
                "status": "verified",
                "message": f"Chain verified: {len(links)} links",
                "total_links": len(links),
                "first_link": links[0].get("timestamp"),
                "last_link": links[-1].get("timestamp")
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }

    # ========================================================================
    # Approval Queue Methods
    # ========================================================================

    def _get_approval_manager(self):
        """Get or create ApprovalManager instance."""
        if not hasattr(self, '_approval_manager'):
            from .approval import ApprovalManager
            db_path = Path(self.incident_db_path).parent / "approvals.db"
            self._approval_manager = ApprovalManager(db_path)
        return self._approval_manager

    async def _get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Get pending approval requests."""
        try:
            manager = self._get_approval_manager()
            requests = manager.get_pending(site_id=self.site_id)
            return [r.to_dict() for r in requests]
        except Exception as e:
            logger.error(f"Failed to get pending approvals: {e}")
            return []

    async def _get_approvals(
        self,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get approval requests, optionally filtered by status."""
        try:
            import sqlite3
            manager = self._get_approval_manager()

            conn = sqlite3.connect(manager.db_path)
            conn.row_factory = sqlite3.Row

            if status:
                cursor = conn.execute(
                    'SELECT * FROM approval_requests WHERE status = ? ORDER BY created_at DESC LIMIT ?',
                    (status, limit)
                )
            else:
                cursor = conn.execute(
                    'SELECT * FROM approval_requests ORDER BY created_at DESC LIMIT ?',
                    (limit,)
                )

            rows = cursor.fetchall()
            conn.close()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get approvals: {e}")
            return []

    async def _get_approval_stats(self) -> Dict[str, Any]:
        """Get approval statistics."""
        try:
            manager = self._get_approval_manager()
            return manager.get_stats()
        except Exception as e:
            logger.error(f"Failed to get approval stats: {e}")
            return {"by_status": {}, "by_action": {}, "requests_24h": 0}

    async def _get_approval_detail(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get details for a specific approval request."""
        try:
            manager = self._get_approval_manager()
            request = manager.get_request(request_id)
            if request:
                return request.to_dict()
            return None
        except Exception as e:
            logger.error(f"Failed to get approval detail: {e}")
            return None

    async def _approve_request(self, request_id: str, approved_by: str) -> bool:
        """Approve an approval request."""
        try:
            manager = self._get_approval_manager()
            result = manager.approve(request_id, approved_by)
            return result is not None
        except Exception as e:
            logger.error(f"Failed to approve request: {e}")
            return False

    async def _reject_request(
        self,
        request_id: str,
        rejected_by: str,
        reason: str
    ) -> bool:
        """Reject an approval request."""
        try:
            manager = self._get_approval_manager()
            result = manager.reject(request_id, rejected_by, reason)
            return result is not None
        except Exception as e:
            logger.error(f"Failed to reject request: {e}")
            return False

    async def _get_approval_audit(self, request_id: str) -> List[Dict[str, Any]]:
        """Get audit log for an approval request."""
        try:
            manager = self._get_approval_manager()
            return manager.get_audit_log(request_id=request_id)
        except Exception as e:
            logger.error(f"Failed to get approval audit: {e}")
            return []

    # ========================================================================
    # Regulatory Monitoring Methods
    # ========================================================================

    def _get_federal_register_monitor(self):
        """Get or create FederalRegisterMonitor instance."""
        if not hasattr(self, '_fed_register_monitor'):
            try:
                from .regulatory.federal_register import FederalRegisterMonitor
                cache_dir = Path(self.incident_db_path).parent / "regulatory"
                self._fed_register_monitor = FederalRegisterMonitor(
                    cache_dir=str(cache_dir),
                    lookback_days=30
                )
            except ImportError:
                logger.warning("Federal Register monitor not available")
                self._fed_register_monitor = None
        return self._fed_register_monitor

    async def _get_regulatory_alerts(self) -> Dict[str, Any]:
        """Get regulatory monitoring alerts."""
        try:
            monitor = self._get_federal_register_monitor()
            if monitor is None:
                return {
                    "status": "unavailable",
                    "message": "Federal Register monitor not available",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "new_updates_count": 0,
                    "new_updates": [],
                    "active_comment_periods_count": 0,
                    "active_comment_periods": [],
                    "requires_attention": False
                }

            # Generate compliance alert
            alert = await monitor.generate_compliance_alert()
            return alert

        except Exception as e:
            logger.error(f"Failed to get regulatory alerts: {e}")
            return {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "new_updates_count": 0,
                "new_updates": [],
                "active_comment_periods_count": 0,
                "active_comment_periods": [],
                "requires_attention": False
            }

    async def _get_regulatory_updates(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get latest regulatory updates."""
        try:
            monitor = self._get_federal_register_monitor()
            if monitor is None:
                return []

            from dataclasses import asdict
            updates = await monitor.get_latest_updates(limit=limit)
            return [asdict(u) for u in updates]

        except Exception as e:
            logger.error(f"Failed to get regulatory updates: {e}")
            return []

    async def _get_active_comment_periods(self) -> List[Dict[str, Any]]:
        """Get active comment periods for proposed rules."""
        try:
            monitor = self._get_federal_register_monitor()
            if monitor is None:
                return []

            from dataclasses import asdict
            updates = await monitor.get_active_comment_periods()
            return [asdict(u) for u in updates]

        except Exception as e:
            logger.error(f"Failed to get active comment periods: {e}")
            return []

    async def _trigger_regulatory_check(self) -> Dict[str, Any]:
        """Trigger an immediate regulatory check."""
        try:
            monitor = self._get_federal_register_monitor()
            if monitor is None:
                return {
                    "status": "error",
                    "message": "Federal Register monitor not available"
                }

            # Check for updates
            updates = await monitor.check_for_updates()

            return {
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "new_updates_found": len(updates),
                "updates": [
                    {"document_number": u.document_number, "title": u.title}
                    for u in updates[:5]
                ]
            }

        except Exception as e:
            logger.error(f"Failed to trigger regulatory check: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    # ========================================================================
    # Rollback Tracking Data Methods
    # ========================================================================

    def _get_learning_system(self):
        """Get or create SelfLearningSystem instance."""
        if not hasattr(self, '_learning_system'):
            try:
                from .learning_loop import SelfLearningSystem
                from .incident_db import IncidentDatabase

                # Create incident database if needed
                incident_db = IncidentDatabase(db_path=self.incident_db_path)

                self._learning_system = SelfLearningSystem(incident_db=incident_db)
            except ImportError:
                logger.warning("SelfLearningSystem not available")
                self._learning_system = None
        return self._learning_system

    async def _get_rollback_stats(self) -> Dict[str, Any]:
        """Get rollback statistics."""
        try:
            learning_system = self._get_learning_system()
            if learning_system is None:
                return {
                    "status": "unavailable",
                    "message": "Learning system not available",
                    "rollback_rate": 0.0,
                    "total_rollbacks": 0,
                    "total_promotions": 0
                }

            # Get rollback history
            history = learning_system.get_rollback_history()
            total_rollbacks = len(history)

            # Get promotion report for total promotions
            promotion_report = learning_system.get_promotion_report()
            l1_percentage = promotion_report.get("flywheel_metrics", {}).get("l1_percentage", 0)

            # Calculate rollback rate
            monitoring_report = learning_system.monitor_promoted_rules()
            rules_monitored = monitoring_report.get("rules_monitored", 0)
            rules_degraded = monitoring_report.get("rules_degraded", 0)

            rollback_rate = (rules_degraded / rules_monitored * 100) if rules_monitored > 0 else 0.0

            return {
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rollback_rate": round(rollback_rate, 1),
                "total_rollbacks": total_rollbacks,
                "rules_monitored": rules_monitored,
                "rules_healthy": monitoring_report.get("rules_healthy", 0),
                "rules_degraded": rules_degraded,
                "l1_percentage": round(l1_percentage, 1),
                "flywheel_health": promotion_report.get("flywheel_health", "unknown")
            }

        except Exception as e:
            logger.error(f"Failed to get rollback stats: {e}")
            return {
                "status": "error",
                "message": str(e),
                "rollback_rate": 0.0,
                "total_rollbacks": 0
            }

    async def _get_rollback_history(self) -> Dict[str, Any]:
        """Get rollback history."""
        try:
            learning_system = self._get_learning_system()
            if learning_system is None:
                return {
                    "status": "unavailable",
                    "message": "Learning system not available",
                    "rollbacks": []
                }

            history = learning_system.get_rollback_history()

            return {
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "total": len(history),
                "rollbacks": history
            }

        except Exception as e:
            logger.error(f"Failed to get rollback history: {e}")
            return {
                "status": "error",
                "message": str(e),
                "rollbacks": []
            }

    async def _get_rule_monitoring_status(self) -> Dict[str, Any]:
        """Get current rule monitoring status."""
        try:
            learning_system = self._get_learning_system()
            if learning_system is None:
                return {
                    "status": "unavailable",
                    "message": "Learning system not available",
                    "rules": []
                }

            # Get monitoring report
            report = learning_system.monitor_promoted_rules()

            return {
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rules_monitored": report.get("rules_monitored", 0),
                "rules_healthy": report.get("rules_healthy", 0),
                "rules_degraded": report.get("rules_degraded", 0),
                "rollbacks_triggered": report.get("rollbacks_triggered", []),
                "rule_details": report.get("rule_details", [])
            }

        except Exception as e:
            logger.error(f"Failed to get rule monitoring status: {e}")
            return {
                "status": "error",
                "message": str(e),
                "rules": []
            }


# ============================================================================
# CLI Entry Point
# ============================================================================

def create_app(
    evidence_dir: str = "/var/lib/msp-compliance-agent/evidence",
    incident_db_path: str = "/var/lib/msp-compliance-agent/incidents.db",
    site_id: str = "unknown",
    host_id: str = "unknown"
) -> FastAPI:
    """Create FastAPI application instance."""
    ui = ComplianceWebUI(
        evidence_dir=Path(evidence_dir),
        incident_db_path=incident_db_path,
        site_id=site_id,
        host_id=host_id
    )
    return ui.app


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8080)
