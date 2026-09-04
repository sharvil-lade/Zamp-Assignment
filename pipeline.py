"""Stage sequencing, event emission and error containment.

Contains no rule logic — it only orders the stages defined in
docs/01-solution-overview.md and hands findings to store.py.

Implemented so far: 1 intake · 2 completeness · 4 format · 5 consistency · 6 decision.
Stage 3 (extraction) arrives in Part 3; stage 7 (communicate) in Part 5.
"""

import re
import time
import traceback
from datetime import date

import extract
import matching
import rules
import store

# Default 0 so the test suite is not slowed. The UI passes DEMO_PAUSE_S so the
# live run view is legible on camera — a run that finishes in 80 ms looks broken
# on video, stages flashing from empty to done with nothing visible between.
STAGE_PAUSE_S = 0.0
DEMO_PAUSE_S = 0.4

# The canonical stage list. `ai` is True where a model is always used, False
# where one never is, and None for consistency — which calls a model only when a
# name comparison lands in the ambiguous band, so the UI badges it per run.
STAGES = (
    ("intake", "Intake", False),
    ("completeness", "Completeness", False),
    ("extraction", "Extraction", True),
    ("format", "Format & checksum", False),
    ("consistency", "Consistency", None),
    ("decision", "Decision", False),
    ("communicate", "Communicate", True),
)


# Defence in depth: provider errors are echoed into the audit trail, so scrub
# anything key-shaped before it is persisted or rendered.
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def _safe_error(exc: Exception) -> str:
    return _SECRET_RE.sub("sk-***REDACTED***", f"{type(exc).__name__}: {exc}")[:500]


def _run_stage(run_id: str, stage: str, pause: float, work) -> list[rules.Finding]:
    """Emit started/completed around `work`, persist whatever findings it returns."""
    store.add_event(run_id, stage, "stage_started")
    t0 = time.perf_counter()
    if pause:
        time.sleep(pause)
    found = work()
    store.add_findings(run_id, found)
    store.add_event(run_id, stage, "stage_completed",
                    detail={"findings_added": len(found)},
                    duration_ms=int((time.perf_counter() - t0) * 1000))
    return found


def _document_presence(run_id: str) -> dict | None:
    """Which documents were attached, in the shape R02 already understands.

    `{}` means attached, `None` means not. No AI, no file parsing — just the
    directory listing. Returns None when the run carried no documents at all.
    """
    if not store.upload_dir(run_id).exists():
        return None
    files = store.saved_documents(run_id)
    return {d: ({} if d in files else None) for d in extract.DOC_TYPES}


def _extraction_stage(run_id: str, pause: float, extract_fn) -> dict | None:
    """Stage 3. Returns the extracted map, or None when the run carried no documents.

    A JSON-only submission never creates an upload directory, so extraction is
    skipped and `extracted` stays None — R02 then stays silent under skip
    semantics. A multipart submission always creates the directory, so every
    unattached document is reported.
    """
    if not store.upload_dir(run_id).exists():
        return None

    store.add_event(run_id, "extraction", "stage_started")
    t0 = time.perf_counter()
    if pause:
        time.sleep(pause)

    files = store.saved_documents(run_id)
    extracted: dict = {}
    for doc_type in extract.DOC_TYPES:
        path = files.get(doc_type)
        if path is None:
            extracted[doc_type] = None
            continue
        data, meta = extract_fn(path, doc_type)
        extracted[doc_type] = data
        # The audit record of an AI-assisted decision: exact model, exact
        # response, token usage (docs/02-data-model.md).
        store.add_event(run_id, "extraction", "ai_call", detail=meta)

    store.set_extracted(run_id, extracted)
    store.add_event(run_id, "extraction", "stage_completed",
                    detail={"documents_read":
                            sum(1 for v in extracted.values() if v)},
                    duration_ms=int((time.perf_counter() - t0) * 1000))
    return extracted


def _actionable(findings) -> list[dict]:
    """FIX findings a vendor can actually do something about.

    `ai_uncertain` findings are excluded on purpose: the comparator was unsure and
    routed the case to a *human reviewer*, not to the vendor. Asking a vendor to
    resolve our own uncertainty — and exposing model confidence to them — is the
    wrong message. A run whose only FIX findings are uncertain gets no draft.
    """
    return [{"message": _vendor_message(f), "expected": f.expected,
             "actual": f.actual}
            for f in findings
            if f.severity == rules.FIX and f.tag != "ai_uncertain"]


def _vendor_message(finding) -> str:
    """Reviewer wording out, vendor wording in.

    R01 names the raw form field — right for a reviewer reading the run page,
    wrong in an email to a vendor who has never seen our field names.
    """
    if finding.rule_id == "R01" and "'" in finding.message:
        field = finding.message.split("'")[1]
        label = rules.FIELD_LABELS.get(field, field.replace("_", " "))
        return f"The {label} was left blank on the form."
    return finding.message


def _communicate_stage(run_id: str, status: str, findings, submission: dict,
                       pause: float, draft_fn) -> None:
    """Stage 7. Drafts for PENDING only; REJECTED gets an internal note instead.

    Never fatal. The decision is already made and persisted by the time this runs
    — losing a convenience email must not discard a correct, durable decision.
    """
    store.add_event(run_id, "communicate", "stage_started")
    t0 = time.perf_counter()
    if pause:
        time.sleep(pause)

    try:
        if status == "REJECTED":
            # No vendor-facing message. Telling a suspected fraudster which check
            # caught them is a real-world anti-pattern (docs/05-ai-design.md).
            blocks = [f for f in findings if f.severity == rules.BLOCK]
            store.add_event(run_id, "communicate", "internal_note", detail={
                "reason": "rejected — no vendor-facing message drafted",
                "blocking_rules": sorted({f.rule_id for f in blocks}),
                "note": "; ".join(f.message for f in blocks),
            })
            outcome = "internal_note"

        elif status == "PENDING" and (items := _actionable(findings)):
            text, meta = draft_fn(run_id,
                                  submission.get("legal_entity_name") or "",
                                  submission.get("contact_name") or "",
                                  items)
            store.set_draft(run_id, text)
            store.add_event(run_id, "communicate", "ai_call", detail=meta)
            outcome = "draft_created"

        else:
            outcome = "no_communication_needed"

        store.add_event(run_id, "communicate", "stage_completed",
                        detail={"outcome": outcome},
                        duration_ms=int((time.perf_counter() - t0) * 1000))
    except Exception as exc:
        store.add_event(run_id, "communicate", "stage_failed", detail={
            "error": _safe_error(exc),
            "note": "decision already persisted; only the draft was lost",
        })


def run(run_id: str, *, today: date | None = None, names_match=None,
        extract_fn=None, draft_fn=None, pause: float = STAGE_PAUSE_S) -> str:
    """Execute the pipeline for one run. Returns the final status."""
    t0 = time.perf_counter()
    run_row = store.get_run(run_id)
    if run_row is None:
        raise KeyError(f"unknown run {run_id}")

    submission = run_row["submission"]
    extracted = run_row["extracted"]
    if names_match is None:
        # matching.py must not touch store, so its model calls are reported back
        # through a callback the pipeline owns. Same shape as an extraction
        # ai_call, so the audit timeline renders both identically.
        #
        # Memoised per run: R09 and R12 often compare the same two strings, and
        # asking twice costs a second call and puts a duplicate entry in the
        # audit trail. A cache hit emits no event, which is correct — only real
        # calls belong in the record.
        seen: dict = {}

        def names_match(a, b):
            if (a, b) not in seen:
                seen[(a, b)] = matching.names_match(
                    a, b, on_ai_call=lambda meta: store.add_event(
                        run_id, "consistency", "ai_call", detail=meta))
            return seen[(a, b)]

    ctx = rules.RuleContext(today=today or date.today(), names_match=names_match)
    extract_fn = extract_fn or extract.extract_document
    findings: list[rules.Finding] = []
    current = "intake"

    try:
        _run_stage(run_id, "intake", pause, lambda: [])

        # Completeness runs before extraction, as documented. "Was this document
        # attached?" is a file-presence question — it must not wait on three API
        # calls to tell a vendor they forgot an attachment. R02 is handed a
        # presence map with the same shape as `extracted`, so the rule is
        # unchanged and never sees AI output.
        current = "completeness"
        presence = _document_presence(run_id)
        findings += _run_stage(run_id, current, pause, lambda: rules.apply(
            rules.COMPLETENESS_RULES, submission, presence, ctx))

        current = "extraction"
        extracted = _extraction_stage(run_id, pause, extract_fn)

        current = "format"
        findings += _run_stage(run_id, current, pause, lambda: rules.apply(
            rules.FORMAT_RULES, submission, extracted, ctx))

        current = "consistency"
        findings += _run_stage(run_id, current, pause, lambda: rules.apply(
            rules.CONSISTENCY_RULES, submission, extracted, ctx))

        current = "decision"
        status = rules.decide(findings)
        store.add_event(run_id, current, "decision", detail={
            "status": status,
            "block_count": sum(f.severity == rules.BLOCK for f in findings),
            "fix_count": sum(f.severity == rules.FIX for f in findings),
            "rule_ids": sorted({f.rule_id for f in findings}),
        })
        # Persist the decision before stage 7 runs. Communication is downstream of
        # the decision and must never be able to change or delay it.
        store.set_status(run_id, status)

        current = "communicate"
        _communicate_stage(run_id, status, findings, submission, pause,
                           draft_fn or extract.draft_followup)
    except Exception as exc:
        # ERROR is not REJECTED. "Our pipeline crashed" and "this vendor is not
        # credible" are different facts (docs/04-decision-engine.md).
        store.add_event(run_id, current, "stage_failed", detail={
            "error": _safe_error(exc),
            "traceback_head": _SECRET_RE.sub(
                "sk-***REDACTED***", traceback.format_exc().splitlines()[-1])[:300],
        })
        elapsed = int((time.perf_counter() - t0) * 1000)
        store.set_status(run_id, "ERROR", elapsed)
        store.add_event(run_id, "run", "run_finished",
                        detail={"status": "ERROR"}, duration_ms=elapsed)
        return "ERROR"

    elapsed = int((time.perf_counter() - t0) * 1000)
    store.set_status(run_id, status, elapsed)
    # The terminal marker. The status is set before stage 7 so the decision is
    # durable the moment it is made, which means "status is terminal" is NOT the
    # same as "the pipeline has finished" — the live view polls on this event
    # instead, or it stops watching while the follow-up is still being drafted.
    store.add_event(run_id, "run", "run_finished",
                    detail={"status": status}, duration_ms=elapsed)
    return status
