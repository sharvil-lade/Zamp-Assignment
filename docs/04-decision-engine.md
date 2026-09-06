# 04 · Decision Engine

## The whole thing

```python
def decide(findings: list[Finding]) -> str:
    if any(f.severity == "BLOCK" for f in findings):
        return "REJECTED"
    if any(f.severity == "FIX" for f in findings):
        return "PENDING"
    return "APPROVED"
```

Three lines. This is the entire decision layer.

## Mapping

| Condition | Status | Operational meaning |
|---|---|---|
| At least one `BLOCK` | **REJECTED** | Credibility failure. Not payable. No vendor-facing fix list. |
| No `BLOCK`, at least one `FIX` | **PENDING** | Recoverable. Follow-up email drafted, human-gated. |
| No findings | **APPROVED** | Payable. |

Precedence is BLOCK over FIX: a submission with one fraud signal and four missing fields is Rejected, not Pending. You do not ask a suspect entity to complete their paperwork.

## Why the decision stays deterministic

**1. The brief demands visible reasoning.** *"produces a clear status as output — approved, pending, or rejected — with the reasoning visible."* A rule that fired **is** the reasoning. A model's explanation of its own output is a post-hoc narrative, not a cause.

**2. The demo must be reproducible.** The same input produces byte-identical output every run. Under live-demo conditions, on camera, being asked to run something twice, this is not a nice-to-have.

**3. It is auditable and re-runnable.** Because `decide()` reads only stored findings, a rule change can be re-evaluated against every historical run without re-reading a PDF or spending a token.

**4. It is testable without mocking.** The rule tests assert on statuses with no fixtures, no network, no model. Sub-second, every time.

**5. It is explainable to a non-technical buyer** — one of the three stated grading criteria. "It was rejected because the PAN embedded in the GSTIN doesn't match the PAN they typed" is a sentence a procurement lead acts on. "The model scored it 0.31" is not.

**6. A model in this position would be strictly worse.** It adds latency, cost, and non-determinism to checks that already have exactly one right answer.

## The boundary is structural, not a policy

Enforced by the file layout, not by discipline:

- `rules.py` — **no network imports**, no I/O, no clock. Pure functions only.
- `decide(findings) -> str` — its entire input is a list of findings. It cannot see the submission, the documents, or any model output.
- The model's only path into the decision is **as a finding**, produced by a rule that chose to emit one, with a severity the rule chose.

The model *cannot* determine the status. That is a property of the code, and it is a far stronger interview answer than "I chose not to let it."

## What was deliberately not built

**No risk score.** A weighted 0–100 producing "73" cannot be explained to a procurement lead, and it directly contradicts the visible-reasoning requirement. A named list of failed rules is more useful and less code. This is a deliberate choice, not an omission — have that answer ready.

**No risk tiers, no conditional approval, no `APPROVED_PENDING_REVIEW`.** An earlier draft had spend thresholds and cross-border triggers gating approval. Cut: it added a fourth status, a config table, and a reviewer queue to express something the BLOCK/FIX severity already expresses.

**No reviewer override / maker-checker.** Deferred — see `10-assumptions-and-scope.md`. The seam is a new event type on the existing append-only log; nothing in `decide()` would change.

## Failure mode: `ERROR` is not `REJECTED`

If a stage raises, the run ends in `ERROR` with a `stage_failed` event carrying the exception. It never falls through to a status.

"Our extractor crashed" and "this vendor is not credible" are different facts. Conflating them on a dashboard is how a real system quietly rejects good vendors. This is also the behaviour demonstrated in Part 7 hardening.
