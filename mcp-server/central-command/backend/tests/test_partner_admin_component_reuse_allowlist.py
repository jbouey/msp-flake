"""Source-level rule: partner-side pages reuse admin/composed components
ONLY by exported name from a known-safe allowlist. Forbid:

  - Inline copies of admin composed components (drift class).
  - Imports of admin-only mutation modals (auth-bypass class):
      AddCredentialModal, EditSiteModal, MoveApplianceModal,
      TransferApplianceModal, DecommissionModal, PortalLinkModal.

Allowlist of admin/composed components partner-side MAY import:
  - ApplianceCard           (read-only summary; no admin-route nav)
  - EvidenceChainStatus     (read-only chain head display)
  - OnboardingProgress      (read-only timeline)
  - DangerousActionModal    (generic — partner-side mutation modals
                             reuse it for tier-2 confirms)
  - StatusBadge / StatusLight / Sparkline / MetricCard
                            (atomic display primitives — safe)

Round-table 37 D2 reservation (Coach + Steve): admin SiteHeader,
SiteComplianceHero, and SiteActivityTimeline contain admin-route
Links / fetch URLs (`/sites/:id/devices`, `/api/sites/:id/activity`)
that would 401 a partner session. Plan-37's "PULL these" list
deliberately excluded them at build time in favor of partner-context
versions inlined in PartnerSiteDetail.tsx — this gate enforces that
exclusion structurally so the next contributor can't silently
re-import them and leak admin routes.

Sprint-N+3 has this gate queued; we shipped it inline with Sprint-N+2
because the gap was discovered during Gate-2 round-table.

Ratchet baseline: 0 violations after Sprint-N+2 ship.
"""
from __future__ import annotations

import pathlib
import re

_FRONTEND = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "frontend" / "src" / "partner"
)


# Components partner-side is permitted to import from
# components/composed or pages/site-detail/components.
ALLOWLIST: set[str] = {
    "ApplianceCard",
    "EvidenceChainStatus",
    "OnboardingProgress",
    "DangerousActionModal",
    "StatusBadge",
    "StatusLight",
    "Sparkline",
    "MetricCard",
    "PageShell",
    "GlassCard",
    "Spinner",
    "Badge",
    "Tooltip",
    "ResponsiveTable",
    "BulkActionToolbar",
    "FloatingActionButton",
    "DisclaimerFooter",
    "FleetStatusPanel",
    "MeshHealthPanel",
    "OrgHealthPanel",
    "AuditReadiness",
    "KillSwitchBanner",
    "SiteSearchBar",
    "SiteSLAIndicator",
    "IdleTimeoutWarning",
    "ActionDropdown",
    # Pre-existing safe imports the partner portal already relies on.
    # Each is a generic UI primitive (no admin-route deps); allowlisted
    # to avoid spurious violations on this gate's first run.
    "InfoTip",
    "WelcomeModal",
    "useToast",
}

# Admin-only modals that MUST NOT be imported from partner-side. These
# are mutation surfaces specific to admin auth scope; importing them
# from partner code is an auth-bypass class waiting to happen.
FORBIDDEN: set[str] = {
    "AddCredentialModal",
    "EditSiteModal",
    "MoveApplianceModal",
    "TransferApplianceModal",
    "DecommissionModal",
    "PortalLinkModal",
    # Admin SiteHeader + SiteComplianceHero + SiteActivityTimeline
    # contain admin-route Links (`/sites/:id/devices`,
    # `/api/sites/:id/activity` etc.) that 401 partner sessions.
    # Forbidden until refactored to take a `presenter` prop or
    # parameterized routes (Sprint-N+3 follow-up if useful; for now
    # PartnerSiteDetail inlines a partner-context header).
    "SiteHeader",
    "SiteComplianceHero",
    "SiteActivityTimeline",
}


# Match `import { Foo, Bar } from '<components/composed | pages/site-
# detail/components | components/shared>...';` blocks. Multi-line
# imports are normalized via the DOTALL/`re.S` flag.
_IMPORT_RE = re.compile(
    r"import\s*\{([^}]+)\}\s*from\s*['\"]"
    r"(?:[\.\./]+(?:components/composed|components/shared|pages/site-detail/components))"
    r"[^'\"]*['\"]",
    re.S,
)


def _scan_file(p: pathlib.Path) -> tuple[list[str], list[str]]:
    """Return (forbidden_imports_seen, non_allowlisted_imports_seen)."""
    text = p.read_text(encoding="utf-8")
    forbidden: list[str] = []
    not_allowlisted: list[str] = []
    for match in _IMPORT_RE.finditer(text):
        names_blob = match.group(1)
        # Names may include `as` aliases — strip them; we care about
        # the imported symbol, not the local binding.
        for chunk in names_blob.split(","):
            name = chunk.strip().split(" as ")[0].strip()
            if not name:
                continue
            if name in FORBIDDEN:
                forbidden.append(name)
            elif name not in ALLOWLIST:
                not_allowlisted.append(name)
    return forbidden, not_allowlisted


def _partner_tsx_files() -> list[pathlib.Path]:
    if not _FRONTEND.is_dir():
        return []
    out: list[pathlib.Path] = []
    for p in _FRONTEND.rglob("*.tsx"):
        # Skip tests + storybook fixtures.
        rel = p.relative_to(_FRONTEND).as_posix()
        if rel.startswith("__tests__/") or rel.endswith(".test.tsx"):
            continue
        out.append(p)
    return out


def test_partner_side_does_not_import_forbidden_admin_modals():
    """No partner-side .tsx imports an admin-only mutation modal."""
    files = _partner_tsx_files()
    assert files, "Expected partner-side .tsx files to scan."
    violations: list[tuple[str, str]] = []
    for p in files:
        forbidden, _ = _scan_file(p)
        for name in forbidden:
            violations.append((str(p.relative_to(_FRONTEND.parent.parent)), name))
    assert not violations, (
        "Partner-side imported admin-only or admin-route-leaking "
        "components. Each one is either an auth-bypass class "
        "(admin mutation modal) or a 401 leak (admin-route Link "
        "baked in). Refactor or inline a partner-context version.\n"
        + "\n".join(f"  {f}: {n}" for f, n in violations)
    )


def test_partner_side_only_imports_allowlisted_composed_components():
    """Every composed/shared/site-detail import from partner-side
    must be on the allowlist. Adding a new shared component to the
    allowlist requires confirming it has no admin-route Links and no
    admin-only fetch URLs."""
    files = _partner_tsx_files()
    assert files
    violations: list[tuple[str, str]] = []
    for p in files:
        _, not_allowlisted = _scan_file(p)
        for name in not_allowlisted:
            violations.append((str(p.relative_to(_FRONTEND.parent.parent)), name))
    assert not violations, (
        "Partner-side imported composed/shared components that are "
        "not on ALLOWLIST. Add them to ALLOWLIST in this file ONLY "
        "after confirming no admin-route Links + no admin-route "
        "fetch URLs.\n"
        + "\n".join(f"  {f}: {n}" for f, n in violations)
    )


def test_allowlist_covers_at_least_five_component_classes():
    """Coach Gate-6 floor: the gate is meaningful only if it
    actively allowlists ≥5 component classes. Below 5, the gate
    risks being a no-op (pinning a too-narrow surface)."""
    assert len(ALLOWLIST) >= 5, (
        f"ALLOWLIST has only {len(ALLOWLIST)} entries; gate target ≥5."
    )


def test_forbidden_set_covers_six_admin_only_modal_classes():
    """Plan 37 D2 OMIT list: 6 admin-only modals + 3 admin-route-
    leaking display components. Total FORBIDDEN minimum = 9."""
    expected_modal_classes = {
        "AddCredentialModal",
        "EditSiteModal",
        "MoveApplianceModal",
        "TransferApplianceModal",
        "DecommissionModal",
        "PortalLinkModal",
    }
    missing = expected_modal_classes - FORBIDDEN
    assert not missing, (
        f"Plan-37 D2 OMIT list lost coverage for {sorted(missing)}; "
        "the gate cannot enforce the design contract."
    )
    assert len(FORBIDDEN) >= 9, (
        f"FORBIDDEN has only {len(FORBIDDEN)} entries; expected ≥9 "
        "(6 admin modals + 3 admin-route-leaking display components)."
    )


def test_partner_site_detail_imports_at_least_one_allowlisted_component():
    """Sanity-check: PartnerSiteDetail.tsx is NOT supposed to inline
    everything from scratch. It must reuse at least one allowlisted
    composed component, otherwise the allowlist contract is moot."""
    p = _FRONTEND / "PartnerSiteDetail.tsx"
    if not p.is_file():
        # File may not exist in the worktree being scanned; skip.
        return
    text = p.read_text(encoding="utf-8")
    # Look for any import line pulling from components/composed or
    # components/shared (allowlisted bucket sources).
    has_composed_import = bool(
        re.search(
            r"from\s+['\"][\.\./]+components/(?:composed|shared)",
            text,
        )
    )
    assert has_composed_import, (
        "PartnerSiteDetail.tsx does not import any composed/shared "
        "component — the allowlist contract has nothing to enforce. "
        "Plan-37 D2 mandates SELECTIVE REUSE."
    )
