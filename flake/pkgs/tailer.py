#!/usr/bin/env python3
import os
import re
import sys
import time
import json
import pathlib
import threading
import queue
from typing import Dict, Tuple, Optional

import requests
from fastapi import FastAPI
import uvicorn

# -----------------------
# Configuration via env
# -----------------------
LOG_DIRS = [pathlib.Path(p.strip()) for p in os.getenv(
    "LOG_DIRS", "/var/log").split(",") if p.strip()]
# e.g. "*.log" or "app*.log"
FILE_GLOB = os.getenv("FILE_GLOB", "*.log")
ANOMALY_PAT = os.getenv("ANOMALY_PAT", r"(ERROR|CRITICAL|panic|traceback)")
ANOMALY = re.compile(ANOMALY_PAT, re.I)
MCP_URL = os.getenv("MCP_URL", "http://mcp:8000").rstrip("/")
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "1.0"))  # seconds
START_MODE = os.getenv("START_MODE", "end")               # "end" or "begin"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Very cheap “timestamp-ish” detector (used for multiline join)
TS_HINT = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}:\d{2}:\d{2}\b")

# -----------------------
# State & Metrics
# -----------------------
# inode -> (path, offset)
Offsets: Dict[int, Tuple[pathlib.Path, int]] = {}

# Simple counters for /status
metrics = {
    "lines_scanned": 0,
    "anomalies_detected": 0,
    "batches_sent": 0,
    "send_failures": 0,
    "last_activity_ts": None,
}

# Work queue for HTTP posts so tailing never blocks
send_q: "queue.Queue[dict]" = queue.Queue(maxsize=1000)

# -----------------------
# FastAPI (health only)
# -----------------------
app = FastAPI()


@app.get("/status")
def status():
    return {
        "ok": True,
        "metrics": metrics,
        "config": {
            "LOG_DIRS": [str(p) for p in LOG_DIRS],
            "FILE_GLOB": FILE_GLOB,
            "ANOMALY_PAT": ANOMALY_PAT,
            "SCAN_INTERVAL": SCAN_INTERVAL,
            "START_MODE": START_MODE,
        },
    }


def _run_http():
    # quiet uvicorn logs unless DEBUG
    log_level = "debug" if LOG_LEVEL == "DEBUG" else "error"
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level=log_level)


# Fire up the health server in the background
threading.Thread(target=_run_http, daemon=True).start()

# -----------------------
# Utilities
# -----------------------


def dbg(msg: str):
    if LOG_LEVEL in ("DEBUG",):
        print(f"[tailer DEBUG] {msg}", file=sys.stderr)


def warn(msg: str):
    print(f"[tailer WARN] {msg}", file=sys.stderr)


def now_ts() -> float:
    return time.time()


def list_candidate_files():
    for d in LOG_DIRS:
        try:
            for p in d.glob(FILE_GLOB):
                if p.is_file():
                    yield p
        except Exception as e:
            warn(f"Error listing {d}: {e}")


def file_inode(path: pathlib.Path) -> Optional[int]:
    try:
        return path.stat().st_ino
    except FileNotFoundError:
        return None
    except Exception as e:
        warn(f"stat({path}) failed: {e}")
        return None


def open_at_offset(path: pathlib.Path, inode: int):
    f = path.open("r", errors="replace", buffering=1)  # line-buffered
    # Where to start?
    if inode in Offsets:
        _, off = Offsets[inode]
        f.seek(off, os.SEEK_SET)
    else:
        if START_MODE.lower() == "end":
            f.seek(0, os.SEEK_END)
        else:
            f.seek(0, os.SEEK_SET)
    return f


def update_offset(inode: int, path: pathlib.Path, f):
    try:
        off = f.tell()
        Offsets[inode] = (path, off)
    except Exception:
        pass


def looks_like_continuation(line: str) -> bool:
    # Indented or no timestamp hint → likely continuation of previous line (e.g., stack trace)
    return line.startswith((" ", "\t")) or not TS_HINT.search(line)

# -----------------------
# Sender worker
# -----------------------


def sender_worker():
    session = requests.Session()
    # modest retries ourselves
    while True:
        batch = send_q.get()
        if batch is None:
            break
        url = f"{MCP_URL}/diagnose"
        try:
            r = session.post(url, json=batch, timeout=3)
            r.raise_for_status()
            action = (r.json() or {}).get("action")
            if action:
                # best effort remediation
                rem = {
                    "action": action,
                    "snippet": batch["snippet"],
                    "meta": batch.get("meta", {}),
                }
                session.post(f"{MCP_URL}/remediate", json=rem, timeout=3)
            metrics["batches_sent"] += 1
            metrics["last_activity_ts"] = now_ts()
        except Exception as e:
            metrics["send_failures"] += 1
            warn(f"MCP post failed: {e}")


# Start one sender thread (could be more)
threading.Thread(target=sender_worker, daemon=True).start()

# -----------------------
# Main tail loop
# -----------------------
hostname = os.uname().nodename


def scan_text(snippet: str, current_file: pathlib.Path):
    if ANOMALY.search(snippet):
        metrics["anomalies_detected"] += 1
        payload = {
            "snippet": snippet[:2000],
            "meta": {"hostname": hostname, "logfile": str(current_file)},
        }
        try:
            send_q.put_nowait(payload)
        except queue.Full:
            warn("send queue is full; dropping anomaly")


def process_file(path: pathlib.Path):
    inode = file_inode(path)
    if inode is None:
        return
    try:
        f = open_at_offset(path, inode)
    except Exception as e:
        warn(f"Open failed for {path}: {e}")
        return

    # multiline accumulator
    prev: Optional[str] = None

    try:
        while True:
            pos_before = f.tell()
            line = f.readline()
            if not line:
                # no new data this pass
                f.seek(pos_before, os.SEEK_SET)
                break

            metrics["lines_scanned"] += 1

            # handle multiline join
            if prev is None:
                prev = line
            else:
                if looks_like_continuation(line):
                    prev += line
                else:
                    scan_text(prev, path)
                    prev = line

        # flush any leftover multiline
        if prev:
            scan_text(prev, path)
    finally:
        update_offset(inode, path, f)
        f.close()


def reap_rotations():
    """If a file rotated/truncated, its inode will vanish or offset > size; clean mapping."""
    stale = []
    for ino, (p, off) in Offsets.items():
        try:
            st = p.stat()
            if st.st_ino != ino or st.st_size < off:
                stale.append(ino)
        except FileNotFoundError:
            stale.append(ino)
        except Exception:
            pass
    for ino in stale:
        dbg(f"Detected rotation/truncate for inode {ino}; resetting offset")
        Offsets.pop(ino, None)


def main_loop():
    while True:
        reap_rotations()
        for path in list_candidate_files():
            process_file(path)
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        pass
