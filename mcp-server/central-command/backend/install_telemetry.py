"""install_telemetry — v36 generic-install visibility surface.

Session 207 round-table outcome: the ISO needs to be diagnostic-first.
Two endpoints here extend install_sessions with failure signals and
network-environment-survey results so an operator can see the box is
stuck from the dashboard, BEFORE the first successful checkin.

Auth: X-Install-Token header (same shared-secret as install_reports.py).
Weak by design — lives in ISO, keeps internet scanners out of the
telemetry table but isn't a strong identity proof.

Both endpoints are idempotent upserts keyed by MAC address. The
installer calls them from the auto-provision bash script on every
failed retry (failure-report) and once at first-boot after the network
survey completes (net-survey).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .fleet import get_pool
from .tenant_middleware import admin_transaction

logger = logging.getLogger("install_telemetry")

router = APIRouter(prefix="/api/install", tags=["install-telemetry"])

INSTALL_TOKEN = os.getenv("INSTALL_TOKEN", "osiriscare-installer-dev-only")

# Accept either colon-separated or dash-separated MAC; normalize to colon-
# separated uppercase. Aligns with install_sessions.mac_address storage
# (uppercase, colon-separated) and makes URL decoding tolerant.
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-]?[0-9A-Fa-f]{2}){5}$")


def _require_install_token(
    x_install_token: Optional[str] = Header(None, alias="X-Install-Token"),
) -> None:
    if not x_install_token or x_install_token != INSTALL_TOKEN:
        raise HTTPException(status_code=401, detail="invalid install token")


def _normalize_mac(raw: str) -> str:
    """Accepts AA:BB:CC:DD:EE:FF or AA-BB-...  or AABBCCDDEEFF. Returns
    colon-separated uppercase. Rejects anything else with 400."""
    if not raw or not _MAC_RE.match(raw.replace("%3A", ":")):
        raise HTTPException(status_code=400, detail=f"invalid mac: {raw!r}")
    cleaned = raw.replace("%3A", ":").replace("-", ":").upper()
    # Insert colons if the input was bare-hex
    if ":" not in cleaned and len(cleaned) == 12:
        cleaned = ":".join(cleaned[i:i+2] for i in range(0, 12, 2))
    return cleaned


class FailureReport(BaseModel):
    """One snapshot of a failed /api/provision/{mac} attempt."""

    # HTTP status code from the provision request, or special sentinel
    # values: 0 for "no response" (curl exit 7, network unreachable)
    # or curl exit code mapped to negative int (curl 6 NXDOMAIN -> -6).
    # Kept as string because bash shells format these inconsistently
    # (curl's `%{http_code}` emits "000" for no-response).
    http_code: str = Field(..., max_length=8)
    curl_exit: Optional[int] = Field(None, description="curl exit status (0 = success, 6 = NXDOMAIN, 7 = connect refused, 28 = timeout)")
    curl_error: Optional[str] = Field(None, max_length=500)
    dns_resolver: Optional[str] = Field(None, max_length=64)
    resolved_ip: Optional[str] = Field(None, max_length=64)
    attempt_number: int = Field(..., ge=0, le=100000)
    install_stage: Optional[str] = Field(None, max_length=32)


class NetSurveyReport(BaseModel):
    """First-boot network environment survey — verdicts + raw probe output."""

    survey: Dict[str, Any] = Field(..., description="See iso/appliance-disk-image.nix msp-net-survey.service for schema")


@router.post("/failure-report/{mac}", dependencies=[Depends(_require_install_token)])
async def post_failure_report(mac: str, report: FailureReport) -> Dict[str, Any]:
    """Record one failed provisioning attempt for this MAC.

    Idempotent upsert:
      - If install_sessions has a row for this MAC, UPDATE last_error_*
        columns + increment provision_attempts + refresh last_seen.
      - If no row exists, INSERT a minimal row (site_id='', hostname
        unknown — will be filled in once provisioning eventually
        succeeds and the real install_sessions INSERT in sites.py runs).

    The install_sessions table was originally scoped to the installer's
    live-USB phase; v36 extends it to also track the installed-system
    auto-provision retry loop. Both phases report against the same
    MAC key so a single row per MAC tracks the full lifecycle.
    """
    normalized_mac = _normalize_mac(mac)
    now = datetime.now(timezone.utc)

    pool = await get_pool()
    # #138 routing-risk anti-pattern fix: admin_transaction pins
    # SET LOCAL + multi-statement work to one PgBouncer backend
    # in one explicit txn (tenant_middleware.py:147-157 caveat).
    async with admin_transaction(pool) as conn:
        # UPSERT on mac_address. The table's PK is session_id but we
        # uniquely key by MAC via the (site_id, mac_address) unique
        # index — if no row exists for this MAC we create one with a
        # synthetic session_id. Otherwise we update whatever's there.
        existing = await conn.fetchrow(
            "SELECT session_id, site_id, provision_attempts "
            "FROM install_sessions WHERE mac_address = $1 ORDER BY last_seen DESC LIMIT 1",
            normalized_mac,
        )
        if existing:
            await conn.execute(
                """
                UPDATE install_sessions
                   SET last_error_code    = $1,
                       last_error_detail  = $2,
                       last_error_at      = $3,
                       dns_resolver_used  = COALESCE($4, dns_resolver_used),
                       api_resolved_ip    = COALESCE($5, api_resolved_ip),
                       provision_attempts = GREATEST(provision_attempts + 1, $6),
                       last_seen          = $3,
                       install_stage      = COALESCE($7, install_stage)
                 WHERE session_id = $8
                """,
                report.curl_exit if report.curl_exit is not None else _http_to_code(report.http_code),
                (report.curl_error or "")[:500],
                now,
                report.dns_resolver,
                report.resolved_ip,
                report.attempt_number,
                report.install_stage,
                existing["session_id"],
            )
            logger.warning(
                "install_failure_report mac=%s attempt=%d curl=%s http=%s",
                normalized_mac, report.attempt_number, report.curl_exit, report.http_code,
            )
            return {
                "ok": True,
                "session_id": existing["session_id"],
                "provision_attempts": max(int(existing["provision_attempts"] or 0) + 1, report.attempt_number),
                "recorded_at": now.isoformat(),
            }

        # No existing session for this MAC — create a minimal one.
        # The real INSERT in sites.py (when provisioning succeeds)
        # will fill in hostname, agent_version, etc. This row just
        # captures the failure signal so the dashboard can surface it.
        session_id = f"failure-{normalized_mac}-{int(now.timestamp())}"
        await conn.execute(
            """
            INSERT INTO install_sessions
                (session_id, site_id, mac_address, install_stage,
                 first_seen, last_seen, checkin_count,
                 last_error_code, last_error_detail, last_error_at,
                 dns_resolver_used, api_resolved_ip, provision_attempts)
            VALUES ($1, '', $2, COALESCE($3, 'pre_provision'),
                    $4, $4, 0,
                    $5, $6, $4,
                    $7, $8, $9)
            """,
            session_id,
            normalized_mac,
            report.install_stage,
            now,
            report.curl_exit if report.curl_exit is not None else _http_to_code(report.http_code),
            (report.curl_error or "")[:500],
            report.dns_resolver,
            report.resolved_ip,
            report.attempt_number,
        )
        logger.warning(
            "install_failure_report (new) mac=%s attempt=%d curl=%s http=%s",
            normalized_mac, report.attempt_number, report.curl_exit, report.http_code,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "provision_attempts": report.attempt_number,
            "recorded_at": now.isoformat(),
        }


@router.post("/net-survey/{mac}", dependencies=[Depends(_require_install_token)])
async def post_net_survey(mac: str, report: NetSurveyReport) -> Dict[str, Any]:
    """Record first-boot network environment survey for this MAC.

    Called once per appliance boot, after the local probe completes.
    Overwrites any prior survey (latest is authoritative). The survey
    JSONB is surfaced on the appliance-detail view as a Network Health
    matrix: DNS, HTTPS reach, NTP sync, captive portal detection, IPv4/
    IPv6 status, VLAN tagging.
    """
    normalized_mac = _normalize_mac(mac)
    now = datetime.now(timezone.utc)

    pool = await get_pool()
    # #138 routing-risk anti-pattern fix: admin_transaction pins
    # SET LOCAL + multi-statement work to one PgBouncer backend
    # in one explicit txn (tenant_middleware.py:147-157 caveat).
    async with admin_transaction(pool) as conn:
        row = await conn.fetchrow(
            "SELECT session_id FROM install_sessions "
            "WHERE mac_address = $1 ORDER BY last_seen DESC LIMIT 1",
            normalized_mac,
        )
        survey_json = json.dumps(report.survey)

        if row:
            await conn.execute(
                """
                UPDATE install_sessions
                   SET net_survey    = $1::jsonb,
                       net_survey_at = $2,
                       last_seen     = GREATEST(last_seen, $2)
                 WHERE session_id = $3
                """,
                survey_json, now, row["session_id"],
            )
        else:
            session_id = f"survey-{normalized_mac}-{int(now.timestamp())}"
            await conn.execute(
                """
                INSERT INTO install_sessions
                    (session_id, site_id, mac_address, install_stage,
                     first_seen, last_seen, checkin_count,
                     net_survey, net_survey_at)
                VALUES ($1, '', $2, 'pre_provision',
                        $3, $3, 0,
                        $4::jsonb, $3)
                """,
                session_id, normalized_mac, now, survey_json,
            )

    # Keep the log visible — net surveys are rare but valuable.
    # Truncate the survey dict in the log line so we don't blow up journald.
    verdict_summary = {
        k: v.get("ok") if isinstance(v, dict) else v
        for k, v in report.survey.items()
    }
    logger.info(
        "install_net_survey mac=%s verdicts=%s",
        normalized_mac, verdict_summary,
    )
    return {"ok": True, "recorded_at": now.isoformat()}


def _http_to_code(http_code: str) -> int:
    """Map curl's %{http_code} string to an int. "000" means no response
    at all (network failure). 3-digit responses are returned as ints."""
    try:
        n = int(http_code)
    except (TypeError, ValueError):
        return -1
    return n
