# 12 · Submission Checklist

Verified against a clean checkout on 2026-09-05. Re-run before sending.

## Functional requirements (from the brief)

| Requirement | Where | Status |
|---|---|---|
| Takes a vendor submission as input | 14-field form + 3 uploads at `/` | done |
| Produces **Approved / Pending / Rejected** | `rules.decide()` | done |
| **Reasoning is visible** | Finding cards with rule id, severity, expected vs actual | done |
| **Communicates back what's needed** | Drafted follow-up on PENDING, human-gated | done |
| **2–4 non-trivial edge cases** | EC-2, EC-3, EC-4 (plus EC-1 happy path) | done |
| **Live run view**, each stage as it executes | 7 stages, HTMX polling at 700 ms | done |
| **Dashboard** — history, status, outputs | `/dashboard` with tiles + status filter | done |
| Runs live, repeatably, on demand | `uvicorn app:app`, `/reset` between takes | done |
| Explainable to a non-technical buyer | Plain-language findings; no scores | done |

## Demo scenarios

| Scenario | Expected | Verified |
|---|---|---|
| EC-1 Happy path | `APPROVED`, no findings, no draft | yes |
| EC-2 Missing + expired | `PENDING`, R01 R02 R11, dated draft | yes |
| EC-3 Bank beneficiary mismatch | `REJECTED`, R09, internal note, **no model call for matching** | yes |
| EC-4 Cross-field contradiction | `REJECTED`, R06 R07 R08 | yes |

- [x] All four load from the **Load sample** dropdown — no typing on camera
- [x] Fixture GSTINs carry a computed checksum (asserted by test)
- [x] No demo scenario escalates to the ambiguous-band model call (demo stability)
- [x] Run order rehearsed: EC-1 → EC-2 → EC-3 → EC-4 → dashboard → export

## Testing

- [x] `python -m pytest test_rules.py -q` → **224 passed**
- [x] Passes with `ANTHROPIC_API_KEY=""` — suite is fully offline
- [x] All 12 rules unit-tested (pass, fail and skip-semantics)
- [x] Decision precedence, GSTIN checksum, matching thresholds
- [x] AI failure → `ERROR`; AI uncertainty → `PENDING`; never a silent approval
- [x] Bad uploads, missing fields, invalid data, extraction and drafting failure
- [x] `draft ≠ sent`, duplicate send → 409, unknown run → 404
- [x] Reset, audit persistence, export completeness, stage ordering
- [x] Structural test: `rules.py` imports nothing outside the stdlib

## Documentation

- [x] `README.md` — problem, workflow, architecture, stack, setup, env vars, run,
      test, four scenarios, AI vs deterministic, limitations
- [x] `docs/00`–`docs/13` present and internally consistent
- [x] Field counts, rule IDs, stage names and statuses agree across all docs
- [x] Every doc link in the README resolves
- [x] Deviations from the original design recorded **in** the docs, with reasons

## Security and hygiene

- [x] `.gitignore` covers `.env`, `vendor.db*`, `uploads/`, caches, `server.log`
- [x] `.env.example` committed with a blank value; `.env` never committed
- [x] No API key in any shipped module, template or fixture (asserted by test)
- [x] Upload allow-list by extension **and** magic bytes; 10 MB cap
- [x] Bad attachment → `PENDING` + R02, never a crashed run
- [x] Path traversal impossible — filename discarded, run-id format enforced
- [x] Provider errors redacted (`sk-...` → `sk-***REDACTED***`) before persistence
- [x] Zero tracebacks, zero 500s, zero secrets in `server.log` across a full demo
- [x] Browsers get a readable error page; API clients get JSON

## Git status

- [x] Working tree clean apart from intended files
- [x] `vendor.db`, `uploads/`, `__pycache__/`, `.pytest_cache/`, `.env` untracked
- [x] Sample PDFs and fixture JSONs committed (the demo must work on a fresh clone)
- [x] `git status --porcelain` shows no ignored artefact

## Fresh-environment check

- [x] `pip install -r requirements.txt` from a clean interpreter
- [x] `python -m uvicorn app:app --port 8000` starts with no errors
- [x] `vendor.db` created automatically on first start
- [x] `/` and `/dashboard` return 200 with empty states
- [x] README commands work verbatim

## Recording and artefacts

- [ ] 5-minute demo video recorded (Loom or screen capture)
- [ ] Happy path shown running live
- [ ] At least one edge case shown running live
- [ ] Narration covers what each stage does and why
- [ ] Under 5:00
- [ ] Process link + video link sent to the hiring coordinator

Suggested cut, ~4:40 total:

| # | Beat | Time | The line |
|---|---|---|---|
| 0 | `/reset`, empty dashboard | 10 s | "Submission in, explained decision out." |
| 1 | EC-1 live run | 60 s | "Seven stages. Three use AI — badged. One decision function." |
| 2 | EC-2 → draft | 90 s | "Recoverable, so it writes the chase-up. A human sends it." |
| 3 | EC-3 → R09 | 90 s | "Everything's complete. Complete is not the same as legitimate." |
| 4 | EC-4 → R06/R07/R08 | 90 s | "Three contradictions from one 15-character string. No lookups." |
| 5 | Dashboard + Export JSON | 30 s | "This replaces 'whatever's in someone's inbox'." |

## Questions to have rehearsed

| Question | Answer lives in |
|---|---|
| "Why isn't the AI making the decision?" | `04-decision-engine.md` |
| "Where's sanctions screening?" | `10-assumptions-and-scope.md` |
| "What if the model is wrong?" | `05-ai-design.md` — uncertainty → Pending, never Rejected |
| "How would you deploy this?" | `13-deployment-plan.md` |
| "What would you build next?" | `10-assumptions-and-scope.md` deferred table |
