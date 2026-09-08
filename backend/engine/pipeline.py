"""Stage sequencing, event emission and error containment.

Contains no rule logic — it only orders the stages defined in
docs/01-solution-overview.md and hands findings to store.py.

Eight stages: 1 intake · 2 completeness · 3 extraction · 4 format ·
5 consistency · 6 decision · 7 Onboarding Assistant review · 8 communicate.
"""

import re
import time
import traceback
from datetime import date

from ai import employee as ai_employee
from ai import extract
from engine import forms
from ai import matching
from engine import policy
from engine import rules
from data import store

# Zero: nothing waits on purpose. The parameter survives so a test can slow a
# run down and watch the stages land in order.
STAGE_PAUSE_S = 0.0

# The canonical stage list. `ai` is True where a model is always used, False
# where one never is, and None for consistency — which calls a model only when a
# name comparison lands in the ambiguous band, so the UI badges it per run.
STAGES = (
    ("intake", "Submission received", False),
    ("completeness", "Checking completeness", False),
    ("extraction", "Extracting documents", True),
    ("format", "Validating formats", False),
    ("consistency", "Cross-checking information", None),
    ("decision", "Decision", False),
    ("review", "Onboarding Assistant review", True),
    ("communicate", "Preparing communication", False),
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


def _rule_stage(run_id: str, stage: str, pause: float, submission: dict,
                extracted, ctx, outcomes: list, extra=()) -> list[rules.Finding]:
    """Run one stage's rules, persist their findings, and keep every outcome.

    `evaluate` reports the passes and the skips as well as the failures, which
    is the only way the run page can say "14 of 17 checks passed" without
    guessing. `extra` carries findings that no registered rule produced - custom
    form fields, which have no PS-2 rule behind them.
    """
    def work():
        stage_outcomes = rules.evaluate(rules.rules_for_stage(stage),
                                        submission, extracted, ctx)
        outcomes.extend(stage_outcomes)
        return [f for o in stage_outcomes for f in o.findings] + list(extra)

    return _run_stage(run_id, stage, pause, work)


def _document_presence(run_id: str) -> dict | None:
    """Which documents were attached, in the shape R02 already understands.

    `{}` means attached, `None` means not. No AI, no file parsing — just the
    directory listing. Returns None when the run carried no documents at all.
    """
    if not store.has_document_channel(run_id):
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
    if not store.has_document_channel(run_id):
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


def _review_stage(run_id: str, status: str, findings, submission: dict,
                  extracted, pause: float, review_fn=None,
                  custom_answers: dict | None = None,
                  schema: dict | None = None) -> None:
    """Stage 7. The Onboarding Assistant reads the decided run and briefs the reviewer.

    Runs *after* the decision and receives it as a fact. Nothing it returns can
    change a status — `decide()` has already run and its result is persisted.
    Never fatal: an advisory briefing is not worth losing a correct decision.
    """
    store.add_event(run_id, "review", "stage_started")
    t0 = time.perf_counter()
    if pause:
        time.sleep(pause)

    def record(meta, failed=False):
        meta.setdefault("actor", "ai_employee")
        store.add_event(run_id, "review", "capability_failed" if failed
                        else "ai_call", actor="ai_employee", detail=meta)

    result = ai_employee.run_review(
        run_id, submission.get("legal_entity_name") or "", status,
        [f.__dict__ for f in findings],
        rules.compare_documents(submission, extracted),
        on_capability=record, review_fn=review_fn,
        custom_answers=custom_answers, schema=schema)

    if result is not None:
        store.add_event(run_id, "review", "ai_summary", actor="ai_employee",
                        detail=result.as_dict())
    store.add_event(run_id, "review", "stage_completed",
                    detail={"outcome": "briefed" if result else "unavailable",
                            "risk": result.risk if result else None},
                    duration_ms=int((time.perf_counter() - t0) * 1000))


def _communicate_stage(run_id: str, status: str, findings, submission: dict,
                       pause: float) -> None:
    """Stage 8. Reopens the case for PENDING; REJECTED gets an internal note.

    Never fatal. The decision is already made and persisted by the time this runs
    — losing the reopen must not discard a correct, durable decision.
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

        elif status == "PENDING" and (items := policy.correction_items(findings)):
            # Record what the vendor would have to fix, but do not reopen
            # their form. A decision landing at 2am must not silently make the
            # case submittable again — a reviewer reads the findings first and
            # opens the form deliberately.
            case = store.get_case_for_run(run_id)
            if case is not None:
                store.add_event(run_id, "communicate", "correction_requested",
                                detail={"case_id": case["id"],
                                        "items": [i["text"] for i in items]})
            outcome = ("correction_requested" if case is not None
                       else "no_communication_needed")

        else:
            outcome = "no_communication_needed"

        store.add_event(run_id, "communicate", "stage_completed",
                        detail={"outcome": outcome},
                        duration_ms=int((time.perf_counter() - t0) * 1000))
    except Exception as exc:
        store.add_event(run_id, "communicate", "stage_failed", detail={
            "error": _safe_error(exc),
            "note": "decision already persisted; only the reopen was lost",
        })


def run(run_id: str, *, today: date | None = None, names_match=None,
        extract_fn=None, review_fn=None,
        pause: float = STAGE_PAUSE_S) -> str:
    """Execute the pipeline for one run. Returns the final status."""
    t0 = time.perf_counter()
    run_row = store.get_run(run_id)
    if run_row is None:
        raise KeyError(f"unknown run {run_id}")

    submission = run_row["submission"]
    extracted = run_row["extracted"]
    # A case created from a form template carries a snapshot of that exact
    # schema. Standard PS-2 cases have no custom fields, so everything below is
    # a no-op for them and their behaviour is unchanged.
    case = store.get_case_for_run(run_id)
    schema = (case or {}).get("form_schema")
    custom_answers = submission.get("_custom") or {}
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
    extract_fn = extract_fn or ai_employee.extract_document
    findings: list[rules.Finding] = []
    outcomes: list[rules.RuleOutcome] = []
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
        custom = (forms.custom_field_findings(schema, custom_answers,
                                              (presence or {}).keys())
                  if schema else [])
        findings += _rule_stage(run_id, rules.STAGE_COMPLETENESS, pause,
                                submission, presence, ctx, outcomes, custom)

        current = "extraction"
        extracted = _extraction_stage(run_id, pause, extract_fn)

        current = "format"
        findings += _rule_stage(run_id, rules.STAGE_FORMAT, pause,
                                submission, extracted, ctx, outcomes)

        current = "consistency"
        findings += _rule_stage(run_id, rules.STAGE_CONSISTENCY, pause,
                                submission, extracted, ctx, outcomes)

        current = "decision"
        status = policy.decide(findings)
        # The full check register: every rule, whether it ran, and what it found.
        # Persisted rather than recomputed, because re-running the rules on a
        # GET would need the name comparator again - and that can call a model.
        store.add_event(run_id, current, "checks_evaluated", detail={
            "checks": [o.as_record() for o in outcomes],
            "summary": rules.summarize(outcomes, findings),
        })
        store.add_event(run_id, current, "decision", detail={
            "status": status,
            "block_count": sum(f.severity == rules.BLOCK for f in findings),
            "fix_count": sum(f.severity == rules.FIX for f in findings),
            "rule_ids": sorted({f.rule_id for f in findings}),
            # Written by the engine, not by a model. The run page can explain a
            # decision even on a run where the Onboarding Assistant was unavailable.
            "explanation": policy.explain(status, findings),
        })
        # Persist the decision before stage 7 runs. Communication is downstream of
        # the decision and must never be able to change or delay it.
        store.set_status(run_id, status)

        current = "review"
        _review_stage(run_id, status, findings, submission, extracted, pause,
                      review_fn=review_fn, custom_answers=custom_answers,
                      schema=schema)

        current = "communicate"
        _communicate_stage(run_id, status, findings, submission, pause)
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
    # instead, or it stops watching while the review and reopen still run.
    store.add_event(run_id, "run", "run_finished",
                    detail={"status": status}, duration_ms=elapsed)
    return status
