"""View-model helpers shared by the API routers.

These exist so the same shape is produced everywhere and so React never has to
derive a label, a status word or a timestamp for itself.
"""

from datetime import datetime

from engine import pipeline
from engine import rules
from data import store

STAGE_LABELS = {key: label for key, label, _ in pipeline.STAGES}

STATUS_LABELS = {
    "APPROVED": "Approved", "PENDING": "Pending", "REJECTED": "Rejected",
    "ERROR": "AI Error", "RUNNING": "Processing",
    store.AWAITING_VENDOR: "Awaiting vendor", store.PROCESSING: "Processing",
}

# What each stage actually produced, in a reviewer's words rather than a
# payload key. Derived from the persisted event detail - never recomputed.
STAGE_RESULTS = {
    "intake": "Submission received",
    "completeness": "Required fields and documents checked",
    "extraction": "Documents read",
    "format": "Identifiers and documents validated",
    "consistency": "Submission cross-checked against documents",
    "decision": "Decision produced",
    "review": "Onboarding Assistant briefing",
    "communicate": "Communication prepared",
}

ACTIVITY_LABELS = {
    "checks_evaluated": "Checks recorded",
    "correction_requested": "Correction requested",
    "documents_expected": "Documents received",
    "upload_rejected": "Attachment rejected",
    "vendor_submitted": "Vendor submitted",
    "stage_started": "Processing",
    "stage_completed": "Processing",
    "stage_failed": "Processing failed",
    "ai_call": "Onboarding Assistant working",
    "ai_summary": "Onboarding Assistant review",
    "capability_failed": "Onboarding Assistant unavailable",
    "decision": "Decision produced",
    "internal_note": "Internal note added",
    "run_finished": "Processing complete",
}


def format_timestamp(value: str | None) -> str:
    """`2026-09-05T14:14:07+00:00` -> `Sep 5, 2026 · 2:14 PM`."""
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "—"
    hour = moment.hour % 12 or 12
    meridiem = "AM" if moment.hour < 12 else "PM"
    return (f"{moment.strftime('%b')} {moment.day}, {moment.year} · "
            f"{hour}:{moment.minute:02d} {meridiem}")


def case_row(case: dict) -> dict:
    """One dashboard row. Same columns and same meaning as before."""
    awaiting = case["run_id"] is None
    status = case["run_status"] if not awaiting else store.AWAITING_VENDOR
    return {
        "case_id": case["id"],
        "run_id": case["run_id"],
        "vendor_name": case["vendor_name"],
        "status": status or store.PROCESSING,
        "status_label": STATUS_LABELS.get(status, status or "Processing"),
        "awaiting": awaiting,
        "finding_count": None if awaiting else case["finding_count"],
        "created_at": format_timestamp(case["created_at"]),
        "created_at_iso": case["created_at"],
        "last_activity_at": format_timestamp(case["last_activity_at"]),
        "last_activity_at_iso": case["last_activity_at"],
        "last_activity_label": ACTIVITY_LABELS.get(
            case.get("last_event_type"), "Case created" if awaiting else "Updated"),
    }


def run_row(run: dict) -> dict:
    """One row of run history: a single pipeline execution, never a case.

    A case with three submissions is three rows here and still one row in the
    case table above it.
    """
    case_id = run.get("case_id")
    return {
        "run_id": run["run_id"],
        "case_id": case_id,
        # Only meaningful for a run that belongs to a case; a run created
        # directly in the store has no journey to be part of.
        "submission_no": run.get("submission_no") if case_id else None,
        "is_latest": bool(case_id
                          and run.get("submission_no") == run.get("submission_total")),
        "vendor_name": run["vendor_name"],
        "status": run["status"],
        "status_label": STATUS_LABELS.get(run["status"], run["status"]),
        "finding_count": run["finding_count"],
        "created_at": format_timestamp(run["created_at"]),
        "duration_ms": run["duration_ms"],
    }


def decoded_events(run_id: str) -> list[dict]:
    import json
    return [{"id": e["id"], "ts": e["ts"], "ts_display": format_timestamp(e["ts"]),
             "stage": e["stage"], "event_type": e["event_type"], "actor": e["actor"],
             "label": ACTIVITY_LABELS.get(e["event_type"], e["event_type"]),
             "duration_ms": e["duration_ms"],
             "detail": json.loads(e["detail_json"]) if e["detail_json"] else None}
            for e in store.get_events(run_id)]


def stage_view(events: list[dict], finished: bool) -> list[dict]:
    """The processing list, derived entirely from persisted events."""
    seen: dict[str, dict] = {}
    for event in events:
        state = seen.setdefault(event["stage"], {
            "started": False, "done": False, "failed": False,
            "ms": None, "detail": None, "ai_calls": 0})
        kind = event["event_type"]
        if kind == "stage_started":
            state["started"] = True
        elif kind == "stage_completed":
            state.update(done=True, ms=event["duration_ms"], detail=event["detail"])
        elif kind == "stage_failed":
            state.update(failed=True, detail=event["detail"])
        elif kind == "ai_call":
            state["ai_calls"] += 1
        elif kind == "decision":
            state.update(started=True, done=True, detail=event["detail"])

    rows = []
    for key, label, ai in pipeline.STAGES:
        state = seen.get(key)
        if state is None:
            rows.append({"key": key, "label": label, "ai": bool(ai),
                         "state": "skipped" if (finished and key == "extraction")
                                  else "pending",
                         "ms": None, "detail": None})
            continue
        rows.append({
            "key": key, "label": label,
            "ai": bool(ai) or (ai is None and state["ai_calls"] > 0),
            "state": ("failed" if state["failed"] else "done" if state["done"]
                      else "running" if state["started"] else "pending"),
            "ms": state["ms"], "detail": state["detail"],
            "result": _stage_result(key, state),
        })
    return rows


def _stage_result(key: str, state: dict) -> str:
    """One line saying what this stage actually did on this run."""
    if state["failed"]:
        return "Failed - see the audit trail"
    if not state["done"]:
        return ""
    detail = state["detail"] or {}
    if "documents_read" in detail:
        n = detail["documents_read"]
        return f"{n} document{'' if n == 1 else 's'} read"
    if "status" in detail:
        return f"Decision: {detail['status']}"
    if "outcome" in detail:
        return str(detail["outcome"]).replace("_", " ").capitalize()
    if "findings_added" in detail:
        n = detail["findings_added"]
        return "No issues" if not n else f"{n} issue{'' if n == 1 else 's'} raised"
    return STAGE_RESULTS.get(key, "")


# ============================================================================
# THE DECISION VIEW
# ============================================================================
#
# Everything here is read back from what the pipeline persisted. Nothing is
# recomputed on a GET: re-running the rules would need the name comparator,
# which can call a model, so a page refresh could quietly change a decision.

def decision_view(run: dict, findings: list[dict], events: list[dict]) -> dict:
    """What was decided, in one line, with what to do about it."""
    status = run["status"]
    decided = next((e["detail"] for e in events
                    if e["event_type"] == "decision"), None)
    explanation = (decided or {}).get("explanation") or rules.explain(status, findings)
    return {
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        **explanation,
    }


def _evidence_rows(finding: dict, evidence) -> list[dict]:
    """The side-by-side a reviewer reads instead of `expected`/`actual`.

    `expected` always holds the evidence and `actual` what the vendor typed, so
    the labels come straight off the rule and never have to be guessed per rule.
    """
    expected, actual = finding.get("expected"), finding.get("actual")
    if not (expected and actual):
        return []
    left, right = evidence or ("Expected format", "Submitted value")
    return [{"source": left, "value": expected, "evidence": bool(evidence)},
            {"source": right, "value": actual, "evidence": False}]


def finding_view(finding: dict) -> dict:
    """A finding with the rule that raised it attached, ready to render."""
    rule = rules.BY_ID.get(finding["rule_id"])
    return {
        **{k: finding.get(k) for k in ("rule_id", "severity", "stage", "message",
                                       "expected", "actual", "tag")},
        "name": rule.name if rule else "Custom form field",
        "category": rule.category if rule else rules.CAT_COMPLETENESS,
        "purpose": rule.purpose if rule else
                   "Raised by a question on a custom onboarding form, which the "
                   "PS-2 rule engine has no business rule for.",
        "evidence_rows": _evidence_rows(finding, rule.evidence if rule else None),
    }


def vendor_url(request, token: str) -> str | None:
    """The case's one stable link. Built from the request, not configuration."""
    if not token:
        return None
    origin = request.headers.get("origin") or str(request.base_url).rstrip("/")
    return f"{origin.rstrip('/')}/vendor/onboard/{token}"


def correction_view(run: dict, case: dict | None, findings: list[dict],
                    request=None) -> dict:
    """Everything the run page needs to close the loop with the vendor.

    `items` is generated from this run's own FIX findings, so two PENDING runs
    never get the same list. The URL is the case's own, stable for its whole
    life — opening corrections moves a gate, it does not mint a link.

    Only a Pending run asks the vendor for anything. A rejected run can still
    carry FIX findings alongside its blocking ones, and listing them would offer
    a correction flow for a decision that is not correctable.
    """
    pending = run["status"] == "PENDING"
    open_now = bool(case and case["status"] == store.AWAITING_VENDOR) and pending
    return {
        "case_id": case["id"] if case else None,
        # The form is accepting a correction right now.
        "awaiting_correction": open_now,
        # It is not, but a reviewer may open it. Same case, same URL.
        "can_reopen": bool(case) and pending and not open_now,
        "status_label": ("Awaiting vendor correction" if open_now
                         else "Corrections not requested yet" if pending else None),
        "vendor_url": (vendor_url(request, case["token"])
                       if case and request else None),
        "items": rules.correction_items(findings) if pending else [],
    }


def rounds_view(case: dict | None, run_id: str) -> list[dict]:
    """The submission rounds for this case, so a reviewer can see it is a cycle.

    Empty for a case with a single run: there is nothing to switch between, and
    a one-tab switcher is just noise.
    """
    if case is None:
        return []
    runs = store.runs_for_case(case["id"])
    if len(runs) < 2:
        return []
    return [{"round": n, "run_id": r["run_id"], "status": r["status"],
             "status_label": STATUS_LABELS.get(r["status"], r["status"]),
             "finding_count": r["finding_count"],
             "created_at": format_timestamp(r["created_at"]),
             "current": r["run_id"] == run_id,
             "latest": n == len(runs)}
            for n, r in enumerate(runs, 1)]


def case_status_view(case: dict | None) -> dict | None:
    """Where the *case* stands right now.

    Deliberately independent of which run is on screen: reading submission 1 of
    3 must not make a reviewer think the vendor is still Pending when the case
    was approved on submission 3.
    """
    if case is None:
        return None
    latest = store.get_run(case["run_id"]) if case["run_id"] else None
    status = latest["status"] if latest else case["status"]
    return {"case_id": case["id"], "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "latest_run_id": case["run_id"]}


def checks_view(events: list[dict]) -> dict | None:
    """The full check register for this run, grouped into business categories.

    None when the run never reached the decision stage - an errored run has no
    register, and inventing one would be presenting a check that never ran.
    """
    detail = next((e["detail"] for e in events
                   if e["event_type"] == "checks_evaluated"), None)
    if not detail:
        return None

    grouped: dict = {}
    for check in detail["checks"]:
        grouped.setdefault(check["category"], []).append({
            **check,
            "findings": [{**f, "evidence_rows": _evidence_rows(f, check.get("evidence"))}
                         for f in check["findings"]],
        })

    return {
        "summary": detail["summary"],
        "categories": [{
            "name": name,
            "passed": sum(c["state"] == rules.PASSED for c in grouped[name]),
            "failed": sum(c["state"] == rules.FAILED for c in grouped[name]),
            "skipped": sum(c["state"] == rules.SKIPPED for c in grouped[name]),
            "checks": grouped[name],
        } for name in rules.CATEGORY_ORDER if name in grouped],
    }
