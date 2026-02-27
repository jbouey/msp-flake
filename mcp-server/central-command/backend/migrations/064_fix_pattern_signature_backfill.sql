-- Migration 064: Fix pattern_signature backfill from migration 052
--
-- Migration 052 backfilled pattern_signature as:
--   incident_type:incident_type:hostname (BUG: duplicated incident_type)
-- Correct format is:
--   incident_type:runbook_id:hostname
--
-- This migration re-backfills rows where the first two colon-delimited
-- segments are identical (the 052 bug fingerprint) AND a valid runbook_id exists.

BEGIN;

-- Fix rows corrupted by 052 backfill (first two segments match = bug fingerprint)
UPDATE execution_telemetry
SET pattern_signature = incident_type || ':' || runbook_id || ':' || hostname
WHERE pattern_signature IS NOT NULL
  AND runbook_id IS NOT NULL
  AND runbook_id != ''
  AND hostname IS NOT NULL
  AND split_part(pattern_signature, ':', 1) = split_part(pattern_signature, ':', 2)
  AND split_part(pattern_signature, ':', 1) = incident_type;

-- Also fix any remaining NULL pattern_signatures with the correct format
UPDATE execution_telemetry
SET pattern_signature = incident_type || ':' || runbook_id || ':' || hostname
WHERE pattern_signature IS NULL
  AND incident_type IS NOT NULL
  AND runbook_id IS NOT NULL
  AND runbook_id != ''
  AND hostname IS NOT NULL;

COMMIT;
