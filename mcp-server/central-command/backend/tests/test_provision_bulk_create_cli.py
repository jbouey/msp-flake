"""CI gates for #119 fleet_cli provision-bulk-create per
audit/coach-119-bulk-onboarding-gate-a-2026-05-16.md.

Source-shape sentinels pin the 3 P0s + 4 named P1s:

  P0-1 — single aggregate admin_audit_log row, NOT N rows
         (audit-log explosion + cardinality loss avoidance)
  P0-2 — 100-entry hard cap enforced BEFORE opening the DB
         connection (matches POST /me/provisions/bulk cap)
  P0-3 — --actor-email mandatory + rejected for bot-y values
         (Maya rule on audit-actor naming)

  P1-1 — generate_provision_code extracted to provision_code.py
         (CLI must not import partners.py / FastAPI)
  P1-2 — CSV/JSON column allowlist (rejects unknown columns;
         closes CSV injection of arbitrary kwargs)
  P1-3 — THIS file (P1-3 binding from Gate A's CI gates list)
  P1-4 — docstring states non-idempotency

Anti-scope sentinels (Gate A "what NOT to add"):
  - no new HTTP endpoint
  - no privileged-chain attestation engagement
  - no new substrate invariant
"""
from __future__ import annotations

import pathlib
import re


_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_FLEET_CLI = _BACKEND / "fleet_cli.py"
_PROVISION_CODE = _BACKEND / "provision_code.py"


def _read_cli() -> str:
    return _FLEET_CLI.read_text(encoding="utf-8")


def _read_cmd_body() -> str:
    src = _read_cli()
    m = re.search(
        r"async def cmd_provision_bulk_create.*?(?=\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert m, "cmd_provision_bulk_create not found in fleet_cli.py"
    return m.group(0)


# ── P1-1: extracted module exists ────────────────────────────────


def test_p1_1_provision_code_module_exists():
    assert _PROVISION_CODE.exists(), (
        f"provision_code.py missing at {_PROVISION_CODE}. Per Gate A "
        f"P1-1: fleet_cli must NOT import partners.py (drags FastAPI "
        f"into CLI startup). Shared module is the right home."
    )
    body = _PROVISION_CODE.read_text()
    assert "def generate_provision_code" in body
    assert "secrets.token_hex(8)" in body, (
        "generate_provision_code must use secrets.token_hex(8) — "
        "matches the existing prod-shipped 16-char hex format. "
        "Changing would break QR-decode on appliance side + auditor "
        "kit references."
    )
    assert "def is_valid_site_id" in body, (
        "provision_code.py must export is_valid_site_id helper "
        "for CSV pre-flight validation."
    )


def test_p1_1_fleet_cli_does_not_import_partners():
    """If fleet_cli imports partners (which depends on FastAPI), the
    CLI startup pulls in the whole web framework. Per Gate A P1-1."""
    src = _read_cli()
    assert "from partners import" not in src
    assert "import partners\n" not in src
    assert "from .partners" not in src


def test_p1_1_partners_re_exports_from_provision_code():
    """partners.py must re-export generate_provision_code from the
    shared module — otherwise the two definitions drift (Coach class
    flagged in Gate A)."""
    partners_src = (_BACKEND / "partners.py").read_text()
    assert "from provision_code import generate_provision_code" in partners_src


# ── Subcommand registration ──────────────────────────────────────


def test_subcommand_registered():
    src = _read_cli()
    assert 'sub.add_parser(\n        "provision-bulk-create"' in src or \
           'sub.add_parser("provision-bulk-create"' in src, (
        "provision-bulk-create subparser not wired into main()"
    )
    assert '"provision-bulk-create": cmd_provision_bulk_create' in src, (
        "handler map must dispatch provision-bulk-create to "
        "cmd_provision_bulk_create"
    )


# ── P0-1: single aggregate audit row, NOT N ──────────────────────


def test_p0_1_single_aggregate_audit_row():
    """The admin_audit_log INSERT must be OUTSIDE the per-entry
    `for entry in rows:` loop body. Counts how many INSERT INTO
    admin_audit_log appear in the function — must be exactly 1."""
    body = _read_cmd_body()
    audit_inserts = re.findall(r"INSERT\s+INTO\s+admin_audit_log", body)
    assert len(audit_inserts) == 1, (
        f"Expected exactly 1 INSERT INTO admin_audit_log inside "
        f"cmd_provision_bulk_create; found {len(audit_inserts)}. "
        f"Per Gate A P0-1: single aggregate row encodes provision_"
        f"ids[] array + cardinality. N rows would explode the audit "
        f"log + lose cross-batch cardinality."
    )
    # And it must reference 'provision_bulk_create' as the action.
    assert "'provision_bulk_create'" in body, (
        "audit row action must be 'provision_bulk_create' (matches "
        "the agreed Coach-class naming convention from Gate A)."
    )


def test_p0_1_audit_row_encodes_provision_ids_array():
    body = _read_cmd_body()
    assert ('"provision_ids"' in body or "'provision_ids'" in body), (
        "audit row details must encode 'provision_ids' as a JSON "
        "array (cross-batch cardinality)."
    )


# ── P0-2: 100-cap enforced BEFORE conn opens ─────────────────────


def test_p0_2_100_cap_before_conn():
    body = _read_cmd_body()
    # The 100-cap exit must appear BEFORE the asyncpg.connect call.
    cap_idx = body.find("100-entry hard cap")
    connect_idx = body.find("asyncpg.connect")
    assert cap_idx != -1, (
        "100-entry hard cap message not found in cmd_provision_bulk_"
        "create. Per Gate A P0-2."
    )
    assert connect_idx != -1, "asyncpg.connect not found"
    assert cap_idx < connect_idx, (
        f"100-cap sys.exit must run BEFORE asyncpg.connect "
        f"(cap_idx={cap_idx}, connect_idx={connect_idx}). Operator "
        f"with a 500-row CSV must fail-fast WITHOUT opening a DB "
        f"connection — defensive sentinel per Gate A P0-2."
    )


def test_p0_2_explicit_100_literal():
    body = _read_cmd_body()
    assert "> 100" in body, (
        "Cap must be a literal `> 100` comparison (matches the "
        "server-side cap at partners.py:2902)."
    )


# ── P0-3: actor-email validation ─────────────────────────────────


def test_p0_3_actor_email_required_and_validated():
    body = _read_cmd_body()
    # The banned-actor set must include the Maya-rule prohibited values.
    src = _read_cli()
    banned_match = re.search(
        r"_BANNED_ACTOR_EMAILS\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src, re.DOTALL,
    )
    assert banned_match, (
        "_BANNED_ACTOR_EMAILS set must be a module-level frozenset"
    )
    banned_text = banned_match.group(1)
    for required in ("system", "fleet-cli", "admin", "operator", '""'):
        assert required in banned_text, (
            f"_BANNED_ACTOR_EMAILS missing {required!r}. Per Gate A "
            f"P0-3: never let audit-actor be a bot-y value."
        )
    # And the cmd body must check both '@' shape AND banned set.
    assert '"@" not in actor' in body, (
        "cmd must validate email shape via '@' check"
    )
    assert "_BANNED_ACTOR_EMAILS" in body, (
        "cmd must consult _BANNED_ACTOR_EMAILS"
    )


# ── P1-2: CSV/JSON column allowlist ───────────────────────────────


def test_p1_2_input_column_allowlist_enforced():
    src = _read_cli()
    m = re.search(
        r"_PROVISION_BULK_INPUT_COLUMNS\s*=\s*frozenset\(\s*\{([^}]+)\}\s*\)",
        src, re.DOTALL,
    )
    assert m, (
        "_PROVISION_BULK_INPUT_COLUMNS frozenset must be defined at "
        "module level per Gate A P1-2"
    )
    cols = m.group(1)
    assert '"client_name"' in cols
    assert '"target_site_id"' in cols
    # Loader must reject unknown columns
    loader_match = re.search(
        r"def _provision_bulk_load_input.*?(?=\ndef |\nasync def |\Z)",
        src, re.DOTALL,
    )
    assert loader_match
    loader = loader_match.group(0)
    assert "unknown column" in loader, (
        "loader must reject unknown columns with explicit error "
        "(closes CSV injection of arbitrary kwargs per Gate A P1-2)"
    )


# ── P1-4: non-idempotency documented ──────────────────────────────


def test_p1_4_non_idempotency_in_docstring():
    body = _read_cmd_body()
    assert "NOT IDEMPOTENT" in body or "not idempotent" in body.lower(), (
        "cmd docstring must state non-idempotency per Gate A P1-4. "
        "Re-running with the same --input produces DIFFERENT codes."
    )


# ── Anti-scope sentinels ──────────────────────────────────────────


def test_no_new_http_endpoint_for_bulk_create():
    """Gate A anti-scope: NO new HTTP endpoint. partners.py
    /me/provisions/bulk already covers the API path."""
    cli_src = _read_cli()
    # The CLI module must not contain FastAPI router decorators.
    assert "@router.post" not in cli_src
    assert "@app.post" not in cli_src
    assert "APIRouter" not in cli_src


def test_no_privileged_chain_engagement():
    """Gate A: provision bulk-create is NOT a privileged event class.
    Must not write to compliance_bundles, must not add to PRIVILEGED_
    ORDER_TYPES, must not invoke create_privileged_access_attestation."""
    body = _read_cmd_body()
    assert "compliance_bundles" not in body
    assert "create_privileged_access_attestation" not in body
    assert "attestation_bundle_id" not in body


def test_no_new_substrate_invariant_added():
    """Gate A: single-txn semantics make orphans impossible; no
    substrate invariant needed for this surface. If a future
    failure-class surfaces, file a separate task — don't bundle."""
    body = _read_cmd_body()
    assert "Assertion(" not in body
    assert "_check_provision_bulk" not in body


# ── Argparse wiring ──────────────────────────────────────────────


def test_argparse_actor_email_required():
    src = _read_cli()
    # Find the p_pbc block — starts at `p_pbc = sub.add_parser(`, ends
    # at the next `args = parser.parse_args()` or end of file.
    start = src.find('p_pbc = sub.add_parser(')
    assert start != -1, "p_pbc = sub.add_parser(...) not found"
    end = src.find("args = parser.parse_args()", start)
    assert end != -1, "args = parser.parse_args() not found after p_pbc"
    block = src[start:end]
    assert "--actor-email" in block
    assert "--partner-id" in block
    assert "--input" in block
    # `required=True` must appear at least 3 times (one per required arg).
    assert block.count("required=True") >= 3, (
        "--actor-email + --partner-id + --input must each be argparse-"
        "required (not just runtime-checked) so missing args surface "
        "at the CLI level not deep in cmd body."
    )


def test_argparse_input_required():
    src = _read_cli()
    assert '"--input", required=True' in src or \
           'metavar="PATH",' in src
