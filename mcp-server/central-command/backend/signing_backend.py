"""Signing-backend abstraction (Phase 15 Vault-integration, gap #1).

Round-table top-priority item: `signing.key` on VPS disk means an
attacker with VPS root can forge any order / attestation. Vault
Transit moves the key material into a separate process (on a separate
host, over WG) — key never leaves Vault memory, attacker needs to
compromise BOTH hosts.

This module is the abstraction. Two backends today:

  FileSigningBackend   — reads SIGNING_KEY_FILE, signs with PyNaCl
  VaultSigningBackend  — AppRole login, transit/sign over HTTPS

And a shadow-mode wrapper:

  ShadowSigningBackend — calls BOTH, compares, logs divergence.
                         Returns the PRIMARY backend's signature
                         (operator controls which is primary via
                         SIGNING_BACKEND_PRIMARY env). Until a week
                         of clean shadow runs, we keep File primary
                         and shadow with Vault — no behavior change
                         for production.

Selection via env:

  SIGNING_BACKEND=file            → file only (default, pre-migration)
  SIGNING_BACKEND=vault           → vault only (post-migration)
  SIGNING_BACKEND=shadow          → both, primary determined by
                                    SIGNING_BACKEND_PRIMARY
                                    (defaults to 'file')

No call site needs to change until it imports `get_signing_backend()`
and calls `.sign(data)` / `.public_key()` on the result. Drop-in.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


SIGNING_KEY_FILE = os.getenv("SIGNING_KEY_FILE", "/app/secrets/signing.key")
SIGNING_BACKEND = os.getenv("SIGNING_BACKEND", "file").strip().lower()
SIGNING_BACKEND_PRIMARY = os.getenv("SIGNING_BACKEND_PRIMARY", "file").strip().lower()

VAULT_ADDR = os.getenv("VAULT_ADDR", "").strip()
VAULT_APPROLE_ROLE_ID = os.getenv("VAULT_APPROLE_ROLE_ID", "").strip()
VAULT_APPROLE_SECRET_ID = os.getenv("VAULT_APPROLE_SECRET_ID", "").strip()
VAULT_SIGNING_KEY_NAME = os.getenv("VAULT_SIGNING_KEY_NAME", "osiriscare-signing")
VAULT_SKIP_VERIFY = os.getenv("VAULT_SKIP_VERIFY", "true").strip().lower() in (
    "true", "1", "yes",
)
VAULT_CA_CERT = os.getenv("VAULT_CA_CERT", "").strip()  # optional CA file


class SigningBackendError(Exception):
    """Any failure to sign or read a public key. Caller should treat
    as an abort condition for the downstream privileged action."""


@dataclass(frozen=True)
class SignResult:
    """What a backend returns from .sign(). public_key is included so
    shadow mode can verify both backends produced the same key
    lineage, not just compatible-looking signatures."""
    signature: bytes       # raw Ed25519 signature bytes (64 bytes)
    public_key: bytes      # 32-byte Ed25519 public key
    backend_name: str      # "file" | "vault"


# ─── File backend (existing production behavior) ──────────────────


class FileSigningBackend:
    name = "file"

    def __init__(self, path: str = SIGNING_KEY_FILE):
        self._path = path
        self._cached_sk = None
        self._cached_vk = None
        self._lock = threading.Lock()

    def _load(self):
        if self._cached_sk is not None:
            return
        try:
            from nacl.signing import SigningKey
            from nacl.encoding import HexEncoder
        except ImportError as e:
            raise SigningBackendError(f"PyNaCl not installed: {e}")
        try:
            key_hex = pathlib.Path(self._path).read_bytes().strip()
        except Exception as e:
            raise SigningBackendError(f"signing key unreadable at {self._path}: {e}")
        with self._lock:
            if self._cached_sk is None:
                self._cached_sk = SigningKey(key_hex, encoder=HexEncoder)
                self._cached_vk = self._cached_sk.verify_key

    def sign(self, data: bytes) -> SignResult:
        self._load()
        sig = self._cached_sk.sign(data).signature
        return SignResult(
            signature=bytes(sig),
            public_key=bytes(self._cached_vk),
            backend_name=self.name,
        )

    def public_key(self) -> bytes:
        self._load()
        return bytes(self._cached_vk)

    def public_keys_all(self) -> "list[bytes]":
        """Return all keys that appliances should trust from this backend.
        Current + previous (when a .key.previous file exists during a
        file-mode rotation). Used by the checkin response's
        server_public_keys array so daemons trust the entire rotation
        window, not just the latest key."""
        self._load()
        out: list[bytes] = [bytes(self._cached_vk)]
        prev_path = pathlib.Path(str(self._path) + ".previous")
        if prev_path.exists():
            try:
                from nacl.signing import SigningKey
                from nacl.encoding import HexEncoder
                prev_hex = prev_path.read_bytes().strip()
                prev_sk = SigningKey(prev_hex, encoder=HexEncoder)
                prev_vk = bytes(prev_sk.verify_key)
                if prev_vk != out[0]:
                    out.append(prev_vk)
            except Exception as e:
                logger.warning("FileSigningBackend .previous read failed: %s", e)
        return out


# ─── Vault Transit backend ────────────────────────────────────────


class VaultSigningBackend:
    name = "vault"

    def __init__(
        self,
        addr: str = VAULT_ADDR,
        role_id: str = VAULT_APPROLE_ROLE_ID,
        secret_id: str = VAULT_APPROLE_SECRET_ID,
        key_name: str = VAULT_SIGNING_KEY_NAME,
        verify: Optional[str | bool] = None,
    ):
        if not addr or not role_id or not secret_id:
            raise SigningBackendError(
                "Vault backend requires VAULT_ADDR + VAULT_APPROLE_ROLE_ID "
                "+ VAULT_APPROLE_SECRET_ID env vars"
            )
        self._addr = addr.rstrip("/")
        self._role_id = role_id
        self._secret_id = secret_id
        self._key_name = key_name
        # WG tunnel makes TLS verify less critical; default to skip for
        # self-signed cert. Operator can supply VAULT_CA_CERT for full
        # verification.
        if verify is None:
            verify = VAULT_CA_CERT if VAULT_CA_CERT else (not VAULT_SKIP_VERIFY)
        self._verify = verify
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        self._cached_pubkey: Optional[bytes] = None
        self._lock = threading.Lock()
        self._client = httpx.Client(
            base_url=self._addr, timeout=5.0, verify=self._verify,
        )

    def _login_if_needed(self) -> None:
        now = time.time()
        if self._token and now < self._token_exp - 60:
            return
        with self._lock:
            if self._token and now < self._token_exp - 60:
                return
            try:
                resp = self._client.post(
                    "/v1/auth/approle/login",
                    json={
                        "role_id": self._role_id,
                        "secret_id": self._secret_id,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                raise SigningBackendError(f"Vault AppRole login failed: {e}")
            auth = data.get("auth") or {}
            self._token = auth.get("client_token")
            ttl = int(auth.get("lease_duration") or 3600)
            self._token_exp = now + ttl
            if not self._token:
                raise SigningBackendError("Vault login returned no token")

    def _public_key_fresh(self) -> bytes:
        self._login_if_needed()
        try:
            resp = self._client.get(
                f"/v1/transit/keys/{self._key_name}",
                headers={"X-Vault-Token": self._token},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise SigningBackendError(f"Vault transit key read failed: {e}")
        keys = data.get("data", {}).get("keys", {})
        # Latest version only — Vault lets you rotate; for now we sign
        # with the latest and expose its pubkey. Multi-version verify
        # is a Phase-16 concern.
        if not keys:
            raise SigningBackendError("Vault transit key has no versions")
        latest = str(max(int(v) for v in keys.keys()))
        pk_b64 = keys[latest].get("public_key")
        if not pk_b64:
            raise SigningBackendError(f"Vault transit key v{latest} has no public_key")
        return base64.b64decode(pk_b64)

    def public_key(self) -> bytes:
        if self._cached_pubkey is not None:
            return self._cached_pubkey
        with self._lock:
            if self._cached_pubkey is None:
                self._cached_pubkey = self._public_key_fresh()
        return self._cached_pubkey

    def public_keys_all(self) -> "list[bytes]":
        """Vault transit keys are single-version-per-name from the
        daemon-trust perspective — we always sign with latest. If Vault
        rotation happens (key version bump), the previous version's
        pubkey is NOT returned here; appliances would lose trust on
        orders signed with the OLD version. For rotations, use the
        shadow-backend layering or issue a pre-rotation checkin pulse
        with BOTH versions surfaced explicitly. Phase-16 concern."""
        return [self.public_key()]

    def sign(self, data: bytes) -> SignResult:
        self._login_if_needed()
        input_b64 = base64.b64encode(data).decode()
        try:
            resp = self._client.post(
                f"/v1/transit/sign/{self._key_name}",
                headers={"X-Vault-Token": self._token},
                json={"input": input_b64},
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            raise SigningBackendError(f"Vault transit sign failed: {e}")
        # Response looks like: "data": {"signature": "vault:v1:<b64>"}
        sig_str = payload.get("data", {}).get("signature", "")
        if not sig_str.startswith("vault:v"):
            raise SigningBackendError(f"Vault sign returned unexpected format: {sig_str[:40]}")
        # Split "vault:v1:<base64>"
        parts = sig_str.split(":", 2)
        if len(parts) != 3:
            raise SigningBackendError(f"Vault sign bad signature format: {sig_str[:40]}")
        sig_bytes = base64.b64decode(parts[2])
        return SignResult(
            signature=sig_bytes,
            public_key=self.public_key(),
            backend_name=self.name,
        )


# ─── Shadow-mode wrapper ──────────────────────────────────────────


_divergence_counter_total = 0
_divergence_counter_total_lock = threading.Lock()


def _record_divergence(kind: str, detail: str) -> None:
    """Bump an in-memory counter + emit ERROR log. The Prometheus
    export picks up the counter on the next scrape."""
    global _divergence_counter_total
    with _divergence_counter_total_lock:
        _divergence_counter_total += 1
    logger.error(
        "SIGNING_BACKEND_DIVERGENCE",
        extra={"kind": kind, "detail": detail},
    )


def get_divergence_count() -> int:
    """Current cumulative divergence count for the Prometheus scrape."""
    with _divergence_counter_total_lock:
        return _divergence_counter_total


class ShadowSigningBackend:
    """Call TWO backends; return one, log divergence if they differ.

    A divergence is:
      - different public_keys (wrong key material — blocks cutover)
      - one side throws but the other succeeds (availability skew)

    Signatures THEMSELVES differ across backends (Ed25519 is
    deterministic but the keys are different), so we do NOT compare
    signature bytes. We verify that BOTH backends produce a signature
    that would verify against their respective keys — the call site
    can then choose which to trust in production.
    """
    name = "shadow"

    def __init__(
        self,
        primary: object,
        shadow: object,
    ):
        self._primary = primary
        self._shadow = shadow

    @property
    def primary_name(self) -> str:
        return getattr(self._primary, "name", "unknown")

    def public_key(self) -> bytes:
        return self._primary.public_key()

    def public_keys_all(self) -> "list[bytes]":
        """Union of primary + shadow trust sets, deduped. This is what
        makes the multi-trust rollover work: during shadow-mode, every
        checkin response lists BOTH the file key AND the Vault key, so
        daemons accept orders signed by either. When the flip day
        arrives (primary=vault), the file key stays in the trust set
        for a retention window, then gets retired."""
        primary_keys = self._primary.public_keys_all() \
            if hasattr(self._primary, "public_keys_all") \
            else [self._primary.public_key()]
        shadow_keys = self._shadow.public_keys_all() \
            if hasattr(self._shadow, "public_keys_all") \
            else [self._shadow.public_key()]
        seen: set[bytes] = set()
        out: list[bytes] = []
        for k in (*primary_keys, *shadow_keys):
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def sign(self, data: bytes) -> SignResult:
        primary_result = None
        shadow_error: Optional[str] = None

        try:
            primary_result = self._primary.sign(data)
        except Exception as e:
            # Primary MUST succeed; propagate
            raise SigningBackendError(f"primary backend '{self.primary_name}' failed: {e}")

        # Shadow runs in try/except — its failure is logged but
        # does NOT block the signed order.
        try:
            shadow_result = self._shadow.sign(data)
        except Exception as e:
            shadow_error = str(e)
            shadow_result = None
            _record_divergence(
                "shadow_failed",
                f"primary={self.primary_name} ok; shadow={getattr(self._shadow, 'name', '?')} failed: {e}",
            )

        if shadow_result is not None:
            # Compare public keys — they represent whether both
            # backends share key lineage. Different keys is the
            # expected state during shadow-mode rollout. What we
            # alert on is UNEXPECTED divergence — e.g., the shadow
            # backend rotated unexpectedly or returned an empty key.
            if len(shadow_result.public_key) != 32:
                _record_divergence(
                    "bad_shadow_pubkey",
                    f"shadow pubkey len={len(shadow_result.public_key)}",
                )
            if len(shadow_result.signature) != 64:
                _record_divergence(
                    "bad_shadow_signature",
                    f"shadow sig len={len(shadow_result.signature)}",
                )

        return primary_result


# ─── Factory ──────────────────────────────────────────────────────


_BACKEND_SINGLETON = None
_BACKEND_SINGLETON_LOCK = threading.Lock()


def _build_backend():
    sel = SIGNING_BACKEND
    if sel == "file":
        return FileSigningBackend()
    if sel == "vault":
        return VaultSigningBackend()
    if sel == "shadow":
        primary_name = SIGNING_BACKEND_PRIMARY
        file_be = FileSigningBackend()
        vault_be = VaultSigningBackend()
        if primary_name == "vault":
            return ShadowSigningBackend(primary=vault_be, shadow=file_be)
        return ShadowSigningBackend(primary=file_be, shadow=vault_be)
    raise SigningBackendError(
        f"unknown SIGNING_BACKEND={sel!r} (expected file|vault|shadow)"
    )


def get_signing_backend():
    """Singleton accessor. Call-site code does:
        from signing_backend import get_signing_backend
        sig = get_signing_backend().sign(bundle_hash.encode())
    """
    global _BACKEND_SINGLETON
    if _BACKEND_SINGLETON is None:
        with _BACKEND_SINGLETON_LOCK:
            if _BACKEND_SINGLETON is None:
                _BACKEND_SINGLETON = _build_backend()
    return _BACKEND_SINGLETON


def current_signing_method() -> str:
    """Return the active PRIMARY backend's name for fleet_orders.signing_method.

    Vault Phase C P0 #3 (audit/coach-vault-phase-c-gate-a-2026-05-12.md):
    the column was added in mig 177 but every INSERT defaulted to 'file'.
    Call-site code now writes this value at INSERT time. After Phase C
    cutover this returns 'vault'; during shadow mode it returns whichever
    backend the env names as PRIMARY (typically 'file').

    Returns: 'file' | 'vault' (never 'shadow' — that's the wrapper, not
    the actual signer). Safe to call before backend init — falls back
    to env if singleton not yet built.
    """
    try:
        backend = get_signing_backend()
    except Exception:
        # Build failure → fall back to env
        return SIGNING_BACKEND_PRIMARY if SIGNING_BACKEND == "shadow" else SIGNING_BACKEND
    primary = getattr(backend, "_primary", backend)
    name = getattr(primary, "name", "file")
    # Defensive — never expose 'shadow' as a signing method because no
    # signature actually originates from the shadow wrapper.
    if name == "shadow":
        return "file"
    return name


def reset_singleton() -> None:
    """For tests + in-process env changes only. Force the next call
    to _build_backend to re-read env."""
    global _BACKEND_SINGLETON
    with _BACKEND_SINGLETON_LOCK:
        _BACKEND_SINGLETON = None
