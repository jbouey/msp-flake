"""Unit tests for signing_backend (Phase 15 Vault HSM replacement).

Covers:
  - FileSigningBackend produces a valid Ed25519 signature that verifies
    against the public key
  - VaultSigningBackend logs in via AppRole, signs via transit API,
    parses the 'vault:v1:<b64>' response correctly
  - ShadowSigningBackend returns the primary's result and logs
    divergence (counter bumps) when the shadow fails or returns
    garbage
  - Factory honors SIGNING_BACKEND env + reset_singleton() for tests
"""
from __future__ import annotations

import base64
import os
import secrets

import pytest


# ─── FileSigningBackend ────────────────────────────────────────────


def test_file_backend_signs_and_pubkey_roundtrip(tmp_path, monkeypatch):
    try:
        from nacl.signing import SigningKey, VerifyKey
        from nacl.encoding import HexEncoder
    except ImportError:
        pytest.skip("PyNaCl not installed")

    sk = SigningKey.generate()
    key_hex = sk.encode(encoder=HexEncoder)
    path = tmp_path / "signing.key"
    path.write_bytes(key_hex)

    monkeypatch.setenv("SIGNING_KEY_FILE", str(path))
    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb
    importlib.reload(sb)

    backend = sb.FileSigningBackend(path=str(path))
    payload = b"OsirisCare signing_backend unit test"
    result = backend.sign(payload)

    assert result.backend_name == "file"
    assert len(result.signature) == 64
    assert len(result.public_key) == 32

    # Signature verifies against returned pubkey
    vk = VerifyKey(result.public_key)
    vk.verify(payload, result.signature)  # raises on mismatch


def test_file_backend_cached_after_first_load(tmp_path):
    try:
        from nacl.signing import SigningKey
        from nacl.encoding import HexEncoder
    except ImportError:
        pytest.skip("PyNaCl not installed")

    import signing_backend as sb

    sk = SigningKey.generate()
    path = tmp_path / "signing.key"
    path.write_bytes(sk.encode(encoder=HexEncoder))

    backend = sb.FileSigningBackend(path=str(path))
    _ = backend.sign(b"first")
    # Delete the file; cache should still serve the key
    path.unlink()
    result = backend.sign(b"second")
    assert result.backend_name == "file"


def test_file_backend_missing_key_raises():
    import signing_backend as sb
    backend = sb.FileSigningBackend(path="/nonexistent/no/such/file")
    with pytest.raises(sb.SigningBackendError, match="unreadable"):
        backend.sign(b"x")


# ─── VaultSigningBackend (httpx mocked) ───────────────────────────


class _FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeVaultClient:
    """Minimal mock matching the httpx.Client surface the backend uses."""

    def __init__(self, pubkey_b64: str = None, sign_response: str = None,
                 login_fail: bool = False, sign_fail: bool = False):
        self._login_fail = login_fail
        self._sign_fail = sign_fail
        # Generate a real Ed25519 key for the mock if none provided, so
        # test pubkey length + format match the real thing.
        if pubkey_b64 is None:
            try:
                from nacl.signing import SigningKey
                pk = bytes(SigningKey.generate().verify_key)
                pubkey_b64 = base64.b64encode(pk).decode()
            except ImportError:
                pubkey_b64 = base64.b64encode(b"\x00" * 32).decode()
        self._pubkey_b64 = pubkey_b64
        # Default sign response: 64 zero bytes (not a real signature)
        if sign_response is None:
            sign_response = "vault:v1:" + base64.b64encode(b"\x01" * 64).decode()
        self._sign_response = sign_response
        self.calls = []

    def post(self, path, json=None, headers=None):
        self.calls.append(("POST", path))
        if path.endswith("/auth/approle/login"):
            if self._login_fail:
                return _FakeResponse(500, {"errors": ["forced"]})
            return _FakeResponse(200, {
                "auth": {"client_token": "fake-token", "lease_duration": 3600},
            })
        if "/transit/sign/" in path:
            if self._sign_fail:
                return _FakeResponse(500, {"errors": ["forced"]})
            return _FakeResponse(200, {
                "data": {"signature": self._sign_response},
            })
        return _FakeResponse(404, {})

    def get(self, path, headers=None):
        self.calls.append(("GET", path))
        if "/transit/keys/" in path:
            return _FakeResponse(200, {
                "data": {"keys": {"1": {"public_key": self._pubkey_b64}}},
            })
        return _FakeResponse(404, {})


def test_vault_backend_login_then_sign(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://fake-vault:8200")
    monkeypatch.setenv("VAULT_APPROLE_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_APPROLE_SECRET_ID", "secret")

    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb
    importlib.reload(sb)

    backend = sb.VaultSigningBackend()
    fake = _FakeVaultClient()
    backend._client = fake  # swap real httpx client for mock

    result = backend.sign(b"payload-to-sign")
    assert result.backend_name == "vault"
    assert len(result.signature) == 64
    assert len(result.public_key) == 32
    assert result.signature == b"\x01" * 64

    # Verify the call sequence: login → sign (login was done once,
    # sign triggers a pubkey lookup for public_key())
    paths = [c[1] for c in fake.calls]
    assert any("/auth/approle/login" in p for p in paths)
    assert any("/transit/sign/osiriscare-signing" in p for p in paths)


def test_vault_backend_login_failure_raises(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://fake-vault:8200")
    monkeypatch.setenv("VAULT_APPROLE_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_APPROLE_SECRET_ID", "secret")
    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb
    importlib.reload(sb)

    backend = sb.VaultSigningBackend()
    backend._client = _FakeVaultClient(login_fail=True)
    with pytest.raises(sb.SigningBackendError, match="login failed"):
        backend.sign(b"data")


def test_vault_backend_bad_signature_format_raises(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "https://fake-vault:8200")
    monkeypatch.setenv("VAULT_APPROLE_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_APPROLE_SECRET_ID", "secret")
    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb
    importlib.reload(sb)

    backend = sb.VaultSigningBackend()
    backend._client = _FakeVaultClient(sign_response="not-a-valid-vault-signature")
    with pytest.raises(sb.SigningBackendError, match="unexpected format"):
        backend.sign(b"data")


def test_vault_backend_missing_env_raises(monkeypatch):
    monkeypatch.setenv("VAULT_ADDR", "")
    monkeypatch.setenv("VAULT_APPROLE_ROLE_ID", "")
    monkeypatch.setenv("VAULT_APPROLE_SECRET_ID", "")
    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb
    importlib.reload(sb)
    with pytest.raises(sb.SigningBackendError, match="VAULT_ADDR"):
        sb.VaultSigningBackend()


# ─── ShadowSigningBackend ─────────────────────────────────────────


class _StubBackend:
    def __init__(self, name: str, sig: bytes = None, pub: bytes = None, fail: bool = False):
        self.name = name
        self._sig = sig or (b"\xAA" * 64)
        self._pub = pub or (b"\xBB" * 32)
        self._fail = fail
        self.sign_calls = 0

    def sign(self, data):
        self.sign_calls += 1
        if self._fail:
            raise RuntimeError("stub failure")
        import signing_backend as sb
        return sb.SignResult(signature=self._sig, public_key=self._pub, backend_name=self.name)

    def public_key(self):
        return self._pub


def test_shadow_returns_primary_when_both_ok():
    import signing_backend as sb
    primary = _StubBackend("file", sig=b"\x01" * 64, pub=b"\x10" * 32)
    shadow = _StubBackend("vault", sig=b"\x02" * 64, pub=b"\x20" * 32)
    before = sb.get_divergence_count()

    wrapper = sb.ShadowSigningBackend(primary=primary, shadow=shadow)
    result = wrapper.sign(b"data")

    assert result.backend_name == "file"
    assert result.signature == b"\x01" * 64
    assert primary.sign_calls == 1
    assert shadow.sign_calls == 1
    # No divergence — both backends returned valid-shape results
    assert sb.get_divergence_count() == before


def test_shadow_primary_failure_propagates():
    import signing_backend as sb
    primary = _StubBackend("file", fail=True)
    shadow = _StubBackend("vault")
    wrapper = sb.ShadowSigningBackend(primary=primary, shadow=shadow)
    with pytest.raises(sb.SigningBackendError, match="primary backend"):
        wrapper.sign(b"data")


def test_shadow_shadow_failure_logged_not_propagated():
    import signing_backend as sb
    primary = _StubBackend("file", sig=b"\x01" * 64, pub=b"\x10" * 32)
    shadow = _StubBackend("vault", fail=True)
    before = sb.get_divergence_count()

    wrapper = sb.ShadowSigningBackend(primary=primary, shadow=shadow)
    # Must NOT raise — shadow failure is logged, primary succeeds
    result = wrapper.sign(b"data")
    assert result.backend_name == "file"
    assert sb.get_divergence_count() == before + 1


def test_shadow_bad_shadow_pubkey_logs_divergence():
    import signing_backend as sb
    primary = _StubBackend("file", sig=b"\x01" * 64, pub=b"\x10" * 32)
    # Shadow returns wrong-size pubkey (only 16 bytes)
    shadow = _StubBackend("vault", sig=b"\x02" * 64, pub=b"\x20" * 16)
    before = sb.get_divergence_count()

    wrapper = sb.ShadowSigningBackend(primary=primary, shadow=shadow)
    result = wrapper.sign(b"data")
    assert result.backend_name == "file"
    # At least one divergence recorded
    assert sb.get_divergence_count() >= before + 1


def test_shadow_bad_shadow_signature_length_logs_divergence():
    import signing_backend as sb
    primary = _StubBackend("file", sig=b"\x01" * 64, pub=b"\x10" * 32)
    shadow = _StubBackend("vault", sig=b"\x02" * 10, pub=b"\x20" * 32)
    before = sb.get_divergence_count()

    wrapper = sb.ShadowSigningBackend(primary=primary, shadow=shadow)
    wrapper.sign(b"data")
    assert sb.get_divergence_count() >= before + 1


# ─── Factory + reset_singleton ────────────────────────────────────


def test_factory_file_selection(monkeypatch):
    monkeypatch.setenv("SIGNING_BACKEND", "file")
    import signing_backend as sb
    sb.reset_singleton()
    # Force module to re-read env
    import importlib
    importlib.reload(sb)
    # Minimal stub so FileSigningBackend doesn't try to actually read
    sb._BACKEND_SINGLETON = sb.FileSigningBackend(path="/dev/null")
    backend = sb.get_signing_backend()
    assert backend.name == "file"


def test_factory_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("SIGNING_BACKEND", "wizardry")
    import sys, importlib
    sys.modules.pop("signing_backend", None)
    import signing_backend as sb
    importlib.reload(sb)
    sb.reset_singleton()
    with pytest.raises(sb.SigningBackendError, match="unknown SIGNING_BACKEND"):
        sb.get_signing_backend()
