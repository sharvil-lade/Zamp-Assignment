# Vendor Onboarding Decision Engine

A vendor submission goes in. An explained decision comes out.

Built for **PS-2 · Operations — Vendor onboarding: from submission to approval**
(AI Solutions Associate case study, 2026).

**One line:** *AI reads documents, rules decide, humans send.*

---

## The problem

Before a company can pay a vendor, someone has to verify they are legitimate:
collect company details, banking information, tax registration and compliance
documents, then check that all of it is **complete, consistent and credible**.

Today that review is manual, chasing missing information is manual, and the only
record of why a vendor was approved is somebody's inbox.

The expensive failure is not an incomplete form. It is a submission that *looks*
complete and is internally inconsistent — a bank account in a different name, a
tax ID that contradicts the declared jurisdiction or legal form. Those pass a
completeness check and cause payment fraud.

## The solution

A form and up to five documents in; **Approved / Pending / Rejected** out, with every
reason visible, a drafted follow-up for anything recoverable, and an immutable
audit record of how the decision was reached.

When something is fixable the loop closes: the case reopens, the vendor corrects
their submission through the same secure link, and that produces a new decision
against the same case with every previous round still on the record.

It is a **decision engine**, not a KYB platform. It does not maintain vendor
master data, monitor vendors over time, or screen against live sanctions feeds.
Those boundaries are deliberate and documented in
[docs/10-assumptions-and-scope.md](docs/10-assumptions-and-scope.md).

---

## Two ways in

Employee pages require a login. Vendors never sign in — they get a secure link.

**Vendor portal (the product flow).** A signed-in employee creates a case at `/onboardings/new`,
picks which onboarding form the vendor gets, copies the generated secure link, and sends it. The
vendor opens `/vendor/onboard/<token>`, fills in their own details, uploads their documents, and
submits — which hands straight to the pipeline below. The employee watches it on `/dashboard`.

```
Employee -> Create case -> Secure link -> Vendor -> Submit -> PS-2 pipeline -> Dashboard
```

The token is 32 random bytes; only its SHA-256 is stored, it is never written to the logs, and
it works exactly once. A vendor sees their own company name and nothing internal — no status,
no findings, no rules, no run id.

**There is no employee-side submission form.** A vendor's details are entered by the vendor,
once, against a link only they hold — retyping someone's bank account into an internal form
reintroduces the transcription risk this engine exists to catch, and it would mean the audit
trail could not say who actually asserted the account number. The four demo scenarios are
fixtures in `backend/samples/`, submitted through the vendor portal like any other vendor.

**One UI.** React 18 is the whole front end: `npm run build` writes `frontend/dist`, and the
backend serves it at the root while answering `/api` in the same process. The server-rendered
Jinja layer that preceded it was removed once React covered every route — the reasoning is in
[docs/10-assumptions-and-scope.md](docs/10-assumptions-and-scope.md), the app itself in
[docs/16-frontend.md](docs/16-frontend.md).

## Configurable onboarding forms

The 15-field PS-2 form is no longer hardcoded — it is a seeded form, generated from `rules.py` so
it cannot drift away from the engine that judges it. An employee can create others at `/forms`.

- **Generated, not typed.** Sections, labels, options and help text come from the rule engine.
  So does `required`: a field is browser-required only if it is in `rules.ALWAYS_REQUIRED`, a
  document only if in `rules.required_documents({})`. `gstin`, `pan`, `ifsc`, the PAN card and the
  GST certificate are therefore *not* starred — they are conditional on country and tax type, R01
  and R02 decide them from the answers, and starring them would stop a US vendor on an EIN from
  submitting at all.
- **Edited in place, with no versions.** A case snapshots the schema it was created against, which
  is the guarantee an immutable published version was for. An edit cannot change what a vendor was
  already asked.
- **The standard form is rebuilt if it falls behind the engine.** On every start-up,
  `seed_standard_template()` regenerates it if it stops validating or stops asking for something
  the rules read (`forms.missing_engine_fields`). A form that has merely been reordered, reworded
  or extended is kept. Duplicate it if you want a permanently different one.
- A field carrying `canonical:` **is** the PS-2 field it names — it hits the existing
  deterministic rules unchanged.
- A field without one gets schema validation only (required, type, options) plus the AI
  Employee's review. **No invented business rules.**

Detail: [docs/02-data-model.md](docs/02-data-model.md).

## End-to-end workflow

```
  Vendor submission (15 fields + up to 5 documents)
            |
        [1] Submission received --------> run created, status=RUNNING
        [2] Checking completeness ------> findings[]        R01 R02
        [3] Extracting documents [AI] --> extracted{} persisted
        [4] Validating formats ---------> findings[]        R03 R04 R05
            and the documents as artefacts                 R13 R14 R17
        [5] Cross-checking information -> findings[]        R06 R07 R08 R09
                                                            R10 R12 R15 R16 R18
            |     +-- ambiguous name pair? -> Claude -> {same_entity, confidence, reason}
        [6] Decision  decide(findings) -> APPROVED | PENDING | REJECTED
        [7] AI Employee review   [AI] --> risk · summary · recommendation
        [8] Preparing communication [AI] -> draft email --> human clicks send
            |
   every stage appends to events[]  ->  live run view · dashboard · JSON export
```

Stages 2, 4 and 5 emit **findings**. Stage 6 turns them into a status. Stage 7 is
the **AI Employee**, which reads the decided run and briefs the reviewer — it
receives the decision as a fact and cannot change it. See
[docs/15-ai-employee.md](docs/15-ai-employee.md).

**The load-bearing idea:** stages 2, 4 and 5 decide nothing. They only emit
**findings**. Stage 6 derives the status mechanically:

```python
def decide(findings):
    if any(f.severity == "BLOCK" for f in findings): return "REJECTED"
    if any(f.severity == "FIX"   for f in findings): return "PENDING"
    return "APPROVED"
```

Three lines. That inversion is why the reasoning is visible without building an
explainability feature, why adding a rule can never break the decision, and why
the audit trail is a byproduct rather than a feature.

## AI vs deterministic responsibilities

One **AI Employee** with several capabilities, orchestrated by `ai_employee.py`:

| Where | Why it must be AI |
|---|---|
| **Document extraction** (stage 3) | Vendors format documents however they like. No rule reads an arbitrary cancelled cheque. |
| **Ambiguous name matching** (stage 5) | Only inside a measured band — `score ≥ 0.92` matches and `≤ 0.75` mismatches are settled by `difflib`, so ~90% of comparisons never reach a model. |
| **Review, summary, recommendation** (stage 7) | Reads the *already decided* run and briefs the reviewer in plain language. |
| **Follow-up drafting** (stage 8) | Deterministic input (the findings), natural-language output, human-gated. |

**Risk is deterministic, not judged.** BLOCK → High, FIX → Medium, none → Low. A
model that could talk risk down could talk a rejection down.

**Deterministic does everything else** — regex, checksums, string slicing, date
arithmetic, exact comparison, **and the decision itself**.

**The boundary is structural, not a policy.** `rules.py` imports nothing outside
the standard library, and `decide()` takes a list of findings and nothing else.
The model *cannot* reach the decision — that is a property of the file layout,
enforced by a test that walks the module's imports.

If the model is unsure (`confidence < 0.7`) it does not decide: the rule emits a
`FIX` tagged `ai_uncertain` and a human reviews. Uncertainty becomes **Pending**,
never Rejected — and never a silent approval.

---

## Architecture

```
backend/
  app.py            wiring only: lifespan and config checks, log redaction, CORS,
                    JSON error handlers, the /api router, the React catch-all
  config.py         environment, backend selection, production refusal
  auth.py           the shared password, session tokens, route dependency
  routes/           the HTTP layer, one router per domain, all under /api
    _shared.py        view models: decision, checks, findings, stages
    auth.py           /login /session
    dashboard.py      /dashboard
    onboardings.py    cases and their invite links
    forms.py          form templates
    runs.py           run detail and JSON export
    communication.py  the human send gate
    vendor.py         the vendor portal (public, token-only)
  engine/           the deterministic decision engine
    rules.py          the 17 rules + decide(); pure, stdlib only, zero I/O
    pipeline.py       the 8 stages; writes events as it goes
    forms.py          form schemas: validation, canonical mapping; pure
  ai/               everything that talks to a model
    employee.py       one AI worker, several capabilities
    extract.py        Claude document extraction
    matching.py       normalize + difflib + model escalation
  data/             persistence
    store.py          all SQL; SQLite or Postgres
    storage.py        document objects: local disk or Supabase Storage
  samples/          4 scenario fixtures + 6 generated PDFs + their generators
frontend/           React 18 + Vite; see docs/16-frontend.md
  src/api.js        the single API client — the only URLs in the app
  src/App.jsx       the route table; the employee/vendor split lives here
  src/hooks/        useSession · useResource
  src/components/   Layout · SchemaForm · ui
  src/pages/        ten screens
  src/__tests__/    vitest + @testing-library/react
  src/styles.css    one plain stylesheet
tests/
  conftest.py       puts backend/ on the path; shared fixtures
  test_rules.py     the deterministic core and the pipeline end to end
  test_auth.py      passwords, access tokens, and what they unlock
  test_forms.py     forms, the schema snapshot, invite links, the vendor portal
  test_api.py       the REST layer, and how the bundle is served alongside it
docs/               00–16, the design record
requirements.txt · vercel.json · .env.example
```

Eleven Python modules, the `api/` package and one React app. Full detail in
[docs/07-architecture.md](docs/07-architecture.md).

**No SQL in React, and none in an API route handler.** Every router calls a named function on
`store.py`, where the query is written once. Two tests assert it — one greps the route modules for
SQL keywords, the other fails if `sqlite3` or `psycopg` appears in their imports.

**Data flows one way.** The submission snapshot is never mutated; extraction
writes once at stage 3; stages 4–6 only read. Re-running a decision is free,
instant and deterministic.

### Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web | FastAPI | One process, background tasks built in |
| REST API | `backend/routes/`, one router per domain, under `/api` | JSON only. No templates, no SQL. |
| Database | Supabase Postgres (`psycopg` 3), SQLite in dev | One `store.py`, two dialects. No ORM. |
| Documents | Supabase Storage (private bucket), local disk in dev | One `storage.py`, two backends |
| Auth | One shared password + HMAC-signed session token (stdlib) | No session store, no cookie, no user table, no roles — right size for an internal tool |
| Hosting | Vercel (`vercel.json`) | Same ASGI app, no rewrite |
| Frontend | React 18 + Vite, plain JSX, `react-router-dom` | The whole UI, served at the root from `frontend/dist` |
| Styling | One plain CSS file | No second toolchain to break the build |
| Frontend state | React state + two hooks | No Redux, no Zustand, no React Query. Ten screens. |
| AI | Anthropic SDK, `claude-opus-5` | Native PDF input, structured outputs |
| Tests | pytest + vitest | Both fully offline |

No TypeScript, no Tailwind, no state library, no Docker, no queue, no ORM. One
Python process serves the API and the bundle from the same origin, so there is no
proxy to configure and no CORS in production — `CORS_ORIGINS` exists only for the
Vite dev server.

---

## Repository layout

```
zamp/
├── backend/       the FastAPI application — the /api routers, the pipeline,
│                  the rules, auth, all SQL, all storage, the sample fixtures
│   ├── api/       one router per domain
│   └── samples/   4 scenario fixtures + 6 generated PDFs
├── frontend/      the React 18 + Vite app; `npm run build` writes frontend/dist
│   └── src/       api.js · App.jsx · hooks · components · pages · __tests__
├── tests/         the pytest suite (backend); run from the repository root
├── docs/          00–16, the design record
├── uploads/       documents in development; gitignored
├── requirements.txt
├── .env.example   copy to .env
└── vercel.json
```

`.env` lives at the **repository root**, not inside `backend/`. `config.py` is the
only module that reads it.

## Local setup

Requires **Python 3.11+** and **Node 18+**. Both halves are needed: the backend
answers `/api`, React is the UI.

### 1. Backend

From the repository root:

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill it in — see below
python -m uvicorn app:app --reload --app-dir backend --port 8000
```

`--app-dir backend` is what puts the backend package on the import path; without
it `app:app` will not resolve. `vendor.db` is created on startup, and the standard
onboarding form is seeded from the rule engine on first start — and rebuilt on any
later start where it no longer asks for everything the rules read.

There is no sign-up, so create an employee before you try to sign in:

```bash
# No key to mint and no directory to build — just pick a password.
```

Generate the key that signs access tokens:

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

Then put both in `.env`:

```
APP_PASSWORD=gozamp
```

Without `APP_PASSWORD` the app falls back to `gozamp`, so
a restart signs you out. `APP_ENV=production` refuses to start without a real one.

### 2. Frontend

React is the UI, so this half is not optional. Start it with:

```bash
cd frontend
cp .env.example .env    # VITE_API_BASE=http://localhost:8000
npm install
npm run dev             # Vite dev server on http://localhost:5173
```

`VITE_API_BASE` tells the client where the API is. `frontend/.env.example` already
points it at `http://localhost:8000`, which is what a separate dev server needs;
blank means "same origin". The backend already allows that origin to call `/api` —
`CORS_ORIGINS` defaults to `http://localhost:5173,http://127.0.0.1:5173` and only
has to be set if Vite runs somewhere else.

Other scripts:

```bash
npm run test       # vitest, once
npm run test:watch # vitest, watching
npm run build      # writes frontend/dist
npm run preview    # serve the built bundle locally
```

`npm run build` is what lets the backend serve the UI on its own: it mounts
`frontend/dist/assets` at `/assets` and answers every non-`/api` path with the
bundle. Build with `VITE_API_BASE` blank so it calls `/api` on its own origin,
then open **http://127.0.0.1:8000** and you land on the login page. Without a
build, use the Vite dev server on `:5173` — a backend with no `frontend/dist`
logs a warning at start-up and answers 503 on the UI paths.

### Environment variables

| Variable | Where | Required | Used for |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | `.env` | always | Extraction, name matching, follow-up drafting |
| `APP_PASSWORD` | `.env` | production | The one shared password for the employee side. Defaults to `gozamp` |
| `APP_ENV` | `.env` | production | `production` refuses development backends |
| `DATABASE_URL` | `.env` | production | Supabase Postgres; blank = SQLite |
| `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` | `.env` | production | Supabase Storage; blank = local disk |
| `SUPABASE_BUCKET` | `.env` | optional | Defaults to `onboarding-documents` |
| `PIPELINE_MODE` | `.env` | serverless | `inline` on Vercel; `background` locally |
| `CORS_ORIGINS` | `.env` | dev only | Origins allowed to call `/api` from a browser. Defaults to the Vite dev server |
| `VITE_API_BASE` | `frontend/.env` | dev only | Where the React app finds the API. Baked in at **build** time; blank = same origin |

Leaving the Supabase variables blank selects the **development** backends —
SQLite and local disk. That fallback is refused when `APP_ENV=production`, so a
missing variable can never silently downgrade a deployment.

Nothing above is required for either test suite: both are fully offline and the
backend suite passes with an empty `ANTHROPIC_API_KEY`.

### Full local walkthrough

1. Configure `.env`.
2. `python -m uvicorn app:app --reload --app-dir backend --port 8000`.
3. `cd frontend && npm install && npm run dev`, then open http://localhost:5173
   and **sign in** (`/` then takes you to the dashboard). With `npm run build`
   instead, http://127.0.0.1:8000 serves the same thing.
4. **New onboarding** → vendor name, contact and email → pick a form (it defaults
   to the standard one) → **Create case**.
5. **Copy link** — this is the only time the token is shown.
6. Open the link in a private window (no employee session).
7. Fill the form, attach the five PDFs from `backend/samples/pdfs/`, submit.
8. The vendor sees only *Submission received*.
9. Back on **Dashboard**, the case moves from *Awaiting vendor* to its decision.
10. Click the row for the full run: status, **AI Employee summary**, findings,
    extracted-vs-submitted, documents, communication, audit trail.

To run against Supabase instead, follow the checklist in
[docs/13-deployment-plan.md](docs/13-deployment-plan.md).

### Running the tests

Two suites. Both are offline; neither needs the other running.

```bash
# backend — from the repository root
python -m pytest tests -q

# frontend — from frontend/
cd frontend && npm run test
```

The backend suite covers all 17 rules and `decide()`, the pipeline end to end
across all four scenarios, passwords and access tokens and what they unlock, form
schema validation and the per-case snapshot, the vendor portal's isolation, and the
REST layer itself — including that no SQL and no database driver reaches
`backend/routes/`, and that everything under `/api` answers in JSON while every other
path serves the bundle. The frontend suite covers the API client, the session
gate, the route table, the dashboard, run polling, the form builder and the
vendor boundary.

It needs no API key, no network, no fixtures and no mocking framework — the payoff
of `rules.py` being pure. Verify that yourself with:

```bash
ANTHROPIC_API_KEY="" python -m pytest tests -q
```

`tests/conftest.py` puts `backend/` on the import path and forces the development
backends, so the suite never inherits whatever `.env` happens to be on the
machine. The frontend suite runs in jsdom with `src/api.js` mocked, so it tests
what React does with a payload and never the payload itself.

### Regenerating the fixtures

Committed, so this is optional (`pip install reportlab` first):

```bash
python backend/samples/make_pdfs.py      # the sample documents
python backend/samples/make_fixtures.py  # the 4 scenario JSONs, GSTIN checksum computed
```

---

## The four demo scenarios

Each is a fixture in `backend/samples/`: a submission JSON plus the PDFs it attaches. To run
one, create a case at `/onboardings/new`, open its vendor link, fill the form from the JSON and
attach the named PDFs from `backend/samples/pdfs/`. The offline test suite runs all four
end to end without touching a browser — see `tests/test_decision.py`.

### EC-1 · Happy path → `APPROVED`
`Sundaram Industrial Supplies LLP`, everything consistent. All five documents are
attached, readable, in the right slots, and agree with the form. All 17 rules
pass, no findings, no follow-up drafted.

### EC-2 · Incomplete submission → `PENDING`
The certificate of incorporation and the address proof are not attached, and the
contact phone is blank. Fires **R01 and R02** — and drafts an itemised follow-up.
The only scenario that exercises *"communicate back what's needed"*.

### EC-3 · Bank beneficiary mismatch → `REJECTED`
Everything is complete and every format is valid; the bank letter reads
`S. Ramesh Kumar`. Fires **R09 (BLOCK)**. Name score 0.348 — settled
deterministically, **no model call**. The line for this one: *complete is not the
same as legitimate.*

### EC-4 · Cross-field identity contradiction → `REJECTED`
Nothing looks wrong on the form, and the documents are the same clean fixtures
EC-1 uses. One character of the PAN was changed. Fires **R06, R07, R15 and R16
(BLOCK)** and **R08, R18 (FIX)**:

```
R06  GSTIN-embedded PAN does not match the submitted PAN
       expected ABCFS1234K   actual ABCFS1234Z
R07  PAN encodes entity type 'Firm/LLP' but the submission declares 'Proprietorship'
R15  The PAN on the card does not match the submitted PAN
       expected ABCFS1234K   actual ABCFS1234Z
R16  The GSTIN on the certificate belongs to a different PAN than the one submitted
       expected ABCFS1234K   actual ABCFS1234Z
R08  GSTIN is registered in Karnataka (state code 29) but the address is in Maharashtra
R18  The address proof is for a different state than the registered address
       expected Maharashtra   actual Karnataka
```

One changed character, contradicted **four independent ways** — by the GSTIN the
vendor typed, by the entity type they declared, by the PAN card they uploaded and
by the GST certificate they uploaded — with zero external API calls. A GSTIN is
`[2 state][10 PAN][1 count][Z][1 checksum]`, the PAN's 4th character encodes legal
form, and R16 reads the PAN back out of the GSTIN printed on the certificate.

Rejected runs get an **internal note**, never a vendor-facing message — telling a
suspected fraudster which check caught them is deliberate policy.

Full detail: [docs/06-demo-scenarios.md](docs/06-demo-scenarios.md).

---

## Known limitations

Stated up front rather than discovered:

1. **No existence verification, and no external verification services at all.**
   PAN and GST verification here means format, checksum, internal consistency and
   document cross-check — nothing calls NSDL, the GST portal, the MCA registry or
   a bank. A structurally perfect but unissued GSTIN passes. Deferred, not
   overlooked; every check that ships runs offline and reproducibly.
   **Nor is document authenticity checked** — no signature, QR code, or tamper
   forensics. What is checked is type, extractability, format, readability and
   cross-consistency. A cleanly forged set of documents that agree with each other
   passes, and that boundary is stated rather than implied.
2. **No duplicate detection.** The same vendor submitted twice produces two
   independent approvals.
3. **No sanctions or PEP screening.** A stage-5 rule against a name list; the
   seam exists, the data source does not.
4. **Extraction is not cross-verified.** A misread account number would produce a
   false positive — which is exactly why the extracted-vs-submitted panel exists.
5. **Single-jurisdiction depth.** Cross-field checks are India-specific; the US
   path validates format only.
6. **No identity, no RBAC and no rate limiting.** Access is one shared password,
   so the audit trail records `user:operator` rather than a person and anyone who
   is in can do anything. Login is not rate limited. CSRF is not
   a live concern: the credential is an `Authorization` header, not a cookie, so a
   browser never attaches it to a cross-site request.
7. **Document contents live in the audit trail by design.** `ai_call` events
   store the model's raw response — that *is* the auditable record. Production
   would need encryption at rest.
8. **No concurrency control.** Many simultaneous submissions would each spawn
   background extraction with no queue.
9. **Custom form fields get no business rules.** Anything not mapped to a PS-2
   canonical is checked for presence, type and allowed options, then shown to the
   AI Employee and a human. Inventing a rule from a field label would be a guess
   dressed as a decision.
10. **The registered address is collected and never matched.** The form asks for a
    full address and the address proof yields the one printed on it; the two are
    shown side by side and nothing compares them. A street address has a dozen
    correct spellings, so a fuzzy match would manufacture findings on honest
    vendors — R18 checks the addressee name and the state instead, which are the
    parts with one right answer. Deliberate, and the row is rendered unhighlighted
    to say so.
11. **A form can only be deleted while nothing has used it.** The standard form
    can never be deleted, and neither can one an onboarding was created from — a
    case must always be able to name where its questions came from. There is no
    archive state, so forms accumulate.
12. **The access token cannot be revoked before it expires.** It is stateless, so
    signing out is the client discarding it, and a stolen token stays usable for
    up to 12 hours. It lives in `localStorage`, which survives a
    reload but means an XSS bug could read it, and there is no refresh token — a
    session simply ends and the employee signs in again.

Full reasoning, and what would change first in production:
[docs/10-assumptions-and-scope.md](docs/10-assumptions-and-scope.md).

## Deployment

Vercel + FastAPI + Supabase Postgres + Supabase Storage + Anthropic API — the
code is in place and selected by environment variables. **Not yet verified
against a live Supabase project**; every Postgres statement is parsed under the
real PostgreSQL grammar in the test suite, but no connection to a running
instance has been made. Setup checklist and remaining limits:
[docs/13-deployment-plan.md](docs/13-deployment-plan.md),
[docs/14-auth-and-infrastructure.md](docs/14-auth-and-infrastructure.md).

`vercel.json` builds `frontend/` and routes both `/api/(.*)` and `/(.*)` to
`backend/app.py`, which serves the API and the bundle from one origin — one
deployment, no second hosting target, no CORS in production.

## Documentation

| Doc | Contents |
|---|---|
| [00](docs/00-case-study-requirements.md) | PS-2 requirements, extracted from the brief |
| [01](docs/01-solution-overview.md) | Positioning, 7-stage workflow, AI vs deterministic |
| [02](docs/02-data-model.md) | 15 canonical fields, 5 documents, schemas, 6 tables, the form schema and its snapshot |
| [03](docs/03-validation-rules.md) | All 17 rules with IDs, severities and inputs |
| [04](docs/04-decision-engine.md) | `decide()` and why it stays deterministic |
| [05](docs/05-ai-design.md) | The three AI uses and what AI must never do |
| [06](docs/06-demo-scenarios.md) | EC-1 to EC-4 in full |
| [07](docs/07-architecture.md) | Layering, modules, responsibilities, data flow, every route |
| [08](docs/08-ui-and-demo.md) | Screens and the demo script |
| [09](docs/09-implementation-plan.md) | The 8 build parts |
| [10](docs/10-assumptions-and-scope.md) | Assumptions, scope, security posture, tradeoffs |
| [11](docs/11-references.md) | Research sources and what each supports |
| [12](docs/12-submission-checklist.md) | Pre-submission verification |
| [13](docs/13-deployment-plan.md) | Deployment: Supabase + Vercel setup and what is left |
| [14](docs/14-auth-and-infrastructure.md) | Authentication, isolation, database and storage backends |
| [15](docs/15-ai-employee.md) | The AI Employee: capabilities, limits, auditability |
| [16](docs/16-frontend.md) | The React app: structure, API client, session gating, SchemaForm, tests |
