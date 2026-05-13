"""Canonical metric response sampler (Task #63 Phase 2b, Counsel Rule 1).

Records customer-facing endpoint emissions of canonical metrics (today:
compliance_score) into `canonical_metric_samples` (mig 314) for later
drift verification by the substrate invariant
`_check_canonical_compliance_score_drift` (Phase 2c).

Design + Gate A:
  audit/canonical-metric-drift-invariant-design-2026-05-13.md (v3 + Gate A v4)
  audit/coach-canonical-compliance-score-drift-v3-patched-gate-a-2026-05-13.md

Discipline:
  - SAMPLE_RATE = 0.1 stochastic; trades full-coverage for DB-pressure +
    insert-cost. Tunable down if scale grows.
  - Soft-fail wrapped — never blocks the customer-facing endpoint.
  - Caller supplies `classification` per emit-path
    ('customer-facing' | 'operator-internal' | 'partner-internal').
    Only 'customer-facing' rows fire substrate drift alerts (3-layer
    defense-in-depth: CHECK constraint + partial index + invariant
    WHERE clause).
  - `helper_input` MUST capture {site_ids, window_days, include_incidents}
    — `include_incidents` is the v3 P0-E4 trigger; some endpoints pass
    True, others default to False, and the substrate recompute MUST use
    the same kwargs the caller used or false-positives are guaranteed.
"""
from __future__ import annotations

import json
import logging
import random
from typing import Optional

logger = logging.getLogger(__name__)

# 10% sample rate — tunable. See design §6 multi-device-enterprise lens.
SAMPLE_RATE = 0.1

_VALID_CLASSIFICATIONS = frozenset({
    "customer-facing", "operator-internal", "partner-internal",
})

# Tracked metric classes — must match `CANONICAL_METRICS` keys in
# canonical_metrics.py. Phase 2b ships compliance_score; baa_on_file +
# runbook_id_canonical + l2_resolution_tier will land separately.
_TRACKED_METRIC_CLASSES = frozenset({"compliance_score"})


async def sample_metric_response(
    conn,
    metric_class: str,
    tenant_id: str,
    captured_value: Optional[float],
    endpoint_path: str,
    helper_input: dict,
    classification: str,
) -> None:
    """Best-effort sampling of a customer-facing canonical-metric emission.

    Soft-fail by construction — never re-raises. The customer-facing
    response MUST NOT be blocked by sampler unavailability.

    Args:
        conn: asyncpg connection in the calling endpoint's RLS context.
        metric_class: canonical metric class (e.g., 'compliance_score').
        tenant_id: client_org_id (UUID string).
        captured_value: the value the endpoint returned. May be None
            (e.g., no_data state) — caller passes None as-is.
        endpoint_path: the customer-facing URL path
            (e.g., '/api/client/dashboard').
        helper_input: dict capturing the kwargs the endpoint passed to
            compute_compliance_score (site_ids, window_days,
            include_incidents). Must match the substrate recompute
            kwargs exactly or drift detection false-positives.
        classification: one of 'customer-facing', 'operator-internal',
            'partner-internal'. Substrate fires only on 'customer-facing'.
    """
    if metric_class not in _TRACKED_METRIC_CLASSES:
        return
    if classification not in _VALID_CLASSIFICATIONS:
        logger.warning(
            "sample_metric_response: invalid classification %r — skipping",
            classification,
        )
        return
    if random.random() >= SAMPLE_RATE:
        return
    try:
        await conn.execute(
            """
            INSERT INTO canonical_metric_samples
                (metric_class, tenant_id, captured_value,
                 endpoint_path, helper_input, classification)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            """,
            metric_class, tenant_id, captured_value,
            endpoint_path, json.dumps(helper_input),
            classification,
        )
    except Exception:
        # Per design: soft-fail, never block endpoint.
        # Reads MAY eat exceptions (CLAUDE.md rule); this is a write but
        # the customer-facing semantics demand non-blocking. Log at
        # warning so prod monitoring sees the soft-fail rate.
        logger.warning(
            "sample_metric_response soft-fail for %s tenant=%s",
            endpoint_path, tenant_id, exc_info=True,
        )
