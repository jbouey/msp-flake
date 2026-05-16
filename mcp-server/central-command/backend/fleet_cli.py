#!/usr/bin/env python3
"""Fleet order CLI — create, list, and cancel fleet orders.

Runs inside the mcp-server Docker container. Handles Ed25519 signing
and direct database insertion in one step.

Usage:
    python3 fleet_cli.py create nixos_rebuild
    python3 fleet_cli.py create update_daemon --param binary_url=https://... --param binary_sha256=abc --param version=0.3.14
    python3 fleet_cli.py create diagnostic --param command=agent_status
    python3 fleet_cli.py create force_checkin --expires 1
    python3 fleet_cli.py list
    python3 fleet_cli.py list --status active
    python3 fleet_cli.py cancel <order-uuid>
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import asyncpg
except ImportError:
    sys.exit("asyncpg not installed. Run: pip install asyncpg")

try:
    from nacl.signing import SigningKey
    from nacl.encoding import HexEncoder
except ImportError:
    sys.exit("PyNaCl not installed. Run: pip install PyNaCl")


SIGNING_KEY_FILE = Path(os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key"))

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://mcp:mcp@mcp-postgres:5432/mcp")
# Strip SQLAlchemy dialect prefix if present
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

VALID_ORDER_TYPES = {
    "force_checkin", "run_drift", "sync_rules", "restart_agent",
    "nixos_rebuild", "update_agent", "update_iso", "view_logs",
    "diagnostic", "deploy_sensor", "remove_sensor",
    "deploy_linux_sensor", "remove_linux_sensor", "sensor_status",
    "sync_promoted_rule", "healing", "update_credentials", "update_daemon",
    "configure_workstation_agent", "validate_credential",
    "remove_agent", "rotate_wg_key", "isolate_host",
    "chaos_quicktest",
    "enable_emergency_access", "disable_emergency_access",
    "configure_dns",
    # 2026-04-22: reclaim disk in /nix/store. Non-privileged; idempotent.
    # Surfaces bytes_freed in the completion payload.
    "nix_gc",
    # Session 210-B 2026-04-25 audit P1 — daemon handlers that lacked
    # a CLI path. clear_winrm_pin / enable_healing / disable_healing
    # already had Go handlers but couldn't be issued from this CLI.
    # reprovision is intentionally NOT here — issued by the relocate
    # endpoint (sites.py), not by operator CLI.
    "clear_winrm_pin",
    "enable_healing",
    "disable_healing",
}

DEFAULT_PARAMS = {
    "nixos_rebuild": {"flake_ref": "github:jbouey/msp-flake#osiriscare-appliance-disk"},
    "nix_gc": {"older_than_days": 14, "optimise": True},
}

REQUIRED_PARAMS = {
    "update_daemon": ["binary_url", "binary_sha256", "version"],
    "diagnostic": ["command"],
    "isolate_host": ["hostname"],
    "configure_dns": ["extra_hosts"],
}

_HEX_RE = re.compile(r"^[0-9a-f]{128}$")


def load_signing_key() -> SigningKey:
    if not SIGNING_KEY_FILE.exists():
        sys.exit(f"Signing key not found at {SIGNING_KEY_FILE}")
    key_hex = SIGNING_KEY_FILE.read_bytes().strip()
    return SigningKey(key_hex, encoder=HexEncoder)


def sign_order(
    signing_key: SigningKey,
    order_type: str,
    parameters: dict,
    created_at: datetime,
    expires_at: datetime,
) -> tuple[str, str, str]:
    """Sign a fleet order. Returns (nonce, signature, signed_payload).

    Phase 13.5 H6 — embed the signing pubkey inside the signed payload
    so the appliance daemon can cross-check against the pubkey most
    recently delivered via checkin. Bounded trust: the daemon only
    honors this field when it byte-matches its most-recent-checkin
    reference. That closes the "verifier cache stale" race window with
    zero widening of attack surface.

    The pubkey goes inside the signed payload — i.e. it IS part of what
    the signature attests to. An attacker can't swap the pubkey without
    breaking the signature; the daemon's bounded-trust check closes the
    remaining "arbitrary pubkey substitution" window.
    """
    nonce = secrets.token_hex(16)
    pubkey_hex = signing_key.verify_key.encode(encoder=HexEncoder).decode("ascii")
    payload_dict = {
        "order_id": "0",
        "order_type": order_type,
        "parameters": parameters,
        "nonce": nonce,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        # Phase 13.5 H6 — advertise the signing pubkey so the daemon
        # can fall back to verifying against it IF AND ONLY IF it
        # matches the daemon's last-checkin-delivered pubkey.
        "signing_pubkey_hex": pubkey_hex,
    }
    signed_payload = json.dumps(payload_dict, sort_keys=True)
    signature = signing_key.sign(signed_payload.encode()).signature.hex()
    if not _HEX_RE.match(signature):
        sys.exit(f"Signature validation failed: got {len(signature)} chars")
    return nonce, signature, signed_payload


def parse_params(param_list: list[str] | None) -> dict:
    """Parse --param KEY=VALUE arguments into a dict.
    Values starting with '{' or '[' are parsed as JSON (for maps/arrays)."""
    if not param_list:
        return {}
    result = {}
    for p in param_list:
        key, sep, value = p.partition("=")
        if not sep:
            sys.exit(f"Invalid --param format: {p!r} (expected KEY=VALUE)")
        # Auto-parse JSON for map/array values (e.g. extra_hosts={"NVDC01":"192.168.88.250"})
        if value and value[0] in "{[":
            try:
                result[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass  # Fall through to string
        result[key] = value
    return result


# Phase 14 (Session 205): privileged order types that MUST have a signed,
# hash-chained, OTS-anchored attestation bundle written BEFORE the order
# is signed. If the attestation fails, the order MUST NOT be created.
PRIVILEGED_ORDER_TYPES = {
    "enable_emergency_access",
    "disable_emergency_access",
    # Session 207 Phase W0 — watchdog fleet-order whitelist. Each of
    # these is consumed ONLY by the appliance-watchdog service (not the
    # main daemon) via its `<appliance_id>-watchdog` bearer. The
    # three-list lockstep with privileged_access_attestation.ALLOWED_EVENTS
    # + migration 218 v_privileged_types is enforced by CI (see
    # scripts/check_privileged_chain_lockstep.py).
    "watchdog_restart_daemon",
    "watchdog_refetch_config",
    "watchdog_reset_pin_store",
    "watchdog_reset_api_key",
    "watchdog_redeploy_daemon",
    "watchdog_collect_diagnostics",
    # Session 207 Phase S escape hatch: time-boxed SSH re-enable for
    # installed systems that shipped with sshd disabled. Watchdog
    # receives, writes operator pubkey to AuthorizedKeysFile, starts
    # sshd, arms a systemd-run transient timer to stop sshd + wipe
    # keys at duration_hours expiry. Bypassable only with a new order.
    "enable_recovery_shell_24h",
    # Session 219 (2026-05-11) — appliance delegated signing key issuance.
    # Pre-fix this endpoint had zero auth (weekly audit 2026-05-11 P0).
    # The resulting Ed25519 keypair signs evidence + audit-trail entries
    # the customer-facing attestation chain relies on — functionally
    # equivalent to signing_key_rotation. Three-list lockstep with
    # privileged_access_attestation.ALLOWED_EVENTS + migration 305
    # v_privileged_types is enforced by CI.
    "delegate_signing_key",
}
PRIVILEGED_RATE_LIMIT_PER_WEEK = 3  # per site, per event_type


async def cmd_create(args: argparse.Namespace) -> None:
    order_type = args.order_type
    if order_type not in VALID_ORDER_TYPES:
        sys.exit(
            f"Unknown order type: {order_type!r}\n"
            f"Valid types: {', '.join(sorted(VALID_ORDER_TYPES))}"
        )

    params = DEFAULT_PARAMS.get(order_type, {}).copy()
    params.update(parse_params(args.param))

    if order_type in REQUIRED_PARAMS:
        missing = [k for k in REQUIRED_PARAMS[order_type] if k not in params]
        if missing:
            sys.exit(
                f"Order type {order_type!r} requires: {', '.join(missing)}\n"
                f"Use: --param {missing[0]}=VALUE"
            )

    # Validate binary_url for update_daemon orders
    if order_type == "update_daemon":
        url = params.get("binary_url", "")
        if not url.startswith("https://"):
            sys.exit(
                f"binary_url must be HTTPS (got: {url!r})\n"
                f"Upload binary to VPS and use: https://api.osiriscare.net/updates/<filename>"
            )

        # Resolve the hostname against public DNS before inserting.
        # Prevents the class of outage where an order points at a
        # domain that never had an A record (observed 2026-04-14 with
        # release.osiriscare.net — appliances DNS-failed every 60s
        # for an hour with no visible alert).
        import socket
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        if not host:
            sys.exit(f"binary_url has no hostname: {url!r}")
        try:
            socket.gethostbyname(host)
        except socket.gaierror as e:
            sys.exit(
                f"binary_url hostname {host!r} does not resolve: {e}\n"
                f"Check the public DNS A record before issuing this order.\n"
                f"Known-good: api.osiriscare.net"
            )

    # ── Task #118: --target-appliance-id / --all-at-site arg handling ──
    import uuid as _uuid_mod
    if getattr(args, "target_appliance_id", None) and getattr(args, "all_at_site", None):
        sys.exit(
            "--target-appliance-id and --all-at-site are mutually exclusive. "
            "Use ONE to scope the order."
        )
    if getattr(args, "target_appliance_id", None):
        # Validate UUID format upfront — typo-class catch at 250-appliance scale.
        try:
            _uuid_mod.UUID(args.target_appliance_id)
        except (ValueError, AttributeError):
            sys.exit(
                f"--target-appliance-id must be a valid UUID, got "
                f"{args.target_appliance_id!r}"
            )
        params["target_appliance_id"] = args.target_appliance_id
    if getattr(args, "all_at_site", None):
        # --all-at-site derives site_id; enumeration happens inside the
        # conn.transaction() block below so the enumeration + bundle +
        # N-INSERTs are atomic.
        params["site_id"] = args.all_at_site

    # ── Phase 14: privileged-order attestation gate ───────────────────
    if order_type in PRIVILEGED_ORDER_TYPES:
        actor_email = (args.actor_email or "").strip()
        reason = (args.reason or "").strip()
        if not actor_email or "@" not in actor_email:
            sys.exit(
                f"Order type {order_type!r} is privileged and requires\n"
                f"  --actor-email <you@yourdomain.com>\n"
                f"to name the human initiating this action. Audit/HIPAA\n"
                f"compliance requires every privileged access to be bound\n"
                f"to a specific, named individual."
            )
        if len(reason) < 20:
            sys.exit(
                f"Order type {order_type!r} requires --reason \"...\" with ≥20 chars.\n"
                f"Describe the incident, change ticket, or operational reason.\n"
                f"This reason is written into the WORM evidence bundle and\n"
                f"visible to the customer + auditors."
            )
        target_site = params.get("site_id")
        if not target_site:
            sys.exit(
                f"Order type {order_type!r} requires --param site_id=<site> "
                f"or --all-at-site <site_id>."
            )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=args.expires)

    signing_key = load_signing_key()
    # Initial sign for the single-target case. The --all-at-site case
    # re-signs per-appliance inside the loop below (each fleet_order
    # gets a distinct signature because target_appliance_id differs).
    nonce, signature, signed_payload = sign_order(
        signing_key, order_type, params, now, expires_at
    )

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        # Set admin context so this works regardless of the DB role.
        # Default DATABASE_URL connects as `mcp` (superuser, exempt
        # from RLS), but operators who run fleet_cli through a
        # least-privilege role (`mcp_app`) need app.is_admin='true'
        # for the fleet_orders RLS policy fleet_orders_admin_only and
        # the compliance_bundles admin_bypass policy. Session-level
        # SET is fine here: this is a direct asyncpg connection (no
        # PgBouncer DISCARD ALL between transactions) and the
        # connection is closed at the end of this function.
        await conn.execute("SET app.is_admin = 'true'")

        # ── Task #118: --all-at-site enumeration (must run BEFORE the
        # txn block so --dry-run can exit cleanly; the actual INSERT
        # loop below re-runs under the txn for atomicity).
        target_appliance_ids: list[str] = []
        site_appliance_rows: list[dict] = []
        if getattr(args, "all_at_site", None):
            site_appliance_rows_raw = await conn.fetch(
                """
                SELECT appliance_id, site_id, mac_address, hostname,
                       status, last_checkin
                  FROM site_appliances
                 WHERE site_id = $1
                   AND deleted_at IS NULL
                 ORDER BY appliance_id
                """,
                args.all_at_site,
            )
            target_appliance_ids = [r["appliance_id"] for r in site_appliance_rows_raw]
            # Counsel Rule 7 (no unauth context): dry-run output is
            # operator-facing CLI but the field allowlist still applies
            # to prevent ip_addresses / daemon_health / agent_public_key
            # leaking through pipe redirection / shared sessions.
            site_appliance_rows = [
                {
                    "appliance_id": r["appliance_id"],
                    "site_id": r["site_id"],
                    "mac": r["mac_address"],
                    "hostname": r["hostname"],
                    "status": r["status"],
                    "last_checkin": (
                        r["last_checkin"].isoformat() if r["last_checkin"] else None
                    ),
                }
                for r in site_appliance_rows_raw
            ]
            if not target_appliance_ids:
                sys.exit(
                    f"--all-at-site: no active appliances found at site "
                    f"{args.all_at_site!r} (deleted_at IS NULL filter applied)"
                )

        # ── --dry-run early exit ────────────────────────────────────
        if getattr(args, "dry_run", False):
            if getattr(args, "all_at_site", None):
                dry_out = {
                    "order_type": order_type,
                    "site_id": args.all_at_site,
                    "target_count": len(site_appliance_rows),
                    "targets": site_appliance_rows,
                    "fan_out": True,
                }
            elif getattr(args, "target_appliance_id", None):
                dry_out = {
                    "order_type": order_type,
                    "target_count": 1,
                    "targets": [{"appliance_id": args.target_appliance_id}],
                    "fan_out": False,
                }
            else:
                dry_out = {
                    "order_type": order_type,
                    "target_count": 1,
                    "site_id": params.get("site_id"),
                    "fan_out": False,
                }
            print(json.dumps(dry_out, indent=2, sort_keys=True))
            return

        # ── Task #118 P0 (rev #125): random-nonce confirm prompt for
        # privileged --all-at-site. Pre-rev the prompt asked the
        # operator to type the appliance COUNT back — at 20-appliance
        # scale operators build muscle memory ("always type 20")
        # which defeats the defensive-friction purpose. Random
        # per-invocation token defeats memorization while staying
        # short enough to type at the prompt. Closes Gate B P1-1.
        if (
            getattr(args, "all_at_site", None)
            and order_type in PRIVILEGED_ORDER_TYPES
            and not args.override_rate_limit
        ):
            N = len(target_appliance_ids)
            # 6 bytes → 8-char URL-safe base64 → ~48 bits of entropy
            # which is plenty for a single-invocation guard. Avoids
            # confusing characters like 1/I/O/0 via url-safe charset.
            confirm_nonce = secrets.token_urlsafe(6)
            print(
                f"\nAbout to issue {order_type!r} to {N} appliances at site "
                f"{args.all_at_site!r}.",
                file=sys.stderr,
            )
            print(
                f"This is a PRIVILEGED action. To confirm, type this "
                f"random token back: {confirm_nonce}",
                file=sys.stderr,
            )
            try:
                confirm = input("token: ").strip()
            except EOFError:
                sys.exit("Confirmation aborted (no stdin).")
            if confirm != confirm_nonce:
                sys.exit(
                    f"Confirmation failed (got {confirm!r}, expected "
                    f"{confirm_nonce!r}). Aborting — no orders issued."
                )

        # ── Task #118 P0 + latent-bug fix: wrap bundle write + N
        # fleet_order INSERTs in ONE asyncpg transaction. Pre-fix
        # fleet_cli.cmd_create never wrapped, so _get_prev_bundle()'s
        # `assert conn.is_in_transaction()` would have failed on every
        # privileged invocation since the assertion landed 2026-05-09
        # (latent bug — no privileged fleet_cli order has actually
        # succeeded in main for 2 weeks). #118 ships the fix as
        # part of the fan-out atomic-txn requirement.
        attestation = None
        fleet_order_rows: list[dict] = []

        async with conn.transaction():
            # ── Phase 14: write the attestation bundle BEFORE the order(s) ──
            # If the bundle write fails, NO fleet_order is created. For
            # --all-at-site fan-out, ONE bundle covers N orders (per
            # Gate A: 1-bundle:N-orders shape — mig 175 trigger uses
            # EXISTS which is satisfiable by N rows citing the same
            # bundle_id).
            if order_type in PRIVILEGED_ORDER_TYPES:
                sys.path.insert(0, "/app")
                try:
                    from dashboard_api.privileged_access_attestation import (
                        create_privileged_access_attestation,
                        count_recent_privileged_events,
                        PrivilegedAccessAttestationError,
                    )
                except ImportError:
                    sys.exit(
                        "Phase 14 attestation module unavailable — refusing "
                        "privileged order. Check deployment."
                    )
                target_site = params["site_id"]
                if not args.override_rate_limit:
                    recent = await count_recent_privileged_events(
                        conn, target_site, days=7, event_type=order_type,
                    )
                    if recent >= PRIVILEGED_RATE_LIMIT_PER_WEEK:
                        sys.exit(
                            f"RATE LIMIT: site {target_site!r} has had "
                            f"{recent} {order_type} events in the last 7 days "
                            f"(max {PRIVILEGED_RATE_LIMIT_PER_WEEK}). Anomalous "
                            f"activity. If this is a genuine incident, pass "
                            f"--override-rate-limit and document in the reason."
                        )
                try:
                    attestation = await create_privileged_access_attestation(
                        conn,
                        site_id=target_site,
                        event_type=order_type,
                        actor_email=args.actor_email.strip(),
                        reason=args.reason.strip(),
                        # Task #127 closure (Gate B P1-3): fleet_cli
                        # post-#118 uses the cross-link UPDATE pattern
                        # (admin_audit_log.details->>'fleet_order_ids'
                        # array) instead of the singular fleet_order_id
                        # kwarg. Omitting here = the kwarg's None
                        # default — matches privileged_access_api's
                        # behavior when it has no order yet. The
                        # privileged_access_api flow still passes a
                        # real fleet_order_id when it has one.
                        duration_minutes=int(params.get("duration_minutes", 0))
                            if str(params.get("duration_minutes", "")).isdigit()
                            else None,
                        # Task #118 P0: encode count=N + target_appliance_ids
                        # in the bundle summary so the auditor surface shows
                        # the multi-target shape.
                        target_appliance_ids=(
                            target_appliance_ids
                            if getattr(args, "all_at_site", None)
                            else None
                        ),
                    )
                except PrivilegedAccessAttestationError as e:
                    sys.exit(
                        f"Attestation write failed — REFUSING to create privileged order.\n"
                        f"  {e}\n"
                        f"This is a HARD STOP. The order is not signed or inserted."
                    )

                # Phase 14/Migration 175 chain enforcement: bundle_id +
                # chain_position + actor_email in params + covered by
                # signed_payload. mig 175 trigger REJECTS the INSERT
                # without these.
                params["attestation_bundle_id"] = attestation["bundle_id"]
                params["attestation_chain_position"] = attestation["chain_position"]
                params["attestation_actor"] = args.actor_email.strip()

            # ── Sign + INSERT loop ────────────────────────────────
            # For --all-at-site: loop over target_appliance_ids; each
            # order gets distinct target_appliance_id in params + a
            # distinct signature. ALL orders cite the SAME bundle_id.
            # For non-fan-out: single iteration.
            if getattr(args, "all_at_site", None):
                iter_targets = target_appliance_ids
            else:
                iter_targets = [None]  # one iteration, params unchanged

            for per_target in iter_targets:
                per_params = dict(params)
                if per_target is not None:
                    per_params["target_appliance_id"] = per_target
                per_nonce, per_signature, per_signed_payload = sign_order(
                    signing_key, order_type, per_params, now, expires_at,
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO fleet_orders
                        (order_type, parameters, skip_version, status, expires_at,
                         created_by, nonce, signature, signed_payload)
                    VALUES ($1, $2::jsonb, $3, 'active', $4, $5, $6, $7, $8)
                    RETURNING id, order_type, status, created_at, expires_at
                    """,
                    order_type,
                    json.dumps(per_params),
                    args.skip_version,
                    expires_at,
                    args.actor_email.strip() if order_type in PRIVILEGED_ORDER_TYPES else "fleet-cli",
                    per_nonce,
                    per_signature,
                    per_signed_payload,
                )
                fleet_order_rows.append(dict(row))

            # ── Cross-link UPDATE: post-loop aggregate so N orders
            # sharing ONE bundle don't overwrite each other (Gate A
            # P1-3 closure). Always emit `fleet_order_ids` as JSON
            # array — N=1 case = array of 1 element.
            if attestation is not None:
                try:
                    await conn.execute(
                        "UPDATE admin_audit_log SET details = details || "
                        "jsonb_build_object('fleet_order_ids', $1::jsonb) "
                        "WHERE (details->>'bundle_id') = $2",
                        json.dumps([str(r["id"]) for r in fleet_order_rows]),
                        attestation["bundle_id"],
                    )
                except Exception as e:
                    # audit cross-link is best-effort — surface to stderr
                    print(
                        f"WARNING: fleet_order audit cross-link failed "
                        f"({len(fleet_order_rows)} order(s), bundle_id="
                        f"{attestation['bundle_id']}): {e}",
                        file=sys.stderr,
                    )

        # ── Operator-facing output (post-txn) ───────────────────────
        if len(fleet_order_rows) == 1:
            row = fleet_order_rows[0]
            print(f"Fleet order created:")
            print(f"  ID:         {row['id']}")
            print(f"  Type:       {row['order_type']}")
            print(f"  Status:     {row['status']}")
            print(f"  Parameters: {json.dumps(params)}")
            print(f"  Created:    {row['created_at'].isoformat()}")
            print(f"  Expires:    {row['expires_at'].isoformat()}")
            if args.skip_version:
                print(f"  Skip ver:   {args.skip_version}")
        else:
            print(f"Fleet orders created ({len(fleet_order_rows)} at site "
                  f"{args.all_at_site!r}):")
            for row in fleet_order_rows:
                print(f"  {row['id']}  ({row['status']}, expires "
                      f"{row['expires_at'].isoformat()})")
        if attestation:
            print(f"  Attestation bundle_id: {attestation['bundle_id']}")
            print(f"  Attestation hash: {attestation['bundle_hash']}")
            print(f"  Chain position: {attestation['chain_position']}")
            if len(fleet_order_rows) > 1:
                print(f"  Fan-out: 1 bundle ↔ {len(fleet_order_rows)} orders")
    finally:
        await conn.close()


async def cmd_list(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        rows = await conn.fetch(
            """
            SELECT fo.id, fo.order_type, fo.parameters, fo.skip_version,
                   fo.status, fo.created_at, fo.expires_at, fo.created_by,
                   (SELECT COUNT(*) FROM fleet_order_completions c
                    WHERE c.fleet_order_id = fo.id AND c.status = 'completed') as done,
                   (SELECT COUNT(*) FROM fleet_order_completions c
                    WHERE c.fleet_order_id = fo.id AND c.status = 'failed') as fail,
                   (SELECT COUNT(*) FROM fleet_order_completions c
                    WHERE c.fleet_order_id = fo.id AND c.status = 'skipped') as skip
            FROM fleet_orders fo
            WHERE ($1::text IS NULL OR fo.status = $1)
            ORDER BY fo.created_at DESC
            LIMIT $2
            """,
            args.status,
            args.limit,
        )

        if not rows:
            print("No fleet orders found.")
            return

        # Header
        print(f"{'ID':<38} {'TYPE':<18} {'STATUS':<12} {'CREATED':<22} {'EXPIRES':<22} {'BY':<12} {'D':>2} {'F':>2} {'S':>2}")
        print("-" * 132)
        for r in rows:
            oid = str(r["id"])[:36]
            created = r["created_at"].strftime("%Y-%m-%d %H:%M UTC")
            expires = r["expires_at"].strftime("%Y-%m-%d %H:%M UTC")
            by = (r["created_by"] or "")[:11]
            print(
                f"{oid:<38} {r['order_type']:<18} {r['status']:<12} "
                f"{created:<22} {expires:<22} {by:<12} "
                f"{r['done']:>2} {r['fail']:>2} {r['skip']:>2}"
            )
    finally:
        await conn.close()


async def cmd_cancel(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    try:
        # Admin context for fleet_orders_admin_only RLS policy.
        # See cmd_create for rationale.
        await conn.execute("SET app.is_admin = 'true'")
        result = await conn.execute(
            "UPDATE fleet_orders SET status = 'cancelled' WHERE id = $1 AND status = 'active'",
            args.order_id,
        )
        if result == "UPDATE 1":
            print(f"Cancelled order {args.order_id}")
        else:
            print(f"No active order found with ID {args.order_id}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fleet order CLI — create, list, and cancel fleet orders."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a new fleet order")
    p_create.add_argument("order_type", help="Order type (e.g. nixos_rebuild, update_daemon)")
    p_create.add_argument("--param", action="append", help="Parameter KEY=VALUE (repeatable)")
    p_create.add_argument("--expires", type=int, default=24, help="Expiry in hours (default: 24)")
    p_create.add_argument("--skip-version", help="Skip appliances at this version")
    # Phase 14: privileged-order attestation inputs (required for
    # enable_emergency_access / disable_emergency_access)
    p_create.add_argument(
        "--actor-email",
        help="Email of the human initiating this action. REQUIRED for "
             "privileged order types. Written into the WORM attestation "
             "bundle and admin_audit_log.",
    )
    p_create.add_argument(
        "--reason",
        help="Operational reason (≥20 chars). REQUIRED for privileged "
             "order types. Written into the WORM attestation bundle "
             "and visible to customers + auditors.",
    )
    p_create.add_argument(
        "--override-rate-limit",
        action="store_true",
        help="Override the per-site-per-week privileged-access rate "
             "limit. Requires an incident-track reason.",
    )
    # Task #118 (multi-device Counsel Rule 3 UX): first-class target
    # appliance args. Pre-#118 the only way to target a specific
    # appliance was --param target_appliance_id=<UUID> which is typo-
    # risk at 250-appliance scale.
    p_create.add_argument(
        "--target-appliance-id",
        metavar="UUID",
        help="Target a specific appliance (alternative to --param "
             "target_appliance_id=...). Mutually exclusive with "
             "--all-at-site.",
    )
    p_create.add_argument(
        "--all-at-site",
        metavar="SITE_ID",
        help="Fan out: issue the order to EVERY active appliance at "
             "this site_id (one attestation bundle, N fleet_orders). "
             "For privileged order types, requires --confirm + the "
             "operator types the appliance COUNT back. Mutually "
             "exclusive with --target-appliance-id.",
    )
    p_create.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the target appliances + the order shape, then "
             "exit without signing or inserting. JSON-formatted for "
             "piping. Output excludes ip_addresses / daemon_health "
             "/ agent_public_key per Counsel Rule 7 (Layer-2 leak).",
    )

    # list
    p_list = sub.add_parser("list", help="List fleet orders")
    p_list.add_argument("--status", choices=["active", "cancelled", "completed"], help="Filter by status")
    p_list.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")

    # cancel
    p_cancel = sub.add_parser("cancel", help="Cancel an active fleet order")
    p_cancel.add_argument("order_id", help="Fleet order UUID")

    args = parser.parse_args()

    import uuid as _uuid

    if args.command == "cancel":
        try:
            args.order_id = _uuid.UUID(args.order_id)
        except ValueError:
            sys.exit(f"Invalid UUID: {args.order_id}")

    handler = {"create": cmd_create, "list": cmd_list, "cancel": cmd_cancel}[args.command]
    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
