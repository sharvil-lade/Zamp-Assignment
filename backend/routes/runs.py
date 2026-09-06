"""Run detail: status, findings, extracted data, AI summary, audit trail."""

from fastapi import APIRouter, Depends, HTTPException, Request

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
        "case_status": case_status_view(case),
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


