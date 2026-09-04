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
│   ├── run.html          page shell: header + #run-body
│   ├── _run_body.html    the polled fragment — stages, findings, comparison,
│   │                     timeline, draft box. Swapped whole, so polling stops
│   │                     by simply not re-rendering the hx-trigger.
│   └── dashboard.html
├── samples/
│   ├── make_pdfs.py     regenerates pdfs/ (reportlab)
│   ├── make_fixtures.py regenerates the 4 scenario JSONs (computes the GSTIN)
│   ├── ec1_happy.json  ec2_incomplete.json
│   ├── ec3_bank_mismatch.json  ec4_crossfield.json
│   └── pdfs/            6 generated sample documents
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
| `extract.py` | PDF/image to structured JSON via Claude; owns the 3 schemas and prompts | interpret or validate what it extracted, or touch `store` |
| `matching.py` | Name normalization, similarity scoring, ambiguous-band escalation; returns a `NameVerdict` | emit findings, decide severity, or touch `store` (its model calls are reported through an `on_ai_call` callback the pipeline owns) |
| `store.py` | All SQL. Insert/read runs, findings, events | contain business logic |
| `templates/` | Presentation | compute anything |

The strict rule is **`rules.py` imports nothing outside the stdlib and `dataclasses`.** That single constraint is what makes `test_rules.py` twenty asserts with no fixtures, no mocking, and no network — and it is what structurally prevents AI from reaching the decision.

## Data flow

```
POST /submit  (multipart: `submission` JSON field + 3 optional file fields,
               or a plain application/json body with no documents)
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
              +- [2] completeness  -> rules R01, R02  (given a presence map, not
              |                       extraction output - see docs/02)
              +- [3] extraction    -> extract.py -> store.set_extracted()
              |                       one add_event(ai_call, model/response/usage)
              |                       per document actually attached
              +- [4] format        -> rules R03-R05
              +- [5] consistency   -> rules R06-R12  (matching.py may call Claude)
              +- [6] decision      -> rules.decide() -> store.set_status()
              |                       + add_event(decision, rule_ids)
              +- [7] communicate   -> draft email if PENDING -> store.set_draft()

GET  /                    submit form (14 fields, 3 uploads, sample dropdown)
GET  /samples/{name}      one scenario fixture as JSON (fills the form)
POST /submit              multipart -> background run + 303 to /run/{id}
                          application/json -> synchronous, returns the result
GET  /run/{id}            full run page
GET  /run/{id}/stages     the polled fragment, every 700 ms  <- live run view
GET  /run/{id}/export     the whole run as JSON
POST /run/{id}/send       human gate; form post redirects, JSON post returns JSON
GET  /dashboard?status=   history across runs, optionally filtered
POST /reset               wipe runs/findings/events + uploads
```

## Live run view without WebSockets

The pipeline runs in `BackgroundTasks`, writing an event row per completed stage. The page polls with a single attribute:

```html
<div hx-get="/run/{{ run_id }}/stages" hx-trigger="every 700ms" hx-swap="innerHTML">
```

When the run finishes the fragment stops emitting the polling attribute and HTMX stops on its
own. No SSE, no WebSocket, no reconnect logic, no streaming code.

**Poll on `run_finished`, not on the status.** The status is persisted *before* stage 7 so the
decision is durable the instant it is made — which means a terminal status does not mean the
pipeline has finished. Polling on the status stops the live view while the follow-up is still
being drafted, and the draft never appears without a manual refresh. The pipeline writes a
`run_finished` event as its last act; the view polls until that exists.

**A deliberate ~400 ms pause per stage.** A run that completes in 80 ms looks broken on video — stages flash from empty to done with nothing visible in between. The pause is a demo requirement, not padding, and it is marked in code:

`pipeline.DEMO_PAUSE_S = 0.4`, passed explicitly by the UI route. The module default stays
`0.0` so the test suite is not slowed — the pause is a demo-legibility device, not behaviour.

## Concurrency and state

Single process, single SQLite file, `check_same_thread=False`, WAL mode. One reviewer, a handful of runs. Locking, connection pooling, and a real job queue are all deferred — see `10-assumptions-and-scope.md`.

## Error containment

Each stage is wrapped. On exception: write `stage_failed` with the error, set the run to `ERROR`, stop. The run never falls through to a status. `ERROR` and `REJECTED` are rendered differently on the dashboard — "our extractor crashed" and "this vendor is not credible" must never look the same.

## Configuration

One `.env` with `ANTHROPIC_API_KEY`. Thresholds (0.75 / 0.92 / 0.70) are module constants in `matching.py`, not config — they are tuned once against the sample set in Part 4 and then left alone. A settings file for four numbers nobody will change is exactly the abstraction this MVP is avoiding.
