# 07 · Architecture

## Stack

| Layer | Choice | Why |
|---|---|---|
| Web | **FastAPI** | One process, background tasks built in, no boilerplate |
| Storage | **SQLite** (`sqlite3`, stdlib) | Zero setup, single file, ships with Python. No ORM. |
| Templates | **Jinja2** | Server-rendered, no build step |
| Interactivity | **HTMX** (CDN) | Live run view via one HTML attribute. No bundler, no npm. |
| Styling | **Tailwind** (CDN) | Looks deliberate in ~40 lines of class names |
| AI | **Anthropic SDK** (`claude-opus-5`) | Native PDF input, structured outputs |
| Tests | **pytest** | One file |

`uvicorn app:app --reload` and it runs. No build step, no npm, no Docker, no queue, no ORM, no migrations. Under live-demo conditions that is not a preference, it is risk management: every extra process is one more thing that can fail to start in front of an interviewer.

## File structure

```
vendor-onboarding/
├── app.py            FastAPI routes + background task kickoff
├── pipeline.py       the 7 stages; writes events as it goes
├── rules.py          the 12 rules + decide(); pure, zero I/O
├── extract.py        Claude document extraction
├── matching.py       normalize + difflib + LLM escalation
├── store.py          sqlite3 access; 3 tables
├── templates/
│   ├── base.html
│   ├── submit.html
│   ├── run.html          live run view + detail + audit + email draft
│   ├── dashboard.html
│   └── _stages.html      HTMX polling fragment
├── samples/
│   ├── ec1_happy.json  ec2_incomplete.json
│   ├── ec3_bank_mismatch.json  ec4_crossfield.json
│   └── pdfs/            generated sample documents
├── docs/             this directory
├── test_rules.py
├── requirements.txt
├── .env.example      ANTHROPIC_API_KEY=
└── README.md
```

**Six Python modules, five templates.** No `adapters.py`, no `fixtures/` — dropping duplicate detection and sanctions screening removed the entire seeded-state layer. That is the largest single simplification in the design.

## Component responsibilities

| Module | Owns | Must NOT |
|---|---|---|
| `app.py` | HTTP routes, file upload handling, kicking off the background run, rendering templates | contain any validation logic |
| `pipeline.py` | Stage sequencing, event emission, status write-back, error containment | contain any rule logic |
| `rules.py` | The 12 rules + `decide()`. **Pure functions.** | import `anthropic`, `httpx`, `sqlite3`, or read the clock |
| `extract.py` | PDF/image to structured JSON via Claude | interpret or validate what it extracted |
| `matching.py` | Name normalization, similarity scoring, ambiguous-band escalation | emit findings or decide severity |
| `store.py` | All SQL. Insert/read runs, findings, events | contain business logic |
| `templates/` | Presentation | compute anything |

The strict rule is **`rules.py` imports nothing outside the stdlib and `dataclasses`.** That single constraint is what makes `test_rules.py` twenty asserts with no fixtures, no mocking, and no network — and it is what structurally prevents AI from reaching the decision.

## Data flow

```
POST /submit  (form fields + 3 files)
    |
    +--> store.create_run()                -> run_id, status=RUNNING
    +--> save uploads to uploads/<run_id>/
    +--> BackgroundTasks(pipeline.run, run_id)
              |
              |  each stage:  store.add_event(stage_started)
              |               ... work ...
              |               store.add_findings(...)
              |               store.add_event(stage_completed, duration_ms)
              |
              +- [1] intake        (snapshot already persisted)
              +- [2] completeness  -> rules R01, R02
              +- [3] extraction    -> extract.py -> store.set_extracted()
              |                       + add_event(ai_call, model/response/usage)
              +- [4] format        -> rules R03-R05
              +- [5] consistency   -> rules R06-R12  (matching.py may call Claude)
              +- [6] decision      -> rules.decide() -> store.set_status()
              |                       + add_event(decision, rule_ids)
              +- [7] communicate   -> draft email if PENDING -> store.set_draft()

GET  /                    submit form
POST /submit              create run, redirect to /run/{id}
GET  /run/{id}            full run page
GET  /run/{id}/stages     HTMX fragment, polled every 700 ms   <- live run view
GET  /run/{id}/export     the whole run as JSON
POST /run/{id}/send       writes followup_sent event (human gate)
GET  /dashboard           history across runs
POST /reset               wipe runs/findings/events + uploads
```

## Live run view without WebSockets

The pipeline runs in `BackgroundTasks`, writing an event row per completed stage. The page polls with a single attribute:

```html
<div hx-get="/run/{{ run_id }}/stages" hx-trigger="every 700ms" hx-swap="innerHTML">
```

When the run reaches a terminal status the fragment stops emitting the polling attribute and HTMX stops on its own. No SSE, no WebSocket, no reconnect logic, no streaming code.

**A deliberate ~400 ms pause per stage.** A run that completes in 80 ms looks broken on video — stages flash from empty to done with nothing visible in between. The pause is a demo requirement, not padding, and it is marked in code:

```python
# ponytail: 400ms/stage so the run view is legible on camera. Drop for production.
```

## Concurrency and state

Single process, single SQLite file, `check_same_thread=False`, WAL mode. One reviewer, a handful of runs. Locking, connection pooling, and a real job queue are all deferred — see `10-assumptions-and-scope.md`.

## Error containment

Each stage is wrapped. On exception: write `stage_failed` with the error, set the run to `ERROR`, stop. The run never falls through to a status. `ERROR` and `REJECTED` are rendered differently on the dashboard — "our extractor crashed" and "this vendor is not credible" must never look the same.

## Configuration

One `.env` with `ANTHROPIC_API_KEY`. Thresholds (0.75 / 0.92 / 0.70) are module constants in `matching.py`, not config — they are tuned once against the sample set in Part 4 and then left alone. A settings file for four numbers nobody will change is exactly the abstraction this MVP is avoiding.
