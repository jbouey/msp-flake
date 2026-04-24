"""v40.8 guardrail — the appliance_id-missing bug that wedged breakglass-submit.

Background
----------
From v40.0 through v40.7 the provisioning endpoint's `config` dict was
{site_id, api_key, api_endpoint, ssh_authorized_keys} — no `appliance_id`.
msp-auto-provision wrote that dict verbatim to /var/lib/msp/config.yaml.
msp-breakglass-submit.service then read `.appliance_id`, got null, and
permanently printed "submit: site_id/api_key/appliance_id missing — retry
in 5m". Every reflashed appliance hit this, but earlier ISOs died before
reaching the submit stage so the bug stayed hidden until v40.7's canary.

This file locks down BOTH halves of the v40.8 fix:

1.  Backend: get_provision_by_mac()'s config dict MUST include
    appliance_id. Future configs are complete out of the box.
2.  ISO script: msp-breakglass-submit MUST fall back to deriving
    APPL_ID from SITE_ID + MAC when the config.yaml lacks it, so
    existing v40.0-v40.7 boxes still work after upgrade.

journal-upload already did (2) — we just applied the same pattern.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
PROVISIONING_PY = REPO_ROOT / "mcp-server/central-command/backend/provisioning.py"
SITES_PY = REPO_ROOT / "mcp-server/central-command/backend/sites.py"
DISK_IMAGE_NIX = REPO_ROOT / "iso/appliance-disk-image.nix"
PHONEHOME_GO = REPO_ROOT / "appliance/internal/daemon/phonehome.go"


def test_provisioning_config_dict_includes_appliance_id() -> None:
    """Backend: the config dict returned from /api/provision/{mac} must
    include appliance_id. This is the permanent fix."""
    src = PROVISIONING_PY.read_text()
    # Find the config dict assignment in get_provision_by_mac (it's the
    # one preceded by "Merge appliance-specific and site-level SSH keys").
    anchor = 'list(provision[\'site_keys\'] or [])'
    assert anchor in src, "SSH-keys merge anchor moved — test stale"
    tail = src[src.index(anchor):]
    # Next config = {...} block is the one we care about.
    assert 'config = {' in tail, "config dict not found after SSH merge"
    config_block = tail[tail.index('config = {'):]
    config_block = config_block[:config_block.index('}') + 1]
    assert '"appliance_id": appliance_id' in config_block, (
        "config dict in get_provision_by_mac() must include appliance_id "
        "or msp-breakglass-submit on fresh boxes will print "
        "'site_id/api_key/appliance_id missing' forever. "
        f"Found: {config_block!r}"
    )


def test_breakglass_submit_has_appliance_id_fallback() -> None:
    """ISO: msp-breakglass-submit must derive appliance_id from SITE_ID +
    MAC when the config.yaml omits it. This keeps v40.0-v40.7 legacy
    appliances working after they're reflashed to v40.8+."""
    src = DISK_IMAGE_NIX.read_text()
    # Locate the submit service.
    assert 'systemd.services.msp-breakglass-submit' in src
    submit_idx = src.index('systemd.services.msp-breakglass-submit')
    # Narrow to the next top-level block (crude but sufficient — the
    # script is ~100 lines and the fallback is right at the top).
    submit_block = src[submit_idx:submit_idx + 5000]

    # The fallback must reference SITE_ID + MAC and assign APPL_ID.
    assert 'APPL_ID="$SITE_ID-$MAC_UPPER"' in submit_block, (
        "msp-breakglass-submit must derive APPL_ID from SITE_ID and MAC "
        "when config.yaml omits appliance_id (v40.0-v40.7 compat). "
        "Pattern: APPL_ID=\"$SITE_ID-$MAC_UPPER\""
    )
    # Must come AFTER the yq read, otherwise it'd overwrite a present value.
    yq_read_idx = submit_block.index(".appliance_id // empty")
    fallback_idx = submit_block.index('APPL_ID="$SITE_ID-$MAC_UPPER"')
    assert yq_read_idx < fallback_idx, (
        "MAC-derived fallback must run AFTER the yq read of appliance_id, "
        "otherwise it will clobber a correctly-provisioned value from v40.8+"
    )


def test_breakglass_submit_fallback_only_activates_when_missing() -> None:
    """The fallback must be gated on `-z "$APPL_ID"` — not unconditional,
    or it'd overwrite the correct value from v40.8+ backends."""
    src = DISK_IMAGE_NIX.read_text()
    submit_idx = src.index('systemd.services.msp-breakglass-submit')
    submit_block = src[submit_idx:submit_idx + 5000]
    assert 'if [ -z "$APPL_ID" ]' in submit_block, (
        "fallback derivation must be guarded by -z APPL_ID check"
    )


def test_appliance_id_format_matches_backend_convention() -> None:
    """Both halves must agree on the appliance_id format:
    {site_id}-{MAC-uppercase-colon-separated}. The backend
    generates it this way; the fallback must match."""
    # Backend convention — already in provisioning.py:
    src_be = PROVISIONING_PY.read_text()
    assert 'appliance_id = f"{site_id_val}-{mac}"' in src_be, (
        "backend appliance_id format changed — fallback in ISO must match"
    )
    # Fallback uses tr '[:lower:]' '[:upper:]' on MAC, then $SITE_ID-$MAC_UPPER:
    src_iso = DISK_IMAGE_NIX.read_text()
    submit_idx = src_iso.index('systemd.services.msp-breakglass-submit')
    submit_block = src_iso[submit_idx:submit_idx + 5000]
    assert "tr '[:lower:]' '[:upper:]'" in submit_block, (
        "MAC normalization must uppercase to match backend convention"
    )


def test_claim_provision_code_config_is_empty_or_minimal() -> None:
    """The config dict returned by claim_provision_code() has no live
    appliance-side consumers. Pre-cleanup it shipped a dozen dead flags
    (checkin_interval_seconds, discovery_enabled, network_range,
    logging_level, features{...}). Trimmed to empty in the 2026-04-24
    cleanup. Fail the test if any of the dead-field names reappear."""
    src = PROVISIONING_PY.read_text()
    # Locate claim_provision_code's config dict specifically.
    anchor = 'async def claim_provision_code'
    assert anchor in src
    tail = src[src.index(anchor):]
    # Stop at the function-end return statement so we only look inside this
    # function's body.
    body = tail[: tail.index('return ProvisionClaimResponse(')]

    for dead in ('checkin_interval_seconds', 'discovery_enabled',
                 'network_range', 'logging_level', 'compliance_checks',
                 'auto_healing', 'evidence_collection', 'windows_scanning'):
        assert f'"{dead}"' not in body, (
            f"dead field {dead!r} reappeared in claim_provision_code config — "
            "nothing on the appliance reads it; remove or wire a consumer first"
        )


def test_get_appliance_config_trimmed_to_live_fields() -> None:
    """get_appliance_config()'s config dict must contain only fields with
    a live appliance-side consumer (api_endpoint, site_id, appliance_id).
    The 2026-04-24 cleanup removed tier/branding/compliance_checks_enabled/
    auto_healing_enabled/evidence_collection_enabled/windows_scanning_enabled/
    discovery_interval_hours/checkin_interval_seconds."""
    src = PROVISIONING_PY.read_text()
    anchor = 'async def get_appliance_config'
    assert anchor in src
    tail = src[src.index(anchor):]
    body = tail[: tail.index('return {')]

    for dead in ('tier', 'checkin_interval_seconds',
                 'compliance_checks_enabled', 'auto_healing_enabled',
                 'evidence_collection_enabled', 'windows_scanning_enabled',
                 'discovery_interval_hours', 'branding'):
        assert f'"{dead}"' not in body, (
            f"dead field {dead!r} reappeared in get_appliance_config — "
            "remove or wire a consumer before re-adding"
        )


def test_checkin_response_struct_unmarshals_server_directives() -> None:
    """Go daemon's CheckinResponse struct MUST include fields for every
    directive the server sends in /api/appliances/checkin response.
    2026-04-24 lockstep audit found 3 missing: l2_confidence_threshold
    (safety gate for auto-healing), maintenance_until (scheduled window),
    billing_hold (delinquent account pause). Silently dropped because
    Go's json.Unmarshal discards unknown fields. Behavior wiring lands
    in a follow-up PR; this test locks the struct so the fields don't
    regress back to being dropped."""
    go_src = PHONEHOME_GO.read_text()
    anchor = 'type CheckinResponse struct'
    assert anchor in go_src, "CheckinResponse struct moved — test stale"
    tail = go_src[go_src.index(anchor):]
    # Close the struct body at the first unmatched closing brace.
    body = tail[: tail.index('\n}\n') + 2]

    for json_tag in ('"l2_confidence_threshold',
                     '"maintenance_until',
                     '"billing_hold'):
        assert json_tag in body, (
            f"CheckinResponse struct must declare a field with json tag "
            f"{json_tag!r} so the server directive stops being silently "
            f"dropped on unmarshal"
        )


def test_appliance_checkin_model_declares_wg_pubkey() -> None:
    """ApplianceCheckin Pydantic model must declare wg_pubkey so Pydantic
    does not silently drop it. Primary persistence is still
    provisioning.py claim_provision_code (which UPDATEs sites.wg_pubkey
    at claim time); this is defense-in-depth for daemon rekey scenarios."""
    src = SITES_PY.read_text()
    anchor = 'class ApplianceCheckin(BaseModel):'
    assert anchor in src, "ApplianceCheckin class moved — test stale"
    tail = src[src.index(anchor):]
    # Narrow to the class body (next class or function).
    body_end = min(
        (tail.index('\nclass ', 1) if '\nclass ' in tail[1:] else len(tail)),
        (tail.index('\ndef ', 1) if '\ndef ' in tail[1:] else len(tail)),
    )
    body = tail[:body_end]
    assert 'wg_pubkey:' in body, (
        "ApplianceCheckin must declare wg_pubkey so Pydantic doesn't silently "
        "drop the daemon-side field on every checkin"
    )
