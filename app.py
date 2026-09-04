"""Vendor Onboarding Decision Engine — HTTP layer.

Routes and rendering only. No validation, no rules, no AI.
See docs/07-architecture.md.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import extract
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


async def _intake(request: Request) -> str:
    """Create the run from either a multipart form or a JSON body.

    Multipart is the real submission channel: form field `submission` holds the
    JSON, plus up to three file fields named after the document types. It always
    creates the upload directory — even with zero attachments — so R02 reports
    every document that is missing. A JSON-only body carries no documents at all,
    so extraction is skipped entirely.
    """
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        try:
            submission = json.loads(form.get("submission") or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"submission field is not valid JSON: {exc}")
        if not isinstance(submission, dict):
            raise HTTPException(400, "submission must be a JSON object")

        run_id = store.create_run(submission.get("legal_entity_name"), submission)
        dest = store.upload_dir(run_id)
        dest.mkdir(parents=True, exist_ok=True)
        for doc_type in extract.DOC_TYPES:
            upload = form.get(doc_type)
            if upload is None or not getattr(upload, "filename", ""):
                continue
            suffix = Path(upload.filename).suffix.lower() or ".pdf"
            (dest / f"{doc_type}{suffix}").write_bytes(await upload.read())
        return run_id

    submission = await request.json()
    if not isinstance(submission, dict):
        raise HTTPException(400, "submission must be a JSON object")
    return store.create_run(submission.get("legal_entity_name"), submission)


@app.post("/submit")
async def submit(request: Request):
    """Accept a vendor submission, run the pipeline, return the result.

    The submission is deliberately an untyped dict, not a Pydantic model: a
    missing or blank field must become an R01 *finding*, not a 422. Validation is
    the rule engine's job, not the router's.
    """
    run_id = await _intake(request)
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


@app.get("/run/{run_id}/export")
def export_run(run_id: str):
    """The whole run as one file — what replaces 'whatever's in someone's inbox'."""
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    findings = store.get_findings(run_id)
    events = store.get_events(run_id)

    return {
        "run": {k: run[k] for k in ("run_id", "vendor_name", "created_at",
                                    "status", "duration_ms")},
        "submission": run["submission"],
        "extracted": run["extracted"],
        "findings": [{k: f[k] for k in ("rule_id", "severity", "stage", "message",
                                        "expected", "actual", "tag")}
                     for f in findings],
        "communication": _communication(run, events),
        "events": [{**e, "detail": json.loads(e["detail_json"])
                    if e["detail_json"] else None,
                    "detail_json": None} for e in events],
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _communication(run: dict, events: list[dict]) -> dict:
    note = next((json.loads(e["detail_json"]) for e in events
                 if e["event_type"] == "internal_note"), None)
    sent_event = next((e for e in events if e["event_type"] == "followup_sent"), None)
    draft = run["followup_draft"]
    return {
        "draft_exists": draft is not None,
        "draft": draft,
        "sent": run["followup_sent_at"] is not None,
        "sent_at": run["followup_sent_at"],
        "sent_by": json.loads(sent_event["detail_json"])["actor"] if sent_event else None,
        "internal_note": note,
    }


@app.post("/run/{run_id}/send")
async def send_followup(run_id: str, request: Request):
    """The human gate. Nothing leaves this system without someone clicking.

    We do not send email — we record that a human did. See
    docs/10-assumptions-and-scope.md (assumption A6).
    """
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    if run["followup_draft"] is None:
        raise HTTPException(400, "this run has no follow-up draft to send")
    if run["followup_sent_at"] is not None:
        raise HTTPException(409, f"already sent at {run['followup_sent_at']}")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    actor = (payload.get("actor") or "reviewer").strip()
    body = payload.get("body") or run["followup_draft"]

    sent_at = store.mark_followup_sent(run_id, body)
    store.add_event(run_id, "communicate", "followup_sent", actor=f"user:{actor}",
                    detail={"actor": actor, "edited": body != run["followup_draft"]})
    return {"run_id": run_id, "sent": True, "sent_at": sent_at, "sent_by": actor}


@app.post("/reset")
def reset():
    store.reset_all()
    return RedirectResponse("/dashboard", status_code=303)
