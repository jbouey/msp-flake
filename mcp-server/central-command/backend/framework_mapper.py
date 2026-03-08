"""
Multi-Framework Control Mapper

Bridges daemon check_types to all compliance frameworks via HIPAA control crosswalk.

Strategy:
1. CHECK_TYPE_HIPAA_MAP (compliance_packet.py) maps 130+ daemon check_types → HIPAA controls
2. control_mappings.yaml maps conceptual checks → HIPAA + SOC2 + PCI + NIST + CIS controls
3. This module builds: HIPAA control_id → { soc2: [...], pci_dss: [...], ... }
4. Any daemon check_type → HIPAA control → all other framework controls

This enables "one check, many reports" without requiring the YAML to list every
daemon check_type name.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

logger = logging.getLogger(__name__)

# Singleton caches
_YAML_DATA: Optional[Dict] = None
_HIPAA_CROSSWALK: Optional[Dict[str, Dict[str, List[Dict]]]] = None


def _load_yaml() -> Dict:
    """Load control_mappings.yaml once."""
    global _YAML_DATA
    if _YAML_DATA is not None:
        return _YAML_DATA

    yaml_path = Path(__file__).parent / "control_mappings.yaml"
    if not yaml_path.exists():
        logger.error(f"control_mappings.yaml not found at {yaml_path}")
        _YAML_DATA = {}
        return _YAML_DATA

    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)

    _YAML_DATA = data.get("checks", {})
    logger.info(f"Loaded control_mappings.yaml: {len(_YAML_DATA)} conceptual checks")
    return _YAML_DATA


def _build_crosswalk() -> Dict[str, Dict[str, List[Dict]]]:
    """Build HIPAA control_id → { framework: [controls] } crosswalk.

    For each conceptual check in the YAML, extract its HIPAA control_ids,
    then map those HIPAA IDs to equivalent controls in other frameworks.
    """
    global _HIPAA_CROSSWALK
    if _HIPAA_CROSSWALK is not None:
        return _HIPAA_CROSSWALK

    checks = _load_yaml()
    crosswalk: Dict[str, Dict[str, List[Dict]]] = {}

    for check_name, check_data in checks.items():
        fm = check_data.get("framework_mappings", {})
        hipaa_controls = fm.get("hipaa", [])

        # For each HIPAA control this check maps to
        for hc in hipaa_controls:
            hipaa_id = hc.get("control_id", "")
            if not hipaa_id:
                continue

            if hipaa_id not in crosswalk:
                crosswalk[hipaa_id] = {}

            # Add all non-HIPAA framework controls as equivalents
            for framework, controls in fm.items():
                if framework == "hipaa":
                    continue
                existing = crosswalk[hipaa_id].setdefault(framework, [])
                for ctrl in controls:
                    # Avoid duplicates
                    ctrl_id = ctrl.get("control_id", "")
                    if not any(c.get("control_id") == ctrl_id for c in existing):
                        existing.append(ctrl)

    _HIPAA_CROSSWALK = crosswalk
    logger.info(
        f"Built HIPAA crosswalk: {len(crosswalk)} HIPAA controls → "
        f"{sum(len(v) for v in crosswalk.values())} framework mappings"
    )
    return crosswalk


def get_controls_for_check(
    check_type: str,
    hipaa_control_id: str,
    enabled_frameworks: List[str],
) -> List[Dict]:
    """
    Given a daemon check_type, its known HIPAA control, and enabled frameworks,
    return all matching control mappings across frameworks.

    Returns: [
        {"framework": "hipaa", "control_id": "164.312(e)(1)", ...},
        {"framework": "soc2", "control_id": "CC6.6", ...},
        ...
    ]
    """
    crosswalk = _build_crosswalk()
    results = []

    # Always include HIPAA mapping if enabled
    if "hipaa" in enabled_frameworks and hipaa_control_id:
        results.append({
            "framework": "hipaa",
            "control_id": hipaa_control_id,
            "control_name": "",  # Filled by caller if needed
        })

    # Look up equivalent controls via HIPAA crosswalk
    equivalent = crosswalk.get(hipaa_control_id, {})
    for framework in enabled_frameworks:
        if framework == "hipaa":
            continue
        controls = equivalent.get(framework, [])
        for ctrl in controls:
            results.append({
                "framework": framework,
                "control_id": ctrl.get("control_id", ""),
                "control_name": ctrl.get("control_name", ""),
                "category": ctrl.get("category", ""),
                "required": ctrl.get("required", False),
            })

    return results


def get_all_control_ids(framework: str) -> Set[str]:
    """Get all unique control_ids for a framework from the YAML."""
    checks = _load_yaml()
    ids: Set[str] = set()
    for check_data in checks.values():
        fm = check_data.get("framework_mappings", {})
        for ctrl in fm.get(framework, []):
            ids.add(ctrl.get("control_id", ""))
    return ids


def get_controls_for_check_with_hipaa_map(
    check_type: str,
    enabled_frameworks: List[str],
) -> List[Dict]:
    """
    Resolve a daemon check_type to all enabled framework controls.

    Uses CHECK_TYPE_HIPAA_MAP from compliance_packet.py as the bridge:
    check_type → HIPAA control_id → crosswalk → all framework controls.
    """
    from .compliance_packet import CHECK_TYPE_HIPAA_MAP

    hipaa_entry = CHECK_TYPE_HIPAA_MAP.get(check_type, {})
    hipaa_control = hipaa_entry.get("control", "")
    hipaa_desc = hipaa_entry.get("description", check_type.replace("_", " ").title())

    if not hipaa_control:
        return []

    controls = get_controls_for_check(check_type, hipaa_control, enabled_frameworks)

    # Enrich HIPAA entry with description
    for c in controls:
        if c["framework"] == "hipaa" and not c["control_name"]:
            c["control_name"] = hipaa_desc

    return controls


def resolve_control_id(check_type: str, framework: str) -> str:
    """
    Resolve a single check_type to a single control_id for a framework.
    Used by compliance_packet.py for per-framework scoring.
    """
    controls = get_controls_for_check_with_hipaa_map(check_type, [framework])
    if controls:
        return controls[0]["control_id"]
    # Ultimate fallback: return the check_type itself
    return check_type


def resolve_control_description(check_type: str, framework: str) -> str:
    """
    Resolve a check_type to a control description for a framework.
    """
    controls = get_controls_for_check_with_hipaa_map(check_type, [framework])
    if controls:
        return controls[0].get("control_name", "") or check_type.replace("_", " ").title()
    return check_type.replace("_", " ").title()
