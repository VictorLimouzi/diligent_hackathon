"""The Casebook app server.

Run: .venv/bin/uvicorn server.app:app --reload   (data folder: $CASEBOOK_DATA)

Serves the engine's decisions and trails, the advisory ML/LLM layers, the
analyst audit log, and the built frontend from web/dist when it exists.
"""
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from server import advisory  # noqa: E402
from server.state import State  # noqa: E402

DECISIONS = {"DIFFERENT_ENTITY", "SAME_ENTITY", "REVIEW"}
state: Optional[State] = None


def get_state() -> State:
    global state
    if state is None:
        state = State()
    return state


@asynccontextmanager
async def lifespan(_app):
    get_state()
    yield


app = FastAPI(title="Casebook", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _hit(hit_id):
    s = get_state()
    h = s.hits.get(hit_id)
    if h is None:
        raise HTTPException(404, f"hit {hit_id} not found")
    return s, s.alerts[h["alert_id"]], h


def _sse(events):
    def gen():
        try:
            for e in events:
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/summary")
def summary():
    return get_state().summary(advisory.llm_status())


@app.get("/api/alerts")
def alerts(status: Optional[str] = None, q: Optional[str] = None, labelled: bool = False,
           sort: str = Query("priority", pattern="^(priority|file)$")):
    s = get_state()
    rows = [s.alert_row(a) for a in s.alerts.values()]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if labelled:
        rows = [r for r in rows if r["labelled"]]
    if q:
        needle = q.lower()
        rows = [r for r in rows if any(needle in str(v).lower() for v in
                                       [r["alert_id"], r["customer_name"], *r["hit_ids"], *r["hit_names"]])]
    if sort == "priority":
        order = {"open": 0, "compliance": 1, "cleared": 2}
        rows.sort(key=lambda r: (order[r["status"]], -(r["priority"] if r["priority"] is not None else -1)))
    return rows


@app.get("/api/alerts/{alert_id}")
def alert(alert_id: str):
    a = get_state().alerts.get(alert_id)
    if a is None:
        raise HTTPException(404, f"alert {alert_id} not found")
    return a


@app.get("/api/records/{record_id}")
def record(record_id: str):
    rec = get_state().record(record_id)
    if rec is None:
        raise HTTPException(404, f"record {record_id} not found")
    return rec


@app.get("/api/search/customers")
def search_customers(q: str, k: int = Query(10, ge=1, le=50), alert_id: Optional[str] = None):
    return advisory.search_customers(get_state(), q, k, alert_id) if q.strip() else []


@app.get("/api/qa")
def qa():
    return get_state().qa()


@app.get("/api/queue/summary")
def queue_summary():
    s = get_state()
    groups = s.qa()["blockers"]
    return {"groups": groups, **advisory.queue_summary(s, groups)}


@app.post("/api/hits/{hit_id}/note")
def note(hit_id: str):
    s, a, h = _hit(hit_id)
    return advisory.write_note(s, a["alert"], h)


@app.post("/api/hits/{hit_id}/investigate")
def investigate(hit_id: str):
    s, a, h = _hit(hit_id)
    return _sse(advisory.investigate(s, a["alert"], h))


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    messages: List[ChatMessage]


@app.post("/api/hits/{hit_id}/chat")
def chat(hit_id: str, body: ChatBody):
    s, a, h = _hit(hit_id)
    return _sse(advisory.chat(s, a["alert"], h, [m.model_dump() for m in body.messages]))


class ReviewBody(BaseModel):
    decision: str
    reason_code: str
    comment: str = ""
    analyst: str = "analyst"


@app.post("/api/hits/{hit_id}/review")
def review(hit_id: str, body: ReviewBody):
    s, _, _ = _hit(hit_id)
    if body.decision not in DECISIONS:
        raise HTTPException(422, "decision must be DIFFERENT_ENTITY, SAME_ENTITY or REVIEW")
    return s.add_review(hit_id, body.analyst, body.decision, body.reason_code, body.comment)


@app.get("/api/audit")
def audit(hit_id: Optional[str] = None):
    return get_state().audit(hit_id=hit_id)


@app.get("/api/benchmark")
def benchmark():
    """The numbers written by `make benchmark` (evals/benchmark.py), when they exist."""
    from server.state import OUT
    try:
        with open(os.path.join(OUT, "benchmark.json"), encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, ValueError):
        return {"available": False}
    return result if isinstance(result, dict) else {"available": False}


@app.post("/api/admin/reload-advisory")
def reload_advisory():
    s = get_state()
    s.attach_advisory()
    return {"ok": True, "ml": any(h.get("ml") for h in s.hits.values()),
            "llm": sum(1 for h in s.hits.values() if h.get("llm"))}


# The built frontend, when present. API routes above take precedence.
_DIST = os.path.join(ROOT, "web", "dist")
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        candidate = os.path.join(_DIST, path)
        if path and os.path.isfile(candidate) and os.path.realpath(candidate).startswith(os.path.realpath(_DIST)):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))
