from fastapi import FastAPI, Body
from datetime import datetime
import json

app = FastAPI()


@app.post("/diagnose")
def diagnose(data=Body()):
    print(f"\n[{datetime.now()}] DIAGNOSE REQUEST:")
    print(json.dumps(data, indent=2))

    # Simulate intelligent response based on error
    if "postgres" in str(data).lower():
        return {"action": "restart postgresql"}
    elif "nginx" in str(data).lower():
        return {"action": "restart nginx"}
    else:
        return {"action": "create_ticket"}


@app.post("/remediate")
def remediate(data=Body()):
    print(f"\n[{datetime.now()}] REMEDIATION TRIGGERED:")
    print(json.dumps(data, indent=2))
    return {"status": "executed", "timestamp": datetime.now().isoformat()}


@app.get("/health")
def health():
    return {"status": "healthy", "service": "MCP Server"}
