"""Customer-facing template renderer (round-table 2026-05-06 T1.1-T1.4).

Replaces the in-source ``.format()`` template anti-pattern that caused
14 sequential auditor-kit regressions across one day. The pattern was:

  - ``_AUDITOR_KIT_README`` triple-quoted string with mixed
    ``{var}`` placeholders + literal ``{bundle_id}`` prose.
  - ``_AUDITOR_KIT_README.format(site_id=...)`` raised KeyError on
    ``{bundle_id}`` because Python's ``.format()`` cannot distinguish
    prose-literal braces from placeholders.

Replaced by:

  - ``backend/templates/<area>/<name>.j2`` — Jinja2 template files.
    Carol owns copy edits; ``.py`` blame stays clean.
  - ``register_customer_template(name=..., required_kwargs={...})``
    registers the template + its required kwargs in a module-level
    registry.
  - ``render_template(name, **kwargs)`` — strict renderer:
      * ``jinja2.StrictUndefined`` raises ``UndefinedError`` on any
        missing kwarg at render time (NOT silent fall-through).
      * Required-kwargs check fails fast with a clear error before
        Jinja2 even runs.
  - ``run_boot_smoke()`` — at app startup, render every registered
    template with a sentinel kwarg dict. Aborts container start on
    ``UndefinedError`` / ``TemplateError`` / any render exception.
    Surfaces the bug class at deploy verification, not at the
    customer's first download.

Why Jinja2 over ``string.Template``:

  * Jinja2 ``{{ var }}`` and ``{% if %}`` syntax — JSON ``{}`` and
    bash ``${var}`` are literally safe with no escaping. Closes the
    ``{bundle_id}`` and JSON-example bug class structurally.
  * ``StrictUndefined`` fails closed — silent ``""`` substitution is
    NOT possible.
  * Conditionals (``{% if presenter_contact_line %}``) replace the
    fragile empty-string trick.
  * Carol can review ``.j2`` files without ``.format()`` brain.

This module imports Jinja2 (already in requirements.txt as
``jinja2==3.1.4``). Tests import it directly without pulling in
FastAPI / asyncpg / pydantic.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateError,
    UndefinedError,
    select_autoescape,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- Jinja env

_TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent

# Strict environment: any reference to an undefined kwarg raises
# UndefinedError at render time. Autoescape OFF — these are .md, .sh,
# .json, .txt artifacts, NOT HTML; HTML autoescaping would corrupt
# the rendered shell scripts and Markdown.
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    undefined=StrictUndefined,
    autoescape=select_autoescape([]),  # explicit: no autoescape
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)

# ---------------------------------------------------------------- registry


class _TemplateRegistration:
    """Records the contract for one customer-facing template."""

    def __init__(
        self,
        name: str,
        path: str,
        required_kwargs: Set[str],
        sentinel_factory: Optional[Callable[[], Dict[str, Any]]] = None,
        owner: str = "engineering",
    ) -> None:
        self.name = name
        self.path = path  # relative to backend/templates/
        self.required_kwargs = frozenset(required_kwargs)
        self._sentinel_factory = sentinel_factory
        self.owner = owner

    def sentinel_kwargs(self) -> Dict[str, Any]:
        if self._sentinel_factory is not None:
            return self._sentinel_factory()
        return {k: "__SMOKE__" for k in self.required_kwargs}


_REGISTRY: Dict[str, _TemplateRegistration] = {}


def register_customer_template(
    name: str,
    path: str,
    required_kwargs: Set[str],
    sentinel_factory: Optional[Callable[[], Dict[str, Any]]] = None,
    owner: str = "engineering",
) -> None:
    """Register a customer-facing template for boot-smoke rendering.

    Idempotent: re-registering the same name with the same path is
    a no-op (supports module re-import in tests). Re-registering
    with a *different* path raises — that's a configuration bug.
    """
    existing = _REGISTRY.get(name)
    if existing is not None:
        if existing.path != path:
            raise RuntimeError(
                f"Template {name!r} already registered with a different "
                f"path ({existing.path!r} vs {path!r}). Re-registration "
                f"with a different path is a configuration bug."
            )
        return  # idempotent
    # Maya P1: every required_kwarg name must be in the security
    # allow-list. Forces deliberate review of new kwargs.
    _validate_kwargs_against_allowlist(name, set(required_kwargs))
    _REGISTRY[name] = _TemplateRegistration(
        name=name,
        path=path,
        required_kwargs=required_kwargs,
        sentinel_factory=sentinel_factory,
        owner=owner,
    )


def list_registered_templates() -> List[str]:
    """Return registered template names, sorted (deterministic)."""
    return sorted(_REGISTRY.keys())


def get_registration(name: str) -> _TemplateRegistration:
    if name not in _REGISTRY:
        raise KeyError(
            f"Template {name!r} is not registered. Registered: "
            f"{list_registered_templates()}"
        )
    return _REGISTRY[name]


# ---------------------------------------------------------------- render


def render_template(name: str, **kwargs: Any) -> str:
    """Render a registered template with the given kwargs.

    Strict contract:
      1. ``name`` must be registered (KeyError if not).
      2. Every member of ``required_kwargs`` MUST be supplied (or
         raises ``TypeError`` listing what's missing).
      3. Jinja2 ``StrictUndefined`` raises ``UndefinedError`` if the
         template references any kwarg not supplied.
      4. ``.j2`` files are rendered through Jinja2; non-``.j2``
         files are returned as raw text (used for plain shell scripts
         that have no template directives).
    """
    reg = get_registration(name)
    missing = reg.required_kwargs - set(kwargs)
    if missing:
        raise TypeError(
            f"render_template({name!r}) missing required kwargs: "
            f"{sorted(missing)}. Registration declared "
            f"required_kwargs={sorted(reg.required_kwargs)}."
        )
    template_path = _TEMPLATES_DIR / reg.path
    if not template_path.exists():
        raise FileNotFoundError(
            f"Template file {template_path} does not exist. "
            f"Registration name={name!r} path={reg.path!r}."
        )
    if not reg.path.endswith(".j2"):
        return template_path.read_text()
    template = _env.get_template(reg.path)
    return template.render(**kwargs)


def render_static(path: str) -> str:
    """Read a non-Jinja static file under backend/templates/.
    Used for shell scripts that don't take kwargs. Returns the
    file contents unchanged.

    Path-traversal hardening (Maya P1, round-table 2026-05-06):
    resolves the path and rejects anything that escapes the
    templates dir. Closes the surface where a future caller
    could pass user-controlled `path` and exfiltrate source.
    """
    full = (_TEMPLATES_DIR / path).resolve()
    templates_root = _TEMPLATES_DIR.resolve()
    try:
        full.relative_to(templates_root)
    except ValueError:
        raise ValueError(
            f"Path traversal attempt blocked: resolved path "
            f"{full} is outside templates root {templates_root}."
        )
    if not full.exists():
        raise FileNotFoundError(f"Static template file not found: {full}")
    return full.read_text()


# ---------------------------------------------------------------- kwarg allow-list


# Maya P1 (round-table 2026-05-06): security-layer allow-list of
# permitted kwarg names across all customer-facing templates.
# Adding a kwarg name to a template's required_kwargs set without
# also adding it here causes ``register_customer_template`` to
# raise. Forces explicit security review before a future PR can
# introduce a PHI-shaped kwarg (e.g. ``patient_email``,
# ``diagnosis``, ``provider_npi``) into a customer-facing artifact.
# CI gate: tests/test_template_kwargs_allowlisted.py.
_KWARGS_SECURITY_ALLOWLIST = frozenset({
    # auditor-kit README context — site identity + presentation
    "site_id",
    "clinic_name",
    "generated_at",
    "presenter_brand",
    "presenter_contact_line",
    # attestation_letter context (F1, round-table 2026-05-06).
    # Each is a frozen snapshot value at issue time — no live PII
    # lookups, no PHI shapes. Maria reviews this list before
    # signoff. Maya security review pinned: no patient_*, no mrn,
    # no diagnosis, no provider_npi.
    "practice_name",
    "period_start_human",
    "period_end_human",
    "sites_covered_count",
    "appliances_count",
    "workstations_count",
    "bundle_count",
    "privacy_officer_name",
    "privacy_officer_title",
    "privacy_officer_email",
    "privacy_officer_accepted_human",
    "privacy_officer_explainer_version",
    "baa_dated_at_human",
    "baa_practice_name",
    "issued_at_human",
    "valid_until_human",
    "attestation_hash",
    "verify_phone",
    "verify_url_short",
    # partner_portfolio_attestation context (P-F5, partner round-
    # table 2026-05-08). Aggregate-only — NO clinic identifiers,
    # NO patient-shaped names. Maya-grade review pending; PM
    # round-table verified the round-table copy avoids leakage.
    "site_count",
    "appliance_count",
    "workstation_count",
    "control_count",
    "bundle_count",
    "ots_anchored_pct_str",
    "chain_root_hex",
    "chain_head_at_human",
    # partner_weekly_digest context (P-F7). Aggregate operational
    # metrics only — NO incident details, NO host names.
    "orders_run",
    "alerts_triaged",
    "escalations_closed",
    "mttr_median_human",
    "top_noisy_sites",
    "week_start_human",
    "week_end_human",
    "technician_name",
    # partner_ba_compliance context (P-F6, partner round-table
    # 2026-05-08). Tony's three-party BAA chain artifact. Roster is
    # a list of dicts with counterparty_label + monitored_site_count;
    # NO PHI, NO patient identifiers (counterparty_label is the
    # CE's business name from client_orgs, public information).
    "subcontractor_baa_dated_at_human",
    "roster_count",
    "roster",
    "total_monitored_sites",
    "onboarded_counterparty_count",
    # partner_incident_timeline context (P-F8, partner round-table
    # 2026-05-08). Per-incident chronological event timeline. The
    # incident_id is opaque (UUID-shaped); site_label is a hash-
    # prefix label (NOT clinic_name, per P-F7 site-label posture);
    # events is a list of dicts with timestamp_human + kind +
    # description. Description is an action label (e.g. "L1 rule
    # fired", "operator ack", "remediation issued") — never a
    # patient/clinic name. PHI is scrubbed at the appliance
    # before egress so execution_telemetry is PHI-free; this
    # template inherits that boundary.
    "incident_id_short",
    "incident_type",
    "severity",
    "status",
    "resolution_tier_label",
    "created_at_human",
    "resolved_at_human",
    "ttr_human",
    "site_label",
    "events",
    "generated_at_human",
    # quarterly_summary context (F3, sprint 2026-05-08). Maria's last
    # owner-side P1 deferred from Friday: a calendar-quarter Practice
    # Compliance Summary the Privacy Officer signs each quarter and
    # the practice owner files for HIPAA §164.530(j) records-retention
    # compliance. Aggregate, frozen-at-issue snapshot fields. Maya-
    # grade review: NO patient_*, NO mrn, NO diagnosis, NO provider_
    # npi. The surface owner is the same client_org that pulls the
    # report — so the practice_name / privacy_officer_* / appliance
    # counts are the same posture as F1 (already allow-listed above)
    # and are reused. The kwargs listed BELOW are the F3-novel
    # additions:
    #   * period_year / period_quarter — calendar-quarter coordinates
    #     ("Q1 2026"). Pure numeric; no PHI shape.
    #   * drift_detected_count / drift_resolved_count — distinct
    #     (check_type, appliance) opens / closes within the period.
    #     Counts only; no incident details, no host names.
    #   * mean_score_str — stringified rolling-mean compliance score
    #     ("94" or "—" when no_data). Numeric or sentinel.
    #   * monitored_check_types_count — count of is_scored=true rows
    #     in check_type_registry. Public catalog metadata.
    #   * sites_count — F1 used "sites_covered_count"; F3 standardizes
    #     on the shorter "sites_count" (matches the JSON payload
    #     attestation_facts shape). Aggregate count only.
    "period_year",
    "period_quarter",
    "drift_detected_count",
    "drift_resolved_count",
    "mean_score_str",
    "monitored_check_types_count",
    "sites_count",
})


def _validate_kwargs_against_allowlist(name: str, required_kwargs: Set[str]) -> None:
    forbidden = required_kwargs - _KWARGS_SECURITY_ALLOWLIST
    if forbidden:
        raise RuntimeError(
            f"Template {name!r} declares required_kwargs that are "
            f"NOT in the security allow-list: {sorted(forbidden)}. "
            f"Adding a new kwarg requires explicit security review — "
            f"add to ``_KWARGS_SECURITY_ALLOWLIST`` in "
            f"``backend/templates/__init__.py`` AND verify the kwarg "
            f"value source cannot carry PHI / patient identifiers / "
            f"any §164.514 identifier. Example future regression vector: "
            f"adding ``patient_email`` 'for convenience' would expose "
            f"PHI in the auditor-kit README."
        )


# ---------------------------------------------------------------- boot smoke


class BootSmokeFailure(RuntimeError):
    """Raised by ``run_boot_smoke`` when at least one customer
    template fails to render with sentinel kwargs. The container
    MUST NOT serve customer requests in this state."""


def run_boot_smoke() -> Tuple[List[str], List[Tuple[str, Exception]]]:
    """Render every registered customer template with sentinel
    kwargs. Returns (passed_names, failed_pairs).

    Raises ``BootSmokeFailure`` when any template fails. Callers
    (typically ``main.py``'s lifespan ``startup`` event) should
    propagate the exception so uvicorn aborts container start —
    surfacing the bug class at deploy verification.

    Why fail-closed: a stray ``{bundle_id}`` placeholder crashed
    the auditor-kit endpoint in production for hours on 2026-05-06.
    A boot smoke would have caught the same regression at deploy
    time, BEFORE the new bytes started serving customer requests.
    """
    passed: List[str] = []
    failed: List[Tuple[str, Exception]] = []
    for name in list_registered_templates():
        reg = _REGISTRY[name]
        try:
            sentinel = reg.sentinel_kwargs()
            rendered = render_template(name, **sentinel)
            if not rendered:
                raise BootSmokeFailure(
                    f"Template {name!r} rendered empty with sentinel "
                    f"kwargs."
                )
            passed.append(name)
        except (UndefinedError, TemplateError, KeyError, TypeError) as e:
            failed.append((name, e))
            logger.error(
                "boot_template_smoke_failed",
                extra={
                    "template_name": name,
                    "template_path": reg.path,
                    "error_class": type(e).__name__,
                    "error_msg": str(e)[:500],
                    "owner": reg.owner,
                },
            )
        except Exception as e:  # noqa: BLE001 — fail-closed
            failed.append((name, e))
            logger.error(
                "boot_template_smoke_unexpected_error",
                extra={
                    "template_name": name,
                    "template_path": reg.path,
                    "error_class": type(e).__name__,
                    "error_msg": str(e)[:500],
                    "owner": reg.owner,
                },
            )
    if failed:
        names = ", ".join(name for name, _ in failed)
        raise BootSmokeFailure(
            f"Customer-template boot smoke failed for {len(failed)} "
            f"template(s): {names}. Container must not serve customer "
            f"requests in this state. Check logs for per-template "
            f"error_class + error_msg."
        )
    return passed, failed


# Side-effect import: register all customer templates at
# module-load time so callers don't need a separate import-then-use
# step.
from . import auditor_kit  # noqa: F401, E402
from . import attestation_letter  # noqa: F401, E402
from . import partner_portfolio_attestation  # noqa: F401, E402
from . import partner_weekly_digest  # noqa: F401, E402
from . import partner_ba_compliance  # noqa: F401, E402
from . import partner_incident_timeline  # noqa: F401, E402
from . import quarterly_summary  # noqa: F401, E402
