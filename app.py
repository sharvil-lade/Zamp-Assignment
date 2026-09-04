"""Vendor Onboarding Decision Engine — HTTP layer.

Routes and rendering only. No validation, no rules, no AI.
See docs/07-architecture.md.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import pipeline
import store

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


app = FastAPI(title="Vendor Onboarding Decision Engine", lifespan=lifespan)


@app.get("/")
def root():
    # Part 6 replaces this with the submit form.
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/dashboard")
def dashboard(request: Request):
    runs = store.list_runs()
    stats = {
        "total": len(runs),
        "approved": sum(r["status"] == "APPROVED" for r in runs),
        "pending": sum(r["status"] == "PENDING" for r in runs),
        "rejected": sum(r["status"] == "REJECTED" for r in runs),
    }
    return templates.TemplateResponse(
        request, "dashboard.html", {"runs": runs, "stats": stats}
    )


@app.post("/submit")
def submit(submission: dict = Body(...)):
    """Accept a JSON vendor submission, run the pipeline, return the result.

    The body is deliberately an untyped dict, not a Pydantic model: a missing or
    blank field must become an R01 *finding*, not a 422. Validation is the rule
    engine's job, not the router's.
    """
    if not isinstance(submission, dict):
        raise HTTPException(400, "submission must be a JSON object")

    run_id = store.create_run(submission.get("legal_entity_name"), submission)
    status = pipeline.run(run_id)
    run = store.get_run(run_id)

    return {
        "run_id": run_id,
        "status": status,
        "duration_ms": run["duration_ms"],
        "findings": [
            {k: f[k] for k in ("rule_id", "severity", "stage", "message",
                               "expected", "actual", "tag")}
            for f in store.get_findings(run_id)
        ],
        "stages": [
            {"stage": e["stage"], "event_type": e["event_type"],
             "duration_ms": e["duration_ms"]}
            for e in store.get_events(run_id)
        ],
    }


@app.get("/run/{run_id}")
def get_run(run_id: str):
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "vendor_name": run["vendor_name"],
        "created_at": run["created_at"],
        "duration_ms": run["duration_ms"],
        "submission": run["submission"],
        "extracted": run["extracted"],
        "findings": store.get_findings(run_id),
        "events": store.get_events(run_id),
    }


@app.post("/reset")
def reset():
    store.reset_all()
    return RedirectResponse("/dashboard", status_code=303)
