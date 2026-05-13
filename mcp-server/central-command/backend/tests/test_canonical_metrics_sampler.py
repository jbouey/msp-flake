"""Unit tests for canonical_metrics_sampler (Task #63 Phase 2b)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from canonical_metrics_sampler import (
    SAMPLE_RATE,
    _TRACKED_METRIC_CLASSES,
    _VALID_CLASSIFICATIONS,
    sample_metric_response,
)


def test_sample_rate_is_ten_percent():
    """Design §2 — 10% stochastic capture trades full-coverage for
    DB-pressure + insert-cost. Test pins the rate so a future PR can't
    silently bump it to 100% (would blow up canonical_metric_samples
    write throughput in prod).
    """
    assert SAMPLE_RATE == 0.1


def test_tracked_metric_classes_match_phase_2b():
    """Phase 2b ships compliance_score only. Future phases extend to
    baa_on_file + runbook_id_canonical + l2_resolution_tier under their
    own Gate A/B.
    """
    assert _TRACKED_METRIC_CLASSES == frozenset({"compliance_score"})


def test_valid_classifications_match_check_constraint():
    """Must match `canonical_metric_samples_classification_valid` CHECK
    constraint in mig 314. 3-layer defense-in-depth (Carol P0): CHECK +
    partial index + substrate WHERE clause all key off these literals.
    """
    assert _VALID_CLASSIFICATIONS == frozenset({
        "customer-facing", "operator-internal", "partner-internal",
    })


@pytest.mark.asyncio
async def test_unknown_metric_class_returns_silently():
    """Future-proofing: an endpoint that calls sample with a metric we
    don't track yet must NOT crash (return without error).
    """
    conn = AsyncMock()
    await sample_metric_response(
        conn,
        metric_class="future_metric_xyz",
        tenant_id="org-uuid",
        captured_value=85.0,
        endpoint_path="/api/x",
        helper_input={},
        classification="customer-facing",
    )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_classification_returns_silently():
    """Defense: an invalid classification (typo, future label) must
    not crash + must NOT hit the DB (or the CHECK constraint would
    reject it anyway).
    """
    conn = AsyncMock()
    await sample_metric_response(
        conn,
        metric_class="compliance_score",
        tenant_id="org-uuid",
        captured_value=85.0,
        endpoint_path="/api/x",
        helper_input={},
        classification="custumer-facing",  # typo
    )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_below_sample_rate_skips_insert():
    """Stochastic sampling — if random > SAMPLE_RATE, no insert happens."""
    conn = AsyncMock()
    with patch("canonical_metrics_sampler.random.random", return_value=0.95):
        await sample_metric_response(
            conn,
            metric_class="compliance_score",
            tenant_id="org-uuid",
            captured_value=85.0,
            endpoint_path="/api/client/dashboard",
            helper_input={
                "site_ids": ["site-1"],
                "window_days": 30,
                "include_incidents": False,
            },
            classification="customer-facing",
        )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_in_sample_rate_writes_row_with_classification():
    """When the stochastic gate passes, the INSERT MUST include the
    classification column (v3 P0-E10 — Gate A v3 caught the original
    design omitting this).
    """
    conn = AsyncMock()
    with patch("canonical_metrics_sampler.random.random", return_value=0.05):
        await sample_metric_response(
            conn,
            metric_class="compliance_score",
            tenant_id="org-uuid",
            captured_value=85.0,
            endpoint_path="/api/client/dashboard",
            helper_input={
                "site_ids": ["site-1"],
                "window_days": 30,
                "include_incidents": False,
            },
            classification="customer-facing",
        )
    conn.execute.assert_called_once()
    sql, *args = conn.execute.call_args[0]
    assert "classification" in sql
    # 6 positional args (metric_class, tenant_id, captured_value,
    # endpoint_path, helper_input_json, classification)
    assert len(args) == 6
    assert args[-1] == "customer-facing"
    # helper_input MUST capture include_incidents (v3 P0-E4)
    helper_input_json = json.loads(args[4])
    assert "include_incidents" in helper_input_json


@pytest.mark.asyncio
async def test_insert_failure_does_not_raise():
    """Soft-fail contract: never block the customer-facing response.
    A DB error (PgBouncer churn, partition not yet created, etc.) MUST
    be caught + logged, not propagated.
    """
    conn = AsyncMock()
    conn.execute.side_effect = Exception("simulated DB error")
    with patch("canonical_metrics_sampler.random.random", return_value=0.05):
        await sample_metric_response(  # should NOT raise
            conn,
            metric_class="compliance_score",
            tenant_id="org-uuid",
            captured_value=85.0,
            endpoint_path="/api/client/dashboard",
            helper_input={},
            classification="customer-facing",
        )


@pytest.mark.asyncio
async def test_none_captured_value_still_inserts():
    """no_data state — captured_value may legitimately be None
    (e.g., new org with zero compliance_bundles). The sample row
    captures the None so substrate sees that the endpoint returned
    null in this case.
    """
    conn = AsyncMock()
    with patch("canonical_metrics_sampler.random.random", return_value=0.05):
        await sample_metric_response(
            conn,
            metric_class="compliance_score",
            tenant_id="org-uuid",
            captured_value=None,
            endpoint_path="/api/client/dashboard",
            helper_input={
                "site_ids": [],
                "window_days": 30,
                "include_incidents": False,
            },
            classification="customer-facing",
        )
    conn.execute.assert_called_once()
    args = conn.execute.call_args[0]
    # 3rd positional after SQL string is captured_value
    assert args[3] is None
