#!/usr/bin/env python3
import threading
import uvicorn
from fastapi import FastAPI
import os
import re
import time
import requests
import pathlib
import sys

LOG_DIR = pathlib.Path("/var/log")
MCP_URL = os.getenv("MCP_URL", "http://mcp:8000")
ANOMALY = re.compile(r"(ERROR|CRITICAL|panic)", re.I)

app = FastAPI()


@app.get("/status")
def status():
    return {"ok": True}


# health endpoint in background
threading.Thread(
    target=lambda: uvicorn.run(
        app, host="0.0.0.0", port=8080, log_level="error"),
    daemon=True
).start()


def scan(line, current_file):
    if ANOMALY.search(line):
        payload = {
            "snippet": line.strip()[:2000],
            "meta": {"hostname": os.uname()[1], "logfile": str(current_file)},
        }
        try:
            r = requests.post(f"{MCP_URL}/diagnose", json=payload, timeout=3)
            action = (r.json() or {}).get("action")
            if action:
                requests.post(f"{MCP_URL}/remediate",
                              json={**payload, "action": action}, timeout=3)
        except Exception as e:
            print("MCP call failed:", e, file=sys.stderr)


# naive loop (polling)
while True:
    for current_file in LOG_DIR.glob("*.log"):
        try:
            with current_file.open() as f:
                f.seek(0, os.SEEK_END)  # start at end
                while True:
                    line = f.readline()
                    if not line:
                        break
                    scan(line, current_file)
        except Exception as e:
            print(f"Error reading {current_file}: {e}", file=sys.stderr)
    time.sleep(1)
