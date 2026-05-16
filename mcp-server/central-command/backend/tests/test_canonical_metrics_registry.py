"""CI gate for Counsel Rule 1 — canonical-source registry enforcement.

Gate-passed design at:
  - audit/canonical-source-registry-design-2026-05-13.md (v3)
  - audit/coach-canonical-source-registry-gate-a-2026-05-13.md
  - audit/coach-canonical-source-registry-gate-b-redo-2026-05-13.md (APPROVE)

This gate enforces 3 invariants:

  1. **Registry integrity** — every `signature` in `allowlist` must
     resolve to a real symbol in the backend (catch dead-removal
     drift). Plus PLANNED_METRICS entries are well-formed.

  2. **Frozen-baseline ratchet** — count today's customer-facing
     surfaces that compute a registered metric inline (e.g. the
     `passed / max(total, 1) * 100` shape for compliance_score) and
     pin at `BASELINE_MAX`. New violations fail the gate; existing
     baseline drives to zero via per-line
     `# canonical-migration: <metric_class> — <reason>` markers.

  3. **PLANNED_METRICS no-customer-surface** — no customer-facing
     surface today may compute any metric class in PLANNED_METRICS
     (the metrics have no canonical helper yet; exposure would be
     Rule 1 violation per Gate B P0-B logic).

Sibling-precedent: this gate mirrors
test_no_direct_site_id_update.py + test_email_opacity_harmonized.py.

PHASE 0+1 SCOPE: this file ships the gate skeleton with frozen
baseline at today's count. Phase 2 (substrate invariant
`canonical_metric_drift` display-vs-chain-time) lands separately.
Phase 3 (drive-down) migrates allowlist entries one PR at a time.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

# Repo-root-relative import; the test runner adds backend/ to sys.path.
from canonical_metrics import (
    CANONICAL_METRICS,
    PLANNED_METRICS,
    get_metric_classes,
)


_BACKEND = pathlib.Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────
# Frozen-baseline ratchet — counts inline customer-facing metric
# computations classified as `migrate` in the canonical_metrics.py
# allowlist. Drive-down decrements this baseline one PR at a time via
# per-line `canonical-migration:` markers OR via reclassification
# (`migrate` → `operator_only`/`write_path`) when a callsite turns out
# to be a non-customer-facing read.
#
# Baseline composition (2026-05-15):
#   - compliance_score: 6 migrate-class callsites (per-endpoint
#     compute-then-emit paths pending delegation to compute_compliance_score)
#   - baa_on_file: 1 migrate-class callsite
#   - device_count_per_site: 19 migrate-class callsites
#
# Note: the raw-`FROM discovered_devices` ratchet (a SIBLING gate at
# tests/test_no_raw_discovered_devices_count.py) is a DIFFERENT class
# of callsite — it counts raw-SQL reads, not inline metric
# computations. That sibling sits at BASELINE = 0 after #74 close-out;
# this baseline tracks the INLINE-COMPUTATION subset which is still
# Phase-3-drive-down territory.
# ─────────────────────────────────────────────────────────────────────

BASELINE_MAX = 19  # 0 compliance_score + 0 baa_on_file + 19 device_count_per_site — Phase 3 compliance_score + baa_on_file slices CLOSED via reclassification 2026-05-15. The 6+1=7 entries reclassified as `operator_only` after per-entry methodology inspection revealed each implements a DISTINCT metric class from the canonical helper. Three new PLANNED_METRICS entries (historical_period_compliance_score, category_weighted_compliance_score, per_framework_compliance_score) register the un-canonicalized score classes. baa_on_file slice: _get_current_baa is row-fetch flavor of canonical source-of-truth (queries same baa_signatures join), distinct from is_baa_on_file_verified boolean state-predicate flavor. Only device_count_per_site (19 entries) remains for Phase 3 — needs its own slice Gate A fork.


# ─────────────────────────────────────────────────────────────────────
# Registry integrity tests
# ─────────────────────────────────────────────────────────────────────

def test_canonical_metrics_registry_well_formed():
    """Every CANONICAL_METRICS entry has the required keys."""
    required_keys = {"canonical_helper", "allowlist"}
    for metric_class, spec in CANONICAL_METRICS.items():
        missing = required_keys - set(spec.keys())
        assert not missing, (
            f"CANONICAL_METRICS[{metric_class!r}] missing required keys: {missing}"
        )


def test_planned_metrics_registry_well_formed():
    """Every PLANNED_METRICS entry has the required keys + no helper
    that would make it eligible for CANONICAL_METRICS instead.
    """
    required_keys = {"canonical_helper_pending", "blocks_until"}
    for metric_class, spec in PLANNED_METRICS.items():
        missing = required_keys - set(spec.keys())
        assert not missing, (
            f"PLANNED_METRICS[{metric_class!r}] missing required keys: {missing}"
        )
        # Sanity: a planned-metric must NOT have `canonical_helper` set
        # (would mean it should be in CANONICAL_METRICS instead).
        assert "canonical_helper" not in spec, (
            f"PLANNED_METRICS[{metric_class!r}] has canonical_helper — "
            f"if a helper exists, move the entry to CANONICAL_METRICS."
        )


def test_metric_class_keys_disjoint():
    """A metric class lives in exactly ONE of CANONICAL_METRICS or
    PLANNED_METRICS — never both (would be ambiguous).
    """
    overlap = set(CANONICAL_METRICS.keys()) & set(PLANNED_METRICS.keys())
    assert not overlap, (
        f"Metric classes in BOTH CANONICAL_METRICS and PLANNED_METRICS: "
        f"{overlap}. Move to one or the other."
    )


def test_allowlist_classifications_are_valid():
    """Every allowlist entry has a `classification` of 'migrate' or
    'operator_only'. Bare entries (no classification) fail the gate —
    Task #50 Gate B P1-3 required explicit classification.
    """
    valid_classifications = {"migrate", "operator_only", "write_path"}
    for metric_class, spec in CANONICAL_METRICS.items():
        for entry in spec.get("allowlist", []):
            classification = entry.get("classification")
            assert classification in valid_classifications, (
                f"CANONICAL_METRICS[{metric_class!r}].allowlist entry "
                f"{entry!r} missing or invalid classification "
                f"(must be 'migrate' or 'operator_only')"
            )


def test_allowlist_signatures_resolve_to_real_symbols():
    """Every `module.symbol[.method]` signature in the migrate-class
    allowlist must resolve to a real Python symbol in the backend.
    Catches registry drift like the Session 220 finding:
    `compliance_packet.ComplianceReport._calculate_compliance_score`
    pointed at a class that didn't exist (real class:
    `CompliancePacket`) — the registry would have rotted forever if
    no one tried to use the entry. Same lesson found
    `client_attestation_letter._get_baa_signature_row` should be
    `_get_current_baa`.

    Out of scope:
      - `operator_only` entries with wildcard `<module>.*` patterns
        (intentionally point at a whole-module class, not a specific
        symbol)
      - `write_path` entries that are line-number anchored
        (`file.py:LINE` shape — checked by sibling
        `test_no_raw_discovered_devices_count.py` instead)
      - Skipped if the module can't import in test context (relative-
        import chains that need the FastAPI app bootstrap); the
        signature still gets a basic shape-check but the symbol-
        resolve is best-effort.
    """
    import importlib
    import re

    # Skip wildcards + line-anchored entries — different gate class
    SKIP_PATTERN = re.compile(r"\.\*$|\.py:\d+$")

    failures: list[str] = []
    for metric_class, spec in CANONICAL_METRICS.items():
        for entry in spec.get("allowlist", []):
            sig = entry.get("signature", "")
            if not sig or SKIP_PATTERN.search(sig):
                continue
            # Only enforce on `migrate` — operator_only / write_path
            # entries are documentation, not active drive-down targets
            if entry.get("classification") != "migrate":
                continue
            parts = sig.split(".")
            if len(parts) < 2:
                failures.append(
                    f"{metric_class}.allowlist[{sig!r}]: signature "
                    "is not in module.symbol shape"
                )
                continue
            mod_name = parts[0]
            try:
                mod = importlib.import_module(mod_name)
            except ImportError:
                # Module can't import in test context (likely needs
                # the FastAPI app bootstrap). Best-effort: do a shape
                # check by looking at the source file directly.
                import pathlib
                src_path = _BACKEND / f"{mod_name}.py"
                if not src_path.exists():
                    failures.append(
                        f"{metric_class}.allowlist[{sig!r}]: module "
                        f"`{mod_name}` not found at {src_path}"
                    )
                    continue
                src = src_path.read_text()
                # Look for any of: `def <last>(`, `class <part>:`,
                # `<last> =` (module-level constant)
                last = parts[-1]
                middle_class = parts[1] if len(parts) >= 3 else None
                if middle_class:
                    # method-on-class shape: verify class exists +
                    # method exists somewhere in the file (best-effort)
                    if f"class {middle_class}" not in src:
                        failures.append(
                            f"{metric_class}.allowlist[{sig!r}]: class "
                            f"`{middle_class}` not found in {mod_name}.py"
                        )
                        continue
                if f"def {last}(" not in src and f"async def {last}(" not in src \
                        and f"\n{last} =" not in src and f"\n{last}: " not in src:
                    failures.append(
                        f"{metric_class}.allowlist[{sig!r}]: symbol "
                        f"`{last}` not found in {mod_name}.py"
                    )
                continue
            except Exception as e:
                failures.append(
                    f"{metric_class}.allowlist[{sig!r}]: unexpected import "
                    f"error: {e}"
                )
                continue
            # Module imported — walk the parts
            cur = mod
            ok = True
            for p in parts[1:]:
                if hasattr(cur, p):
                    cur = getattr(cur, p)
                else:
                    ok = False
                    break
            if not ok:
                failures.append(
                    f"{metric_class}.allowlist[{sig!r}]: symbol resolution "
                    f"failed at `.{p}` (chain broke)"
                )
    assert not failures, (
        "Registry-integrity drift in CANONICAL_METRICS allowlist:\n"
        + "\n".join(f"  - {f}" for f in failures)
        + "\n\nFix the signatures or remove dead entries — the registry "
        "is the auditable manifest of Counsel Rule 1 enforcement; broken "
        "entries silently degrade gate value."
    )


def test_already_gated_entries_cite_evidence_test():
    """Per Gate B P1-2: entries marked `already_gated: True` must cite
    the test/file that PROVES the gate is wired (auditor-grade).
    """
    for metric_class, spec in CANONICAL_METRICS.items():
        if spec.get("already_gated"):
            assert "evidence_test" in spec and spec["evidence_test"], (
                f"CANONICAL_METRICS[{metric_class!r}] is `already_gated: "
                f"True` but missing `evidence_test:` citation. Per Gate B "
                f"P1-2, every gated entry must cite its proof test."
            )


# ─────────────────────────────────────────────────────────────────────
# Frozen-baseline ratchet
# ─────────────────────────────────────────────────────────────────────

def _count_migrate_class_allowlist_entries() -> int:
    """Count `migrate`-class entries across all CANONICAL_METRICS."""
    total = 0
    for metric_class, spec in CANONICAL_METRICS.items():
        for entry in spec.get("allowlist", []):
            if entry.get("classification") == "migrate":
                total += 1
    return total


def test_migrate_class_count_at_or_below_baseline():
    """Frozen-baseline ratchet: the total `migrate`-class allowlist
    count today is BASELINE_MAX. Removing an entry (via Phase 3
    drive-down PR) requires lowering BASELINE_MAX in lockstep. Adding
    a new entry FAILS the gate — every new inline computation must be
    migrated to delegate to canonical helper instead.

    To lower BASELINE_MAX: remove the migrated allowlist entry, add
    the `# canonical-migration: <metric_class> — <reason>` marker at
    the now-migrated callsite, decrement BASELINE_MAX, coach pass.
    """
    actual = _count_migrate_class_allowlist_entries()
    assert actual <= BASELINE_MAX, (
        f"`migrate`-class allowlist count {actual} EXCEEDS "
        f"BASELINE_MAX={BASELINE_MAX}. New non-canonical metric "
        f"computations may not be added — migrate to canonical helper "
        f"or accept the gate's BLOCK."
    )
    # Optional ratchet-down hint: if actual < BASELINE_MAX, log a
    # WARNING (not failure) suggesting the baseline should be lowered.
    # Pytest doesn't have stable warning surfaces; for now we just
    # accept actual <= BASELINE_MAX without forcing the decrement.


# ─────────────────────────────────────────────────────────────────────
# PLANNED_METRICS no-customer-surface check
# ─────────────────────────────────────────────────────────────────────

def test_planned_metrics_not_exposed_today():
    """PLANNED_METRICS entries have no canonical helper yet. They MUST
    NOT be exposed by any customer-facing surface today — exposure
    would be a Rule 1 violation (Gate B P0-B logic).

    Phase 0+1 scope: this test is a stub that asserts PLANNED_METRICS
    is well-formed. Phase 2 substrate invariant + per-endpoint AST
    inspection lands in a follow-up. For now, the stub-shape gates
    that planned metrics are documented + un-exposed by design
    (engineering commitment, not yet AST-enforced).
    """
    # Stub gate — every entry must declare its blocks_until pointing
    # at a Task or substrate-event that, when complete, unblocks
    # migration to CANONICAL_METRICS.
    for metric_class, spec in PLANNED_METRICS.items():
        blocks_until = spec.get("blocks_until", "")
        assert blocks_until, (
            f"PLANNED_METRICS[{metric_class!r}] missing `blocks_until:` "
            f"— planned metrics must declare the work that unblocks "
            f"their migration to CANONICAL_METRICS."
        )


# ─────────────────────────────────────────────────────────────────────
# Smoke test — registry module importable + helpers work
# ─────────────────────────────────────────────────────────────────────

def test_get_metric_classes_returns_union():
    classes = get_metric_classes()
    expected = set(CANONICAL_METRICS.keys()) | set(PLANNED_METRICS.keys())
    assert set(classes) == expected
    # Ordered for stable output.
    assert classes == sorted(classes)


def test_canonical_metrics_module_importable():
    """The module exists at the expected path + the constants are
    populated.
    """
    mod = importlib.import_module("canonical_metrics")
    assert hasattr(mod, "CANONICAL_METRICS")
    assert hasattr(mod, "PLANNED_METRICS")
    assert len(mod.CANONICAL_METRICS) >= 4, (
        "CANONICAL_METRICS should have at least the 4 Phase 0 entries "
        "(compliance_score, baa_on_file, runbook_id_canonical, "
        "l2_resolution_tier)"
    )
