"""Stage sequencing, event emission and error containment.

Contains no rule logic — it only orders the stages defined in
docs/01-solution-overview.md and hands findings to store.py.

Implemented so far: 1 intake · 2 completeness · 4 format · 5 consistency · 6 decision.
Stage 3 (extraction) arrives in Part 3; stage 7 (communicate) in Part 5.
"""

import time
import traceback
from datetime import date

import rules
import store

# ponytail: 0 for now. Part 6 raises this to 0.4 so the live run view is
# legible on camera; a run that finishes in 80 ms looks broken on video.
STAGE_PAUSE_S = 0.0


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


def run(run_id: str, *, today: date | None = None, names_match=None,
        pause: float = STAGE_PAUSE_S) -> str:
    """Execute the pipeline for one run. Returns the final status."""
    t0 = time.perf_counter()
    run_row = store.get_run(run_id)
    if run_row is None:
        raise KeyError(f"unknown run {run_id}")

    submission = run_row["submission"]
    extracted = run_row["extracted"]        # None until Part 3 populates it
    ctx = rules.RuleContext(today=today or date.today(), names_match=names_match)
    findings: list[rules.Finding] = []
    current = "intake"

    try:
        _run_stage(run_id, "intake", pause, lambda: [])

        current = "completeness"
        findings += _run_stage(run_id, current, pause, lambda: rules.apply(
            rules.COMPLETENESS_RULES, submission, extracted, ctx))

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
    except Exception as exc:
        # ERROR is not REJECTED. "Our pipeline crashed" and "this vendor is not
        # credible" are different facts (docs/04-decision-engine.md).
        store.add_event(run_id, current, "stage_failed", detail={
            "error": f"{type(exc).__name__}: {exc}",
            "traceback_head": traceback.format_exc().splitlines()[-1],
        })
        store.set_status(run_id, "ERROR", int((time.perf_counter() - t0) * 1000))
        return "ERROR"

    store.set_status(run_id, status, int((time.perf_counter() - t0) * 1000))
    return status
