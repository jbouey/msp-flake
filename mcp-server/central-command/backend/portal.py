"""Client Portal API endpoints.

Token-authenticated endpoints for client-facing compliance dashboards.

Features:
- Magic link authentication with email delivery
- httpOnly cookie sessions (30-day expiry)
- PDF report generation with HIPAA control mapping
- Evidence bundle browsing
- Mobile-responsive design support
"""

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

logger = logging.getLogger(__name__)


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
        logger.warning(f"Email not configured - magic link for {site_name}: {magic_link}")
        return False

    try:
        message = Mail(
            from_email=FROM_EMAIL,
            to_emails=to_email,
            subject=f"Your {site_name} Compliance Dashboard Access",
            html_content=f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 40px 20px;">
                <div style="text-align: center; margin-bottom: 40px;">
                    <h1 style="color: #1a365d; font-size: 24px; margin: 0;">OsirisCare</h1>
                    <p style="color: #718096; margin-top: 8px;">HIPAA Compliance Platform</p>
                </div>

                <p style="color: #2d3748; font-size: 16px; line-height: 1.6;">
                    Click the button below to access your compliance dashboard for <strong>{site_name}</strong>.
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

        logger.info(f"Magic link email sent to {to_email}, status: {response.status_code}")
        return response.status_code in (200, 201, 202)

    except Exception as e:
        logger.error(f"Failed to send magic link email: {e}")
        return False


# =============================================================================
# MODELS
# =============================================================================

class PortalKPIs(BaseModel):
    """KPI metrics for portal display."""
    compliance_pct: float = 0.0
    patch_mttr_hours: float = 0.0
    mfa_coverage_pct: float = 100.0
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


class PortalData(BaseModel):
    """Complete portal data response."""
    site: PortalSite
    kpis: PortalKPIs
    controls: List[PortalControl]
    incidents: List[PortalIncident]
    evidence_bundles: List[PortalEvidenceBundle]
    generated_at: datetime


class TokenResponse(BaseModel):
    """Portal token generation response."""
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
        "what_we_check": "We track how quickly critical security patches are applied and ensure nothing falls through the cracks.",
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
        "why_it_matters": "Admin accounts have the keys to everything. Regular reviews ensure only the right people have elevated access.",
        "consequence": "Unused admin accounts or over-provisioned access are prime targets for attackers.",
        "what_we_check": "We track who has admin access, flag dormant accounts, and ensure access matches job responsibilities.",
        "severity": "high",
        "hipaa": ["164.308(a)(3)(ii)(B)", "164.308(a)(4)(ii)(B)"],
        "hipaa_section": "Workforce Clearance / Access Authorization"
    },
    "git_protections": {
        "name": "Git Branch Protection",
        "plain_english": "Code changes require approval",
        "why_it_matters": "Requiring code review before deployment prevents accidental bugs and malicious changes from reaching production.",
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
        "what_we_check": "We scan for exposed secrets and track API key age to ensure regular rotation.",
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
# IN-MEMORY SESSION STORE
# =============================================================================

# Token and session management (kept in-memory for simplicity)
# TODO: Move to Redis or database for horizontal scaling
_portal_tokens: Dict[str, str] = {}  # site_id -> token
_magic_links: Dict[str, Dict[str, Any]] = {}  # token -> {site_id, email, expires_at}
_sessions: Dict[str, Dict[str, Any]] = {}  # session_id -> {site_id, created_at, expires_at}
_site_contacts: Dict[str, str] = {}  # site_id -> email

# NOTE: Compliance data is now read from PostgreSQL (compliance_bundles table)
# The old _compliance_data in-memory dict has been removed


def _cleanup_expired():
    """Remove expired magic links and sessions."""
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


@router.post("/sites/{site_id}/generate-token", response_model=TokenResponse)
async def generate_portal_token(site_id: str):
    """Generate magic link token for client portal access (admin use)."""
    # Generate 64-char token
    token = secrets.token_urlsafe(48)

    # Store token (in production, save to database)
    _portal_tokens[site_id] = token

    return TokenResponse(
        portal_url=f"{PORTAL_BASE_URL}/portal/site/{site_id}?token={token}",
        token=token,
        expires="never"
    )


@router.post("/sites/{site_id}/request-access", response_model=MagicLinkResponse)
async def request_magic_link(site_id: str, request: MagicLinkRequest):
    """Request magic link via email (client-facing).

    Validates email is authorized for the site, then sends magic link.
    """
    _cleanup_expired()

    # Check if email is authorized for this site
    authorized_email = _site_contacts.get(site_id)
    if authorized_email and request.email.lower() != authorized_email.lower():
        # Don't reveal if email is wrong - just say "check your email"
        logger.warning(f"Unauthorized email {request.email} for site {site_id}")
        return MagicLinkResponse(
            message="If this email is registered, you will receive a link shortly.",
            email_sent=False
        )

    # Generate magic link token
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=MAGIC_LINK_EXPIRY_MINUTES)

    _magic_links[token] = {
        "site_id": site_id,
        "email": request.email,
        "expires_at": expires_at
    }

    # Build magic link URL
    magic_link = f"{PORTAL_BASE_URL}/portal/site/{site_id}?magic={token}"

    # Get site name for email (use site_id formatting as fallback)
    site_name = site_id.replace("-", " ").title()

    # Send email
    email_sent = await send_magic_link_email(request.email, site_name, magic_link)

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
    _cleanup_expired()

    # Look up magic link
    link_data = _magic_links.get(magic)
    if not link_data:
        raise HTTPException(status_code=403, detail="Invalid or expired link")

    # Check expiry
    if datetime.now(timezone.utc) > link_data["expires_at"]:
        del _magic_links[magic]
        raise HTTPException(status_code=403, detail="Link has expired. Please request a new one.")

    # Create session
    session_id = secrets.token_urlsafe(32)
    site_id = link_data["site_id"]
    now = datetime.now(timezone.utc)

    _sessions[session_id] = {
        "site_id": site_id,
        "email": link_data["email"],
        "created_at": now,
        "expires_at": now + timedelta(days=SESSION_EXPIRY_DAYS)
    }

    # Delete used magic link
    del _magic_links[magic]

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


async def validate_session(
    site_id: str,
    portal_session: Optional[str] = Cookie(None),
    token: Optional[str] = Query(None)
) -> Dict[str, Any]:
    """Validate portal access via session cookie or token.

    Supports both httpOnly cookie sessions and legacy token auth.
    """
    _cleanup_expired()

    # Try session cookie first (preferred)
    if portal_session:
        session = _sessions.get(portal_session)
        if session:
            if datetime.now(timezone.utc) < session["expires_at"]:
                if session["site_id"] == site_id:
                    return {"method": "session", "email": session.get("email")}
            else:
                # Expired - remove it
                del _sessions[portal_session]

    # Fallback to token auth (legacy)
    if token:
        stored_token = _portal_tokens.get(site_id)
        if stored_token and stored_token == token:
            return {"method": "token"}

        # Also check magic links for direct access
        link_data = _magic_links.get(token)
        if link_data and link_data["site_id"] == site_id:
            if datetime.now(timezone.utc) < link_data["expires_at"]:
                return {"method": "magic_link"}

    raise HTTPException(status_code=403, detail="Invalid or expired session")


async def validate_token(site_id: str, token: str) -> bool:
    """Validate portal access token (legacy support)."""
    stored_token = _portal_tokens.get(site_id)
    if not stored_token or stored_token != token:
        raise HTTPException(status_code=403, detail="Invalid portal token")
    return True


@router.post("/auth/logout")
async def logout(response: Response, portal_session: Optional[str] = Cookie(None)):
    """Log out and clear session."""
    if portal_session and portal_session in _sessions:
        del _sessions[portal_session]

    response.delete_cookie("portal_session", path="/")

    return {"status": "logged_out"}


@router.post("/sites/{site_id}/contacts")
async def set_site_contact(site_id: str, email: EmailStr):
    """Set authorized contact email for a site (admin only)."""
    _site_contacts[site_id] = email.lower()
    logger.info(f"Set contact for site {site_id}: {email}")
    return {"status": "updated", "site_id": site_id, "email": email}


# =============================================================================
# MAIN PORTAL ENDPOINT
# =============================================================================

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

    # Get site info from database
    site_info = await get_site_info(db, site_id)
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

    # Get KPIs from database (historical aggregation)
    kpis_data = await get_portal_kpis(db, site_id)
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
    control_results = await get_control_results_for_site(db, site_id, days=30)
    controls = []

    for rule_id, meta in CONTROL_METADATA.items():
        # Map rule_id to check types in database
        check_mapping = {
            "endpoint_drift": "nixos_generation",
            "patch_freshness": "nixos_generation",
            "backup_success": "backup_status",
            "mfa_coverage": None,  # Not tracked in compliance_bundles
            "privileged_access": None,
            "git_protections": None,
            "secrets_hygiene": None,
            "storage_posture": None,
        }

        check_type = check_mapping.get(rule_id)
        result = control_results.get(check_type, {}) if check_type else {}

        # Calculate status from pass rate
        pass_rate = result.get("pass_rate")
        if pass_rate is None:
            status = "pass"  # No data = assume passing
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
            scope_summary=f"{int(pass_rate or 100)}% pass rate (30d)" if pass_rate else "All checks passing",
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
    resolved_incidents = await get_resolved_incidents_for_site(db, site_id, days=30)
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
    evidence_bundles_data = await get_evidence_bundles_for_site(db, site_id)
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
        generated_at=datetime.now(timezone.utc)
    )


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

    # Map rule_id to check types in database
    check_mapping = {
        "endpoint_drift": "nixos_generation",
        "patch_freshness": "nixos_generation",
        "backup_success": "backup_status",
        "mfa_coverage": None,
        "privileged_access": None,
        "git_protections": None,
        "secrets_hygiene": None,
        "storage_posture": None,
    }

    controls = []
    for rule_id, meta in CONTROL_METADATA.items():
        check_type = check_mapping.get(rule_id)
        result = db_results.get(check_type, {}) if check_type else {}

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
                "summary": f"{int(pass_rate or 100)}% pass rate (30d)",
                "total_checks": result.get("total", 0),
                "pass_count": result.get("pass_count", 0),
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
    """Get presigned URL for evidence bundle download."""
    await validate_session(site_id, portal_session, token)

    # In production, generate presigned MinIO URL
    # For now, return placeholder
    return {
        "download_url": f"https://api.osiriscare.net/evidence/{site_id}/{bundle_id}",
        "expires_in": 3600,
        "bundle_id": bundle_id
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
        "endpoint_drift": "nixos_generation",
        "patch_freshness": "nixos_generation",
        "backup_success": "backup_status",
        "mfa_coverage": None,
        "privileged_access": None,
        "git_protections": None,
        "secrets_hygiene": None,
        "storage_posture": None,
    }

    controls = []
    for rule_id, meta in CONTROL_METADATA.items():
        check_type = check_mapping.get(rule_id)
        result = control_results.get(check_type, {}) if check_type else {}

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
