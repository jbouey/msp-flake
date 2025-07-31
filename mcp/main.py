from fastapi import FastAPI, Body
app = FastAPI()


@app.post("/diagnose")
def diagnose(data=Body()):
    return {"action": "restart nginx"}


@app.post("/remediate")
def remediate(data=Body()):
    print("Remediation requested:", data)
    return {"status": "ok"}
