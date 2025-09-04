#!/usr/bin/env python3
"""
Production-hardened log tailer with anomaly detection.
Features: multiline support, rotation handling, circuit breaker, rate limiting, memory protection.
"""
import os
import re
import sys
import time
import json
import signal
import pathlib
import threading
import queue
import hashlib
import resource
from typing import Dict, Tuple, Optional
from collections import deque, defaultdict

import requests
from fastapi import FastAPI
import uvicorn

# -----------------------
# Configuration via env
# -----------------------
LOG_DIRS = [pathlib.Path(p.strip()) for p in os.getenv(
    "LOG_DIRS", "/var/log").split(",") if p.strip()]
FILE_GLOB = os.getenv("FILE_GLOB", "*.log")
ANOMALY_PAT = os.getenv(
    "ANOMALY_PAT", r"(ERROR|CRITICAL|panic|traceback|failed|exception)", )
ANOMALY = re.compile(ANOMALY_PAT, re.I)
MCP_URL = os.getenv("MCP_URL", "http://localhost:8000").rstrip("/")
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "1.0"))
START_MODE = os.getenv("START_MODE", "end")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
MAX_LINE_LENGTH = int(os.getenv("MAX_LINE_LENGTH", "10000"))
RATE_LIMIT_SECONDS = int(os.getenv("RATE_LIMIT_SECONDS", "60"))
MEMORY_LIMIT_MB = int(os.getenv("MEMORY_LIMIT_MB", "512"))

# Timestamp detection for multiline
TS_HINT = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{2}:\d{2}:\d{2}\b")

# -----------------------
# State & Metrics
# -----------------------
Offsets: Dict[int, Tuple[pathlib.Path, int]] = {}
recent_hashes = deque(maxlen=1000)
file_last_alert = defaultdict(float)
shutdown_event = threading.Event()

metrics = {
    "lines_scanned": 0,
    "anomalies_detected": 0,
    "batches_sent": 0,
    "send_failures": 0,
    "duplicates_skipped": 0,
    "rate_limited": 0,
    "circuit_breaker_opens": 0,
    "last_activity_ts": None,
    "start_time": time.time(),
}

send_q: "queue.Queue[dict]" = queue.Queue(maxsize=1000)

# -----------------------
# Circuit Breaker
# -----------------------


class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = None
        self.state = "closed"
        self.lock = threading.Lock()

    def call_succeeded(self):
        with self.lock:
            self.failure_count = 0
            self.state = "closed"

    def call_failed(self):
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                if self.state != "open":
                    metrics["circuit_breaker_opens"] += 1
                self.state = "open"

    def can_attempt(self):
        with self.lock:
            if self.state == "closed":
                return True
            if self.state == "open":
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = "half-open"
                    return True
            return self.state == "half-open"

    def get_state(self):
        with self.lock:
            return self.state


breaker = CircuitBreaker()

# -----------------------
# FastAPI Health
# -----------------------
app = FastAPI()


@app.get("/status")
def status():
    uptime = time.time() - metrics["start_time"]
    return {
        "ok": True,
        "uptime_seconds": uptime,
        "circuit_breaker": breaker.get_state(),
        "metrics": metrics,
        "config": {
            "LOG_DIRS": [str(p) for p in LOG_DIRS],
            "FILE_GLOB": FILE_GLOB,
            "ANOMALY_PAT": ANOMALY_PAT,
            "MCP_URL": MCP_URL,
        },
    }


@app.get("/health")
def health():
    # Kubernetes-style health check
    if breaker.get_state() == "open":
        return {"status": "degraded"}, 503
    return {"status": "ok"}


def _run_http():
    log_level = "debug" if LOG_LEVEL == "DEBUG" else "error"
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level=log_level)

# -----------------------
# Memory Protection
# -----------------------


def set_memory_limits():
    """Prevent runaway memory usage"""
    try:
        soft = MEMORY_LIMIT_MB * 1024 * 1024
        hard = soft * 2
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    except Exception as e:
        warn(f"Could not set memory limit: {e}")

# -----------------------
# Signal Handling
# -----------------------


def signal_handler(signum, frame):
    warn(f"Received signal {signum}, shutting down gracefully")
    shutdown_event.set()
    send_q.put(None)
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# -----------------------
# Utilities
# -----------------------


def info(msg: str):
    print(f"[tailer] {msg}", file=sys.stderr)


def dbg(msg: str):
    if LOG_LEVEL in ("DEBUG",):
        print(f"[tailer DEBUG] {msg}", file=sys.stderr)


def warn(msg: str):
    print(f"[tailer WARN] {msg}", file=sys.stderr)


def list_candidate_files():
    for d in LOG_DIRS:
        if not d.exists():
            continue
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
    f = path.open("r", errors="replace", buffering=1)
    if inode in Offsets:
        _, off = Offsets[inode]
        try:
            f.seek(off, os.SEEK_SET)
        except Exception:
            f.seek(0, os.SEEK_END)
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
    """Heuristic: indented or no timestamp = continuation"""
    return line.startswith((" ", "\t")) or not TS_HINT.search(line)

# -----------------------
# Sender Worker
# -----------------------


def sender_worker():
    session = requests.Session()
    session.headers.update({"User-Agent": "msp-tailer/1.0"})

    while True:
        batch = send_q.get()
        if batch is None:  # Shutdown signal
            break

        if not breaker.can_attempt():
            metrics["send_failures"] += 1
            dbg(f"Circuit breaker open, dropping batch")
            continue

        url = f"{MCP_URL}/diagnose"
        try:
            r = session.post(url, json=batch, timeout=5)
            r.raise_for_status()
            breaker.call_succeeded()

            response_data = r.json() if r.text else {}
            action = response_data.get("action")

            if action and action != "cooldown_active":
                rem = {
                    "action": action,
                    "snippet": batch["snippet"][:1000],
                    "meta": batch.get("meta", {}),
                }
                try:
                    session.post(f"{MCP_URL}/remediate", json=rem, timeout=5)
                except Exception as e:
                    dbg(f"Remediation failed: {e}")

            metrics["batches_sent"] += 1
            metrics["last_activity_ts"] = time.time()

        except requests.exceptions.RequestException as e:
            breaker.call_failed()
            metrics["send_failures"] += 1
            warn(f"MCP post failed: {e}")
        except Exception as e:
            metrics["send_failures"] += 1
            warn(f"Unexpected error in sender: {e}")


# -----------------------
# Anomaly Detection
# -----------------------
hostname = os.uname().nodename


def scan_text(snippet: str, current_file: pathlib.Path):
    """Check for anomalies and queue for sending"""

    # Truncate extremely long lines
    if len(snippet) > MAX_LINE_LENGTH:
        snippet = snippet[:MAX_LINE_LENGTH] + "...[truncated]"

    # Check for anomaly pattern
    if not ANOMALY.search(snippet):
        return

    # Deduplication
    msg_hash = hashlib.md5(snippet.encode()).hexdigest()
    if msg_hash in recent_hashes:
        metrics["duplicates_skipped"] += 1
        return
    recent_hashes.append(msg_hash)

    # Rate limiting per file
    now = time.time()
    file_key = str(current_file)
    if now - file_last_alert[file_key] < RATE_LIMIT_SECONDS:
        metrics["rate_limited"] += 1
        dbg(f"Rate limited for {current_file}")
        return
    file_last_alert[file_key] = now

    # Queue for sending
    metrics["anomalies_detected"] += 1
    payload = {
        "snippet": snippet,
        "meta": {
            "hostname": hostname,
            "logfile": str(current_file),
            "timestamp": now,
        },
    }

    try:
        send_q.put_nowait(payload)
    except queue.Full:
        warn("Send queue full, dropping anomaly")

# -----------------------
# File Processing
# -----------------------


def process_file(path: pathlib.Path):
    """Tail a single file for new content"""
    inode = file_inode(path)
    if inode is None:
        return

    f = None
    try:
        f = open_at_offset(path, inode)
        prev = None
        lines_this_pass = 0
        max_lines_per_pass = 10000  # Prevent infinite loops

        while lines_this_pass < max_lines_per_pass:
            if shutdown_event.is_set():
                break

            pos_before = f.tell()
            line = f.readline()

            if not line:
                # No new data
                f.seek(pos_before, os.SEEK_SET)
                break

            metrics["lines_scanned"] += 1
            lines_this_pass += 1

            # Multiline handling
            if prev is None:
                prev = line
            else:
                if looks_like_continuation(line):
                    prev += line
                    # Prevent unbounded growth
                    if len(prev) > MAX_LINE_LENGTH * 2:
                        scan_text(prev, path)
                        prev = None
                else:
                    scan_text(prev, path)
                    prev = line

        # Process any remaining line
        if prev:
            scan_text(prev, path)

    except Exception as e:
        warn(f"Error processing {path}: {e}")
    finally:
        if f:
            try:
                update_offset(inode, path, f)
                f.close()
            except Exception:
                pass


def reap_rotations():
    """Clean up after log rotation"""
    stale = []
    for ino, (p, off) in list(Offsets.items()):
        try:
            st = p.stat()
            # File rotated if inode changed or file shrunk
            if st.st_ino != ino or st.st_size < off:
                stale.append(ino)
        except FileNotFoundError:
            stale.append(ino)
        except Exception:
            pass

    for ino in stale:
        dbg(f"Detected rotation for inode {ino}")
        Offsets.pop(ino, None)

# -----------------------
# Main Loop
# -----------------------


def main_loop():
    info(f"Starting tailer: monitoring {LOG_DIRS} for pattern {ANOMALY_PAT}")
    info(f"MCP endpoint: {MCP_URL}")

    while not shutdown_event.is_set():
        try:
            reap_rotations()

            for path in list_candidate_files():
                if shutdown_event.is_set():
                    break
                process_file(path)

            # Use wait instead of sleep for responsive shutdown
            shutdown_event.wait(SCAN_INTERVAL)

        except Exception as e:
            warn(f"Error in main loop: {e}")
            time.sleep(1)


# -----------------------
# Entry Point
# -----------------------
if __name__ == "__main__":
    try:
        set_memory_limits()
        threading.Thread(target=_run_http, daemon=True).start()
        threading.Thread(target=sender_worker, daemon=True).start()
        main_loop()
    except KeyboardInterrupt:
        info("Interrupted")
    finally:
        shutdown_event.set()
        send_q.put(None)
