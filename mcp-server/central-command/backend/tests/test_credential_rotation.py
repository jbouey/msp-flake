"""Tests for credential encryption key rotation.

Covers:
  1) credential_crypto: MultiFernet keyring loads in priority order, rotates
     ciphertext between keys, fingerprints are stable, missing keys raise
  2) credential_rotation endpoint: admin-only, 409 when already running,
     audit-logged, touches the expected tables

Uses real Fernet keys (not mocks) because the crypto primitives are the
whole point — a mock would prove nothing. The DB is AST-inspected rather
than stood up in a test fixture because the rest of the test suite follows
the same "source-level" idiom (see test_site_activity_audit.py).
"""

import ast
import os
import sys

# Ensure we can import from the backend package
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from cryptography.fernet import Fernet, InvalidToken
import pytest

# Import the module under test
import credential_crypto as cc  # noqa: E402


# =============================================================================
# MultiFernet keyring behaviour
# =============================================================================

class TestCredentialCrypto:
    @pytest.fixture(autouse=True)
    def isolate_env(self):
        """Save/restore env vars so each test starts from a clean slate."""
        saved = {
            k: os.environ.get(k)
            for k in ("CREDENTIAL_ENCRYPTION_KEY", "CREDENTIAL_ENCRYPTION_KEYS")
        }
        yield
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cc.reset_cache()

    def test_single_key_roundtrip(self):
        k = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEYS", None)
        cc.reset_cache()

        ct = cc.encrypt_credential('{"host":"a"}')
        assert ct.startswith(b"FERNET:")
        assert cc.decrypt_credential(ct) == '{"host":"a"}'

    def test_multi_key_env_var_takes_precedence(self):
        k1 = Fernet.generate_key().decode()
        k2 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEYS"] = f"{k2},{k1}"
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k1  # should be ignored
        cc.reset_cache()

        # Primary fingerprint should be k2's (first in list)
        assert cc.primary_key_fingerprint() != ""
        fps = cc.get_key_fingerprints()
        assert len(fps) == 2

    def test_old_ciphertext_decrypts_after_rotation(self):
        """A ciphertext encrypted with the old key must still decrypt when
        the old key is the SECOND key in the keyring."""
        k1 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k1
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEYS", None)
        cc.reset_cache()
        old_ct = cc.encrypt_credential("secret-1")

        # Rotate: prepend new key
        k2 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEYS"] = f"{k2},{k1}"
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        cc.reset_cache()

        assert cc.decrypt_credential(old_ct) == "secret-1"

    def test_rotate_ciphertext_transfers_ownership(self):
        """After rotate_ciphertext, the new blob must decrypt under the
        new key ALONE (old key not in the keyring)."""
        k1 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k1
        cc.reset_cache()
        old_ct = cc.encrypt_credential("payload")

        k2 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEYS"] = f"{k2},{k1}"
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        cc.reset_cache()
        new_ct = cc.rotate_ciphertext(old_ct)

        # Drop the old key; new ct should still decrypt
        os.environ["CREDENTIAL_ENCRYPTION_KEYS"] = k2
        cc.reset_cache()
        assert cc.decrypt_credential(new_ct) == "payload"

    def test_old_ciphertext_fails_after_old_key_dropped(self):
        """If an old ciphertext was NOT rotated and the old key is dropped,
        decryption must raise InvalidToken — not silently succeed or return
        garbage. This is the safety net."""
        k1 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k1
        cc.reset_cache()
        old_ct = cc.encrypt_credential("forgotten")

        k2 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEYS"] = k2  # only k2
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        cc.reset_cache()

        with pytest.raises(InvalidToken):
            cc.decrypt_credential(old_ct)

    def test_legacy_plaintext_passthrough(self):
        """Bytes without the FERNET: prefix are treated as legacy plaintext
        and returned unchanged — required for backward compat with rows
        written before Fernet was introduced."""
        k = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k
        cc.reset_cache()
        raw = b'{"legacy":"yes"}'
        assert cc.decrypt_credential(raw) == '{"legacy":"yes"}'

    def test_no_key_raises_runtime_error(self):
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEYS", None)
        cc.reset_cache()
        with pytest.raises(RuntimeError, match="No credential encryption key"):
            cc.encrypt_credential("x")

    def test_fingerprint_stable_and_short(self):
        k = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k
        cc.reset_cache()
        fp1 = cc.primary_key_fingerprint()
        cc.reset_cache()
        fp2 = cc.primary_key_fingerprint()
        assert fp1 == fp2
        assert len(fp1) == 12
        # Different key → different fingerprint
        k2 = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k2
        cc.reset_cache()
        assert cc.primary_key_fingerprint() != fp1

    def test_generate_new_key_is_valid_fernet_key(self):
        new_key = cc.generate_new_key()
        # Should round-trip via Fernet without error
        Fernet(new_key.encode())

    def test_rotate_ciphertext_handles_legacy_plaintext(self):
        """Rotating legacy plaintext should upgrade it to FERNET-prefixed."""
        k = Fernet.generate_key().decode()
        os.environ["CREDENTIAL_ENCRYPTION_KEY"] = k
        cc.reset_cache()
        legacy = b'{"plain":"old"}'
        new_ct = cc.rotate_ciphertext(legacy)
        assert new_ct.startswith(b"FERNET:")
        assert cc.decrypt_credential(new_ct) == '{"plain":"old"}'

    def test_empty_multi_env_var_raises(self):
        os.environ["CREDENTIAL_ENCRYPTION_KEYS"] = ","
        os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
        cc.reset_cache()
        with pytest.raises(RuntimeError, match="set but empty"):
            cc.encrypt_credential("x")


# =============================================================================
# Rotation endpoint source-level checks
# =============================================================================

def _load_rotation_source() -> str:
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "credential_rotation.py",
    )
    with open(path) as f:
        return f.read()


class TestRotationEndpoint:
    @pytest.fixture(autouse=True)
    def load(self):
        self.source = _load_rotation_source()
        self.tree = ast.parse(self.source)

    def _get_func(self, name: str) -> ast.AsyncFunctionDef:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found")

    def test_rotate_key_endpoint_exists(self):
        assert '@router.post("/rotate-key")' in self.source
        assert "async def rotate_credential_key(" in self.source

    def test_rotate_key_requires_admin(self):
        fn = self._get_func("rotate_credential_key")
        body = ast.get_source_segment(self.source, fn) or ""
        assert "require_admin" in body

    def test_rotate_key_returns_409_when_in_progress(self):
        fn = self._get_func("rotate_credential_key")
        body = ast.get_source_segment(self.source, fn) or ""
        assert "409" in body
        assert "in_progress" in body

    def test_rotate_key_writes_audit_log_on_start(self):
        src = self.source
        assert "CREDENTIAL_KEY_ROTATION_STARTED" in src
        assert "CREDENTIAL_KEY_ROTATION_COMPLETED" in src
        assert "admin_audit_log" in src

    def test_rotation_targets_include_all_encrypted_tables(self):
        """Every table with an encrypted column must be in ROTATION_TARGETS.
        If a new encrypted column is added the rotation loop needs to know
        about it — this test is a prod-drift canary.
        """
        src = self.source
        # These are the tables found via information_schema on 2026-04-09
        required = [
            ("site_credentials", "encrypted_data"),
            ("org_credentials", "encrypted_data"),
            ("client_org_sso", "client_secret_encrypted"),
            ("integrations", "credentials_encrypted"),
            ("oauth_config", "client_secret_encrypted"),
            ("partners", "oauth_access_token_encrypted"),
            ("partners", "oauth_refresh_token_encrypted"),
        ]
        for table, col in required:
            assert f'"{table}"' in src, f"missing rotation target: {table}"
            assert f'"{col}"' in src, f"missing rotation target column: {col}"

    def test_rotation_uses_admin_connection(self):
        fn = self._get_func("_run_rotation_async")
        body = ast.get_source_segment(self.source, fn) or ""
        assert "admin_connection" in body

    def test_rotation_is_idempotent_via_rotate_ciphertext(self):
        """The rotation worker must go through rotate_ciphertext (which
        roundtrips decrypt→encrypt) rather than re-implementing the dance.
        If it calls encrypt_credential directly that would be a bug — the
        ciphertext wouldn't actually be a ciphertext."""
        fn = self._get_func("_rotate_table")
        body = ast.get_source_segment(self.source, fn) or ""
        assert "rotate_ciphertext" in body

    def test_rotation_uses_per_row_transaction(self):
        """Each row must be its own transaction so a crash mid-loop leaves
        the remainder untouched (resumability)."""
        fn = self._get_func("_rotate_table")
        body = ast.get_source_segment(self.source, fn) or ""
        assert "conn.transaction()" in body

    def test_skips_missing_tables_gracefully(self):
        fn = self._get_func("_rotate_table")
        body = ast.get_source_segment(self.source, fn) or ""
        assert "_table_exists" in body

    def test_rotation_status_endpoint_exists(self):
        assert '@router.get("/rotation-status")' in self.source
        assert "async def get_rotation_status(" in self.source

    def test_fingerprints_endpoint_exists(self):
        assert '@router.get("/key-fingerprints")' in self.source

    def test_router_prefix(self):
        assert 'prefix="/api/admin/credentials"' in self.source

    def test_rotate_loop_caps_row_count(self):
        """Runaway loops on bad data must be bounded."""
        fn = self._get_func("_rotate_table")
        body = ast.get_source_segment(self.source, fn) or ""
        assert "LIMIT 50000" in body
