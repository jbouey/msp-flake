"""Source-shape ratchet for the auditor-kit synthetic-site refusal
(Task #102, deferred from #66 B1 Gate A).

The customer-facing `GET /api/evidence/sites/{site_id}/auditor-kit`
endpoint MUST refuse to serve content for the MTTR-soak synthetic site
to any non-admin caller. The synthetic site exists for the substrate
engine's ground-truth tick (mig 315 + the noqa-allowlisted invariant
scans in assertions.py); its evidence chain is real but MUST NOT leak
into a real customer's or partner's auditor kit.

Admin callers are exempt — they may need to inspect the synthetic
site's chain to verify the soak's positive control.

This is a SOURCE-SHAPE pin (not a behavioral test). A behavioral
end-to-end test would need real Postgres + real evidence chain + real
sessions; the source-shape pin catches refactor regressions at PR
time without the heavy fixture.
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_EVIDENCE_CHAIN = _BACKEND / "evidence_chain.py"


def _download_auditor_kit_body() -> str:
    """Extract the download_auditor_kit function body (up to the next
    @router decorator)."""
    src = _EVIDENCE_CHAIN.read_text()
    start = src.find("async def download_auditor_kit(")
    assert start != -1, "download_auditor_kit not found in evidence_chain.py"
    rest = src[start:]
    next_router = re.search(r"\n@router\.", rest[100:])
    if next_router is None:
        return rest
    return rest[: 100 + next_router.start()]


def test_auditor_kit_selects_synthetic_column():
    """The site_row SELECT in download_auditor_kit MUST project
    `synthetic` so the refusal logic has the data it needs."""
    body = _download_auditor_kit_body()
    # The SELECT may be split across lines; tolerate whitespace.
    pattern = re.compile(
        r"SELECT[^;]+\bsynthetic\b[^;]+FROM\s+sites",
        re.IGNORECASE | re.DOTALL,
    )
    assert pattern.search(body), (
        "download_auditor_kit's site_row SELECT no longer projects "
        "`synthetic` — the refusal block below can't read the column. "
        "Add `synthetic` to the SELECT (Task #66 B1 / #102)."
    )


def test_auditor_kit_refuses_synthetic_for_non_admin():
    """The endpoint MUST refuse synthetic sites for non-admin
    callers. Pinned via a source-shape check for `site_row.synthetic`
    + `auth_method != "admin"` + a raise/return that prevents the
    kit from being served. Admin carve-out is required (substrate
    engine verification path)."""
    body = _download_auditor_kit_body()

    # The refusal check must reference both the synthetic column AND
    # the admin carve-out.
    assert "site_row.synthetic" in body, (
        "download_auditor_kit no longer reads site_row.synthetic — "
        "synthetic-site evidence could leak into a customer auditor "
        "kit. Restore the refusal block (Task #102)."
    )
    assert 'auth_method != "admin"' in body or "auth_method != 'admin'" in body, (
        "download_auditor_kit's synthetic refusal lost its admin "
        "carve-out — admins need to inspect the soak's positive "
        "control. Restore `auth_method != 'admin'` (Task #102)."
    )

    # The refusal must actually STOP execution — raise an HTTPException
    # in the same block as the synthetic check.
    synth_idx = body.find("site_row.synthetic")
    window = body[synth_idx : synth_idx + 800]
    assert "raise HTTPException" in window, (
        "download_auditor_kit checks site_row.synthetic but doesn't "
        "raise — the kit would still be served. Restore "
        "`raise HTTPException(status_code=404, ...)` (Task #102)."
    )


def test_auditor_kit_refusal_uses_opaque_404():
    """The refusal should return 404 (opaque — 'Site not found'),
    NOT a structured "synthetic-site refused" message. An opaque
    404 prevents an unauthenticated prober from confirming the
    synthetic site exists (Counsel Rule 7: no unauthenticated
    channel gets meaningful context by default)."""
    body = _download_auditor_kit_body()
    synth_idx = body.find("site_row.synthetic")
    if synth_idx == -1:
        return  # earlier test already fails loudly
    window = body[synth_idx : synth_idx + 800]
    assert "status_code=404" in window or "404, " in window, (
        "synthetic-site refusal must be 404, not a structured error "
        "code that confirms the site exists (Counsel Rule 7)."
    )
    # The message should be opaque "Site not found", same as the
    # missing-site path above. Avoid leaking 'synthetic' in user-
    # facing text.
    assert "synthetic" not in window.split("raise HTTPException", 1)[-1][:200].lower() \
        or "Site not found" in window, (
        "synthetic-site refusal leaks the word 'synthetic' in the "
        "HTTPException detail — that's a context leak. Use the "
        "opaque 'Site not found' message."
    )
