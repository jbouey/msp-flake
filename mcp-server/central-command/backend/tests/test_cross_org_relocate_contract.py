"""CI gate: cross-org site relocate module contract.

RT21 (2026-05-05) source-level guard. All checks are AST/regex/string
parsing of `cross_org_site_relocate.py`, `migrations/279,280,281`, and
`privileged_access_attestation.py` — NO DB, NO asyncpg, NO pynacl.
Pre-push compatible.

Verifies:
  1. The 6 lifecycle event_types are in ALLOWED_EVENTS.
  2. enable_cross_org_site_relocate is INTENTIONALLY ABSENT from
     ALLOWED_EVENTS (Marcus FK finding) — gate flags drift in either
     direction.
  3. Endpoint module reads feature_flags BEFORE any state mutation.
  4. Initiate endpoint pins expected source + target emails.
  5. Source-release + target-accept endpoints VERIFY the pinned email
     is still an active owner.
  6. Execute endpoint guards the sites UPDATE with WHERE clause.
  7. Initiate endpoint refuses if either org has a pending owner-
     transfer (Steve mit 3).
  8. Target-accept endpoint checks baa_on_file (Steve mit 5).
  9. Initiate endpoint does NOT return plaintext magic-link tokens
     (Patricia RT21 Gate 2).
 10. Migration 279 has the cooling_off_until CHECK.
 11. Migration 281 enforces enable_reason ≥40 chars.
"""
from __future__ import annotations

import pathlib
import re
import sys

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_MODULE = _BACKEND / "cross_org_site_relocate.py"
_MIG_279 = _BACKEND / "migrations" / "279_cross_org_site_relocate_requests.sql"
_MIG_280 = _BACKEND / "migrations" / "280_sites_prior_client_org_id.sql"
_MIG_281 = _BACKEND / "migrations" / "281_feature_flags_attested.sql"


# ─────────────────────────────────────────────────────────────────
# 1+2. ALLOWED_EVENTS membership
# ─────────────────────────────────────────────────────────────────


_LIFECYCLE_EVENTS = {
    "cross_org_site_relocate_initiated",
    "cross_org_site_relocate_source_released",
    "cross_org_site_relocate_target_accepted",
    "cross_org_site_relocate_executed",
    "cross_org_site_relocate_canceled",
    "cross_org_site_relocate_expired",
}


def test_lifecycle_events_in_allowed_events():
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    import privileged_access_attestation as paa  # noqa
    missing = _LIFECYCLE_EVENTS - paa.ALLOWED_EVENTS
    assert not missing, (
        f"Cross-org relocate lifecycle events missing from "
        f"ALLOWED_EVENTS: {missing}"
    )


def test_flag_flip_event_intentionally_absent():
    """Marcus RT21 Gate 2: `enable_cross_org_site_relocate` is NOT in
    ALLOWED_EVENTS because the flag-flip has no site_id anchor (FK
    to sites would fail). Audit lives in feature_flags table itself
    + admin_audit_log. Gate flags drift in either direction."""
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    import privileged_access_attestation as paa  # noqa
    assert "enable_cross_org_site_relocate" not in paa.ALLOWED_EVENTS, (
        "`enable_cross_org_site_relocate` re-added to ALLOWED_EVENTS — "
        "Marcus RT21 Gate 2 finding: this would break the FK in "
        "compliance_bundles INSERT. The flag-flip's audit lives in "
        "feature_flags + admin_audit_log only. Either fix the FK "
        "constraint OR keep this event out of ALLOWED_EVENTS."
    )


# ─────────────────────────────────────────────────────────────────
# 3. Feature-flag gate before mutations
# ─────────────────────────────────────────────────────────────────


def test_state_changing_endpoints_check_feature_flag():
    """Every endpoint that mutates state MUST call _require_feature_enabled."""
    src = _MODULE.read_text()
    # Find every async def for an endpoint (decorator above with @cross_org_relocate_router).
    pattern = re.compile(
        r"@cross_org_relocate_router\.(post|put|patch|delete)\([^)]+\)\s+"
        r"async def (\w+)\([^)]*\)[^:]*:",
        re.DOTALL,
    )
    bad = []
    for m in pattern.finditer(src):
        endpoint_name = m.group(2)
        # Carve-out: the flag-flip endpoints (propose-enable +
        # approve-enable, dual-admin counsel revision 2026-05-06) must
        # NOT call _require_feature_enabled — they ARE the bootstrap.
        if endpoint_name in ("propose_enable", "approve_enable", "enable_feature"):
            continue
        # Find the body of this function — from end of signature to
        # next `async def` or EOF.
        start = m.end()
        next_def = src.find("\nasync def ", start)
        body = src[start: next_def] if next_def != -1 else src[start:]
        if "_require_feature_enabled(" not in body:
            bad.append(endpoint_name)
    assert not bad, (
        "State-mutating cross-org-relocate endpoints missing "
        "_require_feature_enabled call. Without the gate, a deploy of "
        "this code with the flag set false would still let writes "
        "through.\n\n" + "\n".join(f"  - {n}" for n in bad)
    )


# ─────────────────────────────────────────────────────────────────
# 4+5. Pinned email attribution
# ─────────────────────────────────────────────────────────────────


def test_initiate_pins_expected_emails():
    """Patricia RT21 Gate 2: initiate endpoint MUST resolve the
    source + target owners and persist their emails so attribution
    is unambiguous in multi-owner orgs."""
    src = _MODULE.read_text()
    assert "expected_source_release_email" in src, (
        "initiate endpoint missing expected_source_release_email pin"
    )
    assert "expected_target_accept_email" in src, (
        "initiate endpoint missing expected_target_accept_email pin"
    )
    # And the INSERT carries them.
    insert_pattern = re.compile(
        r"INSERT INTO cross_org_site_relocate_requests[^;]+",
        re.DOTALL,
    )
    insert_block = insert_pattern.search(src)
    assert insert_block, "initiate INSERT not found"
    block = insert_block.group(0)
    assert "expected_source_release_email" in block, (
        "INSERT does not persist expected_source_release_email"
    )
    assert "expected_target_accept_email" in block, (
        "INSERT does not persist expected_target_accept_email"
    )


def test_release_and_accept_verify_pinned_email():
    """Source-release and target-accept endpoints MUST verify the
    redeemer's email matches the pinned one AND is still an active
    owner of record."""
    src = _MODULE.read_text()
    for endpoint, expected_col in (
        ("source_release", "expected_source_release_email"),
        ("target_accept", "expected_target_accept_email"),
    ):
        # Find the function body
        m = re.search(
            rf"async def {endpoint}\([^)]*\)[^:]*:(.+?)(?=\nasync def |\Z)",
            src,
            re.DOTALL,
        )
        assert m, f"{endpoint} endpoint not found"
        body = m.group(1)
        assert expected_col in body, (
            f"{endpoint} endpoint must reference {expected_col} "
            f"to verify the pinned owner"
        )
        # And it must look up client_users to confirm the email is
        # still an active owner (defense in depth across email rename).
        assert "FROM client_users" in body, (
            f"{endpoint} endpoint must verify pinned email is still "
            f"an active owner — `FROM client_users` lookup missing"
        )


# ─────────────────────────────────────────────────────────────────
# 6. Execute race-guard
# ─────────────────────────────────────────────────────────────────


def test_execute_guards_sites_update_with_where():
    """Marcus RT21 Gate 2: UPDATE sites in execute MUST filter by
    current client_org_id = source_org_id so a concurrent execute
    can't double-flip. The flipped row count is also checked so a
    no-op transition raises 409."""
    src = _MODULE.read_text()
    m = re.search(
        r"async def execute_relocate\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "execute_relocate endpoint not found"
    body = m.group(0)
    # Match the UPDATE sites block within execute_relocate
    update_match = re.search(
        r"UPDATE\s+sites\s+SET[^;]+?WHERE[^;]+",
        body,
        re.DOTALL,
    )
    assert update_match, "UPDATE sites not found in execute_relocate"
    update_block = update_match.group(0)
    assert "client_org_id = $3" in update_block or "client_org_id = $2" in update_block or "WHERE" in update_block, (
        "execute UPDATE missing the source_org_id WHERE guard — "
        "race condition allows double-flip"
    )
    # Stricter: the sequence must include a WHERE clause that filters
    # by client_org_id (the source guard).
    assert re.search(
        r"AND\s+client_org_id\s*=\s*\$\d+::uuid",
        update_block,
    ), (
        "execute UPDATE missing `AND client_org_id = $N::uuid` guard "
        "for race-safety. Marcus RT21 Gate 2 fix not in place."
    )


# ─────────────────────────────────────────────────────────────────
# 7. Owner-transfer interlock (Steve mit 3)
# ─────────────────────────────────────────────────────────────────


def test_initiate_refuses_if_owner_transfer_pending():
    src = _MODULE.read_text()
    assert "_check_no_pending_owner_transfers" in src, (
        "Steve mit 3: initiate must call _check_no_pending_owner_transfers"
    )
    # And the helper queries client_org_owner_transfer_requests
    assert "client_org_owner_transfer_requests" in src, (
        "_check_no_pending_owner_transfers must query "
        "client_org_owner_transfer_requests for pending rows"
    )


# ─────────────────────────────────────────────────────────────────
# 8. BAA precondition (Steve mit 5)
# ─────────────────────────────────────────────────────────────────


def test_target_accept_checks_baa():
    src = _MODULE.read_text()
    assert "_check_target_org_baa" in src, (
        "Steve mit 5: target-accept must call _check_target_org_baa"
    )
    # And the helper checks client_orgs.baa_on_file
    helper_match = re.search(
        r"async def _check_target_org_baa\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert helper_match, "_check_target_org_baa helper not found"
    helper = helper_match.group(0)
    assert "baa_on_file" in helper, (
        "_check_target_org_baa must read client_orgs.baa_on_file"
    )


def test_target_accept_checks_baa_receipt_authorization():
    """Counsel approval condition #2 (2026-05-06, mig 283): the helper
    must ALSO require baa_relocate_receipt_signature_id (or the
    addendum_signature_id) to be non-NULL. Plain `baa_on_file=true`
    is insufficient — contracts-team must record the specific
    receipt-authorization signature."""
    src = _MODULE.read_text()
    helper_match = re.search(
        r"async def _check_target_org_baa\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert helper_match, "_check_target_org_baa helper not found"
    helper = helper_match.group(0)
    assert "baa_relocate_receipt_signature_id" in helper, (
        "_check_target_org_baa must read "
        "client_orgs.baa_relocate_receipt_signature_id"
    )
    assert "baa_relocate_receipt_addendum_signature_id" in helper, (
        "_check_target_org_baa must read "
        "client_orgs.baa_relocate_receipt_addendum_signature_id"
    )
    assert "has_receipt_auth" in helper or re.search(
        r"signature_id\s+is\s+not\s+None", helper
    ), (
        "_check_target_org_baa must compute a `has_receipt_auth`-style "
        "predicate that requires at least one of the two signature_id "
        "columns to be populated. Counsel approval condition #2."
    )


def test_mig_283_baa_receipt_signature_columns():
    """Migration 283 must ADD the BAA receipt-authorization columns
    on client_orgs and FK them to baa_signatures(signature_id)."""
    mig = _BACKEND / "migrations" / "283_baa_relocate_receipt_signature.sql"
    assert mig.exists(), "Migration 283 missing"
    src = mig.read_text()
    for col in (
        "baa_relocate_receipt_signature_id",
        "baa_relocate_receipt_authorized_at",
        "baa_relocate_receipt_authorized_by_email",
        "baa_relocate_receipt_addendum_signature_id",
    ):
        assert col in src, f"Migration 283 missing column {col}"
    assert "REFERENCES baa_signatures(signature_id)" in src, (
        "Migration 283 must FK signature_id columns to "
        "baa_signatures(signature_id) — match existing "
        "signup_sessions.baa_signature_id pattern (mig 224)"
    )


# ─────────────────────────────────────────────────────────────────
# 9. NO plaintext token leak (Patricia RT21 Gate 2)
# ─────────────────────────────────────────────────────────────────


def test_initiate_does_not_return_plaintext_tokens():
    """Patricia RT21 Gate 2: the initiate response MUST NOT include
    the magic-link plaintext tokens. Email-driven delivery only;
    until email infra wires (Phase 3) the tokens are unreachable
    after issue."""
    src = _MODULE.read_text()
    m = re.search(
        r"async def initiate_cross_org_relocate\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "initiate endpoint not found"
    body = m.group(0)
    # The return dict must NOT contain a key like "*token*" pointing at
    # a plaintext value. Match `: source_release_token,` or
    # `: target_accept_token,` (the plaintext local-var names) inside
    # a return-dict-style literal.
    bad_patterns = (
        re.compile(r'["\']\w*[Tt]oken\w*["\']\s*:\s*source_release_token\b'),
        re.compile(r'["\']\w*[Tt]oken\w*["\']\s*:\s*target_accept_token\b'),
    )
    bad = [p.pattern for p in bad_patterns if p.search(body)]
    assert not bad, (
        "initiate endpoint leaks plaintext magic-link tokens in the "
        "response payload — Patricia RT21 Gate 2 P0. Tokens must be "
        "delivered via email only.\n\nMatched: " + "\n".join(bad)
    )


# ─────────────────────────────────────────────────────────────────
# 10+11. Migration CHECK constraints
# ─────────────────────────────────────────────────────────────────


def test_mig_279_cooling_off_check():
    """Marcus RT21 Gate 1: a row in pending_admin_execute or completed
    MUST have cooling_off_until set, otherwise the cooling-off bypass
    is a footgun."""
    src = _MIG_279.read_text()
    assert "cooling_off_until IS NOT NULL" in src and "CHECK" in src, (
        "Migration 279 missing the cooling_off_until CHECK constraint — "
        "Marcus RT21 Gate 1 fix not present."
    )


def test_mig_281_enable_reason_min_40():
    """Patricia RT21: flag-flip reason ≥40 chars enforced at DB."""
    src = _MIG_281.read_text()
    assert re.search(
        r"length\s*\(\s*enable_reason\s*\)\s*>=\s*40",
        src,
        re.IGNORECASE,
    ), "Migration 281 missing length(enable_reason) >= 40 CHECK"
    # And the bundle_id was DROPPED from the CHECK per Marcus Gate 2.
    enable_check_match = re.search(
        r"CHECK\s*\(\s*enabled\s*=\s*false[^;]+",
        src,
        re.IGNORECASE | re.DOTALL,
    )
    assert enable_check_match, "enable_reason CHECK block not found"
    enable_check = enable_check_match.group(0)
    assert "enable_attestation_bundle_id IS NOT NULL" not in enable_check, (
        "Migration 281 still requires bundle_id NOT NULL on enable — "
        "Marcus Gate 2 said drop this because flag-flip has no "
        "site anchor for the privileged_access bundle."
    )


# ─────────────────────────────────────────────────────────────────
# 12. Mig 280: prior_client_org_id column shape
# ─────────────────────────────────────────────────────────────────


def test_mig_280_adds_prior_client_org_id():
    src = _MIG_280.read_text()
    assert "prior_client_org_id" in src, (
        "Migration 280 missing prior_client_org_id column"
    )
    assert "REFERENCES client_orgs(id)" in src, (
        "prior_client_org_id missing FK to client_orgs"
    )


# ─────────────────────────────────────────────────────────────────
# 13. Sweep loop emits closure attestation (Maya P0-3 parity)
# ─────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────
# 14-17. Email wiring (RT21 Phase 3)
# ─────────────────────────────────────────────────────────────────


def test_three_email_helpers_defined():
    """Three customer-facing emails fire across the lifecycle:
    source-release notice, target-accept notice, post-execute notice."""
    src = _MODULE.read_text()
    for fn in (
        "_send_source_release_email",
        "_send_target_accept_email",
        "_send_post_execute_email",
    ):
        assert f"async def {fn}" in src, (
            f"RT21 Phase 3: {fn} helper missing. The 3 customer-facing "
            f"emails defined in the round-table doc are required."
        )


def test_initiate_sends_source_release_email():
    src = _MODULE.read_text()
    m = re.search(
        r"async def initiate_cross_org_relocate\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "initiate endpoint not found"
    body = m.group(0)
    assert "_send_source_release_email" in body, (
        "initiate endpoint must call _send_source_release_email — "
        "Phase 3 email delivery wiring missing."
    )


def test_source_release_sends_target_accept_email_and_rotates_token():
    """Source-release should rotate the target_accept_token (issuing
    a fresh plaintext) and send the target-accept email. Pinning both
    to defend against a future regression that splits them."""
    src = _MODULE.read_text()
    m = re.search(
        r"async def source_release\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "source_release endpoint not found"
    body = m.group(0)
    assert "_send_target_accept_email" in body, (
        "source_release endpoint must call _send_target_accept_email — "
        "Phase 3 wiring missing."
    )
    assert "target_accept_token = secrets.token_urlsafe" in body, (
        "source_release endpoint must rotate target_accept_token "
        "(generate a fresh plaintext) — defends against the leak-window "
        "class where a token sits idle from initiate-time."
    )
    assert "target_accept_token_hash = " in body, (
        "source_release endpoint must hash the rotated token before "
        "persisting (never plaintext in DB)."
    )


def test_execute_sends_post_execute_email_to_both_owners():
    src = _MODULE.read_text()
    m = re.search(
        r"async def execute_relocate\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "execute_relocate endpoint not found"
    body = m.group(0)
    assert body.count("_send_post_execute_email") >= 2, (
        "execute_relocate must send post-execute receipt to BOTH "
        "owners (source + target) — round-table doc Adam tech-writer "
        "section. Found <2 invocations."
    )


def test_email_bodies_have_no_banned_compliance_language():
    """CLAUDE.md Session 199 banned-words rule applies to email bodies
    too. Customer-facing legal language must NOT use ensures / prevents
    / protects / guarantees / 100%."""
    src = _MODULE.read_text()
    # Extract the three email function bodies
    helpers = re.findall(
        r"async def (?:_send_source_release_email|_send_target_accept_email"
        r"|_send_post_execute_email)\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert len(helpers) == 3, (
        f"Expected 3 email helper bodies; found {len(helpers)}"
    )
    banned = ("ensures", "prevents", "protects", "guarantees", "100%")
    for helper_body in helpers:
        for word in banned:
            # Allow the words inside python identifiers / log keys —
            # only check for the word as a standalone English token.
            pat = re.compile(rf'\b{re.escape(word)}\b', re.IGNORECASE)
            for m_word in pat.finditer(helper_body):
                # Surrounding 30 chars to spot context
                start = max(0, m_word.start() - 20)
                end = min(len(helper_body), m_word.end() + 20)
                ctx = helper_body[start:end]
                # Skip docstring / comment lines that talk ABOUT the rule
                if "banned" in ctx.lower() or "no banned" in ctx.lower():
                    continue
                raise AssertionError(
                    f"Banned compliance-language word {word!r} found "
                    f"in cross-org relocate email body — CLAUDE.md "
                    f"Session 199 rule. Context: ...{ctx}..."
                )


def test_email_helpers_are_best_effort_logging_at_error():
    """Every email helper MUST wrap its send in try/except + logger.error
    so an SMTP outage never aborts the state transition. Pattern matches
    client_owner_transfer._send_target_accept_email."""
    src = _MODULE.read_text()
    helpers = re.findall(
        r"async def (?:_send_source_release_email|_send_target_accept_email"
        r"|_send_post_execute_email)\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    for helper_body in helpers:
        assert "try:" in helper_body and "except" in helper_body, (
            "email helper missing try/except wrapper — SMTP outage "
            "could abort the state transition, which is a chain-of-"
            "custody hazard. Wrap the send in try/except + logger.error."
        )
        assert "logger.error" in helper_body, (
            "email helper missing logger.error on failure — SMTP "
            "failures must be visible to operators."
        )


# ─────────────────────────────────────────────────────────────────
# 20-23. Counsel revision 2026-05-06 — dual-admin + opaque emails
# ─────────────────────────────────────────────────────────────────


def test_dual_admin_flag_flip_endpoints_exist():
    """Counsel governance hardening 2026-05-06: the single
    enable_feature endpoint was split into propose_enable +
    approve_enable. Two distinct admins required."""
    src = _MODULE.read_text()
    assert "@cross_org_relocate_router.post(\"/propose-enable\")" in src, (
        "propose-enable endpoint missing — counsel revision dual-admin "
        "governance hardening not in place."
    )
    assert "@cross_org_relocate_router.post(\"/approve-enable\")" in src, (
        "approve-enable endpoint missing — counsel revision dual-admin "
        "governance hardening not in place."
    )
    # And the legacy single-admin endpoint must NOT exist.
    assert "@cross_org_relocate_router.post(\"/enable-feature\")" not in src, (
        "Legacy /enable-feature single-admin endpoint still present. "
        "Counsel revision split it into /propose-enable + /approve-enable. "
        "Remove the legacy endpoint."
    )


def test_approve_enable_refuses_self_approval():
    """The approve-enable endpoint MUST refuse if the approver is the
    same admin as the proposer. Defense in depth: also pinned by DB
    CHECK constraint in mig 282 (lower(approver_email) <> lower(proposer))."""
    src = _MODULE.read_text()
    m = re.search(
        r"async def approve_enable\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "approve_enable endpoint not found"
    body = m.group(0)
    # Match the explicit check (any of these forms is fine)
    assert re.search(
        r"proposer\s*==\s*actor_email|same.admin.self.approval",
        body,
        re.IGNORECASE,
    ), (
        "approve_enable endpoint missing self-approval guard. Counsel "
        "governance rule: a different admin must approve."
    )


def test_mig_282_dual_admin_check():
    """Migration 282 must enforce approver != proposer at the DB
    layer. Last line of defense if application code is compromised."""
    mig = _BACKEND / "migrations" / "282_feature_flags_dual_admin.sql"
    assert mig.exists(), "Migration 282 missing"
    src = mig.read_text()
    assert re.search(
        r"lower\(enabled_by_email\)\s*<>\s*lower\(enable_proposed_by_email\)",
        src,
    ), (
        "Migration 282 missing the approver != proposer CHECK. Counsel "
        "governance hardening DB layer not in place."
    )
    assert "enable_proposed_by_email" in src and "enable_proposed_at" in src, (
        "Migration 282 missing the propose-side columns."
    )


# Forbidden tokens in OPAQUE email subjects + bodies (counsel revision
# 2026-05-06). Site names + org names + initiator email + reason text
# are visible only after portal authentication.
EMAIL_BODY_FORBIDDEN_INTERPOLATIONS = (
    # Old verbose-mode parameter names that emails MUST NOT reference
    "site_name=",
    "source_org_name=",
    "target_org_name=",
    "initiator_email=",
    # f-string interpolations of those would also appear as bare
    # identifiers — guarded by the parameter-name check above since
    # the helpers don't accept those parameters in opaque mode.
)


def test_email_helpers_have_opaque_signatures():
    """The 3 email helpers' signatures must NOT accept site_name /
    source_org_name / target_org_name / initiator_email parameters.
    Counsel revision 2026-05-06: opaque mode by default."""
    src = _MODULE.read_text()
    for helper in (
        "_send_source_release_email",
        "_send_target_accept_email",
        "_send_post_execute_email",
    ):
        m = re.search(
            rf"async def {helper}\((.*?)\)",
            src,
            re.DOTALL,
        )
        assert m, f"{helper} not found"
        sig = m.group(1)
        for forbidden in (
            "site_name",
            "source_org_name",
            "target_org_name",
            "initiator_email",
        ):
            assert forbidden not in sig, (
                f"{helper} signature still accepts {forbidden!r} — "
                f"counsel revision 2026-05-06 made emails opaque. "
                f"The portal serves identifying context after auth."
            )


def test_email_subjects_are_opaque():
    """Email subject lines (the strings passed as second arg to
    send_email) MUST NOT interpolate site/org names. Static-only or
    reference-id-only."""
    src = _MODULE.read_text()
    # Find every send_email(recipient, subject, body) call and check
    # the subject literal doesn't reference forbidden vars.
    # Approximate by extracting send_email argument blocks.
    for m in re.finditer(
        r"await send_email\(\s*[^,]+,\s*([^,]+),",
        src,
        re.DOTALL,
    ):
        subject_arg = m.group(1).strip()
        # Look for f-string interpolations of forbidden var names
        for forbidden in ("site_name", "source_org_name", "target_org_name", "{site}"):
            assert forbidden not in subject_arg, (
                f"Email subject {subject_arg!r} interpolates "
                f"{forbidden!r}. Counsel revision 2026-05-06: "
                f"subjects must be opaque (static or reference-id only)."
            )


def test_email_call_sites_do_not_pass_forbidden_params():
    """Maya wrap (RT21 counsel-revision): even if the helper signatures
    drop the verbose parameters, a refactored call site could silently
    re-introduce verbose mode by passing org/site names as kwargs that
    the helper happens to accept (e.g. **kwargs). Guard the call sites
    too."""
    src = _MODULE.read_text()
    forbidden_kwargs = (
        "site_name=",
        "source_org_name=",
        "target_org_name=",
        "initiator_email=",
        "reason=",  # email body must not echo the user-supplied reason
        "cooling_off_hours=",
    )
    bad = []
    for m in re.finditer(
        r"await _send_(?:source_release_email|target_accept_email|"
        r"post_execute_email)\(([^)]+)\)",
        src,
        re.DOTALL,
    ):
        kwargs_block = m.group(1)
        for forbidden in forbidden_kwargs:
            if forbidden in kwargs_block:
                bad.append(
                    f"send_email call site passes forbidden kwarg "
                    f"{forbidden!r}"
                )
    assert not bad, (
        "Email call site re-introduced a verbose parameter via kwarg. "
        "Counsel revision 2026-05-06 dropped these. Fix the call site "
        "OR (with separate counsel approval) re-add the parameter to "
        "the helper signature.\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )


def test_email_bodies_do_not_interpolate_site_or_org_names():
    """Within the 3 email helper bodies, no f-string interpolation of
    site_name / source_org_name / target_org_name / initiator_email."""
    src = _MODULE.read_text()
    helpers = re.findall(
        r"async def (?:_send_source_release_email|_send_target_accept_email"
        r"|_send_post_execute_email)\b.+?(?=\nasync def |\Z)",
        src,
        re.DOTALL,
    )
    assert len(helpers) == 3, f"Expected 3 email helpers; found {len(helpers)}"
    for helper_body in helpers:
        for forbidden in (
            "{site_name}",
            "{source_org_name}",
            "{target_org_name}",
            "{initiator_email}",
        ):
            assert forbidden not in helper_body, (
                f"Email helper body interpolates {forbidden!r}. "
                f"Counsel revision 2026-05-06: opaque mode forbids "
                f"site/org names in unauthenticated channels."
            )


def test_sweep_loop_emits_expired_attestation():
    """Maya P0-3 parity (RT19 MFA sweep finding): silent expiration
    without a closure attestation row creates a §164.528 chain-of-
    custody gap. The sweep loop must emit cross_org_site_relocate_
    expired for each row it transitions."""
    src = _MODULE.read_text()
    m = re.search(
        r"async def cross_org_relocate_sweep_loop\b.+\Z",
        src,
        re.DOTALL,
    )
    assert m, "sweep loop not found"
    body = m.group(0)
    assert "cross_org_site_relocate_expired" in body, (
        "sweep loop must emit cross_org_site_relocate_expired "
        "attestation per row — Maya P0-3 parity"
    )
    assert "_emit_attestation" in body, (
        "sweep loop must call _emit_attestation on each transition"
    )
