"""CI gate: customer-facing email helpers across THREE modules use
opaque-mode subjects + bodies (task #42 harmonization, 2026-05-06).

Counsel approved opaque-mode for cross-org relocate emails (RT21
v2.3). Maya flagged the inconsistency: owner-transfer +
email-rename emails were still verbose. Once one class shipped
opaque, the asymmetry was attackable. Harmonized to opaque.

Modules in scope:
  - cross_org_site_relocate.py — RT21 (already pinned by
    test_cross_org_relocate_contract.py)
  - client_owner_transfer.py     — _send_initiator_confirmation_email,
                                   _send_target_accept_email
  - client_user_email_rename.py  — _send_dual_notification

This gate enforces that email helpers in the latter two modules
follow the same opaque-mode contract:
  - signatures DO NOT accept org_name / target_email / initiator_email
    / actor_kind / reason / clinic_name parameters
  - subjects + bodies DO NOT interpolate those parameters
  - call sites do NOT pass forbidden kwargs
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_OWNER_TRANSFER = _BACKEND / "client_owner_transfer.py"
_EMAIL_RENAME = _BACKEND / "client_user_email_rename.py"

# Forbidden parameter names in opaque-mode helpers. These were the
# verbose-mode signatures pre-harmonization.
FORBIDDEN_HELPER_PARAMS = (
    "org_name",
    "actor_kind",
    "reason",
    "clinic_name",
    "target_email_inline",
    "initiator_email_inline",
)

# Forbidden kwargs at call sites. `target_email` itself is the
# RECIPIENT — that's allowed (you have to know who to send to). The
# forbidden ones are CONTEXT details that should stay in the portal.
FORBIDDEN_CALL_KWARGS = (
    "org_name=",
    "actor_kind=",
    "reason=",
    "clinic_name=",
)

# Helpers whose signatures must be opaque
OWNER_TRANSFER_HELPERS = (
    "_send_initiator_confirmation_email",
    "_send_target_accept_email",
)
EMAIL_RENAME_HELPERS = (
    "_send_dual_notification",
)


def _helper_signature(src: str, helper_name: str) -> str:
    m = re.search(
        rf"async def {helper_name}\((.*?)\)",
        src,
        re.DOTALL,
    )
    assert m, f"{helper_name} not found in source"
    return m.group(1)


def test_owner_transfer_helpers_have_opaque_signatures():
    src = _OWNER_TRANSFER.read_text()
    for helper in OWNER_TRANSFER_HELPERS:
        sig = _helper_signature(src, helper)
        for forbidden in FORBIDDEN_HELPER_PARAMS:
            assert forbidden not in sig, (
                f"{helper} signature in client_owner_transfer.py still "
                f"accepts `{forbidden}` parameter — task #42 "
                f"harmonization made owner-transfer emails opaque to "
                f"match cross-org relocate. The portal serves "
                f"identifying context after auth."
            )


def test_email_rename_helper_has_opaque_signature():
    src = _EMAIL_RENAME.read_text()
    for helper in EMAIL_RENAME_HELPERS:
        sig = _helper_signature(src, helper)
        for forbidden in FORBIDDEN_HELPER_PARAMS:
            assert forbidden not in sig, (
                f"{helper} signature in client_user_email_rename.py "
                f"still accepts `{forbidden}` parameter — task #42 "
                f"harmonization made email-rename notifications opaque."
            )


def test_owner_transfer_helper_bodies_do_not_interpolate_context():
    """The function bodies of the email helpers must NOT interpolate
    the forbidden context names. Walks each helper's source body."""
    src = _OWNER_TRANSFER.read_text()
    for helper in OWNER_TRANSFER_HELPERS:
        m = re.search(
            rf"async def {helper}\b.+?(?=\nasync def |\Z)",
            src,
            re.DOTALL,
        )
        assert m, f"{helper} body not found"
        body = m.group(0)
        for forbidden in (
            "{org_name}",
            "{target_email}",
            "{initiator_email}",
            "{reason",  # catches {reason} and {reason[:200]}
        ):
            assert forbidden not in body, (
                f"{helper} body interpolates `{forbidden}` — opaque "
                f"mode forbids context details in unauthenticated "
                f"channels. Task #42."
            )


def test_email_rename_helper_body_does_not_interpolate_context():
    src = _EMAIL_RENAME.read_text()
    m = re.search(
        r"async def _send_dual_notification\b.+?(?=\nasync def |\nclass |\ndef [a-z])",
        src,
        re.DOTALL,
    )
    assert m, "_send_dual_notification body not found"
    body = m.group(0)
    for forbidden in (
        "{org_name}",
        "{actor_kind}",
        "{new_email}",  # subject + body of OLD-recipient must not
                        # name the new email; recipient logs in to see
        "{old_email}",  # NEW-recipient body must not name the prior
                        # address either (Maya 2nd-eye P2-1, 2026-05-06)
    ):
        # Allow `{new_email}` ONLY in the call to send_email's first
        # arg (the recipient address itself, which is always safe).
        # Walk lines: any line that interpolates `{new_email}` AND is
        # NOT the recipient-arg line is a violation.
        for line in body.splitlines():
            if forbidden in line and "send_email(" not in line:
                # The send_email recipient line might span multiple
                # lines; tolerate the first arg of the call.
                # Simpler: look at lines that are clearly body content
                # (have triple-quote markers or f-string `f"`).
                if 'f"' in line or "f'" in line or '"""' in line:
                    raise AssertionError(
                        f"_send_dual_notification body interpolates "
                        f"`{forbidden}` in a body line — opaque mode "
                        f"forbids. Task #42 harmonization."
                    )


def test_owner_transfer_call_sites_do_not_pass_forbidden_kwargs():
    src = _OWNER_TRANSFER.read_text()
    bad = []
    for helper in OWNER_TRANSFER_HELPERS:
        for m in re.finditer(
            rf"await {helper}\(([^)]+)\)",
            src,
            re.DOTALL,
        ):
            block = m.group(1)
            for forbidden in FORBIDDEN_CALL_KWARGS:
                if forbidden in block:
                    bad.append(
                        f"{helper} call site passes `{forbidden}`"
                    )
    assert not bad, (
        "Owner-transfer email call sites still pass forbidden "
        "kwargs — task #42 harmonization regression.\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )


def test_email_rename_call_sites_do_not_pass_forbidden_kwargs():
    src = _EMAIL_RENAME.read_text()
    bad = []
    for m in re.finditer(
        r"await _send_dual_notification\(([^)]+)\)",
        src,
        re.DOTALL,
    ):
        block = m.group(1)
        for forbidden in FORBIDDEN_CALL_KWARGS:
            if forbidden in block:
                bad.append(
                    f"_send_dual_notification call site passes `{forbidden}`"
                )
    assert not bad, (
        "Email-rename call sites still pass forbidden kwargs — "
        "task #42 harmonization regression.\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )


def test_no_fstring_subjects_in_send_email_calls():
    """Maya 2nd-eye P2-2 (2026-05-06): regex-based subject scan can
    miss multi-line/kwarg subjects. Defence in depth: ban f-string
    subject literals entirely in opaque-mode modules. A subject that
    needs interpolation has, by definition, leaked context into the
    SMTP channel."""
    for path in (_OWNER_TRANSFER, _EMAIL_RENAME):
        src = path.read_text()
        for m in re.finditer(
            r'send_email\([^)]*?,\s*(f"[^"]*"|f\'[^\']*\')',
            src,
            re.DOTALL,
        ):
            raise AssertionError(
                f"{path.name}: send_email subject is an f-string — "
                f"opaque-mode subjects must be plain string literals "
                f"or include only opaque IDs (transfer_id[:8], "
                f"reference_id). Match: {m.group(1)[:80]!r}"
            )


def test_subjects_are_opaque_across_three_modules():
    """Subjects across all three opaque-mode modules must be static
    or reference-id-only (no interpolated context names)."""
    for path in (_OWNER_TRANSFER, _EMAIL_RENAME):
        src = path.read_text()
        for m in re.finditer(
            r"await send_email\(\s*[^,]+,\s*([^,]+),",
            src,
            re.DOTALL,
        ):
            subject_arg = m.group(1).strip()
            for forbidden in (
                "{org_name}",
                "{site_name}",
                "{target_email}",
                "{new_email}",
                "{actor_kind}",
            ):
                assert forbidden not in subject_arg, (
                    f"{path.name}: email subject interpolates "
                    f"`{forbidden}` — opaque-mode subjects must be "
                    f"static or reference-id-only. Task #42."
                )
