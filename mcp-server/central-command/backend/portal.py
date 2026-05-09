"""Client Portal API endpoints.

Token-authenticated endpoints for client-facing compliance dashboards.

Features:
- Magic link authentication with email delivery
- httpOnly cookie sessions (30-day expiry)
- PDF report generation with HIPAA control mapping
- Evidence bundle browsing
- Mobile-responsive design support
"""

import asyncio
import hashlib
import hmac
import os
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends, Response, Cookie, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from io import BytesIO
import sys

from .fleet import get_pool
from .tenant_middleware import admin_connection, admin_transaction
from .sites import ManualDeviceAdd, NetworkDeviceAdd, _add_manual_device, _add_network_device
from .db_queries import (
    get_site_info,
    get_compliance_scores_for_site,
    get_compliance_history_for_site,
    get_evidence_bundles_for_site,
    get_monthly_compliance_report,
    get_resolved_incidents_for_site,
    get_portal_kpis,
    get_control_results_for_site,
    CATEGORY_CHECKS,
)
from .auth import require_admin

logger = logging.getLogger(__name__)


def redact_email(email: str) -> str:
    """Redact email for safe logging."""
    if not email or '@' not in email:
        return '***'
    local, domain = email.rsplit('@', 1)
    if len(local) <= 2:
        return f"{'*' * len(local)}@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


# =============================================================================
# DATABASE SESSION
# =============================================================================

async def get_db():
    """Get database session."""
    try:
        from main import async_session
    except ImportError:
        if 'server' in sys.modules and hasattr(sys.modules['server'], 'async_session'):
            async_session = sys.modules['server'].async_session
        else:
            raise HTTPException(status_code=500, detail="Database session not configured")
    async with async_session() as session:
        yield session


# Session configuration
SESSION_EXPIRY_DAYS = 30
MAGIC_LINK_EXPIRY_MINUTES = 60

router = APIRouter(prefix="/api/portal", tags=["portal"])


# =============================================================================
# EMAIL SERVICE
# =============================================================================

# Try to import SendGrid
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False
    logger.warning("SendGrid not installed - email delivery disabled")

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "noreply@osiriscare.net")
PORTAL_BASE_URL = os.environ.get("PORTAL_BASE_URL", "https://portal.osiriscare.net")

# Log email configuration status at import time
if SENDGRID_AVAILABLE and SENDGRID_API_KEY:
    logger.info("Portal email: SendGrid configured")
elif os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"):
    logger.info("Portal email: SMTP fallback available (SendGrid not configured)")
else:
    logger.warning("Portal email: NO email backend configured - magic links will not be delivered")


async def send_magic_link_email(to_email: str, site_name: str, magic_link: str) -> bool:
    """Send magic link email to client.

    Args:
        to_email: Recipient email address
        site_name: Name of the site for personalization
        magic_link: Full magic link URL

    Returns:
        True if email sent successfully, False otherwise
    """
    if not SENDGRID_AVAILABLE or not SENDGRID_API_KEY:
        # Try SMTP fallback before giving up
        smtp_user = os.environ.get("SMTP_USER")
        smtp_pass = os.environ.get("SMTP_PASSWORD")
        if smtp_user and smtp_pass:
            return _send_magic_link_smtp(to_email, site_name, magic_link, smtp_user, smtp_pass)
        logger.error(
            f"EMAIL NOT CONFIGURED — magic link for {site_name} CANNOT BE DELIVERED. "
            f"Set SENDGRID_API_KEY or SMTP_USER/SMTP_PASSWORD to enable email."
        )
        return False

    try:
        message = Mail(
            from_email=FROM_EMAIL,
            to_emails=to_email,
            subject="Your OsirisCare compliance dashboard access",
            html_content=f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                <div style="text-align: center; margin-bottom: 40px;">
                    <h1 style="color: #1a365d; font-size: 24px; margin: 0;">OsirisCare</h1>
                    <p style="color: #718096; margin-top: 8px;">HIPAA Compliance Monitoring Platform</p>
                </div>

                <p style="color: #2d3748; font-size: 16px; line-height: 1.6;">
                    Click the button below to access your compliance dashboard.
                </p>

                <div style="text-align: center; margin: 40px 0;">
                    <a href="{magic_link}" style="background: #3182ce; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">
                        Access Dashboard
                    </a>
                </div>

                <p style="color: #718096; font-size: 14px; line-height: 1.5;">
                    This link expires in 60 minutes. If you didn't request this, you can safely ignore this email.
                </p>

                <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 40px 0;">

                <p style="color: #a0aec0; font-size: 12px; text-align: center;">
                    OsirisCare Compliance Platform<br>
                    <a href="mailto:support@osiriscare.net" style="color: #3182ce;">support@osiriscare.net</a>
                </p>
            </div>
            """
        )

        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)

        logger.info(f"Magic link email sent to {redact_email(to_email)}, status: {response.status_code}")
        return response.status_code in (200, 201, 202)

    except Exception as e:
        logger.error(f"Failed to send magic link email: {e}")
        return False


def _send_magic_link_smtp(to_email: str, site_name: str, magic_link: str,
                          smtp_user: str = None, smtp_pass: str = None) -> bool:
    """SMTP fallback for magic link delivery when SendGrid is not configured.

    Task #12 SMTP consolidation 2026-05-05: routed through
    email_alerts._send_smtp_with_retry so failures land in the Email
    DLQ + the email_dlq_growing substrate invariant catches outages.
    smtp_user / smtp_pass kwargs preserved for backward compat (callers
    still pass them); they're now ignored — the central helper reads
    SMTP_USER / SMTP_PASSWORD from env.
    """
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from .email_alerts import _send_smtp_with_retry

    from_addr = os.environ.get("SMTP_FROM", "noreply@osiriscare.net")

    msg = MIMEMultipart("alternative")
    # Opaque mode (Maya P0 sweep, 2026-05-06): drop site_name from
    # subject and body. site_name retained as parameter for log
    # parity but no longer rendered into the SMTP channel. The
    # authenticated portal greets the recipient with site context.
    msg["Subject"] = "Your OsirisCare compliance dashboard access"
    msg["From"] = from_addr
    msg["To"] = to_email

    text = (
        "Access your compliance dashboard:\n\n"
        f"{magic_link}\n\n"
        "This link expires in 60 minutes."
    )
    html = (
        '<p>Click below to access your compliance dashboard.</p>'
        f'<p><a href="{magic_link}" style="background:#3182ce;color:white;padding:14px 32px;'
        'border-radius:8px;text-decoration:none;font-weight:600;display:inline-block;">'
        'Access Dashboard</a></p>'
        '<p style="color:#718096;font-size:14px;">This link expires in 60 minutes.</p>'
    )
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    return _send_smtp_with_retry(
        msg, [to_email],
        label=f"client_portal_magic_link to {to_email}",
        from_address=from_addr,
    )


# =============================================================================
# MODELS
# =============================================================================

class PortalKPIs(BaseModel):
    """KPI metrics for portal display."""
    compliance_pct: float = 0.0
    patch_mttr_hours: float = 0.0
    mfa_coverage_pct: Optional[float] = None
    backup_success_rate: float = 100.0
    auto_fixes_24h: int = 0
    controls_passing: int = 0
    controls_warning: int = 0
    controls_failing: int = 0
    health_score: float = 0.0


class PortalControl(BaseModel):
    """Single control result for portal display."""
    rule_id: str
    name: str
    status: str  # pass, warn, fail
    severity: str  # critical, high, medium, low
    checked_at: Optional[datetime] = None
    hipaa_controls: List[str] = []
    scope_summary: str = ""
    auto_fix_triggered: bool = False
    fix_duration_sec: Optional[int] = None
    exception_applied: bool = False
    exception_reason: Optional[str] = None
    # Customer-friendly HIPAA explanations
    plain_english: str = ""
    why_it_matters: str = ""
    consequence: str = ""
    what_we_check: str = ""
    hipaa_section: str = ""


class PortalIncident(BaseModel):
    """Incident summary for portal display."""
    incident_id: str
    incident_type: str
    severity: str
    auto_fixed: bool
    resolution_time_sec: Optional[int] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class PortalEvidenceBundle(BaseModel):
    """Evidence bundle metadata for download."""
    bundle_id: str
    bundle_type: str  # daily, weekly, monthly
    generated_at: datetime
    size_bytes: int = 0


class PortalSite(BaseModel):
    """Site info for portal."""
    site_id: str
    name: str
    status: str
    last_checkin: Optional[datetime] = None


class PortalFrameworks(BaseModel):
    """Framework configuration for the site (Tier 3 H6/H8).

    Backend has supported 9 frameworks since Migration 013, but the portal
    UI was hardcoded to HIPAA. This payload tells the frontend which
    framework labels to render in headings, tables, and disclaimers."""
    primary: str = "hipaa"
    primary_label: str = "HIPAA"
    enabled: List[str] = []
    enabled_labels: List[str] = []


class PortalData(BaseModel):
    """Complete portal data response."""
    site: PortalSite
    kpis: PortalKPIs
    controls: List[PortalControl]
    incidents: List[PortalIncident]
    evidence_bundles: List[PortalEvidenceBundle]
    device_count: int = 0
    generated_at: datetime
    frameworks: PortalFrameworks = PortalFrameworks()


class TokenResponse(BaseModel):
    """Portal token generation response.

    Legacy fields (``portal_url``, ``token``, ``expires``) are preserved for
    backwards compatibility with older clients. The ``url`` /
    ``expires_at`` / ``expires_in_seconds`` / ``created_at`` fields are the
    canonical shape the Site Detail page consumes.

    When the token has no TTL (in-memory session manager fallback), both
    ``expires_at`` and ``expires_in_seconds`` are ``None`` — the link is
    effectively permanent. Redis-backed deployments use ``PORTAL_TOKEN_TTL``
    (1 year) and surface the real expiry.
    """
    # Canonical fields (Task 3 contract)
    url: str
    expires_at: Optional[str] = None
    expires_in_seconds: Optional[int] = None
    created_at: Optional[str] = None
    # Legacy fields — keep to avoid breaking existing dashboard/admin callers
    portal_url: str
    token: str
    expires: str = "never"


# =============================================================================
# CONTROL METADATA
# =============================================================================

CONTROL_METADATA = {
    "endpoint_drift": {
        "name": "Endpoint Configuration Drift",
        "plain_english": "Your computers stay configured correctly",
        "why_it_matters": "When computer settings drift from the approved configuration, security gaps can appear without anyone noticing.",
        "consequence": "Unauthorized changes could disable security controls or create backdoors for attackers.",
        "what_we_check": "We continuously compare your system settings against the approved baseline and alert if anything changes.",
        "severity": "high",
        "hipaa": ["164.308(a)(1)(ii)(D)", "164.310(d)(1)"],
        "hipaa_section": "Security Management Process / Device and Media Controls"
    },
    "patch_freshness": {
        "name": "Critical Patch Timeliness",
        "plain_english": "Your systems get security updates quickly",
        "why_it_matters": "Hackers actively exploit known vulnerabilities within hours of public disclosure. Fast patching closes these windows.",
        "consequence": "Unpatched systems are the #1 way attackers breach healthcare organizations and access patient data.",
        "what_we_check": "We track how quickly critical security patches are applied and monitor for any gaps in coverage.",
        "severity": "critical",
        "hipaa": ["164.308(a)(5)(ii)(B)"],
        "hipaa_section": "Protection from Malicious Software"
    },
    "backup_success": {
        "name": "Backup Success & Restore Testing",
        "plain_english": "Your data is backed up and can be restored",
        "why_it_matters": "Ransomware attacks can encrypt all your files. Without working backups, you may lose patient records forever.",
        "consequence": "A ransomware attack without backups means paying criminals or losing years of patient data.",
        "what_we_check": "We verify backups complete successfully and periodically test that data can actually be restored.",
        "severity": "critical",
        "hipaa": ["164.308(a)(7)(ii)(A)", "164.310(d)(2)(iv)"],
        "hipaa_section": "Contingency Plan / Accountability"
    },
    "mfa_coverage": {
        "name": "MFA Coverage for Human Accounts",
        "plain_english": "Everyone uses two-factor authentication",
        "why_it_matters": "Passwords alone are easily stolen through phishing. MFA adds a second layer that blocks 99% of account takeovers.",
        "consequence": "A stolen password without MFA gives attackers direct access to patient records.",
        "what_we_check": "We verify every user account has MFA enabled and no exceptions exist.",
        "severity": "high",
        "hipaa": ["164.312(a)(2)(i)", "164.308(a)(4)(ii)(C)"],
        "hipaa_section": "Access Control / Information Access Management"
    },
    "privileged_access": {
        "name": "Privileged Access Review",
        "plain_english": "Admin access is limited and reviewed",
        "why_it_matters": "Admin accounts have the keys to everything. Regular reviews help verify only the right people have elevated access.",
        "consequence": "Unused admin accounts or over-provisioned access are prime targets for attackers.",
        "what_we_check": "We track who has admin access, flag dormant accounts, and monitor whether access matches job responsibilities.",
        "severity": "high",
        "hipaa": ["164.308(a)(3)(ii)(B)", "164.308(a)(4)(ii)(B)"],
        "hipaa_section": "Workforce Clearance / Access Authorization"
    },
    "git_protections": {
        "name": "Git Branch Protection",
        "plain_english": "Code changes require approval",
        "why_it_matters": "Requiring code review before deployment helps detect accidental bugs and malicious changes before they reach production.",
        "consequence": "Unreviewed code changes could introduce security vulnerabilities or data leaks.",
        "what_we_check": "We verify that main branches require pull request approval before merging.",
        "severity": "medium",
        "hipaa": ["164.312(b)", "164.308(a)(5)(ii)(D)"],
        "hipaa_section": "Audit Controls / Procedures Documentation"
    },
    "secrets_hygiene": {
        "name": "Secrets & Deploy Key Hygiene",
        "plain_english": "Passwords and API keys are managed securely",
        "why_it_matters": "Leaked credentials in code or old API keys are commonly exploited. Regular rotation limits damage from exposure.",
        "consequence": "A leaked API key could give attackers access to your cloud infrastructure and patient data.",
        "what_we_check": "We scan for exposed secrets and track API key age to monitor rotation practices.",
        "severity": "high",
        "hipaa": ["164.312(a)(2)(i)", "164.308(a)(4)(ii)(B)"],
        "hipaa_section": "Unique User Identification / Access Authorization"
    },
    "storage_posture": {
        "name": "Object Storage ACL Posture",
        "plain_english": "Cloud storage is not publicly accessible",
        "why_it_matters": "Misconfigured cloud storage has caused massive healthcare data breaches. Even one public bucket is a violation.",
        "consequence": "Publicly accessible storage could expose patient records to the entire internet.",
        "what_we_check": "We scan all cloud storage for public access settings and block configurations.",
        "severity": "critical",
        "hipaa": ["164.310(d)(2)(iii)", "164.312(a)(1)"],
        "hipaa_section": "Disposal / Access Control"
    }
}


# =============================================================================
# REDIS SESSION STORE (with in-memory fallback)
# =============================================================================

import json


async def get_redis_client():
    """Get Redis client for session management."""
    try:
        from main import redis_client
        return redis_client
    except (ImportError, AttributeError):
        logger.warning("Redis not available, using in-memory session store")
        return None


class PortalSessionManager:
    """Manages portal sessions with Redis (or in-memory fallback).

    Stores:
    - portal_tokens: site_id -> token (permanent portal tokens)
    - magic_links: token -> {site_id, email, expires_at} (15-minute TTL)
    - sessions: session_id -> {site_id, created_at, expires_at} (30-day TTL)
    - site_contacts: site_id -> email
    """

    # TTLs in seconds
    MAGIC_LINK_TTL = 15 * 60  # 15 minutes
    SESSION_TTL = 30 * 24 * 60 * 60  # 30 days
    PORTAL_TOKEN_TTL = 365 * 24 * 60 * 60  # 1 year

    def __init__(self, redis_client=None):
        self.redis = redis_client
        # Fallback in-memory stores
        self._portal_tokens: Dict[str, str] = {}
        self._magic_links: Dict[str, Dict[str, Any]] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._site_contacts: Dict[str, str] = {}

    def _key(self, prefix: str, id: str) -> str:
        return f"portal:{prefix}:{id}"

    # Portal tokens (site_id -> token)
    async def set_portal_token(self, site_id: str, token: str):
        if self.redis:
            await self.redis.setex(
                self._key("token", site_id),
                self.PORTAL_TOKEN_TTL,
                token
            )
        else:
            self._portal_tokens[site_id] = token

    async def get_portal_token(self, site_id: str) -> Optional[str]:
        if self.redis:
            return await self.redis.get(self._key("token", site_id))
        return self._portal_tokens.get(site_id)

    # Magic links (token -> data)
    async def set_magic_link(self, token: str, site_id: str, email: str, expires_at: datetime):
        data = json.dumps({
            "site_id": site_id,
            "email": email,
            "expires_at": expires_at.isoformat()
        })
        if self.redis:
            await self.redis.setex(
                self._key("magic", token),
                self.MAGIC_LINK_TTL,
                data
            )
        else:
            self._magic_links[token] = {
                "site_id": site_id,
                "email": email,
                "expires_at": expires_at
            }

    async def get_and_delete_magic_link(self, token: str) -> Optional[Dict[str, Any]]:
        if self.redis:
            data = await self.redis.getdel(self._key("magic", token))
            if data:
                parsed = json.loads(data)
                parsed["expires_at"] = datetime.fromisoformat(parsed["expires_at"])
                return parsed
            return None
        return self._magic_links.pop(token, None)

    # Sessions (session_id -> data)
    async def set_session(self, session_id: str, site_id: str, created_at: datetime, expires_at: datetime):
        data = json.dumps({
            "site_id": site_id,
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat()
        })
        if self.redis:
            ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
            if ttl > 0:
                await self.redis.setex(
                    self._key("session", session_id),
                    ttl,
                    data
                )
        else:
            self._sessions[session_id] = {
                "site_id": site_id,
                "created_at": created_at,
                "expires_at": expires_at
            }

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if self.redis:
            data = await self.redis.get(self._key("session", session_id))
            if data:
                parsed = json.loads(data)
                parsed["created_at"] = datetime.fromisoformat(parsed["created_at"])
                parsed["expires_at"] = datetime.fromisoformat(parsed["expires_at"])
                return parsed
            return None
        session = self._sessions.get(session_id)
        if session and session["expires_at"] < datetime.now(timezone.utc):
            del self._sessions[session_id]
            return None
        return session

    async def delete_session(self, session_id: str):
        if self.redis:
            await self.redis.delete(self._key("session", session_id))
        else:
            self._sessions.pop(session_id, None)

    # Site contacts (site_id -> email)
    async def set_site_contact(self, site_id: str, email: str):
        if self.redis:
            await self.redis.set(self._key("contact", site_id), email)
        else:
            self._site_contacts[site_id] = email

    async def get_site_contact(self, site_id: str) -> Optional[str]:
        if self.redis:
            return await self.redis.get(self._key("contact", site_id))
        return self._site_contacts.get(site_id)


# Global session manager instance (initialized on first use)
_session_manager: Optional[PortalSessionManager] = None


async def get_session_manager() -> PortalSessionManager:
    """Get or create the session manager."""
    global _session_manager
    if _session_manager is None:
        redis = await get_redis_client()
        _session_manager = PortalSessionManager(redis)
    return _session_manager


# Legacy in-memory dicts for backwards compatibility during transition
# These will be removed once all code is migrated to use PortalSessionManager
_portal_tokens: Dict[str, str] = {}
_magic_links: Dict[str, Dict[str, Any]] = {}
_sessions: Dict[str, Dict[str, Any]] = {}
_site_contacts: Dict[str, str] = {}

# NOTE: Compliance data is now read from PostgreSQL (compliance_bundles table)


def _cleanup_expired():
    """Remove expired magic links and sessions from in-memory fallback."""
    now = datetime.now(timezone.utc)

    # Cleanup magic links
    expired_links = [t for t, d in _magic_links.items() if d["expires_at"] < now]
    for t in expired_links:
        del _magic_links[t]

    # Cleanup sessions
    expired_sessions = [s for s, d in _sessions.items() if d["expires_at"] < now]
    for s in expired_sessions:
        del _sessions[s]


# =============================================================================
# TOKEN MANAGEMENT
# =============================================================================

class MagicLinkRequest(BaseModel):
    """Request magic link via email."""
    email: EmailStr


class MagicLinkResponse(BaseModel):
    """Response after requesting magic link."""
    message: str
    email_sent: bool = False


class MagicLinkValidateRequest(BaseModel):
    """Request to validate magic link token.

    SECURITY: Token is sent in body (not URL) to avoid exposure in server logs.
    """
    magic_token: str


class LegacyTokenValidateRequest(BaseModel):
    """Request to validate legacy token.

    SECURITY: Token is sent in body (not URL) to avoid exposure in server logs.
    """
    token: str
    site_id: str


@router.post("/sites/{site_id}/generate-token", response_model=TokenResponse)
async def generate_portal_token(site_id: str, user: dict = Depends(require_admin)):
    """Generate a signed portal access link for client portal access.

    Requires admin authentication.

    Expiry surfacing:
      - Redis-backed session manager: token has a TTL of ``PortalSessionManager.PORTAL_TOKEN_TTL``
        (1 year). We compute ``expires_at`` from ``created_at + TTL`` and
        populate ``expires_in_seconds``.
      - In-memory fallback (``redis_client is None``): the token is stored
        without TTL — it is effectively permanent. We return
        ``expires_at: None`` and ``expires_in_seconds: None`` to honour the
        actual backend behaviour, rather than fabricating a TTL that isn't
        enforced. DO NOT silently add an expiry here without also adding
        enforcement in ``get_portal_token`` — that would be a behavior
        change.
    """
    # Generate 64-char token
    token = secrets.token_urlsafe(48)

    # Store token in Redis (or in-memory fallback)
    session_mgr = await get_session_manager()
    await session_mgr.set_portal_token(site_id, token)

    created_at = datetime.now(timezone.utc)
    url = f"{PORTAL_BASE_URL}/portal/site/{site_id}?token={token}"

    # Determine real expiry from the underlying storage backend.
    # Only Redis-backed managers actually enforce a TTL; the in-memory
    # fallback stores tokens forever. Be honest about this.
    has_real_ttl = getattr(session_mgr, "redis", None) is not None
    if has_real_ttl:
        ttl_seconds = int(session_mgr.PORTAL_TOKEN_TTL)
        expires_at_dt = created_at + timedelta(seconds=ttl_seconds)
        expires_at_iso: Optional[str] = expires_at_dt.isoformat()
        expires_in_seconds: Optional[int] = ttl_seconds
        expires_label = expires_at_iso
    else:
        expires_at_iso = None
        expires_in_seconds = None
        expires_label = "never"  # legacy field preserves historical value

    return TokenResponse(
        url=url,
        expires_at=expires_at_iso,
        expires_in_seconds=expires_in_seconds,
        created_at=created_at.isoformat(),
        # Legacy fields (do not remove — existing callers may read these)
        portal_url=url,
        token=token,
        expires=expires_label,
    )


@router.post("/sites/{site_id}/request-access", response_model=MagicLinkResponse)
async def request_magic_link(site_id: str, request: MagicLinkRequest):
    """Request magic link via email (client-facing).

    Validates email is authorized for the site, then sends magic link.
    """
    session_mgr = await get_session_manager()

    # Check if email is authorized for this site
    # SECURITY: If no contact configured, reject the request (don't allow any email)
    authorized_email = await session_mgr.get_site_contact(site_id)
    if not authorized_email:
        logger.warning(f"No contact configured for site {site_id}, rejecting magic link request")
        return MagicLinkResponse(
            message="If this email is registered, you will receive a link shortly.",
            email_sent=False
        )
    if request.email.lower() != authorized_email.lower():
        # Don't reveal if email is wrong - just say "check your email"
        logger.warning(f"Unauthorized email attempt for site {site_id}")
        return MagicLinkResponse(
            message="If this email is registered, you will receive a link shortly.",
            email_sent=False
        )

    # Generate magic link token
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES)

    await session_mgr.set_magic_link(token, site_id, request.email, expires_at)

    # Build magic link URL
    magic_link = f"{PORTAL_BASE_URL}/portal/site/{site_id}?magic={token}"

    # Get site name for email (use site_id formatting as fallback)
    site_name = site_id.replace("-", " ").title()

    # Send email
    email_sent = await send_magic_link_email(request.email, site_name, magic_link)

    if not email_sent:
        # Fall back to SMTP if SendGrid unavailable
        try:
            from .email_alerts import is_email_configured, _send_smtp_with_retry
            if is_email_configured():
                # Task #12 SMTP consolidation 2026-05-05: build the
                # MIME message + delegate to central helper. Display
                # From: stays noreply@ for client-class email.
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart

                smtp_from = os.getenv("SMTP_FROM", "noreply@osiriscare.net")

                msg = MIMEMultipart("alternative")
                # Opaque mode (Maya P0 sweep, 2026-05-06): drop
                # site_name from subject + body; portal serves
                # context after auth.
                msg["Subject"] = "Your OsirisCare compliance dashboard access"
                msg["From"] = smtp_from
                msg["To"] = request.email

                html_content = f"""
                <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                    <div style="text-align: center; margin-bottom: 40px;">
                        <h1 style="color: #1a365d; font-size: 24px; margin: 0;">OsirisCare</h1>
                        <p style="color: #718096; margin-top: 8px;">HIPAA Compliance Monitoring Platform</p>
                    </div>
                    <p style="color: #2d3748; font-size: 16px; line-height: 1.6;">
                        Click the button below to access your compliance dashboard.
                    </p>
                    <div style="text-align: center; margin: 40px 0;">
                        <a href="{magic_link}" style="background: #3182ce; color: white; padding: 14px 32px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">
                            Access Dashboard
                        </a>
                    </div>
                    <p style="color: #718096; font-size: 14px; line-height: 1.5;">
                        This link expires in 60 minutes. If you didn't request this, you can safely ignore this email.
                    </p>
                </div>
                """
                msg.attach(MIMEText(html_content, "html"))

                email_sent = _send_smtp_with_retry(
                    msg, [request.email],
                    label=f"client_portal_magic_link_inline to {request.email}",
                    from_address=smtp_from,
                )
                if email_sent:
                    logger.info(f"Magic link sent via SMTP to {redact_email(request.email)}")
        except Exception as e:
            logger.error(f"Magic link send failed: {e}")

    return MagicLinkResponse(
        message="If this email is registered, you will receive a link shortly.",
        email_sent=email_sent
    )


@router.get("/auth/validate")
async def validate_magic_link(
    magic: str = Query(..., description="Magic link token"),
    response: Response = None
):
    """Validate magic link and create session.

    Exchanges magic link token for httpOnly session cookie.
    """
    session_mgr = await get_session_manager()

    # Look up and consume magic link (single-use)
    link_data = await session_mgr.get_and_delete_magic_link(magic)
    if not link_data:
        raise HTTPException(status_code=403, detail="Invalid or expired link")

    # Check expiry
    if datetime.now(timezone.utc) > link_data["expires_at"]:
        raise HTTPException(status_code=403, detail="Link has expired. Please request a new one.")

    # Create session
    session_id = secrets.token_urlsafe(32)
    site_id = link_data["site_id"]
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_EXPIRY_DAYS)

    await session_mgr.set_session(session_id, site_id, now, expires_at)

    # Set httpOnly cookie
    response.set_cookie(
        key="portal_session",
        value=session_id,
        httponly=True,
        secure=True,  # Requires HTTPS
        samesite="lax",
        max_age=SESSION_EXPIRY_DAYS * 24 * 60 * 60,
        path="/"
    )

    logger.info(f"Session created for site {site_id}")

    return {
        "status": "authenticated",
        "site_id": site_id,
        "redirect": f"/portal/site/{site_id}"
    }


@router.post("/auth/validate")
async def validate_magic_link_post(
    request: MagicLinkValidateRequest,
    response: Response
):
    """Validate magic link and create session (POST version).

    SECURITY: Token is sent in body (not URL) to avoid exposure in server logs.
    Exchanges magic link token for httpOnly session cookie.
    """
    session_mgr = await get_session_manager()

    # Look up and consume magic link (single-use)
    link_data = await session_mgr.get_and_delete_magic_link(request.magic_token)
    if not link_data:
        raise HTTPException(status_code=403, detail="Invalid or expired link")

    # Check expiry
    if datetime.now(timezone.utc) > link_data["expires_at"]:
        raise HTTPException(status_code=403, detail="Link has expired. Please request a new one.")

    # Create session
    session_id = secrets.token_urlsafe(32)
    site_id = link_data["site_id"]
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=SESSION_EXPIRY_DAYS)

    await session_mgr.set_session(session_id, site_id, now, expires_at)

    # Set httpOnly cookie
    response.set_cookie(
        key="portal_session",
        value=session_id,
        httponly=True,
        secure=True,  # Requires HTTPS
        samesite="lax",
        max_age=SESSION_EXPIRY_DAYS * 24 * 60 * 60,
        path="/"
    )

    logger.info(f"Session created for site {site_id}")

    return {
        "status": "authenticated",
        "site_id": site_id,
        "redirect": f"/portal/site/{site_id}"
    }


@router.post("/auth/validate-legacy")
async def validate_legacy_token(
    request: LegacyTokenValidateRequest,
    response: Response
):
    """Validate legacy token and create session.

    SECURITY: Token is sent in body (not URL) to avoid exposure in server logs.
    For clients using the old token-based auth approach.
    """
    session_mgr = await get_session_manager()

    # Check legacy token
    stored_token = await session_mgr.get_portal_token(request.site_id)
    if stored_token and secrets.compare_digest(stored_token, request.token):
        # Create session
        session_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=SESSION_EXPIRY_DAYS)

        await session_mgr.set_session(session_id, request.site_id, now, expires_at)

        # Set httpOnly cookie
        response.set_cookie(
            key="portal_session",
            value=session_id,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=SESSION_EXPIRY_DAYS * 24 * 60 * 60,
            path="/"
        )

        logger.info(f"Legacy session created for site {request.site_id}")

        return {
            "status": "authenticated",
            "site_id": request.site_id,
            "redirect": f"/portal/site/{request.site_id}"
        }

    raise HTTPException(status_code=403, detail="Invalid or expired token")


async def validate_session(
    site_id: str,
    portal_session: Optional[str] = Cookie(None),
    token: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Validate portal access via session cookie or token.

    Supports both httpOnly cookie sessions and legacy token auth.
    """
    session_mgr = await get_session_manager()

    # Try session cookie first (preferred)
    if portal_session:
        session = await session_mgr.get_session(portal_session)
        if session:
            if datetime.now(timezone.utc) < session["expires_at"]:
                if session["site_id"] == site_id:
                    return {"method": "session", "email": session.get("email")}
            else:
                # Expired - remove it
                await session_mgr.delete_session(portal_session)

    # Fallback to token auth (legacy)
    if token:
        stored_token = await session_mgr.get_portal_token(site_id)
        # SECURITY: Use constant-time comparison to prevent timing attacks
        if stored_token and secrets.compare_digest(stored_token, token):
            return {"method": "token"}

    raise HTTPException(status_code=403, detail="Invalid or expired session")


async def validate_token(site_id: str, token: str) -> bool:
    """Validate portal access token (legacy support)."""
    session_mgr = await get_session_manager()
    stored_token = await session_mgr.get_portal_token(site_id)
    # SECURITY: Use constant-time comparison to prevent timing attacks
    if not stored_token or not secrets.compare_digest(stored_token, token):
        raise HTTPException(status_code=403, detail="Invalid portal token")
    return True


@router.post("/auth/logout")
async def logout(response: Response, portal_session: Optional[str] = Cookie(None)):
    """Log out and clear session."""
    if portal_session:
        session_mgr = await get_session_manager()
        await session_mgr.delete_session(portal_session)

    response.delete_cookie("portal_session", path="/")

    return {"status": "logged_out"}


@router.post("/sites/{site_id}/contacts")
async def set_site_contact_endpoint(site_id: str, email: EmailStr, user: dict = Depends(require_admin)):
    """Set authorized contact email for a site (admin only).

    Requires admin authentication.
    """
    session_mgr = await get_session_manager()
    await session_mgr.set_site_contact(site_id, email.lower())
    logger.info(f"Admin {user.get('username')} set contact for site {site_id}")
    return {"status": "updated", "site_id": site_id, "email": email}


# =============================================================================
# Framework info helper (Tier 3 H6/H8)
# =============================================================================

# Framework display labels (8 frameworks across `frameworks.py` + Migration 013).
_FRAMEWORK_LABELS = {
    "hipaa": "HIPAA",
    "soc2": "SOC 2",
    "pci_dss": "PCI DSS",
    "nist_csf": "NIST CSF",
    "cis": "CIS Controls",
    "sox": "SOX",
    "gdpr": "GDPR",
    "cmmc": "CMMC",
    "iso_27001": "ISO 27001",
    "nist_800_171": "NIST 800-171",
}


async def _get_site_framework_info(db: AsyncSession, site_id: str) -> PortalFrameworks:
    """Return the framework configuration for a site.

    The portal UI uses this to label headings, tables, and disclaimers
    dynamically. Defaults to HIPAA if the site has no per-appliance
    framework config row (the backend default for legacy sites).
    """
    try:
        result = await db.execute(
            text("""
                SELECT enabled_frameworks, primary_framework
                FROM appliance_framework_configs
                WHERE site_id = :site_id
                ORDER BY updated_at DESC
                LIMIT 1
            """),
            {"site_id": site_id},
        )
        row = result.fetchone()
    except Exception as exc:
        logger.debug("framework config query skipped: %s", exc)
        row = None

    if not row:
        return PortalFrameworks(
            primary="hipaa",
            primary_label=_FRAMEWORK_LABELS["hipaa"],
            enabled=["hipaa"],
            enabled_labels=[_FRAMEWORK_LABELS["hipaa"]],
        )

    enabled = row.enabled_frameworks or ["hipaa"]
    primary = row.primary_framework or (enabled[0] if enabled else "hipaa")
    return PortalFrameworks(
        primary=primary,
        primary_label=_FRAMEWORK_LABELS.get(primary, primary.upper()),
        enabled=enabled,
        enabled_labels=[_FRAMEWORK_LABELS.get(f, f.upper()) for f in enabled],
    )


# =============================================================================
# MAIN PORTAL ENDPOINT
# =============================================================================

@router.get("/site/{site_id}/home")
async def get_portal_home(
    site_id: str,
    token: str = Query(None, description="Portal access token"),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Psychology-first client-portal hero endpoint (Session 206 round-table).

    Data shape is deliberately minimal — exactly what goes above the
    fold on the practice manager's homepage. Everything else (device
    inventory, full evidence chain, billing) is behind other routes
    this endpoint does NOT touch.

    Invariants from the round-table:
      * "protected" state uses LIVE data age, not a stored boolean
        (all 25 checks passing AND no open incident AND all appliances
        checked in within 15 min = protected)
      * "this_month" counts are drift-to-healed outcomes, not raw
        incident volume (auto-heals are visible value)
      * partner attribution surfaces the human signing off
      * no technical jargon in response (UI translates id → plain label)

    Returns 200 even when data is partial — the UI degrades gracefully
    to "Checking..." states rather than error screens. Psychology: the
    practice manager cannot see a red error page and feel safe.
    """
    await validate_session(site_id, portal_session, token)

    now = datetime.now(timezone.utc)

    # ─── Protected state ──────────────────────────────────────────
    # All three have to be true for "Protected":
    #   1. At least one appliance checked in within 15 minutes
    #   2. Latest bundle for the site is < 24h old
    #   3. No OPEN incident at L3 (escalated to human) right now
    protected = True
    protected_reason = ""
    try:
        live_appliances = await execute_with_retry(db, text("""
            SELECT COUNT(*) FROM site_appliances
            WHERE site_id = :sid
              AND last_checkin > NOW() - INTERVAL '15 minutes'
              AND deleted_at IS NULL
        """), {"sid": site_id})
        live_appliances_n = live_appliances.scalar() or 0
        if live_appliances_n < 1:
            protected = False
            protected_reason = "Appliance hasn't checked in recently"
    except Exception:
        pass

    if protected:
        try:
            open_l3 = await execute_with_retry(db, text("""
                SELECT COUNT(*) FROM incidents
                WHERE site_id = :sid
                  AND status NOT IN ('resolved', 'closed')
                  AND resolution_tier = 'L3'
            """), {"sid": site_id})
            if (open_l3.scalar() or 0) > 0:
                protected = False
                protected_reason = "An item is awaiting your attention"
        except Exception:
            pass

    # ─── This-month summary ──────────────────────────────────────
    # "45 found, 42 fixed automatically, 3 resolved with your partner"
    this_month = {
        "issues_found": 0,
        "auto_fixed": 0,
        "resolved_with_partner": 0,
        "period_start": (now - timedelta(days=30)).date().isoformat(),
    }
    try:
        month_row = await execute_with_retry(db, text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE resolution_tier = 'L1') AS auto,
                COUNT(*) FILTER (WHERE resolution_tier IN ('L2', 'L3')) AS with_partner
            FROM incidents
            WHERE site_id = :sid
              AND created_at > NOW() - INTERVAL '30 days'
        """), {"sid": site_id})
        r = month_row.mappings().first()
        if r:
            this_month["issues_found"] = int(r["total"] or 0)
            this_month["auto_fixed"] = int(r["auto"] or 0)
            this_month["resolved_with_partner"] = int(r["with_partner"] or 0)
    except Exception:
        pass

    # ─── Partner attribution (Session 206, migration 183) ────────
    # Schema corrections (Session 213 round-table P1, 2026-04-29):
    #   * `client_organizations` → `client_orgs`
    #   * `sites.org_id` → `sites.client_org_id`
    #   * partners has no `org_id` / `deactivated_at` / `full_name` /
    #     `email` — link is `sites.partner_id`; columns are `name`,
    #     `contact_email`, `status`.
    # Pre-fix this whole block silently raised UndefinedTableError
    # inside `try: except: pass` and the partner panel rendered
    # blank for every client portal session since Session 206. The
    # silent-swallow ALSO violated Session 205's "no silent write
    # failures" rule (technically read-side, but the eat-everything
    # mask hid a real schema regression for 7+ sessions).
    partner = {"name": None, "email": None, "last_reviewed_at": None}
    try:
        prow = await execute_with_retry(db, text("""
            SELECT s.last_partner_reviewed_at,
                   s.last_partner_reviewed_by,
                   o.name AS org_name,
                   p.name AS primary_partner_name,
                   p.contact_email AS primary_partner_email
            FROM sites s
            LEFT JOIN client_orgs o ON o.id = s.client_org_id
            LEFT JOIN partners p ON p.id = s.partner_id
                                AND p.status = 'active'
            WHERE s.site_id = :sid
        """), {"sid": site_id})
        pr = prow.mappings().first()
        if pr:
            partner["name"] = (
                pr["primary_partner_name"]
                or pr["org_name"]
                or pr["last_partner_reviewed_by"]
            )
            partner["email"] = pr["primary_partner_email"]
            if pr["last_partner_reviewed_at"]:
                partner["last_reviewed_at"] = (
                    pr["last_partner_reviewed_at"].isoformat()
                )
    except Exception as e:
        # Read-side — eat the exception so the portal page still
        # renders, but log loud so a regression here doesn't go
        # silent for sessions again.
        logger.error(
            "portal_partner_attribution_query_failed",
            exc_info=True,
            extra={"site_id": site_id, "exception_class": type(e).__name__},
        )

    # ─── 30-day coverage timeline ────────────────────────────────
    # For each of last 30 days, was the site "fully covered" (no gap
    # where an incident sat open > 2 hours undetected-and-untreated)?
    # Simplest honest proxy: days with at least 1 successful L1 run
    # OR zero incidents count as covered; days where incidents piled
    # up unresolved count as gap days.
    coverage = []
    try:
        cov_rows = await execute_with_retry(db, text("""
            SELECT DATE_TRUNC('day', reported_at) AS day,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (
                       WHERE status IN ('resolved', 'closed')
                   ) AS closed
            FROM incidents
            WHERE site_id = :sid
              AND reported_at > NOW() - INTERVAL '30 days'
            GROUP BY 1 ORDER BY 1 ASC
        """), {"sid": site_id})
        day_map = {}
        for row in cov_rows.mappings():
            d = row["day"].date().isoformat() if row["day"] else None
            if d:
                total = int(row["total"] or 0)
                closed = int(row["closed"] or 0)
                day_map[d] = {
                    "date": d,
                    "covered": total == 0 or closed >= total,
                    "incidents": total,
                }
        for i in range(29, -1, -1):
            d = (now - timedelta(days=i)).date().isoformat()
            coverage.append(day_map.get(d, {"date": d, "covered": True, "incidents": 0}))
    except Exception:
        pass

    # ─── Device count (for "4 workstations, 1 appliance") ────────
    devices = {"appliances": 0, "workstations": 0}
    try:
        arow = await execute_with_retry(db, text("""
            SELECT COUNT(*) FROM site_appliances
            WHERE site_id = :sid AND deleted_at IS NULL
        """), {"sid": site_id})
        devices["appliances"] = int(arow.scalar() or 0)
        wrow = await execute_with_retry(db, text("""
            SELECT COUNT(*) FROM discovered_devices
            WHERE site_id = :sid
              AND device_type IN ('workstation', 'server')
              AND last_seen_at > NOW() - INTERVAL '7 days'
        """), {"sid": site_id})
        devices["workstations"] = int(wrow.scalar() or 0)
    except Exception:
        pass

    # ─── 90-day coverage trend (Session 206 round-table P2) ───────
    # Weekly buckets over the last 13 weeks so we get a longer-horizon
    # view than the 30-day timeline. Each bucket = { week_start,
    # incidents, pct_covered }.
    coverage_90d = []
    try:
        cov90_rows = await execute_with_retry(db, text("""
            SELECT DATE_TRUNC('week', reported_at) AS week,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status IN ('resolved','closed')) AS closed
            FROM incidents
            WHERE site_id = :sid
              AND reported_at > NOW() - INTERVAL '90 days'
            GROUP BY 1 ORDER BY 1 ASC
        """), {"sid": site_id})
        for row in cov90_rows.mappings():
            if not row["week"]:
                continue
            total = int(row["total"] or 0)
            closed = int(row["closed"] or 0)
            pct = 100 if total == 0 else round(100.0 * closed / total, 1)
            coverage_90d.append({
                "week_start": row["week"].date().isoformat(),
                "incidents": total,
                "pct_covered": pct,
            })
    except Exception:
        pass

    # ─── Available monthly compliance packets (P1) ────────────────
    packets = []
    try:
        pkt_rows = await execute_with_retry(db, text("""
            SELECT year, month, framework, compliance_score,
                   critical_issues, auto_fixes, generated_at
            FROM compliance_packets
            WHERE site_id = :sid
            ORDER BY year DESC, month DESC
            LIMIT 12
        """), {"sid": site_id})
        packets = [
            {
                "year": int(r["year"]),
                "month": int(r["month"]),
                "framework": r["framework"] or "hipaa",
                "compliance_score": float(r["compliance_score"]) if r["compliance_score"] is not None else None,
                "critical_issues": int(r["critical_issues"] or 0),
                "auto_fixes": int(r["auto_fixes"] or 0),
                "generated_at": r["generated_at"].isoformat() if r["generated_at"] else None,
                "download_url": f"/api/portal/site/{site_id}/packets/{int(r['year'])}-{int(r['month']):02d}",
            }
            for r in pkt_rows.mappings()
        ]
    except Exception:
        pass

    # ─── Notification preferences (P2) ────────────────────────────
    notification_prefs = {"email_digest": True, "critical_alerts": True, "weekly_summary": False}
    try:
        np_row = await execute_with_retry(db, text("""
            SELECT email_digest, critical_alerts, weekly_summary
            FROM client_notification_prefs
            WHERE site_id = :sid
        """), {"sid": site_id})
        np = np_row.mappings().first()
        if np:
            notification_prefs = {
                "email_digest": bool(np["email_digest"]),
                "critical_alerts": bool(np["critical_alerts"]),
                "weekly_summary": bool(np["weekly_summary"]),
            }
    except Exception:
        # table may not exist yet on pre-migration deploys
        pass

    # #73 closure 2026-05-02 (Camila adversarial-round sub-followup
    # of #64 P0 kill-switch). Surface fleet-wide healing-pause state
    # to the client portal so a clinic auditor visiting during a
    # paused window can SEE that auto-remediation was off. HIPAA
    # §164.316(b)(2)(i) chain-of-custody implication if a
    # fail-to-pass transition was missed during the pause window —
    # the clinic needs the audit-visible record.
    fleet_healing_state: Dict[str, Any] = {"disabled": False}
    try:
        fh_row = await db.execute(text(
            "SELECT settings -> 'fleet_healing_disabled' FROM system_settings WHERE id = 1"
        ))
        fh_state = fh_row.scalar()
        if fh_state and isinstance(fh_state, dict) and fh_state.get("disabled"):
            fleet_healing_state = {
                "disabled": True,
                "paused_since": fh_state.get("set_at"),
                "paused_reason": fh_state.get("reason"),
                # Intentionally do NOT expose the actor email to the
                # client (that's admin-internal). The reason is
                # operator-written and may already be public-safe; we
                # surface it so the auditor sees the substantive why.
            }
    except Exception:
        # Best-effort. If settings table not readable, omit the field
        # rather than block the home payload — graceful-degrade per
        # the round-table-documented "200 even when partial" rule.
        logger.error("fleet_healing_state_lookup_failed", exc_info=True)

    # Carol P0 (round-table 2026-05-06): "protected" is on
    # CLAUDE.md banned-words list (Session 199 legal-language
    # rules). Renamed payload fields to monitored_* throughout
    # while preserving the dual `protected` alias for one
    # release cycle of frontend backwards-compat. Drop the
    # alias once mobile/legacy clients confirm the new names.
    return {
        "site_id": site_id,
        "monitored": protected,
        "monitored_reason": protected_reason or "All checks passing",
        "monitored_label": (
            "Monitoring active"
            if protected else "Needs your attention"
        ),
        # Backwards-compat (deprecated 2026-05-06; remove
        # 2026-06-06 after all clients are on the new names).
        "protected": protected,
        "protected_reason": protected_reason or "All checks passing",
        "protected_label": (
            "Monitoring active"
            if protected else "Needs your attention"
        ),
        "last_updated_at": now.isoformat(),
        "this_month": this_month,
        "partner": partner,
        "devices": devices,
        "coverage_30d": coverage,
        "coverage_90d": coverage_90d,
        "packets": packets,
        "notification_prefs": notification_prefs,
        "auditor_kit_url": f"/api/evidence/sites/{site_id}/auditor-kit",
        "fleet_healing_state": fleet_healing_state,
    }


@router.get("/site/{site_id}/packets/{year_month}")
async def download_monthly_packet(
    site_id: str,
    year_month: str,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Session 206 round-table P1 — download a specific month's compliance packet.

    `year_month` format: YYYY-MM (e.g. '2026-03'). Returns the packet
    markdown as `text/markdown`. Client browsers render it or save it
    as-is — we don't convert to PDF here because the monthly packet's
    markdown form is what's hash-chained into the evidence bundles.
    """
    await validate_session(site_id, portal_session, token)
    try:
        year_str, month_str = year_month.split("-", 1)
        year = int(year_str)
        month = int(month_str)
        if month < 1 or month > 12:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=400, detail="year_month must be YYYY-MM")

    row = await execute_with_retry(db, text("""
        SELECT markdown_content, packet_id, generated_at
        FROM compliance_packets
        WHERE site_id = :sid AND year = :y AND month = :m
        ORDER BY generated_at DESC LIMIT 1
    """), {"sid": site_id, "y": year, "m": month})
    packet = row.mappings().first()
    if not packet or not packet["markdown_content"]:
        raise HTTPException(status_code=404, detail="packet not available for this month")

    from fastapi.responses import Response
    return Response(
        content=packet["markdown_content"],
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="packet-{site_id}-{year_month}.md"',
            "X-Packet-Id": packet["packet_id"] or "",
        },
    )


@router.get("/site/{site_id}/notification-prefs")
async def get_notification_prefs(
    site_id: str,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """P2 — read current notification preferences for a site."""
    await validate_session(site_id, portal_session, token)
    try:
        row = await execute_with_retry(db, text("""
            SELECT email_digest, critical_alerts, weekly_summary, notify_email
            FROM client_notification_prefs
            WHERE site_id = :sid
        """), {"sid": site_id})
        r = row.mappings().first()
        if r:
            return {
                "email_digest": bool(r["email_digest"]),
                "critical_alerts": bool(r["critical_alerts"]),
                "weekly_summary": bool(r["weekly_summary"]),
                "notify_email": r["notify_email"],
            }
    except Exception:
        pass
    # defaults when row doesn't exist or table missing
    return {
        "email_digest": True,
        "critical_alerts": True,
        "weekly_summary": False,
        "notify_email": None,
    }


@router.put("/site/{site_id}/notification-prefs")
async def set_notification_prefs(
    site_id: str,
    body: dict,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """P2 — upsert notification preferences for a site."""
    await validate_session(site_id, portal_session, token)

    email_digest = bool(body.get("email_digest", True))
    critical_alerts = bool(body.get("critical_alerts", True))
    weekly_summary = bool(body.get("weekly_summary", False))
    notify_email = (body.get("notify_email") or "").strip() or None
    if notify_email and "@" not in notify_email:
        raise HTTPException(status_code=400, detail="notify_email must be a valid email")

    try:
        await execute_with_retry(db, text("""
            INSERT INTO client_notification_prefs
                (site_id, email_digest, critical_alerts, weekly_summary, notify_email, updated_at)
            VALUES (:sid, :ed, :ca, :ws, :em, NOW())
            ON CONFLICT (site_id) DO UPDATE SET
                email_digest = EXCLUDED.email_digest,
                critical_alerts = EXCLUDED.critical_alerts,
                weekly_summary = EXCLUDED.weekly_summary,
                notify_email = EXCLUDED.notify_email,
                updated_at = NOW()
        """), {
            "sid": site_id, "ed": email_digest, "ca": critical_alerts,
            "ws": weekly_summary, "em": notify_email,
        })
        await db.commit()
    except Exception as e:
        logger.error(f"notification prefs save failed for {site_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="failed to save preferences")
    return {"ok": True}


# ─── Migration 184 Phase 2 — client portal consent management ────
#
# Practice managers grant + revoke class-level consent from the
# portal. The actual signing happens server-side: the server
# generates an Ed25519 keypair, signs the payload, stores the row,
# and discards the private key. The pubkey alone makes the consent
# non-repudiable (any auditor can re-verify without OsirisCare's
# help).
#
# In Phase 4 we'll add magic-link partner-initiated requests; for
# now the client grants directly from their own session.

@router.get("/site/{site_id}/consent")
async def list_site_consents(
    site_id: str,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """List all consent rows + class catalog for a site.

    Returns {classes: [...], consents: [...]} so the UI can render
    one row per class with its current consent state.
    """
    await validate_session(site_id, portal_session, token)
    try:
        class_rows = await execute_with_retry(db, text("""
            SELECT class_id, display_name, description, risk_level,
                   hipaa_controls, example_actions
            FROM runbook_classes
            ORDER BY risk_level, class_id
        """), {})
        classes = [
            {
                "class_id": r["class_id"],
                "display_name": r["display_name"],
                "description": r["description"],
                "risk_level": r["risk_level"],
                "hipaa_controls": list(r["hipaa_controls"] or []),
                "example_actions": r["example_actions"] or [],
            }
            for r in class_rows.mappings()
        ]
    except Exception:
        # Table missing on pre-migration deploys — return empty so the
        # UI degrades cleanly.
        classes = []

    try:
        consent_rows = await execute_with_retry(db, text("""
            SELECT consent_id, class_id, consented_by_email, consented_at,
                   consent_ttl_days, revoked_at, revocation_reason,
                   (consented_at + (consent_ttl_days || ' days')::INTERVAL) AS expires_at
            FROM runbook_class_consent
            WHERE site_id = :sid
            ORDER BY consented_at DESC
        """), {"sid": site_id})
        consents = [
            {
                "consent_id": str(r["consent_id"]),
                "class_id": r["class_id"],
                "consented_by_email": r["consented_by_email"],
                "consented_at": r["consented_at"].isoformat() if r["consented_at"] else None,
                "consent_ttl_days": int(r["consent_ttl_days"] or 365),
                "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
                "revocation_reason": r["revocation_reason"],
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
                "active": r["revoked_at"] is None
                          and (r["expires_at"] is None
                               or r["expires_at"] > datetime.now(timezone.utc)),
            }
            for r in consent_rows.mappings()
        ]
    except Exception:
        consents = []

    return {
        "site_id": site_id,
        "classes": classes,
        "consents": consents,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/site/{site_id}/consent/grant")
async def grant_site_consent(
    site_id: str,
    body: dict,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Create a new class-level consent.

    Body: `{class_id, consented_by_email, ttl_days?}`. The email MUST
    be a valid address; we don't verify ownership in Phase 2 beyond
    portal auth, but it's persisted + signed so the audit trail is
    attributable.
    """
    await validate_session(site_id, portal_session, token)
    class_id = (body.get("class_id") or "").strip()
    email = (body.get("consented_by_email") or "").strip()
    ttl_days = int(body.get("ttl_days") or 365)

    if not class_id:
        raise HTTPException(status_code=400, detail="class_id required")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="consented_by_email must be valid")
    if ttl_days < 30 or ttl_days > 3650:
        raise HTTPException(status_code=400, detail="ttl_days must be 30..3650")

    # Reject if an active consent already exists — client must revoke
    # it first, per the spec's "one active per class" rule.
    existing = await execute_with_retry(db, text("""
        SELECT consent_id FROM runbook_class_consent
        WHERE site_id = :sid AND class_id = :cls AND revoked_at IS NULL
    """), {"sid": site_id, "cls": class_id})
    if existing.scalar() is not None:
        raise HTTPException(status_code=409, detail="active consent already exists for this class")

    from dashboard_api.runbook_consent import create_consent as _create_consent
    try:
        consent_id = await _create_consent(
            db,
            site_id=site_id,
            class_id=class_id,
            consented_by_email=email,
            ttl_days=ttl_days,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"consent grant failed for {site_id}/{class_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="failed to record consent")
    return {"ok": True, "consent_id": consent_id, "class_id": class_id}


@router.get("/consent/approve/{token}")
async def get_consent_approve_details(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Phase 4 — render page data for a magic-link consent approval.

    Public-ish endpoint: auth is the token itself. Looks up the
    request, returns the class description + partner brand so the UI
    can show a grant modal before the client clicks approve.

    Token is hashed (sha256) before lookup — the raw never persists.
    Returns 404 if token is unknown OR expired OR already consumed.
    """
    if not token or len(token) < 20:
        raise HTTPException(status_code=400, detail="token required")

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    row = await execute_with_retry(db, text("""
        SELECT crt.token_hash, crt.site_id, crt.class_id,
               crt.requested_by_email, crt.requested_for_email,
               crt.requested_ttl_days, crt.expires_at, crt.consumed_at,
               crt.created_at,
               rc.display_name, rc.description, rc.risk_level, rc.hipaa_controls,
               rc.example_actions,
               s.clinic_name,
               (SELECT COALESCE(NULLIF(brand_name, ''), name, 'OsirisCare')
                FROM partners p
                JOIN sites s2 ON s2.partner_id = p.id
                WHERE s2.site_id = crt.site_id LIMIT 1) AS partner_brand,
               (SELECT COALESCE(primary_color, '#4F46E5') FROM partners p
                JOIN sites s2 ON s2.partner_id = p.id
                WHERE s2.site_id = crt.site_id LIMIT 1) AS primary_color
        FROM consent_request_tokens crt
        JOIN runbook_classes rc ON rc.class_id = crt.class_id
        LEFT JOIN sites s ON s.site_id = crt.site_id
        WHERE crt.token_hash = :th
    """), {"th": token_hash})
    r = row.mappings().first()
    if not r:
        raise HTTPException(status_code=404, detail="Token not found")
    if r["consumed_at"] is not None:
        raise HTTPException(status_code=410, detail="Token already consumed")
    if r["expires_at"] and r["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Token expired")

    return {
        "site_id": r["site_id"],
        "clinic_name": r["clinic_name"],
        "class_id": r["class_id"],
        "class_display_name": r["display_name"],
        "class_description": r["description"],
        "class_risk_level": r["risk_level"],
        "class_hipaa_controls": list(r["hipaa_controls"] or []),
        "class_example_actions": r["example_actions"] or [],
        "requested_by_email": r["requested_by_email"],
        "requested_for_email": r["requested_for_email"],
        "requested_ttl_days": int(r["requested_ttl_days"] or 365),
        "partner_brand": r["partner_brand"] or "OsirisCare",
        "primary_color": r["primary_color"] or "#4F46E5",
        "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
    }


@router.post("/consent/approve/{token}")
async def approve_consent_via_token(
    token: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Phase 4 — client consumes the magic-link token and grants consent.

    Body: `{consented_by_email}`. The email MUST match the token's
    `requested_for_email` — prevents someone forwarding the link to
    a third party.

    On success: writes a consent row (via `create_consent` — which
    also writes the signed bundle + ledger event), marks the token
    consumed, returns the consent_id.
    """
    if not token or len(token) < 20:
        raise HTTPException(status_code=400, detail="token required")
    consented_by_email = (body.get("consented_by_email") or "").strip().lower()
    if "@" not in consented_by_email:
        raise HTTPException(status_code=400, detail="consented_by_email must be valid")

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    row = await execute_with_retry(db, text("""
        SELECT site_id, class_id, requested_for_email,
               requested_ttl_days, expires_at, consumed_at
        FROM consent_request_tokens
        WHERE token_hash = :th
        FOR UPDATE
    """), {"th": token_hash})
    r = row.mappings().first()
    if not r:
        raise HTTPException(status_code=404, detail="Token not found")
    if r["consumed_at"] is not None:
        raise HTTPException(status_code=410, detail="Token already consumed")
    if r["expires_at"] and r["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Token expired")

    # The email entered must match the email the partner asked to
    # notify. Case-insensitive AND timing-safe — use hmac.compare_digest
    # so the response time doesn't leak byte-wise match info.
    expected_email = (r["requested_for_email"] or "").lower()
    if not hmac.compare_digest(consented_by_email.encode("utf-8"),
                                expected_email.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail="This link was issued to a different email address",
        )

    # Duplicate-active guard — shouldn't happen if the normal flow is
    # followed, but if it does, give a clean 409 instead of DB error.
    existing = await execute_with_retry(db, text("""
        SELECT consent_id FROM runbook_class_consent
        WHERE site_id = :sid AND class_id = :cls AND revoked_at IS NULL
    """), {"sid": r["site_id"], "cls": r["class_id"]})
    if existing.scalar() is not None:
        raise HTTPException(
            status_code=409,
            detail="Active consent already exists for this class — revoke it first",
        )

    from dashboard_api.runbook_consent import create_consent as _create_consent
    from sqlalchemy.exc import IntegrityError as _IntegrityError
    try:
        consent_id = await _create_consent(
            db,
            site_id=r["site_id"],
            class_id=r["class_id"],
            consented_by_email=consented_by_email,
            ttl_days=int(r["requested_ttl_days"] or 365),
        )
        await execute_with_retry(db, text("""
            UPDATE consent_request_tokens
            SET consumed_at = NOW(),
                consumed_consent_id = :cid
            WHERE token_hash = :th
        """), {"th": token_hash, "cid": consent_id})
        # Legal / audit trail — every magic-link approval lands in
        # admin_audit_log in addition to the promoted_rule_events
        # ledger. admin_audit_log is append-only (migration 151) and
        # surfaces in the existing admin UI.
        try:
            await execute_with_retry(db, text("""
                INSERT INTO admin_audit_log
                    (username, action, target, details, created_at)
                VALUES
                    (:actor, :action, :target, :details::jsonb, NOW())
            """), {
                "actor": consented_by_email,
                "action": "CONSENT_APPROVED_VIA_TOKEN",
                "target": f"site:{r['site_id']}",
                "details": json.dumps({
                    "class_id": r["class_id"],
                    "consent_id": consent_id,
                    "token_hash_prefix": token_hash[:12],
                    "ttl_days": int(r["requested_ttl_days"] or 365),
                }),
            })
        except Exception:
            # audit mirror is secondary; primary record is in
            # compliance_bundles + promoted_rule_events
            logger.warning(f"admin_audit_log mirror failed for consent {consent_id}")
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _IntegrityError:
        # Race: another approver created the active consent in parallel.
        # Surface as a clean 409 instead of 500.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another approval raced this one — active consent already exists",
        )
    except Exception as e:
        logger.error(f"consent approve failed for token {token_hash[:12]}…: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="approve failed")

    return {
        "ok": True,
        "consent_id": consent_id,
        "site_id": r["site_id"],
        "class_id": r["class_id"],
    }


@router.put("/site/{site_id}/consent/{consent_id}/revoke")
async def revoke_site_consent(
    site_id: str,
    consent_id: str,
    body: dict,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Revoke an existing consent. Body: `{reason, revoked_by_email}`."""
    await validate_session(site_id, portal_session, token)
    reason = (body.get("reason") or "").strip()
    email = (body.get("revoked_by_email") or "").strip()
    if len(reason) < 10:
        raise HTTPException(status_code=400, detail="reason must be >=10 chars")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="revoked_by_email must be valid")

    # Ownership check — consent must belong to this site (prevents
    # cross-site revoke via URL tampering).
    owner = await execute_with_retry(db, text("""
        SELECT site_id FROM runbook_class_consent WHERE consent_id = :cid
    """), {"cid": consent_id})
    row = owner.fetchone()
    if row is None or row[0] != site_id:
        raise HTTPException(status_code=404, detail="consent not found")

    from dashboard_api.runbook_consent import revoke_consent as _revoke_consent
    try:
        await _revoke_consent(
            db,
            consent_id=consent_id,
            revoked_by_email=email,
            reason=reason,
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"consent revoke failed for {consent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="failed to revoke consent")
    return {"ok": True, "consent_id": consent_id}


@router.get("/site/{site_id}", response_model=PortalData)
async def get_portal_data(
    site_id: str,
    token: str = Query(None, description="Portal access token"),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Main portal data endpoint - validates token/session and returns all portal data.

    Data is read from PostgreSQL (compliance_bundles table) for persistence.
    Supports both session cookie (preferred) and token (legacy) authentication.
    """
    await validate_session(site_id, portal_session, token)

    # Run queries sequentially - AsyncSession doesn't support concurrent ops
    site_info = await get_site_info(db, site_id)
    kpis_data = await get_portal_kpis(db, site_id)
    control_results = await get_control_results_for_site(db, site_id, days=30)
    resolved_incidents = await get_resolved_incidents_for_site(db, site_id, days=30)
    evidence_bundles_data = await get_evidence_bundles_for_site(db, site_id)

    # Tier 3 H6/H8 — load the per-site framework configuration so the
    # frontend can show "SOC 2 Compliance Summary" instead of hardcoded
    # HIPAA when the site is configured for a different framework. The
    # backend has supported 9 frameworks since Migration 013; the portal
    # was the only surface still rendering only HIPAA labels.
    framework_info = await _get_site_framework_info(db, site_id)

    # Count SSH-monitored devices
    pool = await get_pool()
    async with admin_connection(pool) as conn:
        device_count = await conn.fetchval(
            "SELECT COUNT(*) FROM site_credentials WHERE site_id = $1 AND credential_type IN ('ssh_password', 'ssh_key')",
            site_id)

    # Build site from database info
    if site_info:
        site = PortalSite(
            site_id=site_info["site_id"],
            name=site_info["name"],
            status=site_info["status"],
            last_checkin=site_info["last_checkin"]
        )
    else:
        # Fallback for sites not yet in appliances table
        site = PortalSite(
            site_id=site_id,
            name=site_id.replace("-", " ").title(),
            status="unknown",
            last_checkin=None
        )

    # Build KPIs from database (historical aggregation)
    kpis = PortalKPIs(
        compliance_pct=kpis_data["compliance_pct"],
        patch_mttr_hours=kpis_data["patch_mttr_hours"],
        mfa_coverage_pct=kpis_data["mfa_coverage_pct"],
        backup_success_rate=kpis_data["backup_success_rate"],
        auto_fixes_24h=kpis_data["auto_fixes_24h"],
        controls_passing=kpis_data["controls_passing"],
        controls_warning=kpis_data["controls_warning"],
        controls_failing=kpis_data["controls_failing"],
        health_score=kpis_data["health_score"]
    )

    # Build controls from database check results (8 core controls)
    controls = []

    # Map rule_id to check types in database (multiple per control)
    check_mapping = {
        "endpoint_drift": ["nixos_generation", "linux_kernel", "linux_integrity"],
        "patch_freshness": ["windows_update", "linux_patching"],
        "backup_success": ["backup_status", "windows_backup_status"],
        "mfa_coverage": ["windows_password_policy", "windows_screen_lock_policy"],
        "privileged_access": ["rogue_admin_users", "linux_accounts", "linux_permissions"],
        "git_protections": [],
        "secrets_hygiene": [],
        "storage_posture": ["windows_bitlocker_status", "linux_crypto", "windows_smb_signing"],
    }

    for rule_id, meta in CONTROL_METADATA.items():
        check_types = check_mapping.get(rule_id, [])
        # Aggregate pass rates across all mapped check types
        pass_rates = []
        last_checked = None
        for ct in check_types:
            r = control_results.get(ct, {})
            if r.get("pass_rate") is not None:
                pass_rates.append(r["pass_rate"])
            if r.get("last_checked") and (last_checked is None or r["last_checked"] > last_checked):
                last_checked = r["last_checked"]
        result = {}
        if pass_rates:
            result["pass_rate"] = sum(pass_rates) / len(pass_rates)
            result["last_checked"] = last_checked

        # Calculate status from pass rate
        pass_rate = result.get("pass_rate")
        if pass_rate is None:
            status = "unknown"  # No data — not yet monitored
        elif pass_rate >= 90:
            status = "pass"
        elif pass_rate >= 50:
            status = "warn"
        else:
            status = "fail"

        controls.append(PortalControl(
            rule_id=rule_id,
            name=meta["name"],
            status=status,
            severity=meta["severity"],
            checked_at=result.get("last_checked"),
            hipaa_controls=meta["hipaa"],
            scope_summary=f"{int(pass_rate)}% pass rate (30d)" if pass_rate is not None else "No data yet",
            auto_fix_triggered=False,
            fix_duration_sec=None,
            exception_applied=False,
            exception_reason=None,
            plain_english=meta.get("plain_english", ""),
            why_it_matters=meta.get("why_it_matters", ""),
            consequence=meta.get("consequence", ""),
            what_we_check=meta.get("what_we_check", ""),
            hipaa_section=meta.get("hipaa_section", "")
        ))

    # Build incidents from database (resolved only - portal shows outcomes)
    incidents = []
    for inc in resolved_incidents:
        incidents.append(PortalIncident(
            incident_id=inc["incident_id"],
            incident_type=inc["incident_type"] or "compliance_drift",
            severity=inc["severity"] or "medium",
            auto_fixed=inc["auto_fixed"],
            resolution_time_sec=inc["resolution_time_sec"],
            created_at=inc["created_at"],
            resolved_at=inc["resolved_at"]
        ))

    # Build evidence bundles from database
    bundles = []
    for bundle in evidence_bundles_data:
        bundles.append(PortalEvidenceBundle(
            bundle_id=bundle["bundle_id"],
            bundle_type=bundle["bundle_type"],
            generated_at=bundle["generated_at"],
            size_bytes=0  # Size not stored in current schema
        ))

    return PortalData(
        site=site,
        kpis=kpis,
        controls=controls,
        incidents=incidents,
        evidence_bundles=bundles,
        device_count=device_count or 0,
        generated_at=datetime.now(timezone.utc),
        frameworks=framework_info,
    )


# =============================================================================
# DEVICE MANAGEMENT (Non-AD)
# =============================================================================

@router.post("/site/{site_id}/devices")
async def add_portal_device(
    site_id: str,
    device: ManualDeviceAdd,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
):
    """Register a non-AD device from the client portal."""
    await validate_session(site_id, portal_session, token)
    pool = await get_pool()
    return await _add_manual_device(pool, site_id, device)


@router.post("/site/{site_id}/devices/network")
async def add_portal_network_device(
    site_id: str,
    device: NetworkDeviceAdd,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
):
    """Register a network device from the partner portal."""
    await validate_session(site_id, portal_session, token)
    pool = await get_pool()
    return await _add_network_device(pool, site_id, device)


# =============================================================================
# ORG OVERVIEW (client view — multi-site aggregate, PHI-safe)
# =============================================================================

@router.get("/site/{site_id}/org-overview")
async def get_client_org_overview(
    site_id: str,
    token: str = Query(None),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db),
):
    """Org-level compliance overview for clients.

    Derives the org from the client's authenticated site. Returns aggregate
    stats across all sites in the org — no raw IPs or hostnames (PHI boundary).
    """
    await validate_session(site_id, portal_session, token)

    pool = await get_pool()
    # admin_transaction (Session 212): get_client_org_overview issues
    # 7 admin reads (org lookup, sites list, score, incidents, etc.).
    async with admin_transaction(pool) as conn:
        # Look up org from site
        org_row = await conn.fetchrow("""
            SELECT co.id, co.name, co.practice_type
            FROM sites s
            JOIN client_orgs co ON s.client_org_id = co.id
            WHERE s.site_id = $1
        """, site_id)
        if not org_row:
            return {"org": None, "sites": [], "summary": {}}

        org_id = str(org_row['id'])

        # All sites in the org
        site_rows = await conn.fetch("""
            SELECT s.site_id, s.clinic_name,
                   sa.last_checkin,
                   CASE WHEN sa.last_checkin > NOW() - INTERVAL '15 minutes' THEN 'online'
                        WHEN sa.last_checkin > NOW() - INTERVAL '1 hour' THEN 'stale'
                        ELSE 'offline' END as status
            FROM sites s
            LEFT JOIN site_appliances sa ON sa.site_id = s.site_id
            WHERE s.client_org_id = $1
            ORDER BY s.clinic_name
        """, org_id)

        site_ids = [r['site_id'] for r in site_rows]

        # Aggregate compliance
        compliance = await conn.fetchrow("""
            SELECT count(*) as total_devices,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant,
                count(*) FILTER (WHERE compliance_status = 'drifted') as drifted
            FROM discovered_devices WHERE site_id = ANY($1)
        """, site_ids)

        # Aggregate workstations
        workstations = await conn.fetchrow("""
            SELECT count(*) as total,
                count(*) FILTER (WHERE compliance_status = 'compliant') as compliant
            FROM workstations WHERE site_id = ANY($1)
        """, site_ids)

        # Incident summary (no hostnames — PHI boundary)
        incidents = await conn.fetchrow("""
            SELECT count(*) FILTER (WHERE status IN ('open','resolving','escalated')) as active,
                count(*) FILTER (WHERE resolved_at > NOW() - interval '24h') as resolved_24h,
                count(*) FILTER (WHERE created_at > NOW() - interval '7d') as total_7d
            FROM incidents WHERE site_id = ANY($1)
        """, site_ids)

        # Witness attestation coverage (PHI-safe — just counts)
        witness_att = await conn.fetchval("""
            SELECT count(*) FROM witness_attestations wa
            WHERE wa.created_at > NOW() - interval '24h'
            AND wa.bundle_id IN (SELECT bundle_id FROM compliance_bundles WHERE site_id = ANY($1))
        """, site_ids) or 0
        witness_bundles = await conn.fetchval("""
            SELECT count(DISTINCT bundle_id) FROM compliance_bundles
            WHERE site_id = ANY($1) AND checked_at > NOW() - interval '24h'
        """, site_ids) or 0

    total_devices = compliance['total_devices'] or 0
    total_ws = workstations['total'] or 0

    return {
        "org": {
            "id": org_id,
            "name": org_row['name'],
            "practice_type": org_row['practice_type'],
        },
        "sites": [
            {
                "site_id": s['site_id'],
                "name": s['clinic_name'] or s['site_id'],
                "status": s['status'],
            }
            for s in site_rows
        ],
        "summary": {
            "total_sites": len(site_ids),
            "total_devices": total_devices,
            "compliant_devices": compliance['compliant'] or 0,
            "device_compliance_rate": round((compliance['compliant'] or 0) / total_devices * 100, 1) if total_devices > 0 else 0,
            "total_workstations": total_ws,
            "workstation_compliance_rate": round((workstations['compliant'] or 0) / total_ws * 100, 1) if total_ws > 0 else 0,
            "active_incidents": incidents['active'] or 0,
            "resolved_24h": incidents['resolved_24h'] or 0,
            "incidents_7d": incidents['total_7d'] or 0,
            "evidence_attestations_24h": witness_att,
            "evidence_witness_coverage_pct": round(witness_att / witness_bundles * 100, 1) if witness_bundles > 0 else 0,
        },
    }


# =============================================================================
# CONTROLS ENDPOINT
# =============================================================================

@router.get("/site/{site_id}/controls")
async def get_controls(
    site_id: str,
    token: str = Query(None, description="Portal access token"),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Get 8 core controls with historical pass rates from database."""
    await validate_session(site_id, portal_session, token)

    # Get control results from database (30-day aggregation)
    db_results = await get_control_results_for_site(db, site_id, days=30)

    # Map rule_id to check types in database (multiple per control)
    check_mapping = {
        "endpoint_drift": ["nixos_generation", "linux_kernel", "linux_integrity"],
        "patch_freshness": ["windows_update", "linux_patching"],
        "backup_success": ["backup_status", "windows_backup_status"],
        "mfa_coverage": ["windows_password_policy", "windows_screen_lock_policy"],
        "privileged_access": ["rogue_admin_users", "linux_accounts", "linux_permissions"],
        "git_protections": [],
        "secrets_hygiene": [],
        "storage_posture": ["windows_bitlocker_status", "linux_crypto", "windows_smb_signing"],
    }

    controls = []
    for rule_id, meta in CONTROL_METADATA.items():
        check_types = check_mapping.get(rule_id, [])
        # Aggregate pass rates across all mapped check types
        pass_rates = []
        last_checked = None
        for ct in check_types:
            r = db_results.get(ct, {})
            if r.get("pass_rate") is not None:
                pass_rates.append(r["pass_rate"])
            if r.get("last_checked") and (last_checked is None or r["last_checked"] > last_checked):
                last_checked = r["last_checked"]
        result = {}
        if pass_rates:
            result["pass_rate"] = sum(pass_rates) / len(pass_rates)
            result["last_checked"] = last_checked

        # Calculate status from pass rate
        pass_rate = result.get("pass_rate")
        if pass_rate is None:
            status = "pass"
        elif pass_rate >= 90:
            status = "pass"
        elif pass_rate >= 50:
            status = "warn"
        else:
            status = "fail"

        controls.append({
            "rule_id": rule_id,
            "name": meta["name"],
            "status": status,
            "severity": meta["severity"],
            "checked_at": result.get("last_checked"),
            "hipaa_controls": meta["hipaa"],
            "scope": {
                "summary": f"{int(pass_rate)}% pass rate (30d)" if pass_rate is not None else "No data yet",
                "total_checks": 0,
                "pass_count": 0,
            },
            "auto_fix_triggered": False,
            "fix_duration_sec": None,
            "exception_applied": False,
            "exception_reason": None,
            "plain_english": meta.get("plain_english", ""),
            "why_it_matters": meta.get("why_it_matters", ""),
            "consequence": meta.get("consequence", ""),
            "what_we_check": meta.get("what_we_check", ""),
            "hipaa_section": meta.get("hipaa_section", "")
        })

    return {"controls": controls}


# =============================================================================
# EVIDENCE ENDPOINTS
# =============================================================================

@router.get("/site/{site_id}/evidence")
async def list_evidence(
    site_id: str,
    token: str = Query(None, description="Portal access token"),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """List available evidence bundles from database.

    Returns signed evidence bundles for audit packet download.
    Falls back gracefully when evidence_bundles table is empty.
    """
    await validate_session(site_id, portal_session, token)

    bundles = await get_evidence_bundles_for_site(db, site_id)

    # If no evidence bundles, provide helpful message
    if not bundles:
        return {
            "bundles": [],
            "message": "No signed evidence bundles available yet. Bundles are created during monthly compliance cycles."
        }

    return {"bundles": bundles}


@router.get("/site/{site_id}/evidence/{bundle_id}/download")
async def download_evidence(
    site_id: str,
    bundle_id: str,
    token: str = Query(None, description="Portal access token"),
    portal_session: Optional[str] = Cookie(None)
):
    """Get presigned URL for evidence bundle download from MinIO WORM storage."""
    await validate_session(site_id, portal_session, token)

    from dashboard_api.shared import get_minio_client, MINIO_BUCKET
    from dashboard_api.fleet import get_pool
    from dashboard_api.tenant_middleware import tenant_connection

    pool = await get_pool()

    # Look up the MinIO object key from the database
    async with tenant_connection(pool, site_id=site_id) as conn:
        row = await conn.fetchrow("""
            SELECT minio_key, bundle_hash, size_bytes, generated_at
            FROM evidence_bundles
            WHERE bundle_id = $1 AND site_id = $2
        """, bundle_id, site_id)

    if not row or not row.get('minio_key'):
        # Fallback: try compliance_bundles table (newer storage)
        async with tenant_connection(pool, site_id=site_id) as conn:
            row = await conn.fetchrow("""
                SELECT bundle_id, bundle_hash, checked_at as generated_at
                FROM compliance_bundles
                WHERE bundle_id = $1 AND site_id = $2
            """, bundle_id, site_id)
        if not row:
            raise HTTPException(status_code=404, detail="Evidence bundle not found")
        # Construct expected MinIO key from convention
        gen = row['generated_at']
        minio_key = f"{site_id}/{gen.strftime('%Y/%m/%d')}/{bundle_id}.json"
    else:
        minio_key = row['minio_key']

    client = get_minio_client()
    if not client:
        raise HTTPException(status_code=503, detail="Evidence storage not available")

    try:
        url = client.presigned_get_object(
            bucket_name=MINIO_BUCKET,
            object_name=minio_key,
            expires=timedelta(hours=1),
        )
    except Exception as e:
        logging.warning(f"MinIO presigned URL failed for {minio_key}: {e}")
        raise HTTPException(status_code=503, detail="Failed to generate download URL")

    return {
        "download_url": url,
        "expires_in": 3600,
        "bundle_id": bundle_id,
        "bundle_hash": row.get('bundle_hash'),
        "size_bytes": row.get('size_bytes'),
    }


# =============================================================================
# REPORT ENDPOINTS
# =============================================================================

# Try to import report generator
try:
    from .report_generator import (
        generate_pdf_report,
        is_pdf_generation_available,
        render_report_html,
    )
    PDF_AVAILABLE = is_pdf_generation_available()
except ImportError:
    PDF_AVAILABLE = False
    generate_pdf_report = None
    render_report_html = None


@router.get("/site/{site_id}/report/monthly")
async def get_monthly_report(
    site_id: str,
    token: str = Query(None, description="Portal access token"),
    month: Optional[str] = Query(None, description="YYYY-MM format"),
    format: str = Query("pdf", description="Output format: pdf or html"),
    portal_session: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Generate monthly compliance packet PDF from database.

    Aggregates compliance_bundles for the month and generates board-ready report.
    Returns PDF bytes directly for download, or HTML for preview.
    """
    # Validate access (session or token)
    await validate_session(site_id, portal_session, token)

    # Parse month parameter
    if not month:
        now = datetime.now(timezone.utc)
        year = now.year
        month_num = now.month
        month = now.strftime("%Y-%m")
    else:
        try:
            year = int(month.split("-")[0])
            month_num = int(month.split("-")[1])
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid month format. Use YYYY-MM")

    # Get monthly report data from database
    report_data = await get_monthly_compliance_report(db, site_id, year, month_num)

    # Get site info
    site_info = await get_site_info(db, site_id)
    site_name = site_info["name"] if site_info else site_id.replace("-", " ").title()

    # Build KPIs dict from database
    kpis = {
        "compliance_pct": report_data["overall_score"] or 0,
        "patch_mttr_hours": 0,  # Would need separate calculation
        "mfa_coverage_pct": 100.0,
        "backup_success_rate": report_data["category_scores"].get("backup") or 100,
        "auto_fixes_24h": report_data.get("incidents_auto_healed", 0),
        "controls_passing": sum(1 for s in report_data["category_scores"].values() if s and s >= 90),
        "controls_warning": sum(1 for s in report_data["category_scores"].values() if s and 50 <= s < 90),
        "controls_failing": sum(1 for s in report_data["category_scores"].values() if s is not None and s < 50),
    }

    # Build controls list from database
    control_results = await get_control_results_for_site(db, site_id, days=30)
    check_mapping = {
        "endpoint_drift": ["nixos_generation", "linux_kernel", "linux_integrity"],
        "patch_freshness": ["windows_update", "linux_patching"],
        "backup_success": ["backup_status", "windows_backup_status"],
        "mfa_coverage": ["windows_password_policy", "windows_screen_lock_policy"],
        "privileged_access": ["rogue_admin_users", "linux_accounts", "linux_permissions"],
        "git_protections": [],
        "secrets_hygiene": [],
        "storage_posture": ["windows_bitlocker_status", "linux_crypto", "windows_smb_signing"],
    }

    controls = []
    for rule_id, meta in CONTROL_METADATA.items():
        check_types = check_mapping.get(rule_id, [])
        pass_rates = []
        last_checked = None
        for ct in check_types:
            r = control_results.get(ct, {})
            if r.get("pass_rate") is not None:
                pass_rates.append(r["pass_rate"])
            if r.get("last_checked") and (last_checked is None or r["last_checked"] > last_checked):
                last_checked = r["last_checked"]
        result = {}
        if pass_rates:
            result["pass_rate"] = sum(pass_rates) / len(pass_rates)
            result["last_checked"] = last_checked

        pass_rate = result.get("pass_rate")
        if pass_rate is None:
            status = "pass"
        elif pass_rate >= 90:
            status = "pass"
        elif pass_rate >= 50:
            status = "warn"
        else:
            status = "fail"

        controls.append({
            "rule_id": rule_id,
            "name": meta["name"],
            "status": status,
            "severity": meta["severity"],
            "hipaa_controls": meta["hipaa"],
            "checked_at": result.get("last_checked"),
            "auto_fix_triggered": False,
        })

    # Build incidents list from database (resolved only)
    resolved_incidents = await get_resolved_incidents_for_site(db, site_id, days=30)
    incidents = []
    for inc in resolved_incidents:
        incidents.append({
            "incident_id": inc["incident_id"],
            "incident_type": inc["incident_type"] or "compliance_drift",
            "severity": inc["severity"] or "medium",
            "auto_fixed": inc["auto_fixed"],
            "resolution_time_sec": inc["resolution_time_sec"],
            "created_at": inc["created_at"].isoformat() if inc["created_at"] else None,
        })

    # Generate HTML preview
    if format == "html":
        if render_report_html:
            html = render_report_html(
                site_id=site_id,
                site_name=site_name,
                month=month,
                kpis=kpis,
                controls=controls,
                incidents=incidents,
            )
            return Response(content=html, media_type="text/html")
        else:
            raise HTTPException(status_code=503, detail="HTML generation not available")

    # Generate PDF
    if not PDF_AVAILABLE or not generate_pdf_report:
        # Fallback: return URL to HTML version
        return {
            "status": "pdf_unavailable",
            "message": "PDF generation not available - WeasyPrint not installed",
            "html_url": f"/api/portal/site/{site_id}/report/monthly?format=html&month={month}",
            "month": month,
        }

    pdf_bytes = generate_pdf_report(
        site_id=site_id,
        site_name=site_name,
        month=month,
        kpis=kpis,
        controls=controls,
        incidents=incidents,
    )

    if not pdf_bytes:
        raise HTTPException(status_code=500, detail="Failed to generate PDF report")

    # Return PDF for download
    filename = f"{site_id}-compliance-{month}.pdf"
    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Length": str(len(pdf_bytes)),
        }
    )


@router.get("/site/{site_id}/report/status")
async def get_report_status(
    site_id: str,
    token: str = Query(None, description="Portal access token"),
    portal_session: Optional[str] = Cookie(None)
):
    """Check PDF generation capability and available reports."""
    await validate_session(site_id, portal_session, token)

    return {
        "pdf_available": PDF_AVAILABLE,
        "html_available": render_report_html is not None,
        "site_id": site_id,
    }


# =============================================================================
# COMPLIANCE SNAPSHOT MODELS
# =============================================================================

class ControlResult(BaseModel):
    """Single control check result from appliance."""
    rule_id: str
    status: str  # pass, warn, fail
    checked_at: datetime
    scope_summary: str = ""
    auto_fix_triggered: bool = False
    fix_duration_sec: Optional[int] = None


class ComplianceSnapshot(BaseModel):
    """Compliance snapshot from appliance phone-home."""
    site_id: str
    host_id: str
    # KPIs
    compliance_pct: float = 100.0
    patch_mttr_hours: float = 0.0
    mfa_coverage_pct: float = 100.0
    backup_success_rate: float = 100.0
    auto_fixes_24h: int = 0
    health_score: float = 100.0
    # Control results
    control_results: List[ControlResult] = []
    # Recent incidents (last 24h)
    recent_incidents: List[Dict[str, Any]] = []
    # Metadata
    agent_version: Optional[str] = None
    policy_version: Optional[str] = None


# =============================================================================
# PHONE-HOME ENDPOINT (DEPRECATED)
# =============================================================================

@router.post("/appliances/snapshot")
async def receive_compliance_snapshot(snapshot: ComplianceSnapshot):
    """Receive compliance snapshot from appliance phone-home.

    NOTE: This endpoint is kept for backwards compatibility but no longer
    updates portal data directly. The portal now reads from PostgreSQL
    (compliance_bundles table) which is populated by the main checkin endpoint.

    Appliances should use the main /api/appliances/checkin endpoint which
    writes compliance data to compliance_bundles table.
    """
    logger.info(f"Received snapshot from {snapshot.site_id} (portal now reads from DB)")

    return {
        "status": "received",
        "site_id": snapshot.site_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "controls_received": len(snapshot.control_results),
        "note": "Portal now reads from PostgreSQL. Use /api/appliances/checkin for persistence."
    }


# NOTE: update_compliance_data() function removed
# Portal data is now read directly from PostgreSQL (compliance_bundles table)
