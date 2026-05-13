# Gate A — `test_soft_verify_uses_savepoint.py` AST Gate

**Date:** 2026-05-13
**Task:** #79 (Coach P0)
**Reviewers (fork-attributed):** Steve (Eng), Maya (DB), Carol (Sec), Coach (Process), OCR (Auditor), PM, In-house Counsel
**Verdict:** **APPROVE-WITH-FIXES** — proceed to implementation after adopting the SQLAlchemy `begin_nested()` parity finding (Carol/Maya P0) and the ratchet-baseline mechanic (Coach P0).

---

## 200-word Summary

The proposed AST gate closes a real and recurring outage class: a verifier call inside a `try` block whose `except` swallows the exception leaves the asyncpg connection (or SQLAlchemy session) in `InFailedSqlTransactionError` state, silently failing every downstream write on the same handle. Today's 4h+ dashboard outage at sites.py:4244 is the canonical example — soft-verify caught the cast-mismatch exception, then the `appliance_heartbeats` INSERT 40 lines later wrote `signature_valid=NULL` for every appliance fleet-wide. Just-shipped commit `3ec431c8` wraps the verify in `async with conn.transaction():`; the gate prevents regression.

**Gate must cover BOTH connection styles.** Backend uses asyncpg (`conn.transaction()`) AND SQLAlchemy AsyncSession (`db.begin_nested()`); the agent_api.py:1202 `verify_consent_active` callsite is a SQLAlchemy sibling and is NOT currently wrapped — pre-existing exposure under shadow mode, becomes a customer-facing fault when consent flips to enforce (Phase 3+).

**Enumeration today:** 2 verifier-soft-fail callsites in backend — `sites.py:4244` (FIXED in `3ec431c8`) + `agent_api.py:1202` (UNFIXED, latent). Count is small enough to **hard-fail** rather than ratchet, but Coach recommends ratchet-baseline=1 (the agent_api callsite) with a 7-day TaskCreate followup, so the gate ships without coupling to a same-commit fix.

---

## Per-Lens Verdict

### 1. Engineering (Steve) — APPROVE

AST traversal is straightforward. Walk every `ast.Try` node in the backend's `.py` files. For each try, check:

1. **Handler swallows.** At least one `ast.ExceptHandler` whose body contains NO `ast.Raise` (or a bare `raise` re-raise — exclude those). Walk handler body for `logger.exception(...)` / `logger.error(...)` / `logger.warning(...)` / `continue` / `pass` / `return` — any of these without a `raise` = "swallow".
2. **Verifier call in body.** Walk `try.body` for `ast.Await` whose value is `ast.Call`, where the callable name matches `VERIFIER_PATTERN` (manifest list below).
3. **Conn argument heuristic.** First positional arg of the verifier call is named `conn` / `_sigauth_conn` / `db` / similar — flagging that the call is database-bound. Allowlist for verifier names that operate ONLY on bytes (e.g. `verify_password`, `verify_totp`, `verify_consent_signature` which is pure crypto on bytes, `verify_merkle_proof`, `verify_ed25519_signature` — these take no conn).
4. **Savepoint wrapper required.** The `Await` node's nearest enclosing `ast.AsyncWith` must have `items[*].context_expr` matching `<conn>.transaction()` OR `<db>.begin_nested()`. Walk up via parent links (build a parent map in pass 1).

**Edge cases:**
- **Nested try blocks.** Walk ALL `ast.Try` nodes (don't short-circuit on inner). Each is evaluated independently. The current sites.py:4252 pattern is `try → async with conn.transaction(): → await verify_*` — inner try is the savepoint-wrapped one, outer try contains the savepoint, so the OUTER try's body has the AsyncWith → PASS.
- **Multiple verifier calls in one try.** Each verifier call must be individually wrapped (or all be inside one shared `async with`).
- **try-except-finally.** Finally is irrelevant; gate looks at except handlers only.
- **Import-time try blocks.** sites.py:4240-4243 has a `try: from .signature_auth import verify_heartbeat_signature` — handler is `ImportError`, no DB call in body. Gate must NOT match (handler not `Exception` / `BaseException`; body has no verifier *call*, only an import). Filter handlers to those catching `Exception` / `BaseException` / bare `except:` / multi-exception tuples containing `Exception`.
- **Sync verifier in async caller.** `verify_ed25519_signature` is sync — caught by `ast.Await` filter (sync calls aren't Await nodes). Pure-crypto verifiers don't take conn → no savepoint needed → correctly skipped.

**False-positive risk:** LOW. Verifier names are project-specific (`verify_appliance_signature`, `verify_heartbeat_signature`, `verify_consent_active`, `verify_and_consume`, `verify_site_retention`, `verify_appliance_ownership`, `verify_site_ownership`, `verify_exception_ownership`, `verify_site_api_key`, `validate_session`). Manifest-list approach (not regex glob) keeps signal high.

**Effort:** ~45 min AST walker + ~30 min test fixtures + manifest list = ~1.25h.

### 2. Database (Maya) — APPROVE-WITH-FIX (P0)

PG transaction-state poisoning is precisely the failure class. Confirmed by example:

```python
async with admin_connection(pool) as conn:
    try:
        await verify_thing(conn, ...)  # raises UndefinedFunctionError
    except Exception:
        logger.exception("soft fail")
    # conn is now in InFailedSqlTransactionError state.
    await conn.execute("INSERT INTO heartbeats ...")  # silently fails OR raises
```

Wrapping in `async with conn.transaction():` opens a SAVEPOINT; the verifier exception triggers ROLLBACK TO SAVEPOINT (not full ROLLBACK), restoring the outer transaction to a usable state. This is the canonical pattern and matches the "asyncpg savepoint invariant (Session 205)" rule already in CLAUDE.md.

**P0 fix to gate spec:** SQLAlchemy AsyncSession has the SAME poisoning class. The equivalent savepoint primitive is `async with db.begin_nested():` (BEGIN SAVEPOINT under the hood). The `agent_api.py:1202` callsite uses a SQLAlchemy session, not asyncpg. The gate MUST treat `db.begin_nested()` as a valid wrapper alongside `conn.transaction()`. Without this, agent_api.py:1202 either (a) gets a false-negative (gate name-matches `conn.transaction()` only and incorrectly skips SQLAlchemy callers) or (b) gets a false-positive on a future SQLAlchemy callsite that uses `begin_nested()` correctly.

**Recommended wrapper-name allowlist:**
- `conn.transaction()` — asyncpg
- `_sigauth_conn.transaction()` / `_anyname.transaction()` — asyncpg with renamed handle
- `db.begin_nested()` — SQLAlchemy AsyncSession savepoint
- Generic: any `ast.AsyncWith` whose `context_expr.func.attr` is in `{"transaction", "begin_nested"}` — captures both styles without hard-coding handle names.

### 3. Security (Carol) — APPROVE-WITH-FIX (P0 — broaden coverage)

Verifier-exception poisoning that masks downstream writes is a security primitive: it can silently kill audit-log INSERTs, attestation row INSERTs, sigauth_observations writes, evidence-chain anchors, privileged-access magic-link consumption logs. Every one of those is a Counsel Rule 3 "attested chain of custody" carrier.

**Pre-fix sibling-callsite enumeration** (grep `await verify_` + `await validate_session` in backend `.py`, with try-block containment check):

| # | Callsite | Verifier | Handle | Try-wrapped? | Savepoint? | Status |
|---|----------|----------|--------|--------------|------------|--------|
| 1 | `sites.py:4253` | `verify_heartbeat_signature` | `conn` (asyncpg) | YES | YES (`conn.transaction()` at :4252) | **FIXED in `3ec431c8`** |
| 2 | `sites.py:3782` | `verify_appliance_signature` | `_sigauth_conn` (asyncpg) | YES (outer `async with admin_connection`) | YES (`_sigauth_conn.transaction()` at :3780) | PASS |
| 3 | `agent_api.py:1209` | `verify_consent_active` | `db` (SQLAlchemy) | YES (`try:` at :1202, `except Exception:` at :1233 swallows with `logger.exception`) | **NO** — no `db.begin_nested()` wrapper | **LATENT P0** |
| 4 | `privileged_access_api.py:510` | `verify_and_consume` | `conn` (asyncpg) | YES (`try:` at :509, `except MagicLinkError:` re-raises as HTTPException) | NO — but handler RE-RAISES, not swallows | PASS (filter excludes raisers) |
| 5 | `retention_verifier.py:182` | `verify_site_retention` | `conn` (asyncpg) | YES (`try:` at :180, `except Exception:` at :183 logs warning + `continue`) | YES (`conn.transaction()` at :181) | PASS |
| 6 | `exceptions_api.py:411 / :430 / :472 / :513` | `verify_exception_ownership` | `conn` (asyncpg) | NO — direct call, exceptions propagate | N/A | PASS (no try-swallow) |
| 7 | `exceptions_api.py:179` | `verify_site_ownership` | `conn` (asyncpg) | NO — `if not await ...` direct | N/A | PASS |
| 8 | `appliance_delegation.py:298` | `verify_appliance_ownership` | `conn` (asyncpg) | NO — `if not await ...` direct | N/A | PASS |
| 9 | `sites.py:79` | `verify_site_api_key` | `conn` (asyncpg) | YES (`try:` at :78, `except Exception as e:` at :81 logs warning + falls through to `raise HTTPException`) | **NO** | **borderline — see below** |
| 10 | `auth.py:788` | `validate_session` | `db` (SQLAlchemy) | NO — direct call | N/A | PASS |

**Net latent hits: 1 confirmed P0 + 1 borderline = 2.**

- **agent_api.py:1209 (CONFIRMED LATENT).** Shadow-mode today means `consent_result.should_block()` always returns False, so the verifier exception doesn't poison anything user-visible — but the order INSERT on `db` 30 lines later (line 1264) is on the SAME session. If `verify_consent_active` raises while `db` is mid-transaction, the order INSERT silently fails. This is exactly the sites.py:4244 class. Confirmed P0. Fix: wrap line 1209 in `async with db.begin_nested():`.

- **sites.py:79 (BORDERLINE).** The except handler at :81 logs warning and falls through to a `raise HTTPException(401)` at :88, so the connection is never reused after the failed verify — the broken txn state is irrelevant because the handler returns immediately on the auth-rejection path. Gate would flag it but the fix is "no fix needed; mark with `# noqa: soft-verify-savepoint` comment". Recommend allowlist marker.

**Security verdict:** Gate is necessary AND sufficient to close the class structurally. Without the SQLAlchemy/`db.begin_nested()` parity, the agent_api consent path stays exposed.

### 4. Coach (Process) — APPROVE-WITH-FIX (ratchet-baseline, NOT hard-fail-on-new)

Enumeration: 1 latent hit (agent_api.py:1209) + 1 borderline (sites.py:79 — noqa-worthy).

**Hard-fail on PR-1 would couple the gate-landing commit to fixing the agent_api hit.** That's a cleaner outcome but risks the gate getting BLOCKED on Maya §164.504(e) deep-dive ("does adding a savepoint around the consent check change the consent ledger's attestation-chain semantics? Phase 3 enforce-mode behavior under savepoint rollback?"). Don't couple — ship the gate as a ratchet with baseline=1, file the agent_api fix as a same-day TaskCreate followup.

**Ratchet mechanic:**
- Gate file: `tests/test_soft_verify_uses_savepoint.py`.
- Manifest: `VERIFIER_PATTERNS` set + `WRAPPER_NAMES` set + `ALLOWLIST` of `(file, lineno)` carve-outs.
- Initial ALLOWLIST: `[("mcp-server/central-command/backend/agent_api.py", 1209), ("mcp-server/central-command/backend/sites.py", 79)]`.
- Test asserts `len(violations - ALLOWLIST) == 0` AND `len(ALLOWLIST) == 2` (locked-in baseline). Reductions allowed; additions BLOCKED.
- Followup TaskCreate: fix agent_api.py:1209 within 7 days, then drop from ALLOWLIST.

**Why this is better than hard-fail:** the agent_api fix touches the Migration 184 consent path — needs its own mini-Gate-A (Maya) because savepoint rollback semantics interact with the ledger. The AST gate's value is closing the CLASS for all FUTURE code without blocking on per-callsite legal review.

### 5. Auditor (OCR) — N/A

Gate is internal-quality; no §164.528 disclosure surface.

### 6. PM — APPROVE

- AST walker: ~45 min
- Test fixtures (positive + negative): ~30 min
- Manifest list curation: ~15 min
- Ratchet baseline lock-in + ALLOWLIST: ~10 min
- Total: ~1.5h

Followup task (agent_api.py:1209 SQLAlchemy fix + mini-Gate-A): ~2h (Maya consent-ledger review + 1 line code + tests).

### 7. Counsel (in-house) — APPROVE

Counsel Rule 3 ("no privileged action without attested chain of custody") is materially served. Verifier exception poisoning is the textbook way an audit-log INSERT or attestation row write can silently fail post-soft-fail. Closing the class structurally via an AST gate is exactly the Rule 3 hardening pattern. Rule 9 ("determinism and provenance are not decoration") also served — soft-fail without savepoint = nondeterministic write completion is observed in the wild today.

No outside-counsel question raised. No BAA implication. No customer-facing artifact change.

---

## AST Traversal Sketch (Pseudocode)

```python
import ast
from pathlib import Path

VERIFIER_PATTERNS = {
    # asyncpg conn-bound
    "verify_appliance_signature",
    "verify_heartbeat_signature",
    "verify_and_consume",
    "verify_site_retention",
    "verify_appliance_ownership",
    "verify_site_ownership",
    "verify_exception_ownership",
    "verify_site_api_key",
    # SQLAlchemy db-bound
    "verify_consent_active",
    "validate_session",
}

WRAPPER_FUNC_NAMES = {"transaction", "begin_nested"}

# (file, lineno) pairs that pre-date the gate and are explicitly noqa'd
# until per-callsite fix lands.
ALLOWLIST = {
    ("mcp-server/central-command/backend/agent_api.py", 1209),
    ("mcp-server/central-command/backend/sites.py", 79),
}

def _build_parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents

def _handler_swallows(handler: ast.ExceptHandler) -> bool:
    """True if handler catches Exception/BaseException AND body has no
    unconditional `raise`."""
    # Filter on caught-type
    caught = handler.type
    if caught is None:
        caught_names = {"BaseException"}  # bare except
    elif isinstance(caught, ast.Name):
        caught_names = {caught.id}
    elif isinstance(caught, ast.Tuple):
        caught_names = {e.id for e in caught.elts if isinstance(e, ast.Name)}
    else:
        caught_names = set()
    if not (caught_names & {"Exception", "BaseException"}):
        return False  # narrow except (e.g. MagicLinkError) — not a swallow
    # Look for top-level Raise in handler body
    for stmt in handler.body:
        if isinstance(stmt, ast.Raise):
            return False  # re-raises — propagates failure
    return True

def _try_body_has_verifier_call(try_node: ast.Try) -> list[ast.Await]:
    hits = []
    for node in ast.walk(ast.Module(body=try_node.body, type_ignores=[])):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None
        )
        if name in VERIFIER_PATTERNS:
            hits.append(node)
    return hits

def _await_inside_savepoint(await_node: ast.Await, parents: dict) -> bool:
    cur = parents.get(await_node)
    while cur is not None:
        if isinstance(cur, ast.AsyncWith):
            for item in cur.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call) and isinstance(ctx.func, ast.Attribute):
                    if ctx.func.attr in WRAPPER_FUNC_NAMES:
                        return True
        cur = parents.get(cur)
    return False

def check_file(path: Path) -> list[tuple[Path, int, str]]:
    tree = ast.parse(path.read_text())
    parents = _build_parent_map(tree)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if not any(_handler_swallows(h) for h in node.handlers):
            continue
        for await_node in _try_body_has_verifier_call(node):
            if not _await_inside_savepoint(await_node, parents):
                fn_name = (
                    await_node.value.func.attr
                    if isinstance(await_node.value.func, ast.Attribute)
                    else await_node.value.func.id
                )
                violations.append((path, await_node.lineno, fn_name))
    return violations

def test_soft_verify_uses_savepoint():
    root = Path("mcp-server/central-command/backend")
    all_violations = []
    for p in root.glob("*.py"):
        all_violations.extend(check_file(p))
    # Drop allowlisted
    new_violations = [
        v for v in all_violations
        if (str(v[0]), v[1]) not in ALLOWLIST
    ]
    assert not new_violations, (
        f"New soft-verify-without-savepoint callsites: {new_violations}. "
        f"Wrap verifier call in `async with conn.transaction():` or "
        f"`async with db.begin_nested():`."
    )
    # Ratchet — additions to ALLOWLIST also block
    assert len(ALLOWLIST) == 2, (
        f"ALLOWLIST drifted ({len(ALLOWLIST)} entries). Reductions only."
    )
```

---

## Manifest List — Verifier Function Patterns

**INCLUDE (conn/db-bound — DB-state-poisoning class):**
- `verify_appliance_signature` (signature_auth.py)
- `verify_heartbeat_signature` (signature_auth.py)
- `verify_and_consume` (privileged_magic_link.py)
- `verify_site_retention` (retention_verifier.py)
- `verify_appliance_ownership` (appliance_delegation.py)
- `verify_site_ownership` (exceptions_api.py)
- `verify_exception_ownership` (exceptions_api.py)
- `verify_site_api_key` (appliance_delegation.py)
- `verify_consent_active` (runbook_consent.py)
- `validate_session` (auth.py)

**EXCLUDE (pure crypto / pure bytes — no conn argument, no DB state to poison):**
- `verify_password` (auth.py — bcrypt only)
- `verify_totp` (totp.py — HMAC only)
- `verify_backup_code` (totp.py)
- `verify_api_key` (partners.py — hash compare)
- `verify_merkle_proof` (merkle.py — hash compare)
- `verify_ed25519_signature` (evidence_chain.py — pure crypto)
- `verify_consent_signature` (runbook_consent.py — pure crypto on bytes)
- `verify_csr_signature` / `validate_cert_signature` (iso_ca_helpers.py — sync, pure crypto)
- `validate_pattern_signature` (learning_api.py — string format check)
- `verify_evidence` / `verify_chain_integrity` / `verify_bundle_full` / `verify_batch` / `verify_merkle_proof_endpoint` (evidence_chain.py — these DO take conn but are top-level FastAPI endpoints, not inside try-swallow patterns; gate's try-context filter naturally excludes them)
- `verify_totp_login` (routes.py — FastAPI endpoint)

**Growth rule:** when a new verifier function is added that takes a `conn` or `db` first-positional argument, it MUST be added to the INCLUDE list in the same commit. Test asserts `VERIFIER_PATTERNS` is a SET LITERAL in the test file (no dynamic loading) so additions are reviewed.

---

## Pre-fix Sibling Callsite Enumeration

See Carol's table above. Summary:

- **2 callsites in backend match the try-swallow pattern AND need savepoints today:**
  - `sites.py:4253` — FIXED (commit `3ec431c8`).
  - `agent_api.py:1209` — LATENT; SQLAlchemy session; shadow-mode masks the symptom; flips to user-visible at Phase 3 consent enforce.
- **1 borderline:** `sites.py:79` — handler raises HTTPException 401 unconditionally, so downstream conn-reuse never happens. Allowlist with marker.
- **3 verifier callsites in try-blocks but handler re-raises** (privileged_access_api.py:510, retention_verifier.py:182 — savepointed correctly, plus the inner sigauth_observations at sites.py:3789 which is a different verifier-followup pattern) — naturally PASS the gate.

---

## Hard-Fail vs Ratchet Recommendation

**RATCHET-BASELINE with `len(ALLOWLIST) == 2` lock.**

**Why not hard-fail (zero violations):**
- agent_api.py:1209 fix needs its own Maya §Migration 184 mini-Gate-A on consent-ledger savepoint semantics.
- Coupling the AST-gate landing commit to the agent_api fix invites the gate to get BLOCKED on a tangential review.

**Why not ratchet-baseline-only (no lock):**
- Without the `len(ALLOWLIST) == 2` assertion, devs can append to the ALLOWLIST when their PR introduces a new violation. That's exactly the regression the gate is supposed to prevent.

**Mechanic:**
- ALLOWLIST is a literal `set` of `(path, lineno)` tuples in the test file.
- Test asserts `len(ALLOWLIST) == 2`. Reducing requires updating the literal AND the assertion in the same commit (forces conscious decision).
- Same-commit followup TaskCreate `#81`: "Wrap agent_api.py:1209 in `async with db.begin_nested():` + Maya consent-ledger mini-Gate-A". 7-day SLA.

---

## Final Overall Verdict

**APPROVE-WITH-FIXES.**

**P0 fixes required before Gate B:**
1. **(Maya/Carol)** Gate MUST recognize `db.begin_nested()` as a valid savepoint wrapper alongside `conn.transaction()`. Without this, the SQLAlchemy class is invisible to the gate.
2. **(Coach)** Ratchet-baseline mechanic with `len(ALLOWLIST) == 2` lock-in assertion. Hard-fail couples to legal review of consent path.

**P1 followups (same commit, TaskCreate, NOT blocking):**
3. **(Carol)** TaskCreate `#81` — fix agent_api.py:1209 SQLAlchemy savepoint + Maya Migration 184 consent-ledger mini-Gate-A. 7-day SLA.
4. **(Steve)** TaskCreate — add `# noqa: soft-verify-savepoint — handler raises HTTPException`-style comment at sites.py:79 to make the allowlist self-documenting.

**Gate B expectations:**
- Run `tests/test_soft_verify_uses_savepoint.py` from a clean checkout — must pass.
- Run the full SOURCE_LEVEL_TESTS sweep (per Session 220 lock-in) — must pass.
- Inject a synthetic new violation (write a test file with `try: await verify_heartbeat_signature(conn, ...) except Exception: pass`) — gate must catch and fail.
- Inject the opposite (verifier call inside a correct `async with conn.transaction():`) — gate must pass.
- Verify the ALLOWLIST length-lock — bump to 3 in a synthetic patch, gate must fail.
- Cite Maya db-handle-parity fix (P0 #1) and Coach ratchet-lock (P0 #2) as addressed in the commit body.
