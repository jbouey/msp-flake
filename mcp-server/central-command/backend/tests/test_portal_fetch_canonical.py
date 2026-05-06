"""CI gate: portal mutation helpers are SHARED — no inline duplicates.

Round-table 32 (2026-05-05) Maya P0 anti-regression. Pre-fix, 4 modals
+ 1 download handler each defined their own `postJson` / `getJson` /
fetch-and-parse-error logic. The DRY closure extracted them to
`utils/portalFetch.ts`. This gate fails if a portal modal declares
its own postJson/getJson/patchJson/deleteJson when the canonical
helpers exist.

Allowlisted: ClientLogin / PartnerLogin (pre-session pages — no CSRF
token yet on first GET). They use plain fetch and that's correct;
they're not portal mutation surfaces in the chain-of-custody sense.
"""
from __future__ import annotations

import pathlib
import re

_FRONTEND = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "frontend" / "src"
)

# Pages that legitimately use plain fetch (pre-session login flows).
ALLOWED_INLINE_HELPERS = {
    "ClientLogin.tsx",
    "PartnerLogin.tsx",
    "ClientVerify.tsx",  # magic-link verification, pre-session
}

# Files we expect to use portalFetch helpers
PORTAL_MUTATION_SURFACES = [
    "client/ClientOwnerTransferModal.tsx",
    "partner/PartnerAdminTransferModal.tsx",
    "partner/PartnerUsersScreen.tsx",
    "pages/AdminClientUserEmailRenameModal.tsx",
]


def test_portal_mutation_surfaces_use_canonical_helpers():
    """Each modal/handler that mutates portal state must import from
    utils/portalFetch — not roll its own postJson/getJson."""
    missing = []
    for rel in PORTAL_MUTATION_SURFACES:
        path = _FRONTEND / rel
        if not path.exists():
            missing.append(f"{rel} not found — refactor or update list")
            continue
        src = path.read_text()
        if "from '../utils/portalFetch'" not in src and "from \"../utils/portalFetch\"" not in src:
            missing.append(
                f"{rel} doesn't import from '../utils/portalFetch' — "
                f"likely re-rolling postJson/getJson inline. Round-table "
                f"32 closed this DRY gap."
            )
    assert not missing, (
        "Portal mutation surfaces failing to use canonical helpers:\n\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


def test_no_inline_post_json_in_portal_directories():
    """No `async function postJson` / `function postJson` declared
    inline in the client/partner/pages directories (excluding the
    canonical utils/portalFetch.ts)."""
    pat_decl = re.compile(
        r"(async\s+)?function\s+(postJson|getJson|patchJson|deleteJson)\b",
    )
    bad = []
    for tsx in _FRONTEND.rglob("*.tsx"):
        # Skip the canonical helper module
        if tsx.name == "portalFetch.ts":
            continue
        # Skip allowlisted login flows
        if tsx.name in ALLOWED_INLINE_HELPERS:
            continue
        try:
            src = tsx.read_text()
        except Exception:
            continue
        for ln_num, line in enumerate(src.splitlines(), 1):
            if pat_decl.search(line):
                bad.append(
                    f"{tsx.relative_to(_FRONTEND)}:{ln_num} — "
                    f"declares a postJson/getJson/patchJson/deleteJson "
                    f"function inline. Replace with import from "
                    f"'../utils/portalFetch'."
                )
    assert not bad, (
        "Inline declarations of portal mutation helpers found — DRY "
        "regression. Round-table 32 mandate: ONE source of truth at "
        "utils/portalFetch.ts.\n\n"
        + "\n".join(f"  - {b}" for b in bad)
    )


def test_portal_fetch_module_exports_canonical_api():
    """Pin the exported names so a refactor that removes one breaks
    loudly. Each mutation method + the GET probe + the blob-fetch."""
    canonical = _FRONTEND / "utils" / "portalFetch.ts"
    assert canonical.exists()
    src = canonical.read_text()
    for fn in [
        "export async function getJson",
        "export async function postJson",
        "export async function patchJson",
        "export async function deleteJson",
        "export async function fetchBlob",
        "export interface PortalFetchError",
    ]:
        assert fn in src, (
            f"portalFetch.ts missing canonical export `{fn}`. "
            f"Round-table 32 contract requires all 5 helpers + the "
            f"PortalFetchError interface."
        )
    # All mutation helpers MUST set credentials:'include' + csrfHeaders
    # (Steve veto: this is the auth posture that the helper exists to
    # enforce centrally).
    assert "credentials: 'include'" in src
    assert "csrfHeaders()" in src
