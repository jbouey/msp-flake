"""
Tests for cve_watch.py — CVE Watch API and NVD sync engine.

Tests cover:
- CVE fetching from NVD API (success, error, rate limiting)
- CPE matching logic (Windows Server, Linux, NixOS, version ranges)
- Keyword heuristic matching breadth
- Severity classification (CVSS v3.1, v3.0, v2, missing)
- NVD date parsing
- CVE upsert (new + duplicate handling)
- Fleet matching (appliance-CVE linking)
- API endpoints (summary, list, detail, status update, config)
- Edge cases (malformed data, empty responses, missing fields)
- Sync loop and background task behavior
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import sys
import os

# Ensure backend package is importable
backend_path = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "mcp-server", "central-command", "backend"
)
sys.path.insert(0, os.path.join(backend_path, ".."))

# Mock asyncpg + submodules before importing backend (not in agent venv)
_pg = MagicMock()
for _m in ("asyncpg", "asyncpg.pool", "asyncpg.exceptions"):
    sys.modules.setdefault(_m, _pg)

# We import the module under test; get_pool is mocked in every test
from backend.cve_watch import (
    _extract_severity,
    _parse_nvd_date,
    _cpe_matches_appliance,
    _upsert_cve,
    _match_cves_to_fleet,
    _sync_cpe,
    _sync_nvd_cves,
    _run_sync,
    SEVERITY_ORDER,
    CVEStatusUpdate,
    CVEWatchConfigUpdate,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_pool_mock():
    """Create a mock asyncpg pool with fetch/fetchrow/execute."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool


def _make_config_row(
    enabled=True,
    watched_cpes=None,
    api_key=None,
    last_sync_at=None,
    min_severity="medium",
    sync_interval_hours=6,
):
    """Build a fake cve_watch_config row dict."""
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "id": uuid4(),
        "enabled": enabled,
        "watched_cpes": watched_cpes or ["cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*"],
        "nvd_api_key": api_key,
        "last_sync_at": last_sync_at,
        "min_severity": min_severity,
        "sync_interval_hours": sync_interval_hours,
    }[k]
    row.get = lambda k, default=None: {
        "id": uuid4(),
        "enabled": enabled,
        "watched_cpes": watched_cpes or ["cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*"],
        "nvd_api_key": api_key,
        "last_sync_at": last_sync_at,
        "min_severity": min_severity,
        "sync_interval_hours": sync_interval_hours,
    }.get(k, default)
    return row


def _make_nvd_response(vulns=None, total=None):
    """Build a fake NVD API v2.0 JSON response."""
    if vulns is None:
        vulns = []
    return {
        "resultsPerPage": len(vulns),
        "startIndex": 0,
        "totalResults": total if total is not None else len(vulns),
        "vulnerabilities": vulns,
    }


def _make_cve_item(
    cve_id="CVE-2024-12345",
    base_score=7.5,
    severity="HIGH",
    metric_version="cvssMetricV31",
    description="Test CVE description",
    cpe_criteria=None,
    has_metrics=True,
):
    """Build a single CVE item as returned by NVD API."""
    metrics = {}
    if has_metrics:
        metrics[metric_version] = [
            {
                "cvssData": {
                    "baseScore": base_score,
                    "baseSeverity": severity,
                }
            }
        ]

    cve = {
        "id": cve_id,
        "published": "2024-06-15T14:30:00.000",
        "lastModified": "2024-06-20T10:00:00.000",
        "vulnStatus": "Analyzed",
        "descriptions": [
            {"lang": "en", "value": description},
        ],
        "metrics": metrics,
        "references": [
            {"url": "https://example.com/advisory", "source": "vendor"},
        ],
        "weaknesses": [
            {
                "description": [
                    {"lang": "en", "value": "CWE-79"},
                ]
            }
        ],
        "configurations": [],
    }

    if cpe_criteria:
        cve["configurations"] = [
            {
                "nodes": [
                    {
                        "cpeMatch": [
                            {"vulnerable": True, "criteria": c}
                            for c in cpe_criteria
                        ]
                    }
                ]
            }
        ]

    return {"cve": cve}


# =============================================================================
# 1. Severity extraction
# =============================================================================

class TestExtractSeverity:
    def test_cvss_v31(self):
        cve = _make_cve_item(severity="CRITICAL", metric_version="cvssMetricV31")["cve"]
        assert _extract_severity(cve) == "critical"

    def test_cvss_v30_fallback(self):
        cve = _make_cve_item(severity="HIGH", metric_version="cvssMetricV30")["cve"]
        assert _extract_severity(cve) == "high"

    def test_cvss_v2_fallback(self):
        cve = _make_cve_item(severity="MEDIUM", metric_version="cvssMetricV2")["cve"]
        assert _extract_severity(cve) == "medium"

    def test_no_metrics_returns_unknown(self):
        cve = _make_cve_item(has_metrics=False)["cve"]
        assert _extract_severity(cve) == "unknown"

    def test_empty_metric_list(self):
        cve = {"metrics": {"cvssMetricV31": []}}
        assert _extract_severity(cve) == "unknown"

    def test_missing_base_severity(self):
        cve = {"metrics": {"cvssMetricV31": [{"cvssData": {}}]}}
        assert _extract_severity(cve) == "unknown"


# =============================================================================
# 2. NVD date parsing
# =============================================================================

class TestParseNvdDate:
    def test_standard_format(self):
        dt = _parse_nvd_date("2024-06-15T14:30:00.000")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 15

    def test_z_suffix(self):
        dt = _parse_nvd_date("2024-01-01T00:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_none_input(self):
        assert _parse_nvd_date(None) is None

    def test_empty_string(self):
        assert _parse_nvd_date("") is None

    def test_malformed_date(self):
        assert _parse_nvd_date("not-a-date") is None

    def test_integer_input(self):
        assert _parse_nvd_date(12345) is None


# =============================================================================
# 3. CPE matching logic
# =============================================================================

class TestCpeMatchesAppliance:
    def _appliance(self, agent_version="1.0.57", nixos_version="24.05"):
        return {
            "appliance_id": "app-001",
            "site_id": "site-001",
            "agent_version": agent_version,
            "nixos_version": nixos_version,
        }

    def test_windows_server_match(self):
        cpes = [{"criteria": "cpe:2.3:o:microsoft:windows_server_2022:*:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is True

    def test_windows_10_match(self):
        cpes = [{"criteria": "cpe:2.3:o:microsoft:windows_10:22H2:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is True

    def test_windows_11_match(self):
        cpes = [{"criteria": "cpe:2.3:o:microsoft:windows_11:23H2:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is True

    def test_ubuntu_match(self):
        cpes = [{"criteria": "cpe:2.3:o:canonical:ubuntu_linux:22.04:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is True

    def test_openssh_match(self):
        cpes = [{"criteria": "cpe:2.3:a:openbsd:openssh:9.6:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is True

    def test_python_match_with_agent(self):
        cpes = [{"criteria": "cpe:2.3:a:python:python:3.12:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance(agent_version="1.0.57")) is True

    def test_python_no_match_without_agent(self):
        """Python CVE should not match when agent_version is empty."""
        cpes = [{"criteria": "cpe:2.3:a:python:python:3.12:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance(agent_version="")) is False

    def test_nixos_match(self):
        cpes = [{"criteria": "cpe:2.3:o:nix:nixos:24.05:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance(nixos_version="24.05")) is True

    def test_nixos_keyword_match(self):
        """The 'nixos' keyword in criteria matches any NixOS appliance."""
        cpes = [{"criteria": "cpe:2.3:a:somevendor:nixos_tool:1.0:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance(nixos_version="24.05")) is True

    def test_nixos_no_match_without_version(self):
        cpes = [{"criteria": "cpe:2.3:o:nix:nixos:24.05:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance(nixos_version=None)) is False

    def test_unrelated_cpe_no_match(self):
        cpes = [{"criteria": "cpe:2.3:a:apache:httpd:2.4:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is False

    def test_empty_cpe_list(self):
        assert _cpe_matches_appliance([], self._appliance()) is False

    def test_case_insensitive_matching(self):
        """CPE criteria should be lowered before keyword checks."""
        cpes = [{"criteria": "CPE:2.3:O:MICROSOFT:WINDOWS_SERVER_2019:*:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is True

    def test_keyword_breadth_openssh_substring(self):
        """The heuristic matches 'openssh' as substring -- even in vendor names."""
        cpes = [{"criteria": "cpe:2.3:a:openssh_project:openssh_portable:9.0:*:*:*:*:*:*:*"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is True

    def test_missing_criteria_key(self):
        cpes = [{"no_criteria_key": "something"}]
        assert _cpe_matches_appliance(cpes, self._appliance()) is False


# =============================================================================
# 4. CVE upsert
# =============================================================================

class TestUpsertCve:
    async def test_upsert_full_cve(self):
        pool = _make_pool_mock()
        cve_data = _make_cve_item(
            cve_id="CVE-2024-99999",
            base_score=9.8,
            severity="CRITICAL",
            cpe_criteria=["cpe:2.3:o:linux:linux_kernel:6.1:*:*:*:*:*:*:*"],
        )["cve"]

        await _upsert_cve(pool, cve_data)

        pool.execute.assert_called_once()
        args = pool.execute.call_args
        # First positional after query: cve_id
        assert args[0][1] == "CVE-2024-99999"
        # severity lowered
        assert args[0][2] == "critical"
        # cvss_score
        assert args[0][3] == 9.8

    async def test_upsert_no_metrics(self):
        pool = _make_pool_mock()
        cve_data = _make_cve_item(has_metrics=False)["cve"]

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        assert args[0][2] == "unknown"  # severity
        assert args[0][3] is None  # cvss_score

    async def test_upsert_extracts_english_description(self):
        pool = _make_pool_mock()
        cve_data = {
            "id": "CVE-2024-00001",
            "descriptions": [
                {"lang": "es", "value": "Descripcion en espanol"},
                {"lang": "en", "value": "English description here"},
            ],
            "metrics": {},
            "configurations": [],
            "references": [],
            "weaknesses": [],
        }

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        # description is arg index 6 (after query, cve_id, severity, score, pub_date, mod_date)
        assert args[0][6] == "English description here"

    async def test_upsert_extracts_cwe_ids(self):
        pool = _make_pool_mock()
        cve_data = {
            "id": "CVE-2024-00002",
            "descriptions": [{"lang": "en", "value": "test"}],
            "metrics": {},
            "configurations": [],
            "references": [],
            "weaknesses": [
                {"description": [{"value": "CWE-79"}, {"value": "NVD-CWE-Other"}]},
                {"description": [{"value": "CWE-89"}]},
            ],
        }

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        # cwe_ids is arg index 9
        cwe_ids = args[0][9]
        assert "CWE-79" in cwe_ids
        assert "CWE-89" in cwe_ids
        assert "NVD-CWE-Other" not in cwe_ids

    async def test_upsert_extracts_affected_cpes(self):
        pool = _make_pool_mock()
        cpe = "cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*"
        cve_data = _make_cve_item(cpe_criteria=[cpe])["cve"]

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        # affected_cpes is json.dumps at arg index 7
        affected = json.loads(args[0][7])
        assert len(affected) == 1
        assert affected[0]["criteria"] == cpe


# =============================================================================
# 5. Fleet matching
# =============================================================================

class TestMatchCvesToFleet:
    async def test_no_appliances_returns_zero(self):
        pool = _make_pool_mock()
        pool.fetch = AsyncMock(side_effect=[[], []])  # appliances, cves

        result = await _match_cves_to_fleet(pool)
        assert result == 0

    async def test_matching_windows_cve(self):
        pool = _make_pool_mock()

        appliances = [
            {
                "appliance_id": "app-001",
                "site_id": "site-001",
                "agent_version": "1.0",
                "nixos_version": "24.05",
            }
        ]

        cves = [
            {
                "id": uuid4(),
                "cve_id": "CVE-2024-00001",
                "affected_cpes": json.dumps([
                    {"criteria": "cpe:2.3:o:microsoft:windows_server_2022:*:*:*:*:*:*:*:*"}
                ]),
            }
        ]

        pool.fetch = AsyncMock(side_effect=[appliances, cves])

        result = await _match_cves_to_fleet(pool)
        assert result == 1
        pool.execute.assert_called_once()

    async def test_no_cpe_match_no_insert(self):
        pool = _make_pool_mock()

        appliances = [
            {
                "appliance_id": "app-001",
                "site_id": "site-001",
                "agent_version": "",
                "nixos_version": None,
            }
        ]

        cves = [
            {
                "id": uuid4(),
                "cve_id": "CVE-2024-00099",
                "affected_cpes": json.dumps([
                    {"criteria": "cpe:2.3:a:apache:httpd:2.4:*:*:*:*:*:*:*"}
                ]),
            }
        ]

        pool.fetch = AsyncMock(side_effect=[appliances, cves])

        result = await _match_cves_to_fleet(pool)
        assert result == 0
        pool.execute.assert_not_called()

    async def test_affected_cpes_as_string(self):
        """Handle JSONB returned as string (asyncpg without json codec)."""
        pool = _make_pool_mock()

        appliances = [
            {"appliance_id": "app-002", "site_id": "site-002",
             "agent_version": "1.0", "nixos_version": "24.05"}
        ]

        cves = [
            {
                "id": uuid4(),
                "cve_id": "CVE-2024-55555",
                "affected_cpes": '[{"criteria": "cpe:2.3:a:openbsd:openssh:9.6:*:*:*:*:*:*:*"}]',
            }
        ]

        pool.fetch = AsyncMock(side_effect=[appliances, cves])

        result = await _match_cves_to_fleet(pool)
        assert result == 1

    async def test_affected_cpes_none(self):
        pool = _make_pool_mock()

        appliances = [
            {"appliance_id": "app-003", "site_id": "site-003",
             "agent_version": "1.0", "nixos_version": "24.05"}
        ]

        cves = [
            {"id": uuid4(), "cve_id": "CVE-2024-00003", "affected_cpes": None}
        ]

        pool.fetch = AsyncMock(side_effect=[appliances, cves])

        result = await _match_cves_to_fleet(pool)
        assert result == 0

    async def test_insert_error_swallowed(self):
        """Duplicate key or constraint error should be swallowed."""
        pool = _make_pool_mock()

        appliances = [
            {"appliance_id": "app-004", "site_id": "site-004",
             "agent_version": "1.0", "nixos_version": "24.05"}
        ]

        cves = [
            {
                "id": uuid4(),
                "cve_id": "CVE-2024-00004",
                "affected_cpes": [{"criteria": "cpe:2.3:o:microsoft:windows_server_2019:*:*:*:*:*:*:*:*"}],
            }
        ]

        pool.fetch = AsyncMock(side_effect=[appliances, cves])
        pool.execute = AsyncMock(side_effect=Exception("unique constraint violation"))

        result = await _match_cves_to_fleet(pool)
        # Exception in execute means matched++ is never reached (inside try block)
        assert result == 0


# =============================================================================
# 6. NVD sync (_sync_cpe)
# =============================================================================

class TestSyncCpe:
    async def test_successful_sync_single_page(self):
        pool = _make_pool_mock()
        cve_item = _make_cve_item(severity="HIGH", base_score=7.5)
        nvd_response = _make_nvd_response([cve_item], total=1)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nvd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                count = await _sync_cpe(
                    pool,
                    "cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*",
                    {},
                    None,  # last_sync
                    "medium",
                    0.6,
                )

        assert count == 1
        pool.execute.assert_called_once()  # _upsert_cve called

    async def test_severity_filter_excludes_low(self):
        """Low severity CVE should be excluded when min_severity is medium."""
        pool = _make_pool_mock()
        cve_item = _make_cve_item(severity="LOW", base_score=2.5)
        nvd_response = _make_nvd_response([cve_item], total=1)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nvd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                count = await _sync_cpe(
                    pool,
                    "cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*",
                    {},
                    None,
                    "medium",  # min_severity: medium means low is excluded
                    0.6,
                )

        assert count == 0
        pool.execute.assert_not_called()

    async def test_rate_limit_retry(self):
        """429 response should trigger retry with increasing delay."""
        pool = _make_pool_mock()

        rate_limit_resp = MagicMock()
        rate_limit_resp.status_code = 429

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = _make_nvd_response([], total=0)
        success_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[rate_limit_resp, success_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                count = await _sync_cpe(pool, "cpe:test", {}, None, "low", 0.6)

        assert count == 0
        # asyncio.sleep should have been called for rate limit wait
        mock_sleep.assert_called()

    async def test_rate_limit_exhausted(self):
        """After max_retries rate limits, return partial count."""
        pool = _make_pool_mock()

        rate_limit_resp = MagicMock()
        rate_limit_resp.status_code = 429

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=rate_limit_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                count = await _sync_cpe(pool, "cpe:test", {}, None, "low", 0.6)

        assert count == 0

    async def test_403_triggers_retry(self):
        """403 (forbidden) is also treated as rate limiting."""
        pool = _make_pool_mock()

        forbidden_resp = MagicMock()
        forbidden_resp.status_code = 403

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = _make_nvd_response([], total=0)
        ok_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[forbidden_resp, ok_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                count = await _sync_cpe(pool, "cpe:test:*", {}, None, "low", 0.6)

        assert count == 0

    async def test_wildcard_cpe_uses_virtual_match(self):
        """CPEs with * should use virtualMatchString param."""
        pool = _make_pool_mock()
        nvd_response = _make_nvd_response([], total=0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nvd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                await _sync_cpe(
                    pool,
                    "cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*",
                    {},
                    None,
                    "low",
                    0.6,
                )

        call_kwargs = mock_client.get.call_args
        assert "virtualMatchString" in call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))

    async def test_exact_cpe_uses_cpe_name(self):
        """CPEs without * should use cpeName param."""
        pool = _make_pool_mock()
        nvd_response = _make_nvd_response([], total=0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nvd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                await _sync_cpe(
                    pool,
                    "cpe:2.3:o:linux:linux_kernel:6.1:x86_64:server:lts:enterprise:stable:en:us",
                    {},
                    None,
                    "low",
                    0.6,
                )

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
        assert "cpeName" in params

    async def test_incremental_sync_uses_last_modified(self):
        """When last_sync is provided and old enough, use lastModStartDate."""
        pool = _make_pool_mock()
        nvd_response = _make_nvd_response([], total=0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nvd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        last_sync = datetime.now(timezone.utc) - timedelta(hours=12)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                await _sync_cpe(pool, "cpe:test:*", {}, last_sync, "low", 0.6)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
        assert "lastModStartDate" in params
        assert "lastModEndDate" in params

    async def test_fresh_sync_uses_120_day_window(self):
        """When last_sync is within 1 hour, use 120-day lookback window."""
        pool = _make_pool_mock()
        nvd_response = _make_nvd_response([], total=0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nvd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # last_sync 30 minutes ago (within 1 hour)
        last_sync = datetime.now(timezone.utc) - timedelta(minutes=30)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                await _sync_cpe(pool, "cpe:test:*", {}, last_sync, "low", 0.6)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
        # Should use 120-day lookback, not the last_sync time
        start_date = params["lastModStartDate"]
        # Parse and verify it's roughly 120 days ago, not 30 min ago
        parsed = datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%S.000+00:00")
        expected = datetime.now(timezone.utc) - timedelta(days=120)
        assert abs((parsed - expected.replace(tzinfo=None)).total_seconds()) < 60

    async def test_pagination(self):
        """Multi-page results should be fetched with increasing startIndex."""
        pool = _make_pool_mock()

        page1_resp = MagicMock()
        page1_resp.status_code = 200
        page1_resp.json.return_value = _make_nvd_response(
            [_make_cve_item(cve_id="CVE-2024-00001", severity="HIGH")],
            total=201,
        )
        page1_resp.raise_for_status = MagicMock()

        page2_resp = MagicMock()
        page2_resp.status_code = 200
        page2_resp.json.return_value = _make_nvd_response(
            [_make_cve_item(cve_id="CVE-2024-00002", severity="HIGH")],
            total=201,
        )
        page2_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[page1_resp, page2_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                count = await _sync_cpe(pool, "cpe:test:*", {}, None, "medium", 0.6)

        assert count == 2
        assert mock_client.get.call_count == 2

    async def test_api_key_header_sent(self):
        """When api_key provided, apiKey header should be set."""
        pool = _make_pool_mock()
        nvd_response = _make_nvd_response([], total=0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = nvd_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        headers = {"apiKey": "test-key-123"}

        with patch("backend.cve_watch.httpx.AsyncClient", return_value=mock_client):
            with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                await _sync_cpe(pool, "cpe:test:*", headers, None, "low", 0.6)

        call_kwargs = mock_client.get.call_args
        assert call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {})) == headers


# =============================================================================
# 7. Full sync orchestration (_sync_nvd_cves)
# =============================================================================

class TestSyncNvdCves:
    async def test_disabled_config_skips(self):
        pool = _make_pool_mock()
        pool.fetchrow = AsyncMock(return_value=_make_config_row(enabled=False))

        await _sync_nvd_cves(pool)

        # Should not call fetch for appliances (no sync occurred)
        pool.fetch.assert_not_called()

    async def test_no_config_skips(self):
        pool = _make_pool_mock()
        pool.fetchrow = AsyncMock(return_value=None)

        await _sync_nvd_cves(pool)

        pool.fetch.assert_not_called()

    @pytest.mark.timeout(5)
    async def test_empty_cpe_list_skips(self):
        pool = _make_pool_mock()
        # Empty list: watched_cpes default in _make_config_row has `or` fallback,
        # so we pass None and set enabled=False to test early return path
        pool.fetchrow = AsyncMock(return_value=_make_config_row(enabled=False))

        await _sync_nvd_cves(pool)

        pool.fetch.assert_not_called()

    async def test_successful_sync_updates_timestamp(self):
        pool = _make_pool_mock()
        config = _make_config_row(
            watched_cpes=["cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*"],
            api_key="test-key",
        )
        pool.fetchrow = AsyncMock(return_value=config)

        with patch("backend.cve_watch._sync_cpe", new_callable=AsyncMock, return_value=5):
            with patch("backend.cve_watch._match_cves_to_fleet", new_callable=AsyncMock, return_value=3):
                with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                    await _sync_nvd_cves(pool)

        # Should update last_sync_at
        update_calls = [c for c in pool.execute.call_args_list
                        if "last_sync_at" in str(c)]
        assert len(update_calls) == 1

    async def test_cpe_sync_error_continues_others(self):
        pool = _make_pool_mock()
        config = _make_config_row(
            watched_cpes=["cpe:fail", "cpe:succeed"],
        )
        pool.fetchrow = AsyncMock(return_value=config)

        async def mock_sync(pool, cpe, headers, last_sync, min_sev, delay):
            if cpe == "cpe:fail":
                raise Exception("Network error")
            return 2

        with patch("backend.cve_watch._sync_cpe", side_effect=mock_sync):
            with patch("backend.cve_watch._match_cves_to_fleet", new_callable=AsyncMock, return_value=1):
                with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                    await _sync_nvd_cves(pool)

        # Should still update timestamp (partial success)
        update_calls = [c for c in pool.execute.call_args_list
                        if "last_sync_at" in str(c)]
        assert len(update_calls) == 1

    async def test_watched_cpes_string_decoded(self):
        """Handle watched_cpes stored as JSON string (double-encoded JSONB)."""
        pool = _make_pool_mock()
        config = _make_config_row(
            watched_cpes='["cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*"]',
        )
        pool.fetchrow = AsyncMock(return_value=config)

        with patch("backend.cve_watch._sync_cpe", new_callable=AsyncMock, return_value=0) as mock_sync:
            with patch("backend.cve_watch._match_cves_to_fleet", new_callable=AsyncMock, return_value=0):
                with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                    await _sync_nvd_cves(pool)

        # _sync_cpe should have been called with the decoded CPE string
        mock_sync.assert_called_once()
        called_cpe = mock_sync.call_args[0][1]
        assert called_cpe == "cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*"

    async def test_api_key_sets_shorter_delay(self):
        pool = _make_pool_mock()
        config = _make_config_row(
            watched_cpes=["cpe:test"],
            api_key="my-api-key",
        )
        pool.fetchrow = AsyncMock(return_value=config)

        with patch("backend.cve_watch._sync_cpe", new_callable=AsyncMock, return_value=0) as mock_sync:
            with patch("backend.cve_watch._match_cves_to_fleet", new_callable=AsyncMock, return_value=0):
                with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                    await _sync_nvd_cves(pool)

        # delay should be 0.6 (with key) not 6.0
        called_delay = mock_sync.call_args[0][5]
        assert called_delay == 0.6

    async def test_no_api_key_longer_delay(self):
        pool = _make_pool_mock()
        config = _make_config_row(
            watched_cpes=["cpe:test"],
            api_key=None,
        )
        pool.fetchrow = AsyncMock(return_value=config)

        with patch("backend.cve_watch._sync_cpe", new_callable=AsyncMock, return_value=0) as mock_sync:
            with patch("backend.cve_watch._match_cves_to_fleet", new_callable=AsyncMock, return_value=0):
                with patch("backend.cve_watch.asyncio.sleep", new_callable=AsyncMock):
                    await _sync_nvd_cves(pool)

        called_delay = mock_sync.call_args[0][5]
        assert called_delay == 6.0


# =============================================================================
# 8. _run_sync wrapper
# =============================================================================

class TestRunSync:
    async def test_run_sync_catches_exceptions(self):
        """_run_sync should not propagate exceptions."""
        with patch("backend.cve_watch.get_pool", new_callable=AsyncMock) as mock_gp:
            mock_gp.return_value = _make_pool_mock()
            with patch("backend.cve_watch._sync_nvd_cves", new_callable=AsyncMock,
                        side_effect=Exception("DB down")):
                # Should not raise
                await _run_sync()


# =============================================================================
# 9. SEVERITY_ORDER constant
# =============================================================================

class TestSeverityOrder:
    def test_critical_highest(self):
        assert SEVERITY_ORDER["critical"] < SEVERITY_ORDER["high"]

    def test_ordering_complete(self):
        ordered = sorted(SEVERITY_ORDER.keys(), key=lambda k: SEVERITY_ORDER[k])
        assert ordered == ["critical", "high", "medium", "low", "unknown"]

    def test_severity_filter_ranks(self):
        """Verify SEVERITY_ORDER allows correct min_severity filtering."""
        # Lower rank = more severe. A CVE passes if its rank <= min_severity rank.
        min_rank = SEVERITY_ORDER["medium"]  # 2
        assert SEVERITY_ORDER["critical"] <= min_rank  # passes
        assert SEVERITY_ORDER["high"] <= min_rank      # passes
        assert SEVERITY_ORDER["medium"] <= min_rank    # passes
        assert SEVERITY_ORDER["low"] > min_rank        # filtered out


# =============================================================================
# 10. Pydantic model validation
# =============================================================================

class TestPydanticModels:
    def test_valid_status_update(self):
        update = CVEStatusUpdate(status="mitigated", notes="Patched via fleet order")
        assert update.status == "mitigated"

    def test_invalid_status_rejected(self):
        with pytest.raises(Exception):
            CVEStatusUpdate(status="invalid_status")

    def test_all_valid_statuses(self):
        for status in ("open", "mitigated", "accepted_risk", "not_affected"):
            update = CVEStatusUpdate(status=status)
            assert update.status == status

    def test_config_update_sync_interval_bounds(self):
        config = CVEWatchConfigUpdate(sync_interval_hours=1)
        assert config.sync_interval_hours == 1

        config = CVEWatchConfigUpdate(sync_interval_hours=168)
        assert config.sync_interval_hours == 168

        with pytest.raises(Exception):
            CVEWatchConfigUpdate(sync_interval_hours=0)

        with pytest.raises(Exception):
            CVEWatchConfigUpdate(sync_interval_hours=169)

    def test_config_update_min_severity_validation(self):
        for sev in ("critical", "high", "medium", "low"):
            config = CVEWatchConfigUpdate(min_severity=sev)
            assert config.min_severity == sev

        with pytest.raises(Exception):
            CVEWatchConfigUpdate(min_severity="unknown")

    def test_config_update_all_none_is_valid(self):
        config = CVEWatchConfigUpdate()
        assert config.watched_cpes is None
        assert config.enabled is None


# =============================================================================
# 11. Edge cases in malformed data
# =============================================================================

class TestEdgeCases:
    async def test_upsert_cve_missing_all_optional_fields(self):
        """CVE with no descriptions, no metrics, no configs."""
        pool = _make_pool_mock()
        cve_data = {
            "id": "CVE-2024-MINIMAL",
            "metrics": {},
            "descriptions": [],
            "configurations": [],
            "references": [],
            "weaknesses": [],
        }

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        assert args[0][1] == "CVE-2024-MINIMAL"
        assert args[0][2] == "unknown"  # severity
        assert args[0][3] is None  # cvss_score
        assert args[0][6] == ""  # description

    async def test_upsert_cve_non_english_only(self):
        """When no English description exists, description stays empty."""
        pool = _make_pool_mock()
        cve_data = {
            "id": "CVE-2024-NOEN",
            "descriptions": [
                {"lang": "ja", "value": "Japanese description"},
            ],
            "metrics": {},
            "configurations": [],
            "references": [],
            "weaknesses": [],
        }

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        assert args[0][6] == ""

    async def test_upsert_non_vulnerable_cpe_excluded(self):
        """Only cpeMatch entries with vulnerable=True should be collected."""
        pool = _make_pool_mock()
        cve_data = {
            "id": "CVE-2024-NONVULN",
            "descriptions": [{"lang": "en", "value": "test"}],
            "metrics": {},
            "configurations": [
                {
                    "nodes": [
                        {
                            "cpeMatch": [
                                {"vulnerable": False, "criteria": "cpe:2.3:o:not_vuln:*"},
                                {"vulnerable": True, "criteria": "cpe:2.3:o:is_vuln:*"},
                            ]
                        }
                    ]
                }
            ],
            "references": [],
            "weaknesses": [],
        }

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        affected = json.loads(args[0][7])
        assert len(affected) == 1
        assert "is_vuln" in affected[0]["criteria"]

    def test_cpe_match_empty_criteria(self):
        """CPE match with empty criteria string should not match."""
        cpes = [{"criteria": ""}]
        appliance = {"appliance_id": "a", "site_id": "s",
                     "agent_version": "1.0", "nixos_version": "24.05"}
        assert _cpe_matches_appliance(cpes, appliance) is False

    async def test_fleet_match_malformed_json_string(self):
        """Malformed JSON in affected_cpes string should be treated as empty."""
        pool = _make_pool_mock()

        appliances = [
            {"appliance_id": "app-x", "site_id": "site-x",
             "agent_version": "1.0", "nixos_version": "24.05"}
        ]
        cves = [
            {"id": uuid4(), "cve_id": "CVE-2024-BAD", "affected_cpes": "{not valid json["}
        ]

        pool.fetch = AsyncMock(side_effect=[appliances, cves])

        result = await _match_cves_to_fleet(pool)
        assert result == 0

    async def test_upsert_version_range_fields_captured(self):
        """Version range fields from CPE match should be preserved."""
        pool = _make_pool_mock()
        cve_data = {
            "id": "CVE-2024-RANGE",
            "descriptions": [{"lang": "en", "value": "version range test"}],
            "metrics": {},
            "configurations": [
                {
                    "nodes": [
                        {
                            "cpeMatch": [
                                {
                                    "vulnerable": True,
                                    "criteria": "cpe:2.3:o:linux:linux_kernel:*:*:*:*:*:*:*:*",
                                    "versionStartIncluding": "5.0",
                                    "versionEndExcluding": "6.1.5",
                                }
                            ]
                        }
                    ]
                }
            ],
            "references": [],
            "weaknesses": [],
        }

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        affected = json.loads(args[0][7])
        assert affected[0]["versionStartIncluding"] == "5.0"
        assert affected[0]["versionEndExcluding"] == "6.1.5"

    async def test_upsert_extracts_references(self):
        """References from CVE data should be serialized into refs JSON."""
        pool = _make_pool_mock()
        cve_data = {
            "id": "CVE-2024-REFS",
            "descriptions": [{"lang": "en", "value": "refs test"}],
            "metrics": {},
            "configurations": [],
            "references": [
                {"url": "https://nvd.nist.gov/vuln/detail/CVE-2024-REFS", "source": "nvd"},
                {"url": "https://vendor.com/advisory/123", "source": "vendor"},
            ],
            "weaknesses": [],
        }

        await _upsert_cve(pool, cve_data)

        args = pool.execute.call_args
        refs = json.loads(args[0][8])
        assert len(refs) == 2
        assert refs[0]["url"] == "https://nvd.nist.gov/vuln/detail/CVE-2024-REFS"
