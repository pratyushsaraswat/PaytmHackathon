import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import ai  # noqa: E402
import checker  # noqa: E402
import copilot  # noqa: E402
import db  # noqa: E402
from serp import serp  # noqa: E402

app = FastAPI(title="Paytm Trust & Growth")
db.init()
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")


class CheckReq(BaseModel):
    query: str


class ReportReq(BaseModel):
    query: str
    reason: str = ""


class CopilotReq(BaseModel):
    business_name: str
    category: str
    location: str


@app.get("/api/health")
def health():
    return {"mode": "mock" if serp.mock else "live", "claude": ai.enabled(), "serpapi_calls_this_run": serp.calls, "demo_repeat": db.DEMO_REPEAT}


@app.post("/api/check")
def api_check(req: CheckReq):
    if not req.query.strip():
        raise HTTPException(400, "Enter a UPI ID, link, merchant name or message")
    return checker.check(req.query[:600])


@app.post("/api/copilot")
def api_copilot(req: CopilotReq):
    if not (req.business_name.strip() and req.category.strip() and req.location.strip()):
        raise HTTPException(400, "Business name, category and location are required")
    return copilot.run(req.business_name.strip(), req.category.strip(), req.location.strip())


@app.post("/api/report")
def api_report(req: ReportReq, request: Request):
    if not req.query.strip():
        raise HTTPException(400, "Nothing to report")
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
    added, total = db.add_report(req.query, req.reason or "Reported from app", db.reporter_id(ip))
    return {"added": added, "total_reports": total,
            "message": "Thanks - report saved. It now protects other users." if added else "You already reported this one."}


@app.post("/api/demo/reset")
def demo_reset():
    if not db.DEMO_REPEAT:
        raise HTTPException(404, "Not found")
    db.reset_demo()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
