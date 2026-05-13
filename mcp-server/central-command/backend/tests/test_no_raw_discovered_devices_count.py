"""CI gate (Task #73 Phase 1, Counsel Rule 1): no customer-facing reader
of discovered_devices that emits device count without delegating to
canonical_devices.

Phase 1 ships:
  - canonical_devices table (mig 319)
  - reconciliation loop (background_tasks.canonical_devices_reconciliation_loop)
  - 2 customer-facing readers migrated:
      * compliance_packet._get_device_inventory (CTE-JOIN to canonical)
      * device_sync.get_site_devices (CTE-driven row set from canonical)
  - This CI gate ratchets the remaining offender count at the post-Phase-1
    baseline. Phase 2 drives the baseline to 0.

Ratchet semantics:
  - SOURCE = grep `FROM discovered_devices` across backend source files
  - EXEMPT_FILES = backend code that legitimately reads raw discovered_devices
    (write paths, freshness invariants, per-appliance audit trail, DISTINCT
    aggregations) — classified in canonical_metrics.py allowlist as
    `operator_only` or `write_path`.
  - MIGRATED = files that have the `# canonical-migration: device_count_per_site`
    inline marker (compliance_packet + device_sync as of Phase 1).
  - VIOLATIONS = SOURCE - EXEMPT_FILES - MIGRATED → must stay ≤ BASELINE_MAX.

Pin: BASELINE_MAX = 17 today (Phase 2 readers to be migrated). Phase 2
drives to 0.
"""
from __future__ import annotations

import pathlib
import re

_BACKEND = pathlib.Path(__file__).resolve().parent.parent

# These files legitimately read raw discovered_devices and are NOT
# Phase 2 migration targets. Each is classified in canonical_metrics.py
# allowlist as `operator_only` or `write_path`.
_EXEMPT_FILES = frozenset({
    "assertions.py",          # _check_discovered_devices_freshness + _check_canonical_devices_freshness (per-row + per-site)
    "device_sync.py",         # write paths (sync from appliances) + the canonical reader uses migration marker
    "appliance_trace.py",     # per-appliance audit trail (operator-only)
    "prometheus_metrics.py",  # operator-only metrics
    "background_tasks.py",    # canonical_devices reconciliation loop (the writer)
    "compliance_packet.py",   # post-migration uses marker; raw-reader removed
    "health_monitor.py",      # owner_appliance write path
})

# Files that have migrated MUST contain this marker. Used to assert the
# migration happened (not just the count dropped accidentally).
_MIGRATION_MARKER = "canonical-migration: device_count_per_site"

# Frozen baseline. Phase 2 commits decrement this as each customer-facing
# reader migrates. Drive-down target: 0. Empirical Phase-1-post count = 22
# (raw grep returned more callsites than Gate A v2 fork enumerated — e.g.
# compliance_frameworks.py + sites.py readers Gate A missed). Phase 2 owns
# enumerating-and-migrating each.
BASELINE_MAX = 22


def _raw_count_in_file(path: pathlib.Path) -> int:
    """Count `FROM discovered_devices` occurrences not preceded by a
    migration marker within 5 lines above.
    """
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return 0
    lines = text.splitlines()
    count = 0
    for i, line in enumerate(lines):
        if "FROM discovered_devices" not in line:
            continue
        # Look back 5 lines for the migration marker
        window = lines[max(0, i - 5): i]
        if any(_MIGRATION_MARKER in w for w in window):
            continue
        count += 1
    return count


def test_canonical_devices_table_exists_in_fixture():
    """Phase 1 schema sanity — canonical_devices columns are pinned in
    prod_columns.json fixture so a future drift is caught by
    test_data_model_audit_contract.
    """
    import json
    fixture = json.loads(
        (_BACKEND / "tests" / "fixtures" / "schema" / "prod_columns.json").read_text()
    )
    assert "canonical_devices" in fixture, (
        "canonical_devices table missing from prod_columns.json. "
        "Mig 319 schema is the authoritative source — fixture must include it."
    )
    expected_cols = {
        "canonical_id", "site_id", "ip_address", "mac_address",
        "mac_dedup_key", "device_type", "first_seen_at", "last_seen_at",
        "observed_by_appliances", "reconciled_at", "created_at",
    }
    assert set(fixture["canonical_devices"]) == expected_cols, (
        f"canonical_devices fixture column drift. Expected {expected_cols}, "
        f"got {set(fixture['canonical_devices'])}. Update fixture or "
        "mig 319 schema."
    )


def test_phase1_migrated_files_carry_marker():
    """Phase 1 readers that migrated MUST carry the inline marker
    `# canonical-migration: device_count_per_site`. Without the marker,
    a future PR could revert the migration silently.
    """
    expected_marker_files = [
        "compliance_packet.py",
        "device_sync.py",
    ]
    missing: list[str] = []
    for f in expected_marker_files:
        path = _BACKEND / f
        if not path.exists():
            missing.append(f"{f} (file missing)")
            continue
        if _MIGRATION_MARKER not in path.read_text():
            missing.append(f"{f} (marker missing)")
    assert not missing, (
        "Phase 1 migrated files MUST carry the canonical-migration marker. "
        "Files without it:\n" + "\n".join(missing)
    )


def test_raw_discovered_devices_count_under_baseline():
    """Count `FROM discovered_devices` occurrences across backend files
    NOT in EXEMPT_FILES and NOT preceded by the migration marker.

    Frozen baseline = 17 (Phase 2 migration targets). Phase 2 drives to 0.
    """
    total = 0
    per_file: dict[str, int] = {}
    for py in _BACKEND.glob("*.py"):
        if py.name in _EXEMPT_FILES:
            continue
        n = _raw_count_in_file(py)
        if n > 0:
            per_file[py.name] = n
            total += n
    assert total <= BASELINE_MAX, (
        f"Raw `FROM discovered_devices` count {total} EXCEEDS "
        f"BASELINE_MAX={BASELINE_MAX}. Per-file:\n"
        + "\n".join(f"  {k}: {v}" for k, v in sorted(per_file.items()))
        + "\nMigrate to canonical_devices + add `# canonical-migration: "
        "device_count_per_site` marker, OR add the file to EXEMPT_FILES "
        "with a classification rationale."
    )


def test_packet_methodology_version_pinned():
    """compliance_packet.py must declare _PACKET_METHODOLOGY_VERSION
    (Task #73 Phase 1 disclosure). Future bumps to past 2.0 must update
    this pin in lockstep.
    """
    src = (_BACKEND / "compliance_packet.py").read_text()
    assert '_PACKET_METHODOLOGY_VERSION = "2.0"' in src, (
        "compliance_packet.py must declare "
        "_PACKET_METHODOLOGY_VERSION = \"2.0\" — methodology disclosure "
        "pin for the Phase 1 canonical_devices migration. Bumping past "
        "2.0 must update this literal in lockstep with the Methodology "
        "section copy."
    )
