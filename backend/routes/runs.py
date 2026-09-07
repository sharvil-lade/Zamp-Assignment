"""Run detail: status, findings, extracted data, AI summary, audit trail."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from engine import rules
from data import store
from auth import require_session

from ._shared import (STATUS_LABELS, case_status_view, checks_view,
                      correction_view, decision_view, decoded_events,
                      finding_view, format_timestamp, rounds_view,
                      stage_view)

router = APIRouter(prefix="/runs", tags=["runs"])


def _context(run_id: str) -> dict:
    run = store.get_run(run_id)
    if run is None:
        raise HTTPException(404, "That run does not exist.")
    events = decoded_events(run_id)
    return {"run": run, "findings": store.get_findings(run_id), "events": events,
            "finished": any(e["event_type"] == "run_finished" for e in events)}


def _submitted_view(run: dict, run_id: str) -> dict:
    """What the vendor actually sent, before any rule looked at it.

    Canonical fields are always present in a submission (blank where the form
    left them empty), so an unfilled field is shown as unfilled rather than
    silently dropped. Documents are listed from storage, not from the extraction
    result: a file that was attached but could not be read still arrived.
    """
    submission = run["submission"]
    return {
        "fields": [{"field": name,
                    "label": rules.FIELD_LABELS[name],
                    "value": submission.get(name) or None}
                   for name in rules.SUBMISSION_FIELDS],
        "documents": [{"key": doc_type,
                       "label": rules.DOCUMENT_LABELS.get(
                           doc_type, doc_type.replace("_", " "))}
                      for doc_type in store.document_types(run_id)],
    }


@router.get("/{run_id}")
def run_detail(run_id: str, request: Request,
               _: None = Depends(require_session)) -> dict:
    ctx = _context(run_id)
    run, findings, events = ctx["run"], ctx["findings"], ctx["events"]
    case = store.get_case_for_run(run_id)

    return {
        "run_id": run["run_id"],
        "vendor_name": run["vendor_name"],
        "status": run["status"],
        "status_label": STATUS_LABELS.get(run["status"], run["status"]),
        "finished": ctx["finished"],
        "created_at": format_timestamp(run["created_at"]),
        "duration_ms": run["duration_ms"],
        "case_id": case["id"] if case else None,
        # The correction cycle: what the vendor has to fix and whether a link
        # can be issued for them to do it.
        "correction": correction_view(run, case, findings, request),
        "rounds": rounds_view(case, run_id),
        # Where the case stands now, whichever run is being read.
        "case_status": case_status_view(case, run),
        "blocks": sum(f["severity"] == "BLOCK" for f in findings),
        "fixes": sum(f["severity"] == "FIX" for f in findings),
        "uncertain": sum(f["tag"] == "ai_uncertain" for f in findings),
        "stages": stage_view(events, ctx["finished"]),
        # The decision, in the engine's own words. Written at decision time and
        # read back, so it cannot drift from the status beside it.
        "decision": decision_view(run, findings, events),
        # Every rule, whether it ran, and what it found - not only the failures.
        "checks": checks_view(events),
        "findings": [finding_view(f) for f in findings],
        # The vendor's own answers and attachments, as received.
        "submitted": _submitted_view(run, run_id),
        "comparisons": rules.compare_documents(run["submission"], run["extracted"]),
        "has_documents": run["extracted"] is not None,
        "custom_answers": run["submission"].get("_custom") or {},
        "rejected_uploads": [e["detail"] for e in events
                             if e["event_type"] == "upload_rejected"],
        "ai_summary": next((e["detail"] for e in events
                            if e["event_type"] == "ai_summary"), None),
        "internal_note": next((e["detail"] for e in events
                               if e["event_type"] == "internal_note"), None),
        "events": events,
    }


@router.get("/{run_id}/documents/{doc_type}")
def run_document(run_id: str, doc_type: str,
                 _: None = Depends(require_session)) -> FileResponse:
    """The file the vendor attached, as attached.

    Served through the app rather than by a storage URL so the same session
    check applies to a document as to the run it belongs to. `doc_type` is only
    ever matched against the types storage reports for this run, so it cannot
    address anything outside it.
    """
    if store.get_run(run_id) is None:
        raise HTTPException(404, "That run does not exist.")
    path = store.document_path(run_id, doc_type)
    if path is None:
        raise HTTPException(404, "No such document on this run.")
    return FileResponse(path, filename=f"{doc_type}{path.suffix}",
                        content_disposition_type="inline")
