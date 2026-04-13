"""Privileged-access notification loop (Phase 14 T2 — Session 205).

Background task that reads `compliance_bundles WHERE
check_type='privileged_access' AND notified_at IS NULL`, sends
notification emails to:

  1. Partner admins of the site's partner org
  2. Client admin emails listed in privileged_access_consent_config.notify_client_emails
  3. Internal security distribution (SECURITY_NOTIFICATION_EMAIL env)

The email body carries verification links so the recipient can
reproduce the cryptographic proof on their own hardware:

  - /api/evidence/sites/{site_id}/bundles/{bundle_id}
  - /api/evidence/sites/{site_id}/bundles/{bundle_id}/ots
  - /recovery (public verify UI)

Design properties:
  - SELECT FOR UPDATE SKIP LOCKED → safe with multiple workers
  - Per-event mark notified_at AFTER all sends succeed; retries on
    failure keep the row queued
  - Structured log + Prometheus gauge
    osiriscare_privileged_notifier_queue_depth so an operator notices
    if emails aren't flowing
  - Zero PHI in emails (actor email + site_id + reason text;
    reason text was validated ≥20 chars, ≤2000, at intake)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


SECURITY_NOTIFICATION_EMAIL = os.getenv("SECURITY_NOTIFICATION_EMAIL", "").strip()
PORTAL_BASE = os.getenv("PORTAL_BASE_URL", "https://api.osiriscare.net")
RECOVERY_URL = f"{PORTAL_BASE}/recovery"


async def _collect_recipients(conn, site_id: str) -> Dict[str, List[str]]:
    """Determine email recipients for this site's privileged event.

    Returns a dict with:
      partner_admins       list of partner-admin emails
      client_notify_emails list from privileged_access_consent_config
      security             internal security list (from env)
    """
    # Partner admins of the site's partner org
    partner_rows = await conn.fetch("""
        SELECT DISTINCT pu.email
        FROM partner_users pu
        JOIN partners p ON p.id = pu.partner_id
        JOIN client_orgs co ON co.current_partner_id = p.id
        JOIN sites s ON s.client_org_id = co.id
        WHERE s.site_id = $1
          AND pu.role IN ('admin', 'tech')
          AND pu.email IS NOT NULL
    """, site_id)

    # Client-configured notify list (per-site)
    cfg_row = await conn.fetchrow("""
        SELECT notify_client_emails
        FROM privileged_access_consent_config
        WHERE site_id = $1
    """, site_id)
    client_list = list(cfg_row["notify_client_emails"]) if cfg_row and cfg_row["notify_client_emails"] else []

    security = [SECURITY_NOTIFICATION_EMAIL] if SECURITY_NOTIFICATION_EMAIL else []

    return {
        "partner_admins": [r["email"] for r in partner_rows],
        "client_notify_emails": client_list,
        "security": security,
    }


def _compose_email(
    bundle: Dict[str, Any],
    recipients: Dict[str, List[str]],
    approval_urls: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[str, str]:
    """Build subject + body. Plain text to maximize deliverability.

    Phase 14 T2.1 Part 2: if `approval_urls` is provided (a dict keyed
    by recipient email, each value = {'approve': URL, 'reject': URL}),
    the generated body includes the magic-link URLs for that recipient.
    Callers produce ONE email per recipient with their specific links.
    """
    event = bundle.get("event") or {}
    event_type = event.get("event_type", "privileged_access")
    actor = event.get("actor_email", "unknown")
    reason = event.get("reason", "(not provided)")
    site_id = bundle["site_id"]
    bundle_id = bundle["bundle_id"]
    bundle_hash = bundle["bundle_hash"]
    chain_position = bundle["chain_position"]
    duration = event.get("duration_minutes")

    subject = (
        f"[OsirisCare] Privileged access: {event_type} on {site_id} by {actor}"
    )
    verify_url = f"{PORTAL_BASE}/api/evidence/sites/{site_id}/bundles/{bundle_id}"
    ots_url = f"{PORTAL_BASE}/api/evidence/sites/{site_id}/bundles/{bundle_id}/ots"

    approval_block = ""
    if approval_urls:
        # When this email is destined for a single recipient with approval
        # links, insert the CTA section at the top. approval_urls is
        # expected to be keyed by recipient email; caller passes a
        # per-recipient slice.
        first_entry = next(iter(approval_urls.values()), None)
        if first_entry:
            approve_url = first_entry.get("approve")
            reject_url = first_entry.get("reject")
            if approve_url and reject_url:
                approval_block = (
                    "═══════════════════════════════════════════════════════════\n"
                    "ACTION REQUIRED — This privileged access requires your\n"
                    "approval. Click one of the links below; you will be\n"
                    "asked to log in to your OsirisCare portal.\n\n"
                    f"  APPROVE:  {approve_url}\n"
                    f"  REJECT:   {reject_url}\n\n"
                    "These links expire in 30 minutes and can each be used\n"
                    "only once. The link proves WHO should act; your logged-\n"
                    "in session proves YOU acted. Both are required.\n"
                    "═══════════════════════════════════════════════════════════\n\n"
                )

    body = (
        f"{approval_block}"
        f"A privileged-access event was recorded on {site_id}.\n\n"
        f"  Event:      {event_type}\n"
        f"  Actor:      {actor}\n"
        f"  Reason:     {reason}\n"
        + (f"  Duration:   {duration} minutes\n" if duration else "")
        + f"  Timestamp:  {event.get('timestamp', bundle.get('checked_at', 'n/a'))}\n\n"
        f"Cryptographic evidence:\n"
        f"  Bundle ID:       {bundle_id}\n"
        f"  Bundle SHA-256:  {bundle_hash}\n"
        f"  Chain position:  {chain_position}\n\n"
        f"Verify the proof independently on your own hardware:\n"
        f"  {verify_url}\n"
        f"  OTS proof download: {ots_url}\n"
        f"  Public verifier:    {RECOVERY_URL}\n\n"
        f"This record is Ed25519-signed by Central Command, hash-chained\n"
        f"to your site's prior evidence bundles, and anchored to Bitcoin\n"
        f"via OpenTimestamps. No trust in OsirisCare required to verify.\n\n"
        f"-- OsirisCare Privileged-Access Attestation\n"
    )
    return {"subject": subject, "body": body}


async def _mint_approval_links(
    conn,
    bundle: Dict[str, Any],
    client_emails: List[str],
) -> Dict[str, Dict[str, str]]:
    """For an INITIATED privileged_access bundle whose matching request
    is still 'pending' AND the site has client_approval_required=true,
    mint a pair of magic-link tokens (approve + reject) for each
    client admin recipient. Returns {email: {'approve': URL, 'reject': URL}}.
    """
    event = bundle.get("event") or {}
    if (event.get("approvals") or [{}])[0].get("stage") != "initiated":
        return {}

    row = await conn.fetchrow(
        "SELECT par.id::text AS id, par.status, cfg.client_approval_required "
        "FROM privileged_access_requests par "
        "LEFT JOIN privileged_access_consent_config cfg "
        "       ON cfg.site_id = par.site_id "
        "WHERE par.attestation_bundle_id = $1",
        bundle["bundle_id"],
    )
    if not row:
        return {}
    if row["status"] != "pending":
        return {}
    if not (row["client_approval_required"] or False):
        return {}

    try:
        from .privileged_magic_link import mint_token
    except Exception:
        return {}

    out: Dict[str, Dict[str, str]] = {}
    for email in client_emails:
        email_clean = (email or "").strip().lower()
        if not email_clean or "@" not in email_clean:
            continue
        try:
            # Per-recipient SAVEPOINT: a failed mint for one email
            # must not poison the outer SELECT-FOR-UPDATE transaction
            # holding locks on the bundle batch (CLAUDE.md asyncpg
            # savepoint invariant).
            async with conn.transaction():
                approve_tok = await mint_token(conn, row["id"], "approve", email_clean)
                reject_tok = await mint_token(conn, row["id"], "reject", email_clean)
        except Exception as e:
            logger.warning(f"magic-link mint failed for {email_clean}: {e}")
            continue
        out[email_clean] = {
            "approve": (
                f"{PORTAL_BASE}/portal/privileged-access/act"
                f"?t={approve_tok}&action=approve&rid={row['id']}"
            ),
            "reject": (
                f"{PORTAL_BASE}/portal/privileged-access/act"
                f"?t={reject_tok}&action=reject&rid={row['id']}"
            ),
        }
    return out


async def _send_email(to: List[str], subject: str, body: str) -> bool:
    """Send via existing email_alerts._send_smtp_with_retry (single SMTP
    entry point per CLAUDE.md). Returns True on success."""
    if not to:
        return True  # nothing to send is not a failure
    try:
        from .email_alerts import _send_smtp_with_retry
    except Exception:
        logger.warning("email_alerts._send_smtp_with_retry unavailable; skipping send")
        return False
    try:
        await _send_smtp_with_retry(
            to=to, subject=subject, body=body, is_html=False,
        )
        return True
    except Exception as e:
        logger.warning(f"SMTP send failed to {to}: {e}")
        return False


async def privileged_notifier_loop():
    """Background task — runs every 60s. Reads unnotified
    privileged_access bundles and dispatches notifications.

    Registered from main.py lifespan alongside the other flywheel
    background tasks."""
    await asyncio.sleep(60)  # wait past initial startup
    while True:
        try:
            from .bg_heartbeat import record_heartbeat
            record_heartbeat("privileged_notifier")
        except Exception:
            pass  # heartbeat must never break the loop
        try:
            from .fleet import get_pool
            from .tenant_middleware import admin_connection
            pool = await get_pool()
            async with admin_connection(pool) as conn:
                # SELECT FOR UPDATE SKIP LOCKED makes this safe with
                # multiple worker replicas
                async with conn.transaction():
                    rows = await conn.fetch("""
                        SELECT bundle_id, site_id, bundle_hash,
                               chain_position, checked_at,
                               checks, summary
                        FROM compliance_bundles
                        WHERE check_type = 'privileged_access'
                          AND notified_at IS NULL
                          AND checked_at > NOW() - INTERVAL '7 days'
                        ORDER BY checked_at ASC
                        LIMIT 20
                        FOR UPDATE SKIP LOCKED
                    """)

                    sent = 0
                    failed = 0
                    for r in rows:
                        # checks is jsonb; first element is the event
                        try:
                            checks = (
                                json.loads(r["checks"])
                                if isinstance(r["checks"], str) else r["checks"]
                            )
                            event = checks[0] if checks else {}
                        except Exception:
                            event = {}

                        bundle = {
                            "bundle_id": r["bundle_id"],
                            "site_id": r["site_id"],
                            "bundle_hash": r["bundle_hash"],
                            "chain_position": r["chain_position"],
                            "checked_at": r["checked_at"].isoformat() if r["checked_at"] else None,
                            "event": event,
                        }
                        recipients = await _collect_recipients(conn, r["site_id"])

                        # Mint per-recipient magic-link tokens IF this is
                        # an INITIATED bundle whose request still needs
                        # client approval. Returns {} otherwise so the
                        # email path stays unchanged for non-approval
                        # notifications (completed events, monitoring,
                        # config-change attestations, etc.).
                        approval_links = await _mint_approval_links(
                            conn, bundle, recipients["client_notify_emails"],
                        )

                        # Bulk message used for partner-admin + security
                        # rings (no approval CTA), and as the no-link
                        # fallback for any client recipients we couldn't
                        # mint for.
                        bulk_msg = _compose_email(bundle, recipients)

                        all_ok = True
                        all_ok &= await _send_email(
                            recipients["partner_admins"], bulk_msg["subject"], bulk_msg["body"],
                        )
                        all_ok &= await _send_email(
                            recipients["security"], bulk_msg["subject"], bulk_msg["body"],
                        )

                        if approval_links:
                            # ONE email per client recipient — each gets
                            # only their own magic-link URLs. Pre-Session
                            # 205, this was a single bulk email; that
                            # made it impossible to know which recipient
                            # a click came from and would have leaked
                            # other admins' single-use tokens to anyone
                            # forwarded the email.
                            for email, links in approval_links.items():
                                per_msg = _compose_email(
                                    bundle, recipients,
                                    approval_urls={email: links},
                                )
                                all_ok &= await _send_email(
                                    [email], per_msg["subject"], per_msg["body"],
                                )
                            # Any client emails we couldn't mint for
                            # (rare — bad email format) still get the
                            # plain notification so visibility isn't lost.
                            unminted = [
                                e for e in recipients["client_notify_emails"]
                                if (e or "").strip().lower() not in approval_links
                            ]
                            if unminted:
                                all_ok &= await _send_email(
                                    unminted, bulk_msg["subject"], bulk_msg["body"],
                                )
                        else:
                            all_ok &= await _send_email(
                                recipients["client_notify_emails"],
                                bulk_msg["subject"], bulk_msg["body"],
                            )

                        if all_ok:
                            await conn.execute(
                                "UPDATE compliance_bundles SET notified_at = NOW() "
                                "WHERE bundle_id = $1 AND site_id = $2",
                                r["bundle_id"], r["site_id"],
                            )
                            sent += 1
                        else:
                            failed += 1

                    if sent or failed:
                        logger.info(
                            "privileged_notifier cycle",
                            extra={
                                "sent": sent,
                                "failed": failed,
                                "total": len(rows),
                            },
                        )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"Privileged notifier error: {e}")

        await asyncio.sleep(60)
