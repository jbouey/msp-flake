"""Chaos lab activation API (admin-only).

Provides push-button scenario activation from the admin dashboard.
The chaos lab lives on the iMac (MaCs-iMac.local, 192.168.88.50) and
is reachable via a reverse SSH tunnel through the VPS on port 2250.

Trust model: admin-authenticated only. The VPS shells out to the iMac
via sshpass to trigger bundle runs. All subprocess invocations use
asyncio.create_subprocess_exec with argv arrays (no shell=True, no
string interpolation into shell) — SECURITY: command injection not
possible because user-supplied bundle_id is passed as a separate argv
element, not concatenated into a shell string.

Not for customer sites — this is an OsirisCare-internal tool for proving
the platform works. Customer sites receive real attacks, not simulations.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/chaos", tags=["chaos"])


# -- Configuration (env-driven so we can disable or re-target easily) -------

# If unset, chaos lab endpoints return 503. Gates this feature behind an
# explicit opt-in so it doesn't accidentally ship enabled on a customer VPS.
CHAOS_LAB_ENABLED = os.getenv("CHAOS_LAB_ENABLED", "false").lower() in ("true", "1", "yes")

# SSH tunnel destination (as set up by reverse tunnel from iMac).
CHAOS_LAB_SSH_HOST = os.getenv("CHAOS_LAB_SSH_HOST", "localhost")
CHAOS_LAB_SSH_PORT = int(os.getenv("CHAOS_LAB_SSH_PORT", "2250"))
CHAOS_LAB_SSH_USER = os.getenv("CHAOS_LAB_SSH_USER", "jrelly")
CHAOS_LAB_SSH_PASS = os.getenv("CHAOS_LAB_SSH_PASS", "")
CHAOS_LAB_RUNNER_PATH = os.getenv(
    "CHAOS_LAB_RUNNER_PATH", "/Users/jrelly/chaos-lab/v2-runner.py"
)

# Whitelist of characters allowed in bundle_id (defense in depth — bundle_id
# is already passed as an argv element so shell injection is impossible, but
# we also validate to catch silly bugs like newlines in IDs).
import re as _re
_BUNDLE_ID_RE = _re.compile(r"^[a-z0-9_\-]{1,80}$")


def _validate_bundle_id(bundle_id: str) -> None:
    if not _BUNDLE_ID_RE.match(bundle_id):
        raise HTTPException(
            status_code=400,
            detail="bundle_id must match ^[a-z0-9_-]{1,80}$",
        )


def _ensure_enabled() -> None:
    if not CHAOS_LAB_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Chaos lab API disabled. Set CHAOS_LAB_ENABLED=true in the "
                "Central Command environment to enable. This is an internal "
                "test tool, not a customer feature."
            ),
        )
    if not CHAOS_LAB_SSH_PASS:
        raise HTTPException(
            status_code=503,
            detail="Chaos lab SSH credentials not configured",
        )


async def _ssh_run(remote_argv: List[str], timeout: int = 600) -> Dict[str, Any]:
    """Execute a command on the iMac via sshpass + reverse tunnel.

    remote_argv is a list of strings — each becomes a separate argument to
    the REMOTE shell. SSH concatenates them with spaces before executing
    on the remote end, so callers must ensure no element contains
    whitespace OR must pre-quote elements that do (we use argv-only
    commands here to avoid this entirely).

    Returns {returncode, stdout, stderr, duration_s}. Never raises on
    non-zero exit — caller inspects result.
    """
    local_argv = [
        "sshpass", "-p", CHAOS_LAB_SSH_PASS,
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-p", str(CHAOS_LAB_SSH_PORT),
        f"{CHAOS_LAB_SSH_USER}@{CHAOS_LAB_SSH_HOST}",
    ] + remote_argv

    start = datetime.now(timezone.utc)
    try:
        # Using create_subprocess_exec (NOT shell=True) — argv array is passed
        # verbatim to execvp. No shell interpolation possible.
        proc = await asyncio.create_subprocess_exec(
            *local_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"SSH command timed out after {timeout}s",
                "duration_s": timeout,
            }
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
            "duration_s": (datetime.now(timezone.utc) - start).total_seconds(),
        }
    except FileNotFoundError as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"sshpass or ssh not found on VPS: {e}",
            "duration_s": 0,
        }


# -- Models -----------------------------------------------------------------


class BundleInfo(BaseModel):
    id: str
    name: str
    difficulty: Optional[str] = None
    category: Optional[str] = None
    steps: Optional[int] = None
    target: Optional[str] = None
    file: Optional[str] = None
    error: Optional[str] = None


class ActivateRequest(BaseModel):
    bundle_id: str = Field(..., description="ID of bundle to activate")
    mode: str = Field(
        "activate",
        description="Execution mode (activate / test-promotion)",
    )


class JobOutcome(BaseModel):
    job_id: str
    bundle_id: str
    started_at: str
    completed_at: Optional[str] = None
    overall_success: Optional[bool] = None
    duration_s: Optional[float] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    returncode: Optional[int] = None


# -- Endpoints --------------------------------------------------------------


@router.get("/bundles", response_model=List[BundleInfo])
async def list_bundles(user: dict = Depends(require_auth)) -> List[BundleInfo]:
    """List all chaos bundles the lab knows about."""
    _ensure_enabled()
    result = await _ssh_run(["python3", CHAOS_LAB_RUNNER_PATH, "list"], timeout=30)
    if result["returncode"] != 0:
        raise HTTPException(
            status_code=502,
            detail=f"Chaos lab list failed: {result['stderr'][:500]}",
        )
    try:
        bundles_raw = json.loads(result["stdout"])
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail=f"Chaos lab returned non-JSON: {result['stdout'][:500]}",
        )
    return [BundleInfo(**b) for b in bundles_raw]


@router.post("/activate", response_model=JobOutcome)
async def activate_bundle(
    body: ActivateRequest,
    user: dict = Depends(require_auth),
) -> JobOutcome:
    """Activate a chaos bundle on the lab. Blocks until the bundle completes."""
    _ensure_enabled()
    _validate_bundle_id(body.bundle_id)
    if body.mode not in ("activate", "test-promotion", "cadence"):
        raise HTTPException(status_code=400, detail="invalid mode")

    job_id = str(uuid.uuid4())

    # Admin audit: who activated what
    logger.info(
        "Chaos bundle activation",
        extra={
            "user": user.get("username"),
            "bundle_id": body.bundle_id,
            "mode": body.mode,
            "job_id": job_id,
        },
    )

    result = await _ssh_run(
        [
            "python3", CHAOS_LAB_RUNNER_PATH, "run", body.bundle_id,
            "--mode", body.mode,
            "--job-id", job_id,
        ],
        timeout=900,
    )

    overall_success = None
    completed_at = datetime.now(timezone.utc).isoformat()
    if result["stdout"]:
        try:
            outcome_json = json.loads(result["stdout"])
            overall_success = outcome_json.get("overall_success")
            completed_at = outcome_json.get("completed_at", completed_at)
        except json.JSONDecodeError:
            pass

    return JobOutcome(
        job_id=job_id,
        bundle_id=body.bundle_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=completed_at,
        overall_success=overall_success,
        duration_s=result["duration_s"],
        stdout=result["stdout"][:4000],
        stderr=result["stderr"][:1000],
        returncode=result["returncode"],
    )


@router.post("/cleanup/{bundle_id}", response_model=JobOutcome)
async def cleanup_bundle(
    bundle_id: str,
    user: dict = Depends(require_auth),
) -> JobOutcome:
    """Run the cleanup script for a bundle — undoes the injection."""
    _ensure_enabled()
    _validate_bundle_id(bundle_id)
    job_id = str(uuid.uuid4())

    logger.info(
        "Chaos bundle cleanup",
        extra={"user": user.get("username"), "bundle_id": bundle_id, "job_id": job_id},
    )

    result = await _ssh_run(
        ["python3", CHAOS_LAB_RUNNER_PATH, "cleanup", bundle_id],
        timeout=300,
    )

    return JobOutcome(
        job_id=job_id,
        bundle_id=bundle_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        overall_success=(result["returncode"] == 0),
        duration_s=result["duration_s"],
        stdout=result["stdout"][:4000],
        stderr=result["stderr"][:1000],
        returncode=result["returncode"],
    )


@router.get("/history")
async def list_recent_jobs(
    limit: int = 20,
    user: dict = Depends(require_auth),
) -> Dict[str, Any]:
    """Recent bundle-results.csv entries from the lab (summary rows only)."""
    _ensure_enabled()
    limit = max(1, min(200, int(limit)))
    csv_path = "/Users/jrelly/chaos-lab/v2-logs/bundle-results.csv"
    result = await _ssh_run(["tail", "-n", str(limit + 1), csv_path], timeout=30)

    rows: List[Dict[str, Any]] = []
    lines = [ln for ln in result["stdout"].splitlines() if ln.strip()]
    if not lines:
        return {"rows": [], "computed_at": datetime.now(timezone.utc).isoformat()}

    header = lines[0].split(",")
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) == len(header):
            row = dict(zip(header, parts))
            if "overall_success" in row:
                row["overall_success"] = row["overall_success"] == "True"
            rows.append(row)

    rows.reverse()  # newest first
    return {
        "rows": rows[:limit],
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
