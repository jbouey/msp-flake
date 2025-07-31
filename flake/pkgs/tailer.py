#!/usr/bin/env python
import threading
import uvicorn


from fastapi import FastAPI
import os, re, json, time, requests, pathlib

LOG_DIR = pathlib.Path("/var/log")
MCP_URL = os.getenv("MCP_URL", "http://mcp:8000")

ANOMALY = re.compile(r"(ERROR|CRITICAL|panic)", re.I)

app = FastAPI()


@app.get("/status")
def status():
    return {"ok": True}


threading.Thread(target=lambda: uvicorn.run(
    app, host="0.0.0.0", port=8080, log_level="error"), daemon=True).start()


def scan(line):
    if ANOMALY.search(line):
        payload = {
            "snippet": line.strip()[:2_000],
            "meta": {
                "hostname": os.uname()[1],
                "logfile": str(current_file),
            }
        }
        try:
            r = requests.post(f"{MCP_URL}/diagnose", json=payload, timeout=3)
            action = r.json().get("action")
            if action:
                requests.post(f"{MCP_URL}/remediate", json={"action": action, **payload})
        except Exception as e:
            print("MCP call failed:", e, file=sys.stderr)

while True:
    for current_file in LOG_DIR.glob("*.log"):
        with current_file.open() as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if not line:
                    break
                scan(line)
    time.sleep(1)
