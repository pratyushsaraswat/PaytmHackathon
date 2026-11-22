import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import logging_config  # noqa: E402

logging_config.setup()

import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import ai  # noqa: E402
import checker  # noqa: E402
import copilot  # noqa: E402
import db  # noqa: E402
from serp import serp  # noqa: E402

log = logging.getLogger("api")

app = FastAPI(title="Paytm Trust & Growth")
db.init()


@app.middleware("http")
async def request_log(request: Request, call_next):
    """One id per request (also sent back as X-Request-ID) + one access line with timing."""
    rid = (request.headers.get("x-request-id") or uuid.uuid4().hex[:8])[:16]
    token = logging_config.request_id_var.set(rid)
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        log.error("REQ %s %s -> crashed after %dms (traceback logged by the UNHANDLED line)", request.method, request.url.path, (time.perf_counter() - t0) * 1000)
        raise
    ms = (time.perf_counter() - t0) * 1000
    resp.headers["X-Request-ID"] = rid
    path = request.url.path
    if path.startswith("/static") or path == "/api/health":
        lvl = logging.DEBUG
    else:
        lvl = logging.INFO if resp.status_code < 400 else logging.WARNING
    log.log(lvl, "REQ %s %s -> %d in %dms", request.method, path, resp.status_code, ms)
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("UNHANDLED %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal error", "request_id": logging_config.request_id_var.get()}, status_code=500)
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")

llm = ai.model_info()
log.info(
    "startup ok serp=%s cache_entries=%d llm=%s openai_key=%s",
    "mock" if serp.mock else "live",
    len(serp.cache),
    llm.get("provider") or "off",
    "set" if os.getenv("OPENAI_API_KEY", "").strip() else "missing",
)
if llm.get("enabled"):
    log.info("startup llm model=%s", llm.get("model"))
log.info("startup config log_level=%s log_file=%s db=%s demo_repeat=%s admin_endpoint=%s",
         os.getenv("LOG_LEVEL", "INFO").upper(), os.getenv("LOG_FILE", "") or "off", db.DB_PATH, db.DEMO_REPEAT,
         "on" if os.getenv("ADMIN_TOKEN", "").strip() else "off")


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
    info = {
        "mode": "mock" if serp.mock else "live",
        "llm": ai.model_info(),
        "serpapi_calls_this_run": serp.calls,
        "serp_cache_entries": len(serp.cache),
        "demo_repeat": db.DEMO_REPEAT,
    }
    log.debug("api /health ok %s", {k: info[k] for k in ("mode", "serpapi_calls_this_run", "serp_cache_entries")})
    return info


@app.post("/api/check")
def api_check(req: CheckReq):
    if not req.query.strip():
        raise HTTPException(400, "Enter a UPI ID, link, merchant name or message")
    q = req.query[:600]
    log.info("api /check IN query=%r", q[:120])
    out = checker.check(q)
    log.info(
        "api /check OK trust=%s verdict=%s mode=%s serp_jobs=%s errors=%d",
        out.get("trust"), out.get("verdict"), out.get("mode"), out.get("sources_used"), len(out.get("errors") or []),
    )
    if out.get("errors"):
        log.warning("api /check partial errors=%s", out["errors"])
    return out


@app.post("/api/copilot")
def api_copilot(req: CopilotReq):
    if not (req.business_name.strip() and req.category.strip() and req.location.strip()):
        raise HTTPException(400, "Business name, category and location are required")
    b, c, l = req.business_name.strip(), req.category.strip(), req.location.strip()
    log.info("api /copilot IN business=%r category=%r location=%r", b, c, l)
    out = copilot.run(b, c, l)
    trust = out.get("trust") or {}
    log.info(
        "api /copilot OK trust=%s competitors=%d tips=%d mode=%s errors=%d",
        trust.get("trust"), len(out.get("competitors") or []), len(out.get("tips") or []),
        out.get("mode"), len(out.get("errors") or []),
    )
    if out.get("errors"):
        log.warning("api /copilot partial errors=%s", out["errors"])
    return out


@app.post("/api/report")
def api_report(req: ReportReq, request: Request):
    if not req.query.strip():
        raise HTTPException(400, "Nothing to report")
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
    log.info("api /report IN query=%r reason=%r", req.query[:120], (req.reason or "")[:80])
    added, total = db.add_report(req.query, req.reason or "Reported from app", db.reporter_id(ip))
    log.info("api /report OK added=%s total_reports=%d", added, total)
    return {"added": added, "total_reports": total,
            "message": "Thanks - report saved. It now protects other users." if added else "You already reported this one."}


@app.get("/api/admin/db")
def admin_db(token: str = ""):
    """View stored reports + score history. Disabled unless ADMIN_TOKEN is set."""
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected or token != expected:
        raise HTTPException(404, "Not found")
    return db.dump()


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