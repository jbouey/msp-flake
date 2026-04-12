"""CheckinContext — shared state across all checkin STEPs.

The original handler mutates ~30 local variables across 1,300 lines.
This dataclass makes the contract explicit: every helper reads and
writes specific fields, so data flow is traceable.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class CheckinContext:
    """Shared mutable state for the checkin pipeline.

    Fields fall into four groups:
      1. Request inputs (never modified after construction)
      2. Identity outputs (computed in STEPS 0-3, read by later steps)
      3. Response accumulators (built up across STEPS)
      4. Derived metadata (MAC list, boot source, WG state)
    """

    # --- Request inputs (immutable after __init__) ---
    checkin: Any  # ApplianceCheckin model
    request_ip: str
    user_agent: str
    auth_site_id: str
    now: datetime

    # --- Identity outputs (STEPS 0-3) ---
    appliance_id: str = ""
    canonical_appliance_id: str = ""
    canonical_id: str = ""  # alias used by some steps
    site_id: str = ""
    is_ghost: bool = False
    merge_from_ids: List[str] = field(default_factory=list)
    earliest_first_checkin: Optional[datetime] = None
    display_name: str = ""
    rotated_api_key: Optional[str] = None

    # --- Subsystem outputs (STEPS 3.4-3.6b) ---
    signing_key_registered: bool = False
    agent_public_key_hash: str = ""

    # --- Response accumulators (STEPS 3.7-7c) ---
    windows_targets: List[Dict[str, Any]] = field(default_factory=list)
    linux_targets: List[Dict[str, Any]] = field(default_factory=list)
    pending_orders: List[Dict[str, Any]] = field(default_factory=list)
    fleet_orders: List[Dict[str, Any]] = field(default_factory=list)
    disabled_checks: List[str] = field(default_factory=list)
    runbook_config: Dict[str, Any] = field(default_factory=dict)
    mesh_peers: List[Dict[str, Any]] = field(default_factory=list)
    peer_bundle_hashes: List[str] = field(default_factory=list)
    target_assignment: Dict[str, Any] = field(default_factory=dict)
    alert_mode: str = "standard"
    maintenance_window: Optional[Dict[str, Any]] = None
    deployment_triggers: Dict[str, Any] = field(default_factory=dict)
    billing_status: Dict[str, Any] = field(default_factory=dict)
    pending_devices: List[Dict[str, Any]] = field(default_factory=list)

    # --- Derived metadata ---
    all_mac_addresses: List[str] = field(default_factory=list)
    boot_source: str = ""  # "live_usb" | "installed_disk" | ""
    wg_access_state: str = ""
    wg_access_expires: Optional[datetime] = None
