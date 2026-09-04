# 09 · Implementation Plan

Eight parts, executed strictly in order. **Do not start a part until the previous one meets its acceptance criteria.**

The sequence follows the case study's own guidance (`00-case-study-requirements.md`): map first, get the happy path working end to end, then add edge cases one at a time, then UI, then harden and rehearse.

| Part | Day | Theme |
|---|---|---|
| 0 | 1 | Map (this documentation set) |
| 1 | 2 | Foundation |
| 2 | 2 | Core decision engine |
| 3 | 3 | Document extraction |
| 4 | 3–4 | Consistency + AI boundary |
| 5 | 4 | Communication + audit |
| 6 | 5 | UI |
| 7 | 6 | Testing + hardening |
| 8 | 6 | Demo + submission |

---

## Part 0 · Map (complete)

**Goal:** every design decision written down before any code exists.

**Deliverable:** `docs/00` through `docs/11`.

**Acceptance:** all 12 docs exist and are internally consistent — the 14 fields, 3 documents, 12 rules, 7 stages, 4 scenarios, and 3 statuses agree everywhere they appear.

---

## Part 1 · Foundation

**Goal:** a running FastAPI app with a database and nothing to say.

**Files:** `requirements.txt` · `.env.example` · `store.py` · `app.py` · `templates/base.html` · `templates/dashboard.html`

**Functionality**
- `store.py`: `init_db()` creating the 3 tables from `02-data-model.md`; `create_run`, `get_run`, `list_runs`, `add_event`, `add_findings`, `set_status`, `set_extracted`, `set_draft`, `reset_all`
- `app.py`: FastAPI instance, Jinja2 templates, `GET /dashboard` rendering an empty table, `POST /reset`
- Run ID generator producing `VS-0001`, `VS-0002`, ...

**Acceptance**
- `uvicorn app:app` starts with no errors
- `GET /dashboard` returns 200 and renders an empty state
- `POST /reset` runs without error
- `vendor.db` is created with exactly 3 tables

**Checkpoint:** the app runs and persists. Nothing decides anything yet.

---

## Part 2 · Core decision engine

**Goal:** a JSON submission produces a correct status with no AI and no PDFs.

**Files:** `rules.py` (new) · `pipeline.py` (new) · `app.py` (add `POST /submit`, `GET /run/{id}`) · `templates/run.html` (minimal) · `test_rules.py` (new)

**Functionality**
- `Finding` dataclass
- All 12 rule functions. R09/R10/R11/R12 accept an `extracted` argument that is `None` for now — they return `None` under skip semantics, which is correct behaviour, not a stub.
- `gstin_checksum(gstin) -> str` implementing mod-36
- `decide(findings) -> str`
- `pipeline.run(run_id)` executing stages 1, 2, 4, 6 with event emission and error containment
- `POST /submit` accepting a JSON body (no file uploads yet)

**Acceptance**
- A clean submission returns `APPROVED`
- A submission with a blank field returns `PENDING`
- A submission with a GSTIN/PAN mismatch returns `REJECTED`
- `decide()` has no imports beyond stdlib reachable from it
- `test_rules.py` passes with zero network calls

**Checkpoint — required by the brief:** *a JSON submission can produce APPROVED / PENDING / REJECTED without AI or PDFs.* This is the insurance policy: from here on, something runs end to end.

---

## Part 3 · Document extraction

**Goal:** three PDFs become three structured JSON objects, reliably.

**Files:** `extract.py` (new) · `pipeline.py` (add stage 3) · `app.py` (multipart upload) · `samples/pdfs/` (generated)

**Functionality**
- Three JSON schemas from `02-data-model.md`
- `extract_document(path, doc_type) -> dict` using base64 `document` blocks, `output_config.format`, `effort: "low"`
- `ai_call` event written with model, raw response, and token usage
- Sample PDFs generated (write HTML, print to PDF) for all four scenarios
- Upload handling: files saved under `uploads/<run_id>/`

**Acceptance**
- All three sample PDFs extract with every expected field non-null
- A missing upload yields `extracted[key] is None` and no exception
- An API failure produces `stage_failed` and run status `ERROR`, never a decision
- `runs.extracted_json` is populated exactly once per run

**Checkpoint:** PDFs reliably become structured JSON, and the raw model response is visible in the events table.

---

## Part 4 · Consistency + AI boundary

**Goal:** all four demo scenarios produce exactly the expected findings and statuses.

**Files:** `matching.py` (new) · `rules.py` (activate R06–R12) · `pipeline.py` (add stage 5) · `samples/*.json` (finalise)

**Functionality**
- `normalize(name)` and `similarity(a, b)` using `difflib` — stdlib only
- `names_match(a, b) -> (bool, str)` with the three-band logic (0.92 / 0.75) and Claude escalation in between
- `confidence < 0.7` produces the `ai_uncertain` FIX path
- R06, R07, R08 wired to real GSTIN parsing; R09–R12 wired to extracted values
- Generate the four sample fixtures **using `gstin_checksum()`** so R04 does not fire spuriously

**Acceptance**

| Scenario | Status | Exact findings |
|---|---|---|
| EC-1 | `APPROVED` | none |
| EC-2 | `PENDING` | R01, R02, R11 — and **not** R12 |
| EC-3 | `REJECTED` | R09 only |
| EC-4 | `REJECTED` | R06, R07, R08 |

- EC-3 resolves deterministically with **no model call** (verify: no `ai_call` event for matching)
- Thresholds tuned once against the samples, then frozen

**Checkpoint — required by the brief:** *EC-1 through EC-4 produce the expected findings and statuses.* If a scenario produces the right status for the wrong reasons, it fails this checkpoint.

---

## Part 5 · Communication + audit

**Goal:** Pending runs draft a real follow-up; every run exports a complete record.

**Files:** `extract.py` (add the drafting call — it stays here so `extract.py` and `matching.py` remain the only Anthropic importers, per `05-ai-design.md`) · `pipeline.py` (add stage 7) · `app.py` (`/export`, `/send`) · `templates/run.html`

**Functionality**
- Drafting prompt receives vendor name + FIX findings only — never the raw submission
- Runs only when status is `PENDING`; `REJECTED` produces an internal note
- Editable draft in the UI, Copy button, Mark-as-sent writing a `followup_sent` event with actor
- `GET /run/{id}/export` returning submission + extracted + findings + events + status

**Acceptance**
- EC-2 produces a draft naming all three items with the correct expiry date
- EC-3 and EC-4 produce **no** vendor-facing draft
- Export JSON round-trips: every finding and event present
- `followup_sent` appears in the timeline after clicking, with the actor recorded

**Checkpoint:** the "communicate back what's needed" requirement is demonstrably met, and the audit trail is exportable.

---

## Part 6 · UI

**Goal:** the graded screens.

**Files:** `templates/submit.html` · `templates/run.html` (full) · `templates/_stages.html` · `templates/dashboard.html` · `app.py` (`/run/{id}/stages`)

**Functionality**
- Submit form with the 14 fields, 3 file inputs, and the **Load sample** dropdown
- Live run view: 7 stages with states, `[AI]` badges, per-stage timing, HTMX polling every 700 ms, polling stops on terminal status
- Finding cards with severity colour and expected/actual
- Extracted-vs-form comparison table with mismatches highlighted
- Audit timeline with expandable AI events
- Dashboard: 4 stat tiles, run table, status filter, ERROR styled distinctly
- Reset button

**Acceptance**
- Loading a sample from the dropdown pre-fills fields **and** attaches the documents
- Stages visibly appear one at a time on screen (400 ms pause in place)
- Polling stops when the run is terminal — verified in the network tab
- Identifiers render monospace; a one-character PAN difference is visible at a glance
- Dashboard shows all runs with correct statuses and counts

**Checkpoint:** a stranger can watch a run and understand what happened without narration.

---

## Part 7 · Testing + hardening

**Goal:** nothing produces a stack trace on screen.

**Files:** `test_rules.py` (complete) · `pipeline.py` · `app.py` · `templates/run.html`

**Functionality and cases**
- Unit tests for all 12 rules, including the five demoed only by unit test (R03, R04, R05, R10, R12)
- Status assertions for all four scenarios
- Deliberate failure injection: corrupt PDF · zero-byte upload · non-PDF file · empty form submit · missing `ANTHROPIC_API_KEY` · API timeout mid-extraction · malformed GSTIN reaching R06
- Every failure renders a readable error state and an `ERROR` status, never a traceback in the browser

**Acceptance**
- `pytest` passes, no network access
- Each injected failure produces a `stage_failed` event with a readable message
- `ERROR` never renders as `REJECTED` anywhere in the UI
- A second run immediately after a failure works normally

**Checkpoint:** the app survives being used wrong, on camera.

---

## Part 8 · Demo + submission

**Goal:** the two deliverables.

**Files:** `README.md` · `docs/10-assumptions-and-scope.md` (finalise) · the recording

**Functionality**
- README: setup, run instructions, architecture summary, the AI-vs-rules rationale, the deferred list
- Assumptions finalised from actual build decisions, not the plan
- Rehearse the run order aloud twice, timed
- Record: EC-1 60 s, EC-2 90 s, EC-3 90 s, EC-4 90 s, dashboard + export 30 s
- `/reset` between takes

**Acceptance**
- A clean clone plus `pip install -r requirements.txt` plus an API key runs the app
- The video is under 5:00, shows the happy path and at least one edge case, and is narrated
- Every doc still matches the code that shipped

**Checkpoint:** submission-ready. Day 7 is send-only; nothing new gets built.

---

## Standing rules for every part

1. State what is being built and what the checkpoint proves, before starting.
2. Summarise what changed and name the exact next part, after finishing.
3. **Update `docs/` in the same step as any implementation decision that contradicts them.** Documentation that drifts from the code is worse than no documentation, because it will be quoted in the interview.
4. Do not add React, Docker, Redis, Celery, Postgres, OCR infrastructure, live verification APIs, or sanctions screening.
5. Business rules stay pure and deterministic. AI never determines the final status.
6. Prefer readable code over abstractions. Twelve functions do not need a registry.
