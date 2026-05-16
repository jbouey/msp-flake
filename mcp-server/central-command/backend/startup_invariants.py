"""Startup security-invariant check (Phase 15 enterprise-grade hygiene).

Runs once at lifespan startup. Verifies every security-critical
DB-layer protection is actually installed. Logs ERROR + writes an
admin_audit_log row for each broken invariant, so the operator sees
the failure in their dashboard and the log shipper pages on-call.

Does NOT crash the server. A broken invariant is a CREDIBILITY event,
not an availability event — the code that WRITES privileged rows
still refuses without the chain, and a fail-loud log is stronger than
a fail-closed process that operators will just restart.

Invariants checked:

  INV-CHAIN-175
    Trigger `trg_enforce_privileged_chain` exists on fleet_orders.
    Without it, privileged fleet orders can be INSERTed without an
    attestation bundle (chain-of-custody break).

  INV-CHAIN-176
    Trigger `trg_enforce_privileged_immutability` exists on
    fleet_orders. Without it, attestation_bundle_id / site_id /
    signed_payload can be UPDATEd post-insert (chain mutation).

  INV-EVIDENCE-DELETE
    Trigger `prevent_audit_deletion` exists on compliance_bundles
    (migration 151). Without it, evidence can be DELETEd.

  INV-AUDIT-DELETE
    Same trigger on admin_audit_log / client_audit_log. HIPAA
    §164.316(b)(2)(i) 7-year retention requires append-only.

  INV-COMPLETED-LOCK
    Trigger `prevent_completed_order_modification` exists on
    fleet_orders (migration 151).

  INV-SIGNING-KEY
    Signing key file exists, readable, non-empty.

  INV-MAGIC-LINK-TABLE
    privileged_access_magic_links table exists (migration 178).

Result surfaced via:
  - logger.error(...) per failure (log shipper alerts)
  - Prometheus gauge osiriscare_startup_invariant_ok{name=...} (1|0)
  - admin_audit_log row per failure (operator-visible)
  - Returns list of broken invariants so lifespan can print a
    summary banner at startup.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


SIGNING_KEY_PATH = os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key")

# Vault P0 iter-4 P0-B + P0-C (2026-05-16):
#   - OUTER timeout (5.0s) wraps the entire Vault probe coroutine via
#     asyncio.wait_for. asyncio.wait_for cancels awaits ONLY — pure
#     sync calls (httpx.Client.get / .post) can't be cancelled.
#   - INNER timeout (4.0s) sets the per-request HTTP timeout so the
#     socket-level operation completes (or hard-errors) BEFORE the
#     asyncio cancellation fires — clean thread exit, no thread leak.
#   - The 1.0s buffer between INNER and OUTER is the iter-3 root-cause
#     mitigation. Don't equalize them; don't lower the inner; don't
#     raise the outer.
VAULT_PROBE_OUTER_TIMEOUT_S = 5.0
VAULT_PROBE_INNER_TIMEOUT_S = 4.0


@dataclass
class InvariantResult:
    name: str
    ok: bool
    detail: str = ""


async def _check_trigger_exists(
    conn, trigger_name: str, table_name: str
) -> bool:
    """Verify a pg_trigger row exists for (trigger_name, table_name)."""
    row = await conn.fetchrow(
        """
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE t.tgname = $1
          AND c.relname = $2
          AND NOT t.tgisinternal
        LIMIT 1
        """,
        trigger_name, table_name,
    )
    return row is not None


async def _check_table_exists(conn, table_name: str) -> bool:
    row = await conn.fetchrow(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = $1 LIMIT 1",
        table_name,
    )
    return row is not None


async def check_all_invariants(conn) -> List[InvariantResult]:
    """Run the full invariant suite. Returns one InvariantResult per
    check. Caller decides what to do with failures (log, alert, refuse
    to accept writes, etc.)."""
    results: List[InvariantResult] = []

    # ── Chain enforcement ─────────────────────────────────────────
    ok = await _check_trigger_exists(
        conn, "trg_enforce_privileged_chain", "fleet_orders"
    )
    results.append(InvariantResult(
        "INV-CHAIN-175", ok,
        "" if ok else (
            "Privileged INSERT without attestation_bundle_id is NOT "
            "rejected — reapply migration 175_privileged_chain_enforcement.sql"
        ),
    ))

    ok = await _check_trigger_exists(
        conn, "trg_enforce_privileged_immutability", "fleet_orders"
    )
    results.append(InvariantResult(
        "INV-CHAIN-176", ok,
        "" if ok else (
            "Privileged UPDATE of attestation_bundle_id / site_id / "
            "signed_payload is NOT blocked — reapply migration "
            "176_privileged_chain_update_guard.sql"
        ),
    ))

    # ── Evidence + audit immutability ────────────────────────────
    # Migration 151 uses a shared trigger function
    # `prevent_audit_deletion` across multiple tables. Check one
    # sentinel table per class.
    for table, inv_name in [
        ("compliance_bundles", "INV-EVIDENCE-DELETE"),
        ("admin_audit_log", "INV-AUDIT-DELETE-ADMIN"),
        ("client_audit_log", "INV-AUDIT-DELETE-CLIENT"),
        ("portal_access_log", "INV-AUDIT-DELETE-PORTAL"),
    ]:
        # We don't know the exact trigger name per table from this
        # context, so check that SOME BEFORE-DELETE trigger exists.
        # pg_trigger.tgtype is a bitfield (src/include/catalog/pg_trigger.h):
        #   TRIGGER_TYPE_ROW      1 << 0 = 0x01 (row-level vs statement)
        #   TRIGGER_TYPE_BEFORE   1 << 1 = 0x02 (SET means BEFORE; CLEAR = AFTER)
        #   TRIGGER_TYPE_INSERT   1 << 2 = 0x04
        #   TRIGGER_TYPE_DELETE   1 << 3 = 0x08
        #   TRIGGER_TYPE_UPDATE   1 << 4 = 0x10
        # So BEFORE DELETE trigger = (tgtype & 2) = 2 AND (tgtype & 8) = 8
        # We accept either row or statement granularity.
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS n
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = $1
              AND (t.tgtype & 2) = 2   -- BEFORE (bit 1 SET)
              AND (t.tgtype & 8) = 8   -- DELETE
              AND NOT t.tgisinternal
            """,
            table,
        )
        ok = bool(row and row["n"] > 0)
        results.append(InvariantResult(
            inv_name, ok,
            "" if ok else (
                f"No BEFORE DELETE trigger on {table} — retention "
                f"immutability compromised. Reapply migration 151."
            ),
        ))

    # ── Completed-order lock ─────────────────────────────────────
    ok = await _check_trigger_exists(
        conn, "trg_prevent_completed_order_modification", "fleet_orders"
    )
    # That's the expected name if 151 wires it with trg_ prefix;
    # some earlier migrations used a different convention. Fall back
    # to any BEFORE UPDATE trigger.
    if not ok:
        # BEFORE UPDATE = (tgtype & 2) = 2 AND (tgtype & 16) = 16
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS n
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = 'fleet_orders'
              AND (t.tgtype & 2) = 2   -- BEFORE
              AND (t.tgtype & 16) = 16 -- UPDATE
              AND NOT t.tgisinternal
              AND t.tgname LIKE '%complet%'
            """
        )
        ok = bool(row and row["n"] > 0)
    results.append(InvariantResult(
        "INV-COMPLETED-LOCK", ok,
        "" if ok else (
            "No BEFORE UPDATE trigger guarding status='completed' on "
            "fleet_orders — historical orders can be rewritten. "
            "Reapply migration 151."
        ),
    ))

    # ── Signing key presence ─────────────────────────────────────
    try:
        raw = pathlib.Path(SIGNING_KEY_PATH).read_bytes().strip()
        ok = len(raw) >= 16
        detail = "" if ok else f"signing key file exists but is <16 bytes"
    except Exception as e:
        ok = False
        detail = f"signing key unreadable at {SIGNING_KEY_PATH}: {e}"
    results.append(InvariantResult("INV-SIGNING-KEY", ok, detail))

    # ── Magic-link tracking table ────────────────────────────────
    ok = await _check_table_exists(conn, "privileged_access_magic_links")
    results.append(InvariantResult(
        "INV-MAGIC-LINK-TABLE", ok,
        "" if ok else (
            "privileged_access_magic_links table missing — magic-link "
            "approval flow will fail. Apply migration 178."
        ),
    ))

    # ── ENABLE ALWAYS on chain triggers (session_replication_role
    # bypass defense from migration 179) ─────────────────────────
    # pg_trigger.tgenabled: 'O' origin-only (default; skipped in
    # replica mode), 'A' always (fires regardless). We REQUIRE 'A'
    # on the chain-enforcement triggers so a superuser cannot
    # `SET session_replication_role='replica'` to bypass the chain.
    rows = await conn.fetch(
        """
        SELECT tgname, tgenabled
        FROM pg_trigger
        WHERE tgname IN (
            'trg_enforce_privileged_chain',
            'trg_enforce_privileged_immutability'
        ) AND NOT tgisinternal
        """
    )
    # pg_trigger.tgenabled is 'char' type; asyncpg returns it as bytes.
    # Normalize to str so comparisons work regardless.
    def _tgenabled_str(v):
        if isinstance(v, bytes):
            return v.decode()
        return v
    status = {r["tgname"]: _tgenabled_str(r["tgenabled"]) for r in rows}
    always_ok = (
        status.get("trg_enforce_privileged_chain") == "A"
        and status.get("trg_enforce_privileged_immutability") == "A"
    )
    results.append(InvariantResult(
        "INV-CHAIN-ALWAYS-ENABLED", always_ok,
        "" if always_ok else (
            "Chain triggers are not ENABLE ALWAYS — "
            f"got {status}. A superuser can bypass by setting "
            "session_replication_role='replica'. Apply migration 179."
        ),
    ))

    # ── TRUNCATE defenses on evidence + audit tables ─────────────
    # Migration 179 adds BEFORE TRUNCATE triggers so bulk wipe is
    # blocked even by a superuser. pg_trigger tgtype bit for TRUNCATE
    # is 1 << 5 = 32. BEFORE bit still 2.
    for table, inv in [
        ("compliance_bundles", "INV-TRUNCATE-EVIDENCE"),
        ("admin_audit_log", "INV-TRUNCATE-AUDIT-ADMIN"),
        ("client_audit_log", "INV-TRUNCATE-AUDIT-CLIENT"),
        ("portal_access_log", "INV-TRUNCATE-AUDIT-PORTAL"),
    ]:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS n
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE c.relname = $1
              AND (t.tgtype & 2) = 2    -- BEFORE
              AND (t.tgtype & 32) = 32  -- TRUNCATE
              AND NOT t.tgisinternal
            """,
            table,
        )
        ok = bool(row and row["n"] > 0)
        results.append(InvariantResult(
            inv, ok,
            "" if ok else (
                f"No BEFORE TRUNCATE trigger on {table} — bulk wipe "
                f"bypasses DELETE protection. Apply migration 179."
            ),
        ))

    # ── INV-SIGNING-BACKEND-VAULT (Vault Phase C iter-4 Commit 2) ──
    # Bootstrap-INSERT observed (key_name, key_version, pubkey) into
    # vault_signing_key_versions; verify a known_good=TRUE row exists
    # for the observed version. asyncio.wait_for OUTER bounds the
    # whole probe; httpx inner timeout bounds the socket (one full
    # second under the asyncio timeout — clean thread exit on Vault
    # hang). All paths log + return InvariantResult; NEVER raise.
    try:
        vault_inv = await asyncio.wait_for(
            _check_signing_backend_vault(conn),
            timeout=VAULT_PROBE_OUTER_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        vault_inv = InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=False,
            detail=(
                f"vault probe exceeded {VAULT_PROBE_OUTER_TIMEOUT_S}s "
                f"— startup proceeding non-blocking (CREDIBILITY event, "
                f"not availability event)"
            ),
        )
    except Exception as e:
        vault_inv = InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=False,
            detail=f"vault probe raised: {type(e).__name__}: {e}",
        )
    results.append(vault_inv)

    return results


async def _check_signing_backend_vault(conn) -> InvariantResult:
    """Vault probe inner — sync get_signing_backend + sync httpx calls
    wrapped via asyncio.to_thread so the outer asyncio.wait_for can
    actually cancel a hang. P0-B closure (Gate A iter-4 2026-05-16).

    Bootstrap-INSERT uses ON CONFLICT (key_name, key_version) DO NOTHING
    — Gate A P0 #3 explicit: never DO UPDATE (first-observed telemetry
    must not be masked by side-effect updates on every restart). All
    $N params are explicitly cast (Gate A P0 #8).

    Non-fatal on every error path; returns InvariantResult.ok=False
    with a detail string. Lifespan continues regardless.
    """
    import os as _os

    # Synthetic-soak / shadow-mode carve-out: when SIGNING_BACKEND=file
    # AND no Vault env is configured, skip the probe entirely. Common
    # for dev / chaos-lab / staging without Vault.
    backend_mode = _os.getenv("SIGNING_BACKEND", "file").strip().lower()
    vault_addr = _os.getenv("VAULT_ADDR", "").strip()
    if backend_mode == "file" and not vault_addr:
        return InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=True,
            detail="SIGNING_BACKEND=file + VAULT_ADDR unset — probe skipped",
        )

    # Build backend in a thread — guards against AppRole login hang.
    from .signing_backend import get_signing_backend, VAULT_SIGNING_KEY_NAME
    try:
        backend = await asyncio.to_thread(get_signing_backend)
    except Exception as e:
        return InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=False,
            detail=f"get_signing_backend raised: {type(e).__name__}: {e}",
        )

    # Resolve the primary VaultSigningBackend through any ShadowBackend
    # wrapper. For pure file mode this returns the FileSigningBackend
    # which has no Vault to probe; treat as skip.
    primary = getattr(backend, "_primary", backend)
    primary_name = getattr(primary, "name", "file")
    if primary_name != "vault":
        return InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=True,
            detail=(
                f"primary backend is {primary_name!r} (not vault); "
                f"probe deferred until cutover"
            ),
        )

    # Pull (key_name, key_version, pubkey) from Vault. httpx.Client
    # internal timeout = 5.0; the to_thread + wait_for=5.0 outer caps
    # the entire chain. Per-request override to 4.0 here so the socket
    # has 1.0s buffer under the asyncio cancellation (P0-C).
    try:
        key_name = VAULT_SIGNING_KEY_NAME

        def _probe_sync():
            primary._login_if_needed()  # raises SigningBackendError on failure
            resp = primary._client.get(
                f"/v1/transit/keys/{key_name}",
                headers={"X-Vault-Token": primary._token},
                timeout=VAULT_PROBE_INNER_TIMEOUT_S,
            )
            resp.raise_for_status()
            d = resp.json().get("data") or {}
            latest_version = int(d.get("latest_version") or 1)
            keys = d.get("keys") or {}
            entry = keys.get(str(latest_version)) or {}
            pub = entry.get("public_key")
            return key_name, latest_version, pub

        key_name, key_version, pubkey_pem = await asyncio.to_thread(_probe_sync)
    except Exception as e:
        return InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=False,
            detail=f"vault transit read raised: {type(e).__name__}: {e}",
        )

    if not pubkey_pem:
        return InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=False,
            detail=(
                f"vault returned no public_key for key_name={key_name!r} "
                f"version={key_version}"
            ),
        )

    # Normalize to hex for storage. Vault returns PEM-ish or base64 —
    # treat the raw string as hex if it parses, else as base64.
    import binascii
    import base64
    try:
        pubkey_bytes = bytes.fromhex(pubkey_pem.strip())
    except (ValueError, binascii.Error):
        try:
            pubkey_bytes = base64.b64decode(pubkey_pem.strip())
        except Exception:
            pubkey_bytes = pubkey_pem.strip().encode()
    pubkey_hex = pubkey_bytes.hex()

    # Bootstrap-INSERT — Gate A P0 #3 NEVER DO UPDATE; P0 #8 explicit casts.
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO vault_signing_key_versions
                    (key_name, key_version, pubkey_hex, pubkey_b64,
                     first_observed_at, last_observed_at)
                VALUES
                    ($1::text, $2::int, $3::text,
                     encode(decode($3::text, 'hex'), 'base64'),
                     now(), now())
                ON CONFLICT (key_name, key_version) DO NOTHING
                """,
                key_name, key_version, pubkey_hex,
            )
    except Exception as e:
        return InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=False,
            detail=f"vault_signing_key_versions write raised: {type(e).__name__}: {e}",
        )

    # Verify known_good row exists for this (key_name, key_version).
    row = await conn.fetchrow(
        """
        SELECT known_good, approved_by, approved_at
          FROM vault_signing_key_versions
         WHERE key_name = $1::text AND key_version = $2::int
        """,
        key_name, key_version,
    )
    if not row or not row["known_good"]:
        return InvariantResult(
            "INV-SIGNING-BACKEND-VAULT",
            ok=False,
            detail=(
                f"observed vault key {key_name!r} v{key_version} but no "
                f"known_good row — operator must approve via admin "
                f"endpoint. attacker-rotated-key class until approved."
            ),
        )

    return InvariantResult(
        "INV-SIGNING-BACKEND-VAULT",
        ok=True,
        detail=(
            f"vault key {key_name!r} v{key_version} approved by "
            f"{row['approved_by']!r}"
        ),
    )


async def enforce_startup_invariants(conn, metrics_registry=None) -> int:
    """Run the full check, log + audit each failure, update the
    Prometheus gauge. Returns the number of broken invariants (0 = green).

    `metrics_registry` is optional — when provided, must expose
    `.startup_invariant_ok.labels(name).set(value)`.
    """
    import json
    results = await check_all_invariants(conn)

    broken = [r for r in results if not r.ok]
    for r in results:
        if metrics_registry is not None:
            try:
                metrics_registry.startup_invariant_ok.labels(name=r.name).set(
                    1 if r.ok else 0
                )
            except Exception:
                pass

    if not broken:
        logger.info(
            "startup_invariants_ok",
            extra={"checked": len(results)},
        )
        return 0

    # Log ERROR per broken invariant
    for r in broken:
        logger.error(
            "STARTUP_INVARIANT_BROKEN",
            extra={
                "invariant": r.name,
                "detail": r.detail,
            },
        )

    # Persist to admin_audit_log in one batch so a single query covers
    # the whole failed suite (one row per invariant).
    try:
        async with conn.transaction():
            for r in broken:
                await conn.execute(
                    """
                    INSERT INTO admin_audit_log
                    (action, target, details, created_at)
                    VALUES (
                        'STARTUP_INVARIANT_BROKEN',
                        $1,
                        $2::jsonb,
                        NOW()
                    )
                    """,
                    f"invariant:{r.name}",
                    json.dumps({
                        "invariant": r.name,
                        "detail": r.detail,
                        "broken_at_startup": True,
                    }),
                )
    except Exception as e:
        logger.error(
            "startup_invariant_audit_write_failed",
            extra={"error": str(e)},
            exc_info=True,
        )

    # One-shot summary banner for humans at startup
    logger.error(
        "STARTUP_INVARIANTS_DEGRADED",
        extra={
            "broken_count": len(broken),
            "broken": [r.name for r in broken],
            "note": (
                "Server is running but the DB layer is not fully "
                "protecting privileged operations. Investigate before "
                "accepting writes."
            ),
        },
    )

    return len(broken)
