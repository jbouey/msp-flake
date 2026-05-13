"""Canonical-devices CTE helper (Task #74 Phase 2 P0 fix).

Phase 1 migrated 2 readers (compliance_packet + device_sync) by inlining
the same CTE shape. Phase 2 migrates 17 more — without a shared helper,
the CTE shape would drift across 17 hand-written copies, exactly the
Class-B regression Gate A flagged.

This module exports:
  - FRESHEST_DD_FROM_CANONICAL_CTE — the canonical SQL CTE string. Use
    as a constant; callers paste it into their own query then SELECT
    from `dd_freshest`.
  - COUNT_CANONICAL_DEVICES_SQL — a 1-line SELECT COUNT for the
    COUNT-only callsites (partners.py:2595, routes.py:5322, portal.py:1251)
    that don't need per-device columns.

Both patterns must be followed by `# canonical-migration: device_count_per_site`
inline marker on the consuming line so the CI gate counts the migration
toward Phase 2 drive-down.
"""
from __future__ import annotations


# CTE that produces one row per canonical_devices row, with the freshest
# discovered_devices observation's fields JOIN'd back. Use:
#
#     query = f\"\"\"
#         {FRESHEST_DD_FROM_CANONICAL_CTE}
#         SELECT ... FROM dd_freshest WHERE ...
#     \"\"\"
#
# The CTE expects `$1` to be the site_id parameter. Callers with
# different parameter positions must pass a fully-formatted version.
#
# Columns available in `dd_freshest`:
#   - canonical_id, site_id, ip_address, mac_address (from canonical_devices)
#   - cd_first_seen_at, cd_last_seen_at, cd_device_type (from canonical_devices)
#   - All columns of discovered_devices via `dd.*`
FRESHEST_DD_FROM_CANONICAL_CTE = """
    WITH dd_freshest AS (
        SELECT DISTINCT ON (cd.canonical_id)
               cd.canonical_id,
               cd.site_id,
               cd.ip_address,
               cd.mac_address,
               cd.first_seen_at AS cd_first_seen_at,
               cd.last_seen_at AS cd_last_seen_at,
               cd.device_type AS cd_device_type,
               dd.*
          FROM canonical_devices cd
          JOIN discovered_devices dd
            ON dd.site_id = cd.site_id
           AND dd.ip_address = cd.ip_address
           AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
         WHERE cd.site_id = $1
         ORDER BY cd.canonical_id, dd.last_seen_at DESC
    )
"""


# Variant for callsites that filter by `site_id = ANY($N)` (multi-site).
FRESHEST_DD_FROM_CANONICAL_CTE_MULTI_SITE = """
    WITH dd_freshest AS (
        SELECT DISTINCT ON (cd.canonical_id)
               cd.canonical_id,
               cd.site_id,
               cd.ip_address,
               cd.mac_address,
               cd.first_seen_at AS cd_first_seen_at,
               cd.last_seen_at AS cd_last_seen_at,
               cd.device_type AS cd_device_type,
               dd.*
          FROM canonical_devices cd
          JOIN discovered_devices dd
            ON dd.site_id = cd.site_id
           AND dd.ip_address = cd.ip_address
           AND COALESCE(dd.mac_address, '') = cd.mac_dedup_key
         WHERE cd.site_id = ANY($1)
         ORDER BY cd.canonical_id, dd.last_seen_at DESC
    )
"""


# Direct COUNT for COUNT-only callsites. Saves the CTE overhead.
COUNT_CANONICAL_DEVICES_SQL = (
    "SELECT COUNT(*) FROM canonical_devices WHERE site_id = $1"
)
COUNT_CANONICAL_DEVICES_MULTI_SITE_SQL = (
    "SELECT COUNT(*) FROM canonical_devices WHERE site_id = ANY($1)"
)
