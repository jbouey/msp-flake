"""Unit tests for the partner_agreements_api + partner_invites_api modules.

These cover Pydantic validation, helper functions, and import hygiene.
Integration tests against a live DB + RLS are out of scope — we verify
runtime behavior in CI by running the migrations on the staging schema
and hitting the endpoints end-to-end.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Environment setup (mirrors test_partner_auth.py)
# ---------------------------------------------------------------------------

os.environ.setdefault("SESSION_TOKEN_SECRET", "test-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("MINIO_ACCESS_KEY", "minio")
os.environ.setdefault("MINIO_SECRET_KEY", "minio-password")
os.environ.setdefault("SIGNING_KEY_FILE", "/tmp/test-signing.key")
os.environ.setdefault("API_KEY_SECRET", "test-api-key-secret")

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp_server_dir = os.path.dirname(os.path.dirname(backend_dir))
for path in (backend_dir, mcp_server_dir):
    if path not in sys.path:
        sys.path.insert(0, path)

# Restore real FastAPI/Pydantic if earlier tests stubbed them
_stub_prefixes = ("fastapi", "pydantic", "sqlalchemy", "aiohttp", "starlette")
for _mod_name in list(sys.modules):
    if any(_mod_name == p or _mod_name.startswith(p + ".") for p in _stub_prefixes):
        _mod = sys.modules[_mod_name]
        if not hasattr(_mod, "__file__") or _mod.__file__ is None:
            del sys.modules[_mod_name]


# ---------------------------------------------------------------------------
# Imports-under-test
# ---------------------------------------------------------------------------
# These must not raise. If they do, the deploy would fail at
# app-startup time and every endpoint 500s.

def test_partner_agreements_api_imports_clean():
    from dashboard_api import partner_agreements_api as mod
    assert hasattr(mod, "router")
    assert hasattr(mod, "sign_agreement")
    assert hasattr(mod, "get_my_agreements")
    assert hasattr(mod, "require_active_partner_agreements")


def test_partner_invites_api_imports_clean():
    from dashboard_api import partner_invites_api as mod
    assert hasattr(mod, "partner_router")
    assert hasattr(mod, "public_router")
    assert hasattr(mod, "consume_invite_for_signup")


# ---------------------------------------------------------------------------
# partner_agreements_api — version lockstep + Pydantic validation
# ---------------------------------------------------------------------------

def test_current_versions_covers_required_types():
    """Three legal artifacts — any version drift = audit gap."""
    from dashboard_api.partner_agreements_api import CURRENT_VERSIONS, REQUIRED_TYPES
    assert set(CURRENT_VERSIONS.keys()) == set(REQUIRED_TYPES)
    assert {"msa", "subcontractor_baa", "reseller_addendum"}.issubset(CURRENT_VERSIONS.keys())
    for v in CURRENT_VERSIONS.values():
        assert v and isinstance(v, str) and len(v) > 5


def test_sign_agreement_model_rejects_wrong_version():
    from pydantic import ValidationError
    from dashboard_api.partner_agreements_api import SignAgreement, MSA_VERSION

    # Correct version passes
    good = SignAgreement(
        agreement_type="msa",
        version=MSA_VERSION,
        signer_name="Alice Example",
        text_sha256="a" * 64,
    )
    assert good.agreement_type == "msa"

    # Version drift fails validation — prevents partners from binding to
    # a stale agreement hash.
    with pytest.raises(ValidationError) as exc:
        SignAgreement(
            agreement_type="msa",
            version="msa-v0.0-1970-01-01",
            signer_name="Alice",
            text_sha256="a" * 64,
        )
    assert "not current" in str(exc.value)


def test_sign_agreement_model_rejects_invalid_type():
    from pydantic import ValidationError
    from dashboard_api.partner_agreements_api import SignAgreement, MSA_VERSION

    with pytest.raises(ValidationError):
        SignAgreement(
            agreement_type="not_a_real_type",  # type: ignore[arg-type]
            version=MSA_VERSION,
            signer_name="Alice",
            text_sha256="a" * 64,
        )


def test_sign_agreement_model_rejects_malformed_hash():
    from pydantic import ValidationError
    from dashboard_api.partner_agreements_api import SignAgreement, MSA_VERSION

    with pytest.raises(ValidationError):
        SignAgreement(
            agreement_type="msa",
            version=MSA_VERSION,
            signer_name="Alice",
            text_sha256="notahexdigest",  # length + charset invalid
        )


def test_missing_or_stale_flags_unsigned_types():
    from dashboard_api.partner_agreements_api import _missing_or_stale, CURRENT_VERSIONS

    # Empty → all three missing
    assert set(_missing_or_stale({})) == set(CURRENT_VERSIONS.keys())

    # Two signed with current versions, one missing
    active = {
        "msa": {"version": CURRENT_VERSIONS["msa"]},
        "subcontractor_baa": {"version": CURRENT_VERSIONS["subcontractor_baa"]},
    }
    assert _missing_or_stale(active) == ["reseller_addendum"]

    # One signed at stale version → flagged as stale
    stale = {
        "msa": {"version": "msa-v0.9-stale"},
        "subcontractor_baa": {"version": CURRENT_VERSIONS["subcontractor_baa"]},
        "reseller_addendum": {"version": CURRENT_VERSIONS["reseller_addendum"]},
    }
    assert _missing_or_stale(stale) == ["msa"]

    # All current → nothing missing
    all_current = {t: {"version": v} for t, v in CURRENT_VERSIONS.items()}
    assert _missing_or_stale(all_current) == []


# ---------------------------------------------------------------------------
# partner_invites_api — helpers + Pydantic validation
# ---------------------------------------------------------------------------

def test_sha256_hex_is_deterministic_and_64_chars():
    from dashboard_api.partner_invites_api import _sha256_hex

    digest_a = _sha256_hex("hello")
    digest_b = _sha256_hex("hello")
    assert digest_a == digest_b
    assert len(digest_a) == 64
    assert all(c in "0123456789abcdef" for c in digest_a)


def test_sha256_hex_distinct_inputs_differ():
    from dashboard_api.partner_invites_api import _sha256_hex
    assert _sha256_hex("a") != _sha256_hex("b")


def test_redact_token_preserves_8_chars_max():
    from dashboard_api.partner_invites_api import _redact_token
    # Production tokens are 43 chars (token_urlsafe(32))
    sample = "abc123xyz789verylongtoken"
    redacted = _redact_token(sample)
    assert redacted.startswith("abc123xy")
    assert "verylongtoken" not in redacted
    # Short inputs don't crash
    assert _redact_token("short") == "…"


def test_create_invite_model_rejects_invalid_plan():
    from pydantic import ValidationError
    from dashboard_api.partner_invites_api import CreateInvite

    with pytest.raises(ValidationError):
        CreateInvite(plan="not_a_plan", ttl_days=14)


def test_create_invite_model_ttl_bounds():
    from pydantic import ValidationError
    from dashboard_api.partner_invites_api import CreateInvite, MAX_TTL_DAYS

    # Accept inside bounds
    m = CreateInvite(plan="pilot", ttl_days=1)
    assert m.ttl_days == 1
    m = CreateInvite(plan="pilot", ttl_days=MAX_TTL_DAYS)
    assert m.ttl_days == MAX_TTL_DAYS

    # Reject zero + over-max
    with pytest.raises(ValidationError):
        CreateInvite(plan="pilot", ttl_days=0)
    with pytest.raises(ValidationError):
        CreateInvite(plan="pilot", ttl_days=MAX_TTL_DAYS + 1)


def test_create_invite_model_defaults_ttl_to_14():
    from dashboard_api.partner_invites_api import CreateInvite, DEFAULT_TTL_DAYS
    m = CreateInvite(plan="pilot")
    assert m.ttl_days == DEFAULT_TTL_DAYS == 14


def test_revoke_invite_model_requires_reason():
    from pydantic import ValidationError
    from dashboard_api.partner_invites_api import RevokeInvite

    with pytest.raises(ValidationError):
        RevokeInvite(reason="")
    ok = RevokeInvite(reason="partner requested revocation")
    assert ok.reason.startswith("partner")


# ---------------------------------------------------------------------------
# Router path sanity — catches a future rename that would break the
# main.py wire-up silently.
# ---------------------------------------------------------------------------

def test_agreements_router_paths():
    from dashboard_api.partner_agreements_api import router
    paths = {r.path for r in router.routes}
    assert "/api/partners/agreements/sign" in paths
    assert "/api/partners/agreements/mine" in paths


def test_invites_partner_router_paths():
    from dashboard_api.partner_invites_api import partner_router
    paths = {r.path for r in partner_router.routes}
    assert "/api/partners/invites/create" in paths
    assert "/api/partners/invites/mine" in paths
    assert "/api/partners/invites/{invite_id}/revoke" in paths


def test_invites_public_router_paths():
    from dashboard_api.partner_invites_api import public_router
    paths = {r.path for r in public_router.routes}
    assert "/api/partner-invites/{token}/validate" in paths
