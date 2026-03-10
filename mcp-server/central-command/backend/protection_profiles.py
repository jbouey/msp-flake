"""Application Protection Profiles API.

Partners register proprietary business applications (Epic EHR, Dentrix, etc.),
trigger a discovery swarm to find critical assets, establish a golden baseline,
and auto-generate strict L1 rules that enforce no-drift for those assets.

Uses existing L1 rule sync — no custom runbook packages needed.
"""

import json
import logging
import uuid as _uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Query, HTTPException, Depends, Path
from pydantic import BaseModel, Field

from .fleet import get_pool
from .order_signing import sign_admin_order
from .auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/protection-profiles", tags=["protection-profiles"])


# =============================================================================
# Pydantic Models
# =============================================================================

class ProfileCreate(BaseModel):
    site_id: str
    name: str
    description: Optional[str] = None
    template_id: Optional[str] = None


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class AssetToggle(BaseModel):
    enabled: bool


class ProfileSummary(BaseModel):
    id: str
    site_id: str
    name: str
    description: Optional[str]
    status: str
    created_by: Optional[str]
    created_at: str
    updated_at: str
    asset_count: int = 0
    rule_count: int = 0
    enabled_asset_count: int = 0


class AssetInfo(BaseModel):
    id: str
    asset_type: str
    asset_name: str
    display_name: Optional[str]
    baseline_value: Dict[str, Any]
    enabled: bool
    runbook_id: Optional[str]


class RuleInfo(BaseModel):
    id: str
    l1_rule_id: str
    asset_id: str
    enabled: bool
    rule_json: Dict[str, Any]


class ProfileDetail(BaseModel):
    id: str
    site_id: str
    name: str
    description: Optional[str]
    status: str
    created_by: Optional[str]
    created_at: str
    updated_at: str
    discovery_data: Optional[Dict[str, Any]]
    baseline_data: Optional[Dict[str, Any]]
    template_id: Optional[str]
    assets: List[AssetInfo]
    rules: List[RuleInfo]


class TemplateSummary(BaseModel):
    id: str
    name: str
    description: Optional[str]
    category: Optional[str]
    discovery_hints: Dict[str, Any]
    icon: Optional[str]


# =============================================================================
# Runbook Mapping — asset type → existing runbook ID
# =============================================================================

ASSET_RUNBOOK_MAP = {
    "service":        "RB-WIN-SVC-001",
    "port":           "RB-WIN-FW-002",
    "registry_key":   "RB-WIN-REG-001",
    "scheduled_task": "RB-WIN-TASK-001",
    "config_file":    "RB-WIN-CFG-001",
    "database_conn":  "RB-WIN-TCP-001",
    "iis_binding":    "RB-WIN-IIS-001",
    "odbc_dsn":       "RB-WIN-ODBC-001",
    "process":        "RB-WIN-PROC-001",
}

# Incident type mapping — what drift scanner reports for each asset type
ASSET_INCIDENT_TYPE_MAP = {
    "service":        "service_stopped",
    "port":           "port_closed",
    "registry_key":   "registry_drift",
    "scheduled_task": "scheduled_task_disabled",
    "config_file":    "config_file_changed",
    "database_conn":  "tcp_connectivity_lost",
    "iis_binding":    "iis_binding_drift",
    "odbc_dsn":       "odbc_dsn_drift",
    "process":        "process_unhealthy",
}

# Data field name for each asset type's matching condition
ASSET_DATA_FIELD_MAP = {
    "service":        "data.service_name",
    "port":           "data.port",
    "registry_key":   "data.registry_path",
    "scheduled_task": "data.task_name",
    "config_file":    "data.file_path",
    "database_conn":  "data.host_port",
    "iis_binding":    "data.site_binding",
    "odbc_dsn":       "data.dsn_name",
    "process":        "data.process_name",
}


# =============================================================================
# Templates
# =============================================================================

@router.get("/templates", response_model=List[TemplateSummary])
async def list_templates(user: Dict[str, Any] = Depends(require_auth)):
    """List available application profile templates."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, description, category, discovery_hints, icon
            FROM app_profile_templates
            ORDER BY category, name
        """)
        return [
            TemplateSummary(
                id=str(r["id"]),
                name=r["name"],
                description=r["description"],
                category=r["category"],
                discovery_hints=r["discovery_hints"] or {},
                icon=r["icon"],
            )
            for r in rows
        ]


# =============================================================================
# Profile CRUD
# =============================================================================

@router.get("", response_model=List[ProfileSummary])
async def list_profiles(
    site_id: str = Query(..., description="Required: site to list profiles for"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """List protection profiles for a specific site."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.*,
                (SELECT count(*) FROM app_profile_assets a WHERE a.profile_id = p.id) as asset_count,
                (SELECT count(*) FROM app_profile_assets a WHERE a.profile_id = p.id AND a.enabled) as enabled_asset_count,
                (SELECT count(*) FROM app_profile_rules r WHERE r.profile_id = p.id) as rule_count
            FROM app_protection_profiles p
            WHERE p.site_id = $1 AND p.status != 'archived'
            ORDER BY p.created_at DESC
        """, site_id)

        return [_profile_summary(r) for r in rows]


@router.post("", response_model=ProfileSummary, status_code=201)
async def create_profile(body: ProfileCreate, user: Dict[str, Any] = Depends(require_auth)):
    """Create a new protection profile (draft status)."""
    pool = await get_pool()
    profile_id = _uuid.uuid4()
    now = datetime.now(timezone.utc)

    # If template specified, fetch discovery hints
    template_id = None
    if body.template_id:
        template_id = _uuid.UUID(body.template_id)

    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                INSERT INTO app_protection_profiles
                    (id, site_id, name, description, status, created_at, updated_at, template_id)
                VALUES ($1, $2, $3, $4, 'draft', $5, $5, $6)
            """, profile_id, body.site_id, body.name, body.description, now, template_id)
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"Profile '{body.name}' already exists for this site")
            raise

        row = await conn.fetchrow("""
            SELECT p.*,
                0::bigint as asset_count, 0::bigint as enabled_asset_count, 0::bigint as rule_count
            FROM app_protection_profiles p WHERE p.id = $1
        """, profile_id)
        return _profile_summary(row)


@router.get("/{profile_id}", response_model=ProfileDetail)
async def get_profile(
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Get profile with all assets and rules."""
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
            pid, site_id,
        )
        if not row:
            raise HTTPException(404, "Profile not found")

        assets = await conn.fetch(
            "SELECT * FROM app_profile_assets WHERE profile_id = $1 ORDER BY asset_type, asset_name",
            pid,
        )
        rules = await conn.fetch(
            "SELECT * FROM app_profile_rules WHERE profile_id = $1 ORDER BY l1_rule_id",
            pid,
        )

        return ProfileDetail(
            id=str(row["id"]),
            site_id=row["site_id"],
            name=row["name"],
            description=row["description"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
            discovery_data=row["discovery_data"],
            baseline_data=row["baseline_data"],
            template_id=str(row["template_id"]) if row["template_id"] else None,
            assets=[
                AssetInfo(
                    id=str(a["id"]),
                    asset_type=a["asset_type"],
                    asset_name=a["asset_name"],
                    display_name=a["display_name"],
                    baseline_value=a["baseline_value"] or {},
                    enabled=a["enabled"],
                    runbook_id=a["runbook_id"],
                )
                for a in assets
            ],
            rules=[
                RuleInfo(
                    id=str(r["id"]),
                    l1_rule_id=r["l1_rule_id"],
                    asset_id=str(r["asset_id"]),
                    enabled=r["enabled"],
                    rule_json=r["rule_json"] or {},
                )
                for r in rules
            ],
        )


@router.patch("/{profile_id}", response_model=ProfileSummary)
async def update_profile(
    body: ProfileUpdate,
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Update profile name, description, or status."""
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)
    now = datetime.now(timezone.utc)

    sets = ["updated_at = $2"]
    params: list = [pid, now]
    idx = 3

    if body.name is not None:
        sets.append(f"name = ${idx}")
        params.append(body.name)
        idx += 1
    if body.description is not None:
        sets.append(f"description = ${idx}")
        params.append(body.description)
        idx += 1
    if body.status is not None:
        valid = {"draft", "discovering", "discovered", "baseline_locked", "active", "paused", "archived"}
        if body.status not in valid:
            raise HTTPException(400, f"Invalid status. Must be one of: {', '.join(sorted(valid))}")
        sets.append(f"status = ${idx}")
        params.append(body.status)
        idx += 1

    async with pool.acquire() as conn:
        # site_id co-constraint prevents cross-tenant modification
        params.append(site_id)
        result = await conn.execute(
            f"UPDATE app_protection_profiles SET {', '.join(sets)} WHERE id = $1 AND site_id = ${idx}",
            *params,
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Profile not found")

        row = await conn.fetchrow("""
            SELECT p.*,
                (SELECT count(*) FROM app_profile_assets a WHERE a.profile_id = p.id) as asset_count,
                (SELECT count(*) FROM app_profile_assets a WHERE a.profile_id = p.id AND a.enabled) as enabled_asset_count,
                (SELECT count(*) FROM app_profile_rules r WHERE r.profile_id = p.id) as rule_count
            FROM app_protection_profiles p WHERE p.id = $1
        """, pid)
        return _profile_summary(row)


@router.delete("/{profile_id}")
async def delete_profile(
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Archive a profile (soft delete). Also disables its L1 rules."""
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Verify profile belongs to site before any mutations
            profile = await conn.fetchrow(
                "SELECT id FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
                pid, site_id,
            )
            if not profile:
                raise HTTPException(404, "Profile not found")

            # Disable all L1 rules generated from this profile
            rule_ids = await conn.fetch(
                "SELECT l1_rule_id FROM app_profile_rules WHERE profile_id = $1", pid
            )
            for r in rule_ids:
                await conn.execute(
                    "UPDATE l1_rules SET enabled = false WHERE rule_id = $1",
                    r["l1_rule_id"],
                )

            await conn.execute(
                "UPDATE app_protection_profiles SET status = 'archived', updated_at = $2 WHERE id = $1",
                pid, now,
            )

    return {"status": "archived", "rules_disabled": len(rule_ids)}


# =============================================================================
# Discovery
# =============================================================================

@router.post("/{profile_id}/discover")
async def trigger_discovery(
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Trigger app discovery scan on the site's appliance.

    Creates a run_drift fleet order with mode=app_discovery.
    The appliance runs discovery and reports results in next checkin.
    """
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)

    async with pool.acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT * FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
            pid, site_id,
        )
        if not profile:
            raise HTTPException(404, "Profile not found")

        if profile["status"] not in ("draft", "discovered"):
            raise HTTPException(
                400, f"Cannot discover in status '{profile['status']}'. Must be draft or discovered."
            )

        # Get discovery hints from template if available
        hints = {}
        if profile["template_id"]:
            tmpl = await conn.fetchrow(
                "SELECT discovery_hints FROM app_profile_templates WHERE id = $1",
                profile["template_id"],
            )
            if tmpl and tmpl["discovery_hints"]:
                hints = tmpl["discovery_hints"]

        # Verify credentials exist before discovery (discovery will fail without them)
        has_creds = await conn.fetchval("""
            SELECT EXISTS(
                SELECT 1 FROM site_credentials
                WHERE site_id = $1
                AND credential_type IN ('winrm', 'domain_admin', 'local_admin')
            )
        """, profile["site_id"])
        if not has_creds:
            raise HTTPException(
                400,
                "No Windows credentials configured for this site. "
                "Add domain_admin or local_admin credentials before running discovery."
            )

        # Find appliance for this site
        appliance = await conn.fetchrow("""
            SELECT appliance_id FROM site_appliances
            WHERE site_id = $1
            ORDER BY last_checkin DESC NULLS LAST
            LIMIT 1
        """, profile["site_id"])
        if not appliance:
            raise HTTPException(400, "No appliance found for this site")

        # Create fleet order for app discovery
        order_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc)
        exp = now + timedelta(hours=24)
        parameters = {
            "mode": "app_discovery",
            "profile_id": str(pid),
            "profile_name": profile["name"],
            "hints": hints,
        }

        nonce, signature, signed_payload = sign_admin_order(
            order_id, "run_drift", parameters, now, exp,
            target_appliance_id=appliance["appliance_id"],
        )

        await conn.execute("""
            INSERT INTO admin_orders (
                order_id, appliance_id, site_id, order_type,
                parameters, priority, status, created_at, expires_at,
                nonce, signature, signed_payload
            ) VALUES ($1, $2, $3, 'run_drift', $4::jsonb, 2, 'active', $5, $6, $7, $8, $9)
        """,
            order_id,
            appliance["appliance_id"],
            profile["site_id"],
            json.dumps(parameters),
            now, exp,
            nonce, signature, signed_payload,
        )

        await conn.execute(
            "UPDATE app_protection_profiles SET status = 'discovering', updated_at = $2 WHERE id = $1",
            pid, now,
        )

    logger.info(f"Triggered app discovery for profile {pid} on appliance {appliance['appliance_id']}")
    return {"status": "discovering", "order_id": order_id}


@router.post("/{profile_id}/discovery-results")
async def receive_discovery_results(
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    results: Dict[str, Any] = ...,
    user: Dict[str, Any] = Depends(require_auth),
):
    """Receive discovery results from appliance (called via checkin pipeline).

    Results are stored and assets are created for review.
    """
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            profile = await conn.fetchrow(
                "SELECT * FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
                pid, site_id,
            )
            if not profile:
                raise HTTPException(404, "Profile not found")

            # Store raw discovery data
            await conn.execute(
                "UPDATE app_protection_profiles SET discovery_data = $2::jsonb, status = 'discovered', updated_at = $3 WHERE id = $1",
                pid, json.dumps(results), now,
            )

            # Clear previous assets (re-discovery)
            await conn.execute("DELETE FROM app_profile_assets WHERE profile_id = $1", pid)

            # Create assets from discovery results
            asset_count = 0
            for asset_type, items in results.get("assets", {}).items():
                runbook_id = ASSET_RUNBOOK_MAP.get(asset_type)
                for item in items:
                    asset_id = _uuid.uuid4()
                    await conn.execute("""
                        INSERT INTO app_profile_assets
                            (id, profile_id, asset_type, asset_name, display_name,
                             baseline_value, enabled, runbook_id, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb, true, $7, $8)
                    """,
                        asset_id, pid, asset_type,
                        item.get("name", ""),
                        item.get("display_name"),
                        json.dumps(item.get("value", {})),
                        runbook_id, now,
                    )
                    asset_count += 1

    logger.info(f"Stored {asset_count} discovered assets for profile {pid}")
    return {"status": "discovered", "asset_count": asset_count}


# =============================================================================
# Asset Management
# =============================================================================

@router.patch("/{profile_id}/assets/{asset_id}")
async def toggle_asset(
    body: AssetToggle,
    profile_id: str = Path(...),
    asset_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Enable or disable an asset for baseline protection."""
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)
    aid = _uuid.UUID(asset_id)

    async with pool.acquire() as conn:
        # Verify profile belongs to site before mutating assets
        profile = await conn.fetchrow(
            "SELECT id FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
            pid, site_id,
        )
        if not profile:
            raise HTTPException(404, "Profile not found")

        result = await conn.execute(
            "UPDATE app_profile_assets SET enabled = $3 WHERE id = $2 AND profile_id = $1",
            pid, aid, body.enabled,
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Asset not found")

    return {"status": "ok", "enabled": body.enabled}


# =============================================================================
# Baseline Lock & Rule Generation
# =============================================================================

@router.post("/{profile_id}/lock-baseline")
async def lock_baseline(
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Lock the golden baseline and auto-generate L1 rules for all enabled assets.

    This is the critical step that turns discovered assets into protection rules.
    Rules are inserted into l1_rules table and will be synced automatically
    via the existing sync_rules mechanism.
    """
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            profile = await conn.fetchrow(
                "SELECT * FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
                pid, site_id,
            )
            if not profile:
                raise HTTPException(404, "Profile not found")

            if profile["status"] not in ("discovered", "baseline_locked", "active"):
                raise HTTPException(
                    400,
                    f"Cannot lock baseline in status '{profile['status']}'. Run discovery first.",
                )

            # Get enabled assets
            assets = await conn.fetch(
                "SELECT * FROM app_profile_assets WHERE profile_id = $1 AND enabled = true ORDER BY asset_type, asset_name",
                pid,
            )
            if not assets:
                raise HTTPException(400, "No enabled assets to protect")

            # Build baseline snapshot
            baseline = {}
            for a in assets:
                key = f"{a['asset_type']}:{a['asset_name']}"
                baseline[key] = a["baseline_value"]

            # Clear old rules for this profile
            old_rule_ids = await conn.fetch(
                "SELECT l1_rule_id FROM app_profile_rules WHERE profile_id = $1", pid
            )
            for r in old_rule_ids:
                await conn.execute("DELETE FROM l1_rules WHERE rule_id = $1", r["l1_rule_id"])
            await conn.execute("DELETE FROM app_profile_rules WHERE profile_id = $1", pid)

            # Generate L1 rules per asset
            prefix = str(pid)[:8].upper()
            rules_created = 0
            type_counters: Dict[str, int] = {}

            for asset in assets:
                at = asset["asset_type"]
                incident_type = ASSET_INCIDENT_TYPE_MAP.get(at)
                runbook_id = ASSET_RUNBOOK_MAP.get(at)
                data_field = ASSET_DATA_FIELD_MAP.get(at)

                if not incident_type or not runbook_id:
                    logger.warning(f"No mapping for asset type '{at}', skipping")
                    continue

                type_counters[at] = type_counters.get(at, 0) + 1
                suffix = f"{at[:3].upper()}-{type_counters[at]:03d}"
                rule_id = f"APP-{prefix}-{suffix}"

                # Build L1 rule conditions
                conditions = [
                    {"field": "incident_type", "operator": "eq", "value": incident_type},
                ]
                if data_field:
                    conditions.append(
                        {"field": data_field, "operator": "eq", "value": asset["asset_name"]}
                    )

                # Build action params from baseline value
                action_params = {
                    "runbook_id": runbook_id,
                    "profile_id": str(pid),
                    "profile_name": profile["name"],
                }
                # Merge baseline value into action params for the runbook
                bv = asset["baseline_value"] or {}
                action_params.update(bv)

                rule_json = {
                    "id": rule_id,
                    "name": f"{profile['name']}: {asset['display_name'] or asset['asset_name']}",
                    "description": f"App protection for {profile['name']} - {at} {asset['asset_name']}",
                    "conditions": conditions,
                    "actions": [f"run_windows_runbook:{runbook_id}"],
                    "action_params": action_params,
                    "severity": "critical",
                    "cooldown_seconds": 300,
                    "max_retries": 3,
                    "source": "protection_profile",
                    "hipaa_controls": ["164.312(a)(1)", "164.312(c)(1)"],
                    "priority": 10,
                }

                # Insert into l1_rules table (for sync)
                pattern = {
                    "incident_type": incident_type,
                }
                if data_field and data_field.startswith("data."):
                    pattern[data_field.replace("data.", "")] = asset["asset_name"]

                await conn.execute("""
                    INSERT INTO l1_rules (rule_id, incident_pattern, runbook_id, confidence, enabled, source)
                    VALUES ($1, $2::jsonb, $3, 0.99, true, 'protection_profile')
                    ON CONFLICT (rule_id) DO UPDATE SET
                        incident_pattern = EXCLUDED.incident_pattern,
                        runbook_id = EXCLUDED.runbook_id,
                        enabled = true,
                        source = 'protection_profile'
                """, rule_id, json.dumps(pattern), runbook_id)

                # Track in profile rules table
                rule_row_id = _uuid.uuid4()
                await conn.execute("""
                    INSERT INTO app_profile_rules (id, profile_id, asset_id, l1_rule_id, rule_json, enabled, created_at)
                    VALUES ($1, $2, $3, $4, $5::jsonb, true, $6)
                """, rule_row_id, pid, asset["id"], rule_id, json.dumps(rule_json), now)

                rules_created += 1

            # Update profile
            await conn.execute("""
                UPDATE app_protection_profiles
                SET status = 'active', baseline_data = $2::jsonb, updated_at = $3
                WHERE id = $1
            """, pid, json.dumps(baseline), now)

    logger.info(
        f"Locked baseline for profile {pid}: {len(assets)} assets, {rules_created} L1 rules generated"
    )
    return {
        "status": "active",
        "assets_protected": len(assets),
        "rules_created": rules_created,
    }


# =============================================================================
# Profile from Template
# =============================================================================

@router.post("/from-template", response_model=ProfileSummary, status_code=201)
async def create_from_template(
    site_id: str = Query(...),
    template_id: str = Query(...),
    name: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Create a new profile pre-populated from a template."""
    pool = await get_pool()
    tid = _uuid.UUID(template_id)

    async with pool.acquire() as conn:
        tmpl = await conn.fetchrow(
            "SELECT * FROM app_profile_templates WHERE id = $1", tid
        )
        if not tmpl:
            raise HTTPException(404, "Template not found")

        profile_name = name or tmpl["name"]
        profile_id = _uuid.uuid4()
        now = datetime.now(timezone.utc)

        try:
            await conn.execute("""
                INSERT INTO app_protection_profiles
                    (id, site_id, name, description, status, created_at, updated_at, template_id)
                VALUES ($1, $2, $3, $4, 'draft', $5, $5, $6)
            """, profile_id, site_id, profile_name, tmpl["description"], now, tid)
        except Exception as e:
            if "unique" in str(e).lower():
                raise HTTPException(409, f"Profile '{profile_name}' already exists for this site")
            raise

        row = await conn.fetchrow("""
            SELECT p.*,
                0::bigint as asset_count, 0::bigint as enabled_asset_count, 0::bigint as rule_count
            FROM app_protection_profiles p WHERE p.id = $1
        """, profile_id)
        return _profile_summary(row)


# =============================================================================
# Pause / Resume
# =============================================================================

@router.post("/{profile_id}/pause")
async def pause_profile(
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Pause protection — disables all L1 rules without deleting them."""
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            profile = await conn.fetchrow(
                "SELECT status FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
                pid, site_id,
            )
            if not profile:
                raise HTTPException(404, "Profile not found")
            if profile["status"] != "active":
                raise HTTPException(400, "Can only pause active profiles")

            # Disable L1 rules
            rule_ids = await conn.fetch(
                "SELECT l1_rule_id FROM app_profile_rules WHERE profile_id = $1", pid
            )
            for r in rule_ids:
                await conn.execute(
                    "UPDATE l1_rules SET enabled = false WHERE rule_id = $1",
                    r["l1_rule_id"],
                )

            await conn.execute(
                "UPDATE app_protection_profiles SET status = 'paused', updated_at = $2 WHERE id = $1",
                pid, now,
            )

    return {"status": "paused", "rules_disabled": len(rule_ids)}


@router.post("/{profile_id}/resume")
async def resume_profile(
    profile_id: str = Path(...),
    site_id: str = Query(..., description="Site ID co-constraint for tenant isolation"),
    user: Dict[str, Any] = Depends(require_auth),
):
    """Resume protection — re-enables all L1 rules."""
    pool = await get_pool()
    pid = _uuid.UUID(profile_id)
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            profile = await conn.fetchrow(
                "SELECT status FROM app_protection_profiles WHERE id = $1 AND site_id = $2",
                pid, site_id,
            )
            if not profile:
                raise HTTPException(404, "Profile not found")
            if profile["status"] != "paused":
                raise HTTPException(400, "Can only resume paused profiles")

            rule_ids = await conn.fetch(
                "SELECT l1_rule_id FROM app_profile_rules WHERE profile_id = $1 AND enabled = true",
                pid,
            )
            for r in rule_ids:
                await conn.execute(
                    "UPDATE l1_rules SET enabled = true WHERE rule_id = $1",
                    r["l1_rule_id"],
                )

            await conn.execute(
                "UPDATE app_protection_profiles SET status = 'active', updated_at = $2 WHERE id = $1",
                pid, now,
            )

    return {"status": "active", "rules_enabled": len(rule_ids)}


# =============================================================================
# Helpers
# =============================================================================

def _profile_summary(row) -> ProfileSummary:
    return ProfileSummary(
        id=str(row["id"]),
        site_id=row["site_id"],
        name=row["name"],
        description=row["description"],
        status=row["status"],
        created_by=row["created_by"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
        asset_count=row["asset_count"],
        enabled_asset_count=row["enabled_asset_count"],
        rule_count=row["rule_count"],
    )
