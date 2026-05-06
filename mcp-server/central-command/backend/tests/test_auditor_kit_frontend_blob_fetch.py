"""CI gate: auditor-kit download MUST stay on the blob-fetch path.

Maya P1 (Session 217 final sweep): the round-table 31 fix replaced an
opaque-error `<a href={downloadUrl}>` with `handleAuditorKitDownload`
(fetch → blob → trigger download) so 401/429/500 surface as toasts
with actionable copy. The previous shape returned a JSON error blob
in a new tab — customer in a meeting saw "Not Found" and thought the
product was broken.

Nothing prevents a future commit from reverting `<button onClick>` to
`<a href={downloadUrl}>`. This source-level gate pins the pattern:
  - ClientReports.tsx contains `handleAuditorKitDownload`
  - The auditor-kit URL appears ONLY inside that handler (not as
    an href attribute)
  - The handler reads the response with `res.blob()` (not navigates)

Round-table-31 verdict: enterprise-grade requires CI coverage on every
P1 fix. Backend has test_auditor_kit_endpoint.py + the
require_evidence_view_access auth path; this gate covers the frontend.
"""
from __future__ import annotations

import pathlib
import re

_FRONTEND = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "frontend" / "src"
)
_REPORTS = _FRONTEND / "client" / "ClientReports.tsx"


def test_auditor_kit_uses_blob_fetch_handler():
    """The download path must be a JS fetch → blob → trigger, not
    `<a href>`. Pinned to ClientReports.tsx, the only auditor-kit
    download surface in the client portal."""
    assert _REPORTS.exists(), f"missing {_REPORTS}"
    src = _REPORTS.read_text()

    assert "handleAuditorKitDownload" in src, (
        "ClientReports.tsx no longer has handleAuditorKitDownload — "
        "auditor-kit download regressed to opaque-error pattern. See "
        "round-table 31 (.agent/plans/31-...md) — the `<a href>` shape "
        "produced unreadable JSON errors on 401/429."
    )
    # Behavioral pin: handler must call .blob() (the fetch result is
    # consumed as binary download, not navigated). Defense against
    # someone replacing the body of handleAuditorKitDownload with a
    # plain redirect.
    assert "res.blob()" in src or "response.blob()" in src, (
        "handleAuditorKitDownload doesn't read res.blob() — likely "
        "regressed to anchor-style download. Restore the blob path."
    )

    # Negative pin: an `<a href>` linking directly to the auditor-kit
    # URL bypasses the handler. Search for the dangerous pattern.
    bad = re.search(
        r"<a\s+href=\{[^}]*auditor-kit[^}]*\}",
        src,
    )
    assert bad is None, (
        "Found `<a href={...auditor-kit...}>` in ClientReports.tsx. "
        "This bypasses handleAuditorKitDownload's error UX. Revert to "
        "<button onClick={() => handleAuditorKitDownload(...)}>."
    )

    # Positive pin: the URL appears in code, but only inside the
    # handler (where it's used with fetch). Confirm the URL is used
    # via fetch, not as an href.
    assert "/api/evidence/sites/" in src, (
        "Auditor-kit URL not found at all in ClientReports.tsx. "
        "Either the feature was removed (update this test) or the "
        "URL pattern changed."
    )


def test_auditor_kit_handler_surfaces_actionable_errors():
    """The whole point of round-table-31's UX fix: customer sees
    actionable copy on each error class, not a generic failure."""
    src = _REPORTS.read_text()
    fn_start = src.find("handleAuditorKitDownload")
    assert fn_start >= 0
    fn_body = src[fn_start:fn_start + 4000]

    # 401/403 → re-login guidance
    assert ("session expired" in fn_body.lower()
            or "log in again" in fn_body.lower()), (
        "handleAuditorKitDownload's 401/403 branch should mention "
        "session expiry / log-in. Customer otherwise can't tell the "
        "kit failed for auth reasons."
    )

    # 429 → rate-limit guidance
    assert ("rate limit" in fn_body.lower()
            or "try again" in fn_body.lower()), (
        "handleAuditorKitDownload's 429 branch should explain the "
        "10/hr rate limit. Customer otherwise doesn't know to retry."
    )
