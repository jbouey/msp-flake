"""CI gate: idle-session timeout + warning UI is wired in both portals.

HIPAA §164.312(a)(2)(iii) requires automatic logoff after a period of
inactivity. The implementation lives in:

  - frontend/src/hooks/useIdleTimeout.ts          (15-min timeout, 2-min warning)
  - frontend/src/components/shared/IdleTimeoutWarning.tsx  (countdown modal)

Both Client and Partner portals wire these together at the root context
provider so EVERY authenticated page is covered. Round-table 31 audit
swarm flagged this as missing (C3 finding) — false positive, the wiring
already existed. This gate pins it so a future refactor can't silently
drop it and reintroduce the gap the audit thought it had found.
"""
from __future__ import annotations

import pathlib
import re

_FRONTEND = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "frontend" / "src"
)

_CONTEXTS = [
    _FRONTEND / "client" / "ClientContext.tsx",
    _FRONTEND / "partner" / "PartnerContext.tsx",
]


def test_both_portals_import_idle_timeout_hook():
    """Both context providers MUST import useIdleTimeout from the
    canonical hook module — no inline reimplementations."""
    for ctx in _CONTEXTS:
        src = ctx.read_text()
        assert (
            "from '../hooks/useIdleTimeout'" in src
            or 'from "../hooks/useIdleTimeout"' in src
        ), (
            f"{ctx.name} does not import useIdleTimeout from "
            f"'../hooks/useIdleTimeout'. HIPAA §164.312(a)(2)(iii) "
            f"requires automatic logoff — re-wire the hook at the "
            f"context provider."
        )


def test_both_portals_render_idle_timeout_warning():
    """Both context providers MUST render <IdleTimeoutWarning /> so the
    user sees the countdown before forced logout."""
    for ctx in _CONTEXTS:
        src = ctx.read_text()
        assert (
            "from '../components/shared/IdleTimeoutWarning'" in src
            or 'from "../components/shared/IdleTimeoutWarning"' in src
        ), (
            f"{ctx.name} does not import IdleTimeoutWarning from the "
            f"canonical shared component. Re-wire it."
        )
        assert "<IdleTimeoutWarning" in src, (
            f"{ctx.name} imports IdleTimeoutWarning but never renders "
            f"it — the user will be logged out with no warning. Render "
            f"the component conditionally on the hook's showWarning flag."
        )


def test_idle_timeout_hook_uses_15_minute_timeout():
    """The hook's default timeout MUST stay aligned with the server-side
    HIPAA window. If you change this, also update the session TTL in
    auth.py so the client and server agree on when sessions expire."""
    hook = _FRONTEND / "hooks" / "useIdleTimeout.ts"
    src = hook.read_text()
    # Look for "15 * 60" or "900" or a default param of 15 minutes.
    has_15min = bool(
        re.search(r"\b15\s*\*\s*60\b", src)
        or re.search(r"\btimeoutMinutes\s*[:=]\s*15\b", src)
        or re.search(r"\b900\s*(?:\*\s*1000)?\b", src)
    )
    assert has_15min, (
        "useIdleTimeout.ts does not use a 15-minute default timeout. "
        "HIPAA §164.312(a)(2)(iii) recommends 15 minutes for clinical "
        "workstation contexts. If you intentionally changed this, "
        "update the test AND the server-side session TTL in lockstep."
    )
