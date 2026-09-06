# 07 · Architecture

## Stack

| Layer | Choice | Why |
|---|---|---|
| Web | **FastAPI** | One process, background tasks built in, no boilerplate |
| REST API | **`backend/routes/`**, one router per domain, all under `/api` | JSON only. No templates, no SQL. |
| Database | **SQLite** (`sqlite3`, stdlib) in dev, **Supabase Postgres** (`psycopg` 3) in production | One `store.py`, two dialects. No ORM. |
| Auth | **One shared password** (`APP_PASSWORD`) exchanged for an HMAC-signed session token | No session store, no cookie, no user table, no roles |
| Frontend | **React 18 + Vite**, plain JSX, `react-router-dom` | The whole employee and vendor UI, talking only to `/api` |
| Styling | **One plain CSS file** (`frontend/src/styles.css`) | No Tailwind build, no design system, no second toolchain |
| Frontend state | **React state plus two small hooks** | No Redux, no Zustand, no React Query. Ten screens. |
| AI | **Anthropic SDK** (`claude-haiku-4-5-20251001`) | Native PDF input, structured outputs |
| Tests | **pytest** (`tests/`) and **vitest** (`frontend/src/__tests__/`) | Backend offline, frontend in jsdom |

`python -m uvicorn app:app --reload --app-dir backend --port 8000` starts the whole server: the API, and the built React bundle if one exists. `npm run dev` in `frontend/` is the second process during development only. No Docker, no queue, no ORM, no migrations.

## One frontend, one backend

React is the entire UI. The server renders no HTML of its own: `backend/app.py` is lifespan and configuration checks, the vendor-token log filter, CORS, two JSON exception handlers, the `/api` router, a 404 catch-all for unknown `/api` paths, and a catch-all that serves the bundle. The server-rendered Jinja layer that preceded React was removed once React covered every route — see `10-assumptions-and-scope.md`.

```
  browser
     |
     |  /  ·  /dashboard  ·  /run/{id}  ·  /vendor/onboard/{token}
     |      React 18 bundle (Vite build, SPA fallback at every non-/api path)
     |          |
     |          |  fetch with `Authorization: Bearer <token>`
     |          v
     |      frontend/src/api.js   the single API client
     |          |
     +----------+
                v
        /api/*  —  backend/routes/
        auth · dashboard · onboardings · forms · runs
        communication · submit · vendor
                |
                |  view models only; no SQL, no rules
                v
        +----------------------------------------------------------+
        |  engine/  rules · pipeline · forms                        |
        |  ai/      employee · extract · matching                   |
        |  data/    store · storage        auth · config            |
        +---------------------+------------------+-----------------+
                              |                  |
                              v                  v
                   SQLite / Supabase       local disk /
                   Postgres                Supabase Storage
              (all SQL: data/store.py)  (all objects: data/storage.py)
```

**No SQL lives in React, and none lives in an API route handler.** Every router imports `store` and calls a named function; the query itself is written once in `store.py`. Two tests assert it: one greps the route modules for SQL keywords, the other walks their imports and fails if `sqlite3` or `psycopg` appears. The API layer is a translator between HTTP and the modules that already existed — it added no behaviour.

**Authentication is one shared password.** `POST /api/login` exchanges `APP_PASSWORD` for a session token signed with HMAC-SHA256 from the standard library; every later request presents it as `Authorization: Bearer <token>`, and `auth.require_session` verifies the signature and the expiry. Nothing is stored server-side, so there is no session middleware, no cookie and no logout endpoint. There is deliberately no user directory, no role and no sign-up. `CORSMiddleware` runs with `allow_credentials=False` — the credential is a header the client attaches deliberately, not something the browser sends on its own — and `CORS_ORIGINS` exists only so the Vite dev server on another port can call the API during development. Full detail in `14-auth-and-infrastructure.md`.

## File structure

```
zamp/
├── backend/
│   ├── app.py            wiring only: lifespan, log redaction, CORS, JSON error
│   │                     handlers, the /api router, the React catch-all
│   ├── config.py         environment, backend selection, production refusal
│   ├── auth.py           the shared password, session tokens, route dependency
│   │
│   ├── routes/           the HTTP layer — one router per domain, all under /api
│   │   ├── _shared.py        view models: decision, checks, findings, stages
│   │   ├── auth.py           /login /session
│   │   ├── dashboard.py      /dashboard
│   │   ├── onboardings.py    cases and their invite links
│   │   ├── forms.py          form templates
│   │   ├── runs.py           run detail and JSON export
│   │   ├── communication.py  the human send gate
│   │   └── vendor.py         the vendor portal (public, token-only)
│   │
│   ├── engine/           the deterministic decision engine
│   │   ├── rules.py          the 17 rules + decide(); pure, stdlib only
│   │   ├── pipeline.py       the 8 stages; writes events as it goes
│   │   └── forms.py          form schemas: validation, canonical mapping; pure
│   │
│   ├── ai/               everything that talks to a model
│   │   ├── employee.py       one AI worker, several capabilities (docs/15)
│   │   ├── extract.py        Claude document extraction
│   │   └── matching.py       normalize + difflib + model escalation
│   │
│   ├── data/             persistence
│   │   ├── store.py          all SQL; SQLite or Postgres
│   │   └── storage.py        document objects: local disk or Supabase Storage
│   │
│   └── samples/          the four demo scenarios and their PDFs
│
├── frontend/            React 18 + Vite — the whole UI
│   ├── src/
│   │   ├── api.js            the single API client
│   │   ├── App.jsx           the route table; the security split lives here
│   │   ├── store.js          zustand: session + cached server state
│   │   ├── hooks/            useSession · useResource (cache-aware)
│   │   ├── components/       Layout · SchemaForm · ui
│   │   ├── pages/            nine screens
│   │   ├── __tests__/        vitest + @testing-library/react
│   │   └── styles.css        one plain stylesheet
│   ├── index.html · vite.config.js · package.json
│   └── dist/                 the build output, served at the root when present
├── tests/
│   ├── conftest.py       puts backend/ on the path; shared fixtures
│   ├── test_rules.py     the deterministic core and the pipeline end to end
│   ├── test_auth.py      passwords, access tokens, and what they unlock
│   ├── test_forms.py     forms, the schema snapshot, invite links, the vendor portal
│   └── test_api.py       the REST layer and how the bundle is served alongside it
├── docs/                 this directory
├── requirements.txt · vercel.json · .env.example · README.md
```

**Four packages — `routes`, `engine`, `ai`, `data` — plus three top-level modules and one React app.** The grouping is the dependency rule made visible: `routes` may call `engine`, `engine` never imports `routes`, and `data` is the only place SQL exists. There is no template directory: React replaced the server-rendered layer rather than sitting beside it.

## Component responsibilities

| Module | Owns | Must NOT |
|---|---|---|
| `app.py` | Process start-up and configuration refusal, error shape, mounting `api.router`, serving `frontend/dist` | contain any validation logic |
| `routes/` | Translating HTTP into calls on the other packages; view models React can render without deriving anything | contain SQL, rules, or business behaviour |
| `engine/pipeline.py` | Stage sequencing, event emission, status write-back, error containment | contain any rule logic |
| `engine/rules.py` | The 17 rules + `decide()`. **Pure functions.** | import `anthropic`, `httpx`, `sqlite3`, or read the clock |
| `engine/forms.py` | Form schema validation, canonical mapping, generic field checks, the standard PS-2 schema. **Pure module.** | touch the database, the network, or a model |
| `ai/extract.py` | PDF/image to structured JSON via Claude; owns the 5 schemas and prompts | interpret or validate what it extracted, or touch `store` |
| `ai/employee.py` | Orchestrates the AI capabilities; returns advisory data only | touch the database, the shell, or the decision |
| `ai/matching.py` | Name normalization, similarity scoring, ambiguous-band escalation; returns a `NameVerdict` | emit findings, decide severity, or touch `store` |
| `data/store.py` | **All SQL**, both dialects. Runs, findings, events, cases, form templates | contain business logic |
| `config.py` | Environment, backend selection, production refusal, `APP_PASSWORD`, `CORS_ORIGINS` | be bypassed by reading `os.environ` elsewhere |
| `auth.py` | Employee identity, password hashing, access tokens | know anything about vendors |
| `data/storage.py` | Document objects and key derivation | emit findings or touch the database |
| `frontend/src/` | Presentation and navigation | know a URL that is not in `api.js`, or hold a business rule |

The strict rule is **`engine/rules.py` imports nothing outside the stdlib.** `engine/forms.py` holds the same line: it imports `rules` and `ai.extract` only for their field names, and it makes no calls out. That single constraint is what makes the rule tests fixture-free and network-free, and it is what structurally prevents AI from reaching the decision.

## Data flow

```
POST /api/vendor/onboard/{token}   (multipart, from the vendor portal)
    |
    +--> forms.normalize_submission(schema, raw)
    |         -> canonical PS-2 fields   (exactly what rules.py always received)
    |         -> _custom                 (everything the schema added)
    +--> store.create_run()                -> run_id, status=RUNNING
    +--> store.save_document() per attachment (local disk or Supabase Storage)
    +--> pipeline.run(run_id)   background locally, inline on serverless
              |
              |  each stage:  store.add_event(stage_started)
              |               ... work ...
              |               store.add_findings(...)
              |               store.add_event(stage_completed, duration_ms)
              |
              +- [1] intake        (snapshot already persisted)
              +- [2] completeness  -> rules R01, R02  (given a presence map)
              |                       + forms.custom_field_findings() for the
              |                         fields PS-2 has no opinion about
              +- [3] extraction    -> ai/extract.py -> store.set_extracted()
              +- [4] format        -> rules R03-R05, R13, R14, R17
              +- [5] consistency   -> rules R06-R10, R12, R15, R16, R18
              |                       (ai/matching.py may call Claude)
              +- [6] decision      -> rules.decide() -> store.set_status()
              +- [7] review        -> ai_employee: risk · summary · recommendation
              |                       custom answers are shown to it, labelled as
              |                       carrying no rule
              +- [8] communicate   -> draft email if PENDING -> store.set_draft()
```

## Routes

Every route the server answers is either under `/api` or the React catch-all.

```
SESSION
POST /api/login                         {password} -> {access_token,
                                        token_type, expires_in, employee}
GET  /api/session                       who does this token say I am; React
                                        calls it on boot

DASHBOARD
GET  /api/dashboard?status=             stats, case rows, run rows, labels

ONBOARDING CASES
POST /api/onboardings                   {vendor_name, contact_name,
                                         contact_email, form_id?}
                                        -> case_id + the raw vendor_url, once
GET  /api/onboardings/{case_id}         case, its form, link status
POST /api/onboardings/{case_id}/regenerate-link   new token, same case and form

FORM TEMPLATES
GET  /api/forms/templates               templates + field types + canonicals
POST /api/forms/templates               new template, starting as a v1 draft
GET  /api/forms/templates/{id}          one form, schema included
POST /api/forms/templates/{id}/duplicate            copy into a new draft
PUT  /api/forms/templates/{id}          edit a form in place
POST /api/forms/templates/{id}/duplicate {name} -> a copy, the way to fork the standard form
DELETE /api/forms/templates/{id}        409 for the standard form or one a case has used

RUNS
GET  /api/runs/{run_id}                 stages, findings, comparisons, AI
                                        summary, communication, audit trail
GET  /api/runs/{run_id}/export          the whole run as one JSON document
POST /api/runs/{run_id}/send            the human send gate

DIRECT SUBMISSION

VENDOR PORTAL  (public; the token is the only authorisation)
GET  /api/vendor/onboard/{token}        the schema this case was created with
POST /api/vendor/onboard/{token}        submit -> creates run -> pipeline

THE BUNDLE
GET  /assets/*                          the Vite build output, StaticFiles
GET  /{path:path}                       the file if it exists, else index.html
ANY  /api/{path:path}                   404 "No such endpoint." — registered
                                        after the routers, before the catch-all
```

`POST /api/login`, `GET /api/session` and the two vendor routes are the only ones that do not depend on `require_session`. The vendor routes accept no case id and return nothing internal — no status, no findings, no rule, no run or case id.

The client routes React resolves out of that catch-all are `/login`, `/vendor/onboard/:token`, and — inside the session-gated `<Layout>` — `/dashboard`, `/onboardings/new`, `/onboardings/:caseId`, `/forms`, `/forms/:templateId` and `/run/:runId`. `/` redirects to `/dashboard`; anything else renders the not-found page.

## Serving the React bundle

`app.py` mounts `frontend/dist/assets` at `/assets` **only when that directory exists**, and answers every other non-`/api` path with the file if one is there and `index.html` otherwise. Client-side routing means `/dashboard` is not a file on disk; the bundle resolves it once loaded. The resolved path is checked against the build directory, so a crafted `..` cannot read outside it. Vite builds with `base: "/"` for the same reason.

A checkout that has never run `npm run build` still starts — the lifespan logs a warning naming the fix, and the catch-all answers `503` with the same instruction rather than a traceback.

The order of the two catch-alls is load-bearing. The `/api/{path:path}` 404 is registered after the API router, so real endpoints still win, and before the SPA route, so an unknown endpoint is not answered with `index.html` or, for a non-GET method, a misleading 405.

In development the two halves run separately: Vite on `:5173`, FastAPI on `:8000`, and `VITE_API_BASE` pointing the client at the backend. That is the only reason `CORS_ORIGINS` exists.

## Live run view without WebSockets

`RunDetail` polls: `setInterval` calling `GET /api/runs/{id}` every 1200 ms, aborting the in-flight request on unmount.

**Poll on `run_finished`, not on the status.** The status is persisted *before* stage 7 so the decision is durable the instant it is made — which means a terminal status does not mean the pipeline has finished. Polling on the status stops the live view while the follow-up is still being drafted, and the draft never appears without a manual refresh. The pipeline writes a `run_finished` event as its last act; the page polls until that exists.

**A deliberate ~400 ms pause per stage.** A run that completes in 80 ms looks broken on video — stages flash from empty to done with nothing visible in between. The pause is a demo requirement, not padding, and it is marked in code:

`pipeline.DEMO_PAUSE_S = 0.4`, passed explicitly by the routes that start a background run. The module default stays `0.0` so the test suite is not slowed — the pause is a demo-legibility device, not behaviour.

## Concurrency and state

Single process, single SQLite file, `check_same_thread=False`, WAL mode. One reviewer, a handful of runs. Locking, connection pooling, and a real job queue are all deferred — see `10-assumptions-and-scope.md`.

## Error containment

Each stage is wrapped. On exception: write `stage_failed` with the error, set the run to `ERROR`, stop. The run never falls through to a status. `ERROR` and `REJECTED` are rendered differently on the dashboard — "our extractor crashed" and "this vendor is not credible" must never look the same.

Failures cross the HTTP boundary in exactly one shape. `app.py` registers two handlers — one for `NotAuthenticated`, one for every `HTTPException` — and both answer `{"detail": ...}` as JSON. There is no HTML error page to fall back to and no content negotiation to get wrong. `api.js` turns any non-2xx into one `ApiError` carrying a status and a message, so no React page invents its own error UI. No traceback ever reaches a response.

## Configuration

`.env` at the repository root, read only by `config.py`. Thresholds (0.75 / 0.92 / 0.70) are module constants in `matching.py`, not config — they are tuned once against the sample set and then left alone. A settings file for four numbers nobody will change is exactly the abstraction this MVP is avoiding.
