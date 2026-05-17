"""Perf harness — partner `/me/sites` p95 at 250-site fleet shape.

#121 Phase A profile harness per audit/coach-121-partner-dashboard-
gate-a-2026-05-17.md. Recommendation: profile BEFORE shipping any
virtualization or pagination fix.

Pass criterion:
  - p95 < 500ms over 50 sequential requests
  - p99 < 1000ms
  - error rate == 0

Fail → open #121-B with specific fix (server pagination OR
react-virtual + ≥1 sibling adopter per Coach P1 ratchet rule).
Pass → no #121-B needed; the 250-row payload is well within budget.

Requires PG_TEST_URL (sibling _pg.py pattern). Seeds 250 sites
under a fake partner_id, hits the endpoint 50× under a synthetic
session cookie, measures latency. Cleans up its own seed on
teardown.

NOT included in the SOURCE_LEVEL_TESTS pre-push sweep — this is a
perf-class test that runs in CI with the PG sidecar OR on operator
demand. Future task: wire into a nightly perf-regression job.
"""
from __future__ import annotations

import os
import pathlib
import statistics
import time
import uuid

import pytest


PG_TEST_URL = os.getenv("PG_TEST_URL")
PERF_RUN = os.getenv("PERF_RUN") == "1"

pytestmark = pytest.mark.skipif(
    not (PG_TEST_URL and PERF_RUN),
    reason=(
        "Perf harness — opt-in only. Set PG_TEST_URL + PERF_RUN=1 to "
        "run. CI nightly perf job is the production caller; local dev "
        "runs on-demand."
    ),
)


# Pass thresholds per Gate A (audit/coach-121-...md):
P95_BUDGET_MS = 500
P99_BUDGET_MS = 1000
N_REQUESTS = 50
N_SITES_SEED = 250


def _percentile(samples: list[float], p: float) -> float:
    """Linear-interpolation percentile. p in [0, 1]. Returns 0.0 on empty."""
    if not samples:
        return 0.0
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    rank = p * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] * (1 - frac) + s[hi] * frac


@pytest.fixture
def _250_site_partner():
    """Seed a fake partner + 250 sites + 250 site_appliances. Yields
    (partner_id, session_cookie). Cleans up on teardown."""
    import asyncio
    import asyncpg

    async def _setup():
        c = await asyncpg.connect(PG_TEST_URL)
        partner_id = str(uuid.uuid4())
        # Minimal partner row — assume schema permits NULLs on most fields.
        await c.execute(
            "INSERT INTO partners (id, name, status, contact_email) "
            "VALUES ($1::uuid, 'PERF-#121-seed', 'active', "
            "'perf-seed@example.invalid')",
            partner_id,
        )
        # 250 sites + 1 site_appliance each.
        for i in range(N_SITES_SEED):
            site_id = f"perf-121-site-{i:03d}"
            await c.execute(
                "INSERT INTO sites (site_id, clinic_name, partner_id, "
                "tier, industry, status, client_org_id, created_at, "
                "updated_at) VALUES ($1, $2, $3::uuid, 'small', "
                "'healthcare', 'online', "
                "'00000000-0000-4000-8000-00000000ff05'::uuid, "
                "NOW(), NOW())",
                site_id, f"Perf Clinic {i:03d}", partner_id,
            )
            await c.execute(
                "INSERT INTO site_appliances (appliance_id, site_id, "
                "hostname, status, last_checkin, created_at) "
                "VALUES ($1, $2, $3, 'online', NOW(), NOW())",
                f"perf-121-app-{i:03d}", site_id, f"perf-host-{i:03d}",
            )
        # Synthetic partner session for the test caller — out-of-scope
        # for the harness; integration test stub returns no cookie. The
        # session-mint here is intentionally LEFT TO THE OPERATOR (CI
        # nightly job uses an admin-issued partner session token).
        await c.close()
        return partner_id

    async def _teardown(partner_id: str):
        c = await asyncpg.connect(PG_TEST_URL)
        await c.execute(
            "DELETE FROM site_appliances WHERE site_id LIKE 'perf-121-site-%'"
        )
        await c.execute(
            "DELETE FROM sites WHERE site_id LIKE 'perf-121-site-%'"
        )
        await c.execute(
            "DELETE FROM partners WHERE id = $1::uuid", partner_id
        )
        await c.close()

    partner_id = asyncio.run(_setup())
    yield partner_id
    asyncio.run(_teardown(partner_id))


def test_me_sites_p95_under_500ms(_250_site_partner):
    """Hit /api/partners/me/sites 50× under a 250-site fleet shape.
    Pass: p95 < 500ms AND p99 < 1000ms AND zero errors."""
    import httpx

    partner_id = _250_site_partner
    # Operator-supplied session token via env. CI nightly perf job
    # mints one via the admin API + sets PARTNER_SESSION_COOKIE.
    cookie = os.getenv("PARTNER_SESSION_COOKIE")
    if not cookie:
        pytest.skip(
            "PARTNER_SESSION_COOKIE not set — perf harness requires an "
            "operator-issued partner session token. Mint one via admin "
            "API + export PARTNER_SESSION_COOKIE=<value> before re-run."
        )

    base = os.getenv("PERF_TEST_BASE_URL", "http://localhost:8000")
    url = f"{base}/api/partners/me/sites"
    latencies_ms: list[float] = []
    errors: int = 0

    with httpx.Client(timeout=10.0) as client:
        for _ in range(N_REQUESTS):
            t0 = time.monotonic()
            r = client.get(url, cookies={"osiris_partner_session": cookie})
            dt_ms = (time.monotonic() - t0) * 1000
            if r.status_code != 200:
                errors += 1
                continue
            data = r.json()
            assert len(data.get("sites", [])) >= N_SITES_SEED, (
                f"expected ≥{N_SITES_SEED} sites under partner "
                f"{partner_id}; got {len(data.get('sites', []))}"
            )
            latencies_ms.append(dt_ms)

    assert errors == 0, f"{errors} requests errored — investigate"
    assert latencies_ms, "no successful requests recorded"
    p50 = _percentile(latencies_ms, 0.50)
    p95 = _percentile(latencies_ms, 0.95)
    p99 = _percentile(latencies_ms, 0.99)
    print(
        f"\n#121 perf: n={len(latencies_ms)} "
        f"p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms "
        f"mean={statistics.mean(latencies_ms):.1f}ms"
    )
    assert p95 < P95_BUDGET_MS, (
        f"p95={p95:.1f}ms exceeds {P95_BUDGET_MS}ms budget. "
        f"Open #121-B with the specific fix (server pagination on "
        f"/me/sites OR react-virtual on PartnerDashboard Sites tab)."
    )
    assert p99 < P99_BUDGET_MS, (
        f"p99={p99:.1f}ms exceeds {P99_BUDGET_MS}ms budget."
    )
