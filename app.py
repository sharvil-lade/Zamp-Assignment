"""Vendor Onboarding Decision Engine — HTTP layer.

Routes and rendering only. No validation, no rules, no AI.
See docs/07-architecture.md.
"""

import json
import logging
import re as _re
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

import extract
import pipeline
import rules
import store

BASE_DIR = Path(__file__).parent
SAMPLE_DIR = BASE_DIR / "samples"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

STATUSES = ("APPROVED", "PENDING", "REJECTED", "ERROR")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    yield


# The vendor token travels in the URL path, which means the access log would
# record a working credential on every request. Redact it at the log boundary.
_TOKEN_IN_PATH = _re.compile(r"(/vendor/onboard/)[A-Za-z0-9_\-]{16,}")


def _redact(text: str) -> str:
    """Keep the route, drop the credential. A function replacement rather than
    a template string — backreference escaping is not worth the ambiguity."""
    return _TOKEN_IN_PATH.sub(lambda m: m.group(1) + "<redacted>", text)


class _RedactVendorToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(_redact(a) if isinstance(a, str) else a
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactVendorToken())


app = FastAPI(title="Vendor Onboarding Decision Engine", lifespan=lifespan)

HTML_MESSAGES = {
    404: "We couldn't find that.",
    400: "That request wasn't something we could use.",
    409: "That has already been done.",
    500: "Something went wrong on our side.",
}


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    """A browser gets a readable page; an API client still gets JSON."""
    wants_html = "text/html" in request.headers.get("accept", "")
    if not wants_html:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request, "error.html", {
        "code": exc.status_code,
        "message": exc.detail or HTML_MESSAGES.get(exc.status_code, "Something went wrong."),
    }, status_code=exc.status_code)


# --- samples ----------------------------------------------------------------

def _samples() -> dict:
    """The four demo scenarios, loaded fresh so editing a fixture needs no restart."""
    out = {}
    for path in sorted(SAMPLE_DIR.glob("ec*.json")):
        out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return out


@app.get("/samples/{name}")
def get_sample(name: str):
    sample = _samples().get(name)
    if sample is None:
        raise HTTPException(404, f"unknown sample {name}")
    return sample


# --- submit -----------------------------------------------------------------

@app.get("/")
def submit_form(request: Request):
    samples = _samples()
    return templates.TemplateResponse(request, "submit.html", {
        "samples": samples,
        "samples_json": json.dumps(
            {k: v["submission"] for k, v in samples.items()}),
        "documents": [(k, rules.DOCUMENT_LABELS[k]) for k in extract.DOC_TYPES],
        "entity_types": rules.ENTITY_TYPES,
        "countries": rules.COUNTRIES,
        "tax_id_types": rules.TAX_ID_TYPES,
        "fields": rules.SUBMISSION_FIELDS,
        "labels": rules.FIELD_LABELS,
    })


def _save_uploads(run_id: str, form) -> None:
    """Persist attached files, or copy the fixture PDFs when a sample was chosen.

    A browser cannot pre-fill a file input, so the sample dropdown names its
    documents in a hidden field and the server attaches them. That keeps the demo
    to two clicks and removes the most common live-demo failure: fumbling an
    upload on camera.
    """
    dest = store.upload_dir(run_id)
    dest.mkdir(parents=True, exist_ok=True)

    sample = (form.get("sample") or "").strip()
    fixture_docs = _samples().get(sample, {}).get("documents", {}) if sample else {}

    for doc_type in extract.DOC_TYPES:
        upload = form.get(doc_type)
        if upload is not None and getattr(upload, "filename", ""):
            data = upload.file.read(extract.MAX_UPLOAD_BYTES + 1)
            reason = extract.check_upload(upload.filename, data)
            if reason:
                # Not saved, so R02 reports the document as missing — a fixable
                # finding rather than a crashed run. The reason is recorded so the
                # reviewer and the audit trail can both see what was wrong.
                store.add_event(run_id, "intake", "upload_rejected", detail={
                    "document": doc_type,
                    "filename": Path(upload.filename).name,
                    "reason": reason,
                    "bytes": len(data),
                })
                continue
            suffix = Path(upload.filename).suffix.lower()
            (dest / f"{doc_type}{suffix}").write_bytes(data)
        elif doc_type in fixture_docs:
            src = SAMPLE_DIR / "pdfs" / fixture_docs[doc_type]
            if src.exists():
                shutil.copyfile(src, dest / f"{doc_type}.pdf")


def _submission_from_form(form) -> dict:
    return {field: (form.get(field) or "").strip()
            for field in rules.SUBMISSION_FIELDS}


@app.post("/submit")
async def submit(request: Request, background: BackgroundTasks):
    """Accept a vendor submission.

    An HTML form (multipart) runs the pipeline in the background and redirects to
    the live run view. A JSON body runs it synchronously and returns the result —
    that is the API path, and it is what the scripts and tests use.

    The submission is deliberately untyped: a missing or blank field must become
    an R01 *finding*, not a 422. Validation is the rule engine's job, not the
    router's.
    """
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        raw = form.get("submission")
        if raw:                                     # scripted multipart caller
            try:
                submission = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise HTTPException(400, f"submission is not valid JSON: {exc}")
        else:                                       # the HTML form
            submission = _submission_from_form(form)

        run_id = store.create_run(submission.get("legal_entity_name"), submission)
        _save_uploads(run_id, form)
        background.add_task(pipeline.run, run_id, pause=pipeline.DEMO_PAUSE_S)
        return RedirectResponse(f"/run/{run_id}", status_code=303)

    submission = await request.json()
    if not isinstance(submission, dict):
        raise HTTPException(400, "submission must be a JSON object")
    run_id = store.create_run(submission.get("legal_entity_name"), submission)
    status = pipeline.run(run_id)
    run = store.get_run(run_id)
    return {
        "run_id": run_id,
        "status": status,
        "duration_ms": run["duration_ms"],
        "findings": [{k: f[k] for k in ("rule_id", "severity", "stage", "message",
                                        "expected", "actual", "tag")}
                     for f in store.get_findings(run_id)],
        "stages": [{"stage": e["stage"], "event_type": e["event_type"],
                    "duration_ms": e["duration_ms"]}
                   for e in store.get_events(run_id)],
    }


# ============================================================================
# EMPLOYEE — case creation
# ============================================================================

@app.get("/onboardings/new")
def new_case_form(request: Request):
    return templates.TemplateResponse(request, "onboarding_new.html", {})


@app.post("/onboardings")
async def create_case(request: Request):
    """Create a case and mint its one-time vendor link.

    The raw token is returned to the employee exactly once, here. Only its hash
    is persisted, so the link cannot be recovered from the database or the logs.
    """
    form = await request.form()
    vendor_name = (form.get("vendor_name") or "").strip()
    if not vendor_name:
        raise HTTPException(400, "Vendor or company name is required")

    token = store.new_token()
    case_id = store.create_case(
        vendor_name,
        (form.get("contact_name") or "").strip(),
        (form.get("contact_email") or "").strip(),
        token)

    return templates.TemplateResponse(request, "onboarding_created.html", {
        "case": store.get_case(case_id),
        "vendor_url": str(request.base_url).rstrip("/") + f"/vendor/onboard/{token}",
    })


# ============================================================================
# VENDOR PORTAL — token is the only authorisation. No case ids are trusted.
# ============================================================================

def _case_for_token(token: str) -> dict:
    """A bad, unknown or expired token is indistinguishable from a wrong URL."""
    case = store.get_case_by_token(token)
    if case is None:
        raise HTTPException(404, "This onboarding link is not valid.")
    return case


def _vendor_form_context(request: Request, token: str, case: dict) -> dict:
    """Field definitions come from rules.py — the vendor form is not a second schema."""
    return {
        "token": token,
        "case": case,
        "groups": [
            ("Your company", ["legal_entity_name", "entity_type",
                              "country_of_incorporation", "registered_address_state"]),
            ("Contact", ["contact_name", "contact_email", "contact_phone"]),
            ("Tax registration", ["tax_id_type", "gstin", "pan"]),
            ("Bank account", ["account_holder_name", "account_number",
                              "ifsc", "bank_name"]),
        ],
        "labels": rules.FIELD_LABELS,
        "selects": {"entity_type": rules.ENTITY_TYPES,
                    "country_of_incorporation": rules.COUNTRIES,
                    "tax_id_type": rules.TAX_ID_TYPES},
        "mono": ["gstin", "pan", "ifsc", "account_number"],
        "documents": [(k, rules.DOCUMENT_LABELS[k]) for k in extract.DOC_TYPES],
        "prefill": {
            "legal_entity_name": case["vendor_name"],
            "contact_name": case["contact_name"] or "",
            "contact_email": case["contact_email"] or "",
        },
        "max_mb": extract.MAX_UPLOAD_BYTES // 1024 // 1024,
    }


@app.get("/vendor/onboard/{token}")
def vendor_form(request: Request, token: str):
    case = _case_for_token(token)
    if case["status"] != store.AWAITING_VENDOR:
        return templates.TemplateResponse(request, "vendor_submitted.html",
                                          {"case": case, "already": True})
    return templates.TemplateResponse(request, "vendor_form.html",
                                      _vendor_form_context(request, token, case))


@app.post("/vendor/onboard/{token}")
async def vendor_submit(request: Request, token: str, background: BackgroundTasks):
    """Vendor submission. Hands straight to the existing PS-2 pipeline, unchanged."""
    case = _case_for_token(token)
    if case["status"] != store.AWAITING_VENDOR:
        # One link, one submission. Prevents a replayed link starting a second run.
        raise HTTPException(409, "This onboarding has already been submitted.")

    form = await request.form()
    submission = _submission_from_form(form)

    run_id = store.create_run(submission.get("legal_entity_name")
                              or case["vendor_name"], submission)
    _save_uploads(run_id, form)                     # existing upload validation
    store.attach_run_to_case(case["id"], run_id)
    store.add_event(run_id, "intake", "vendor_submitted", actor="vendor",
                    detail={"case_id": case["id"]})   # never the token
    background.add_task(pipeline.run, run_id, pause=pipeline.DEMO_PAUSE_S)

    return RedirectResponse(f"/vendor/onboard/{token}/submitted", status_code=303)


@app.get("/vendor/onboard/{token}/submitted")
def vendor_confirmation(request: Request, token: str):
    """Confirmation only. No status, no findings, no run id, no internal detail."""
    case = _case_for_token(token)
    return templates.TemplateResponse(request, "vendor_submitted.html",
                                      {"case": case, "already": False})


# --- run view ---------------------------------------------------------------

def _decoded_events(run_id: str) -> list[dict]:
    return [{**e, "detail": json.loads(e["detail_json"]) if e["detail_json"] else None}
            for e in store.get_events(run_id)]


def _stage_view(events: list[dict], status: str) -> list[dict]:
    """Turn the event log into the seven-row live run view.

    Derived entirely from persisted events — the UI never guesses where a run is.
    """
    by_stage: dict[str, dict] = {}
    for e in events:
        s = by_stage.setdefault(e["stage"], {"started": False, "done": False,
                                             "failed": False, "ms": None,
                                             "detail": None, "ai_calls": 0})
        if e["event_type"] == "stage_started":
            s["started"] = True
        elif e["event_type"] == "stage_completed":
            s["done"] = True
            s["ms"] = e["duration_ms"]
            s["detail"] = e["detail"]
        elif e["event_type"] == "stage_failed":
            s["failed"] = True
            s["detail"] = e["detail"]
        elif e["event_type"] == "ai_call":
            s["ai_calls"] += 1
        elif e["event_type"] == "decision":
            s["started"] = s["done"] = True
            s["detail"] = e["detail"]

    rows, reached_end = [], status != "RUNNING"
    for key, label, ai in pipeline.STAGES:
        s = by_stage.get(key)
        if s is None:
            # Extraction is genuinely skipped when a run carried no documents;
            # anything else unseen is simply not reached yet.
            state = "skipped" if (reached_end and key == "extraction") else "pending"
            rows.append({"key": key, "label": label, "ai": bool(ai), "state": state,
                         "ms": None, "detail": None, "ai_calls": 0})
            continue
        state = ("failed" if s["failed"] else
                 "done" if s["done"] else
                 "running" if s["started"] else "pending")
        rows.append({"key": key, "label": label,
                     # consistency is badged only when this run actually used AI
                     "ai": bool(ai) or (ai is None and s["ai_calls"] > 0),
                     "state": state, "ms": s["ms"], "detail": s["detail"],
                     "ai_calls": s["ai_calls"]})
    return rows


def _comparisons(run: dict) -> list[dict]:
    """Form values beside document values, mirroring the rules that compare them.

    Each row names the rule that owns it, so a highlighted row is not just "these
    differ" but "this is why R09 fired". EC-3 is the case that makes this matter:
    the form and the cheque agree with each other, and it is the *legal entity*
    that the account holder does not match — so that is the pair we show.
    """
    extracted = run["extracted"] or {}
    sub = run["submission"]
    pairs = [
        (rules.DOCUMENT_LABELS["incorporation_certificate"],
         "incorporation_certificate", [
            ("Legal entity name", "legal_entity_name", "legal_name", "R12")]),
        (rules.DOCUMENT_LABELS["bank_proof"], "bank_proof", [
            ("Account holder — vs legal entity", "legal_entity_name",
             "account_holder_name", "R09"),
            ("Account number", "account_number", "account_number", "R10"),
            ("IFSC", "ifsc", "ifsc", "R10"),
            ("Bank name", "bank_name", "bank_name", None)]),
        (rules.DOCUMENT_LABELS["insurance_certificate"],
         "insurance_certificate", [
            ("Insured name", "legal_entity_name", "insured_name", None)]),
    ]
    out = []
    for label, key, fields in pairs:
        doc = extracted.get(key)
        rows = []
        for row_label, form_field, doc_field, rule_id in fields:
            form_value = sub.get(form_field) or None
            doc_value = (doc or {}).get(doc_field)
            mismatch = bool(rule_id and form_value and doc_value
                            and str(form_value).strip().casefold()
                            != str(doc_value).strip().casefold())
            rows.append({"label": row_label, "form": form_value, "doc": doc_value,
                         "mismatch": mismatch, "rule": rule_id})
        out.append({"label": label, "key": key, "attached": doc is not None,
                    "rows": rows})
    return out


def _run_context(request: Request, run_id: str) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    findings = store.get_findings(run_id)
    events = _decoded_events(run_id)
    note = next((e["detail"] for e in events if e["event_type"] == "internal_note"),
                None)
    return {
        "request": request,
        "run": run,
        "stages": _stage_view(events, run["status"]),
        "findings": findings,
        "blocks": sum(f["severity"] == "BLOCK" for f in findings),
        "fixes": sum(f["severity"] == "FIX" for f in findings),
        "uncertain": sum(f["tag"] == "ai_uncertain" for f in findings),
        "comparisons": _comparisons(run),
        "events": events,
        "internal_note": note,
        "rejected_uploads": [e["detail"] for e in events
                             if e["event_type"] == "upload_rejected"],
        "has_documents": run["extracted"] is not None,
        # Not `status != RUNNING`: the status is durable before stage 7 runs.
        "finished": any(e["event_type"] == "run_finished" for e in events),
    }


@app.get("/run/{run_id}")
def run_view(request: Request, run_id: str):
    return templates.TemplateResponse(request, "run.html",
                                      _run_context(request, run_id))


@app.get("/run/{run_id}/stages")
def run_body(request: Request, run_id: str):
    """The polled fragment. Stops polling itself once the run is terminal."""
    return templates.TemplateResponse(request, "_run_body.html",
                                      _run_context(request, run_id))


# --- export -----------------------------------------------------------------

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


# --- the human send gate ----------------------------------------------------

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

    is_form = request.headers.get("content-type", "").startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data"))
    payload = dict(await request.form()) if is_form else await _maybe_json(request)

    actor = (payload.get("actor") or "reviewer").strip() or "reviewer"
    body = payload.get("body") or run["followup_draft"]

    sent_at = store.mark_followup_sent(run_id, body)
    store.add_event(run_id, "communicate", "followup_sent", actor=f"user:{actor}",
                    detail={"actor": actor, "edited": body != run["followup_draft"]})

    if is_form:
        return RedirectResponse(f"/run/{run_id}", status_code=303)
    return {"run_id": run_id, "sent": True, "sent_at": sent_at, "sent_by": actor}


async def _maybe_json(request: Request) -> dict:
    try:
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


# --- dashboard --------------------------------------------------------------

@app.get("/dashboard")
def dashboard(request: Request, status: str | None = None):
    status = status.upper() if status else None
    if status not in (None, *STATUSES):
        status = None
    all_runs = store.list_runs()
    runs = [r for r in all_runs if r["status"] == status] if status else all_runs
    stats = {
        "total": len(all_runs),
        "approved": sum(r["status"] == "APPROVED" for r in all_runs),
        "pending": sum(r["status"] == "PENDING" for r in all_runs),
        "rejected": sum(r["status"] == "REJECTED" for r in all_runs),
        "error": sum(r["status"] == "ERROR" for r in all_runs),
    }
    return templates.TemplateResponse(request, "dashboard.html", {
        "runs": runs, "stats": stats, "active": status, "statuses": STATUSES,
        "cases": store.list_cases(),
    })


@app.post("/reset")
def reset():
    store.reset_all()
    return RedirectResponse("/dashboard", status_code=303)
