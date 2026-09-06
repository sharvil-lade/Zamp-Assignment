# Vendor Onboarding Decision Engine

A vendor submission goes in. An explained decision comes out.

Built for **PS-2 · Vendor onboarding: from submission to approval**.

> **AI reads the documents. Deterministic rules decide. A human acts.**

---

## The problem

Before a company can pay a vendor, someone has to verify they are legitimate:
collect company details, banking information, tax registration and compliance
documents, then check that all of it is **complete, consistent and credible**.

The review is manual, chasing missing information is manual, and the only record
of why a vendor was approved is somebody's inbox.

The expensive failure is not an incomplete form. It is a submission that *looks*
complete and is internally inconsistent — a bank account in a different name, a
tax ID that contradicts the declared legal form. Those pass a completeness check
and cause payment fraud.

## The solution

A form and five documents in; **Approved / Pending / Rejected** out, with every
reason visible, the evidence beside it, and an append-only record of how the
decision was reached.

When something is fixable the loop closes: the reviewer reopens the vendor's own
form, the vendor corrects it through the same link, and that produces a new
decision on the same case with every previous round still on the record.

It is a **decision engine**, not a KYB platform. It does not maintain vendor
master data, monitor vendors over time, or screen against sanctions feeds. Those
boundaries are deliberate — see
[docs/10-assumptions-and-scope.md](docs/10-assumptions-and-scope.md).

---

## How a case moves

```
employee creates a case ──▶ one secure vendor link
                                  │
                            vendor submits form + documents
                                  │
                            ┌─────▼─────────────────────────────┐
                            │  intake · completeness            │
                            │  extraction · format              │
                            │  consistency · DECISION           │
                            │  AI review · communicate          │
                            └─────┬─────────────────────────────┘
                                  │
             ┌────────────────────┼────────────────────┐
             ▼                    ▼                    ▼
         APPROVED             PENDING              REJECTED
        onboard them     reviewer reopens the     internal note,
                         same link → vendor       nothing sent to
                         corrects → new run,      the vendor
                         same case
```

**Case** = the vendor's onboarding journey. **Run** = one submission through the
pipeline. A case owns many runs; a correction adds one rather than replacing it.

---

## The decision engine

**17 rules**, grouped by what they protect. Each has a stable id, a stated
business purpose, and evidence attached to whatever it finds.

| Category | Rules | Protects |
|---|---|---|
| Completeness | R01, R02 | That there is enough to judge at all |
| Document integrity | R13, R14 | That the evidence is the right kind and legible |
| Identifier & format validation | R03, R04, R05, R17 | That core identifiers are well-formed and issued |
| Identity & tax consistency | R06, R07, R12, R15, R16 | That the vendor is who they say they are |
| Banking consistency | R09, R10 | That the money goes to the vendor and nobody else |
| Address consistency | R08, R18 | That the registered address holds up |

**The decision is five lines**, and it sees findings and nothing else:

```python
def decide(findings):
    if any(f.severity == BLOCK for f in findings): return "REJECTED"
    if any(f.severity == FIX   for f in findings): return "PENDING"
    return "APPROVED"
```

`BLOCK` = the evidence contradicts the submission. `FIX` = something is missing
or unclear and the vendor can fix it.

Checks worth knowing about: the **GSTIN mod-36 checksum** (catches a number that
was invented rather than issued), the **PAN's 4th character** encoding legal form
(a firm's PAN declared as a proprietorship is a contradiction, not a typo), and
the **PAN embedded in a GSTIN** — which catches a certificate belonging to a
different legal person, with no external lookup.

Every rule reports **passed / failed / not applicable**, so the run page can say
*17 checks · 14 passed · 3 not applicable* honestly. A PAN check correctly
skipped for a US vendor is never counted as a pass.

Full table: [docs/03-validation-rules.md](docs/03-validation-rules.md).

---

## Where AI is used, and where it is not

Three model calls, all scoped. `claude-haiku-4-5-20251001`.

| Call | Does | Affects the decision? |
|---|---|---|
| `ai/extract.py` | Reads each PDF into structured JSON | **Yes, upstream** — the document rules judge its output |
| `ai/matching.py` | Compares two entity names, **only** in the 0.75–0.92 ambiguous band | **Yes, narrowly** |
| `ai/employee.py` | One structured call → risk narrative, key points, recommendation | **No** — runs after `decide()` |

**AI supplies facts. Deterministic rules judge them. Only `decide()` sets a
status.** That is enforced structurally, not by convention:
`engine/rules.py` imports nothing outside the standard library and reads no
clock — asserted by tests, so the constraint cannot rot.

Two design points worth calling out:

- **Name matching is three-valued.** ~90% of comparisons never reach a model at
  all. Below 0.70 confidence the model returns *uncertain* rather than a guess,
  and an uncertain match becomes a question for a human — never a rejection.
- **Risk level is arithmetic, not AI.** `BLOCK → High, FIX → Medium, else Low`.
  The model writes the *rationale*; it never sets the level. A model that could
  talk risk down is a model that could talk a rejection down.

Extraction is deliberately **transcription** — the prompt forbids inferring,
correcting or completing anything, and an honest `null` is always preferred to a
plausible guess. That is why a small model is the right one here.

---

## Screens

| Route | What it is |
|---|---|
| `/dashboard` | One row per onboarding case, with the current state of each |
| `/onboardings/new` | Create a case, pick its form, get the vendor link |
| `/onboardings/:id` | The case: contact, form, and its one permanent vendor link |
| `/run/:id` | **The run page** — the decision, why, the evidence, what to do next |
| `/forms` · `/forms/:id` | Configurable onboarding forms |
| `/vendor/onboard/:token` | The vendor's own form. No login, no internal data |

The run page is the product. It leads with the decision in plain English and the
check breakdown, then the findings that caused it — each with rule id, name,
category, severity, and the two values that prove it, **labelled by source**:

```
R09 · Bank account holder is the vendor          BLOCK   Banking consistency
Bank account is held in a different name than the vendor entity.

  Cancelled cheque or bank letter    S. Balan
  Vendor submission                  Ashwin Chemicals Private Limited
```

Everything else — the full 17-check register, extracted-vs-submitted per
document, the AI briefing, the processing timeline, the audit trail — is present
and folded away below.

---

## The correction cycle

Each case has **one vendor URL, stable for its whole life**. It is used for the
first submission and every correction; what changes is only whether the form
behind it is accepting one.

```
submitted   → run 1 PENDING     → case closed
              reviewer clicks "Open correction form"
                                → case open, same URL
vendor corrects (form prefilled, documents retained)
                                → run 2 on the SAME case → APPROVED
```

Reopening is a **human action**, not something the pipeline does when it decides
PENDING — a decision landing overnight must not silently make a case submittable
again before anyone has read the findings. Eligibility is enforced server-side:
the vendor endpoint returns 409 unless the case is awaiting a submission.

Each correction produces a new run. Previous runs keep their status, findings and
audit trail, and the run page shows the rounds as tabs:

```
Submission 1        Submission 2        Submission 3  · LATEST
Pending · 3 issues  Pending · 1 issue   Approved · 0 issues
```

---

## Architecture

```
backend/
  app.py            ASGI app: wiring, failures, log redaction, CORS
  config.py         environment; which database and storage are active
  auth.py           the shared password and session tokens
  routes/           HTTP layer — no SQL, no model calls, no rules
  engine/           rules · pipeline · forms — the only place a status is set
  ai/               employee · extract · matching — the only place a model is called
  data/             store · storage — the only place SQL exists
  samples/          the four scenario fixtures and their PDFs
api/index.py        Vercel entrypoint; re-exports the same ASGI app
frontend/           React 18 + Vite
tests/              pytest
```

The grouping is the dependency rule made visible: `routes → engine → data`,
never backwards. Enforced by tests rather than convention — no SQL outside
`data/store.py`, no database driver in `routes/`, `engine/rules.py` stdlib-only,
`ai/employee.py` with no database handle or shell access.

| Piece | Choice |
|---|---|
| API | FastAPI, one router per domain, all under `/api` |
| Frontend | React 18 + Vite, zustand for session and a stale-while-revalidate cache |
| Database | Supabase Postgres, or SQLite when `DATABASE_URL` is unset |
| Storage | Supabase Storage, or `./uploads` when the keys are unset |
| AI | Anthropic SDK — native PDF input, structured outputs |
| Auth | One shared password → HMAC-signed session token (stdlib only) |

**One configuration path.** There is no development mode and no production mode.
Which database and storage are used is decided by whether the credentials are
present, and the pipeline always runs inline — a background task does not
outlive a serverless response, so deferring the work would silently lose it once
deployed.

---

## Running it

```bash
cp .env.example .env          # set ANTHROPIC_API_KEY and APP_PASSWORD at minimum
pip install -r requirements-dev.txt

cd frontend && npm install && npm run build && cd ..
python -m uvicorn app:app --reload --app-dir backend --port 8000
```

Open <http://localhost:8000>. With `DATABASE_URL` and the Supabase keys unset it
runs on SQLite and `./uploads` — no accounts needed beyond an Anthropic key.

For frontend work, `npm run dev` on `:5173` proxies to the API on `:8000`.

### Environment

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Document extraction and the AI review |
| `APP_PASSWORD` | yes | The single shared password. The app refuses to start without it |
| `DATABASE_URL` | for deployment | Supabase Postgres. Use the **Session pooler** URI — the direct host is IPv6-only |
| `SUPABASE_URL` | for deployment | |
| `SUPABASE_SERVICE_ROLE_KEY` | for deployment | The **secret** key. A publishable key is subject to RLS and cannot write to a private bucket |
| `SUPABASE_BUCKET` | for deployment | Defaults to `onboarding-documents` |

---

## Tests

```bash
python -m pytest -q          # 536
cd frontend && npm test      # 127
```

No network, no mocking of the database, no fixtures beyond the four scenarios.
Tests run on real SQLite, a real FastAPI client, real session tokens; the
Postgres dialect is validated offline against the actual grammar with `pglast`.

Several tests assert *architecture* rather than behaviour, which is what keeps
the design from drifting:

- `engine/rules.py` imports nothing outside the standard library
- no rule reads the wall clock
- no SQL outside `data/store.py`; no database driver inside `routes/`
- `ai/employee.py` has no database handle, no shell, no arbitrary tool use
- every registered rule is wired into a stage and actually executes
- nothing reachable over HTTP can delete a run, a finding or an audit event

---

## The four scenarios

Fixtures in `backend/samples/`, each a submission plus the PDFs it attaches.

| | Outcome | Why it is interesting |
|---|---|---|
| **EC-1** Happy path | `APPROVED` | 17/17. The baseline |
| **EC-2** Incomplete | `PENDING` | The only path that asks the vendor for something. Generates one instruction per finding — *"Please upload your certificate of incorporation"* — never a generic "your submission is incomplete" |
| **EC-3** Bank mismatch | `REJECTED` | Everything is present and well-formed. The account is in a director's personal name. **Complete is not the same as legitimate** |
| **EC-4** Cross-field | `REJECTED` | Three typed edits, six contradictions, zero external lookups. Invisible to a human skimming a form |

Also handled deliberately: an **unsure name match** routes to a human instead of
rejecting; a **corrupt or renamed file** becomes a fixable finding rather than a
crashed run; and a **pipeline failure is `ERROR`, not `REJECTED`** — "our
extraction failed" and "this vendor is not credible" are different facts.

---

## Deploying

`vercel.json` builds the frontend and rewrites every path to one serverless
function at `api/index.py`, which re-exports the same ASGI app `uvicorn` runs
locally. One origin, one deployment, no CORS in production.

Two settings there are load-bearing:

- **`maxDuration`** — the pipeline runs inside the request (five model calls,
  tens of seconds). Vercel's 10-second default would abort every real
  submission. `300` requires Pro; Hobby caps at 60s.
- **`includeFiles`** — the Python builder only bundles files under the
  entrypoint's tree, so the React bundle has to be named explicitly or the
  function ships without a UI.

Set the six environment variables in the Vercel project; there is no `.env`
there. Details in [docs/13-deployment-plan.md](docs/13-deployment-plan.md).

---

## Known limits

Stated plainly, because they are choices rather than oversights.

- **No per-person identity.** One shared password, so the audit trail records
  `user:operator` rather than a name. Restoring *who* means restoring a user
  directory; the seam is `auth.py` alone.
- **No RBAC and no rate limiting.** Anyone who is in can do anything. Rate
  limiting `/login` is the first control to add.
- **No sanctions, registry or bank-account verification.** Every check is
  internal consistency — which is exactly why it needs no external service and
  can explain itself completely.
- **India-depth.** The cross-field checks are India-specific; the US path
  validates format only.
- **The AI Employee is an orchestration layer, not an autonomous agent.** It has
  no planning loop, no tool selection and no memory. It calls three capabilities
  and returns plain data.
- **No measured business KPIs.** Per-run duration and finding counts are
  recorded; turnaround and effort saved are not, because there is no human
  baseline to compare against.

Full list: [docs/10-assumptions-and-scope.md](docs/10-assumptions-and-scope.md).

---

## Documentation

| | |
|---|---|
| [01](docs/01-solution-overview.md) | The process, end to end |
| [02](docs/02-data-model.md) | Submission shape, tables, events |
| [03](docs/03-validation-rules.md) | All 17 rules, categories, decision impact |
| [04](docs/04-decision-engine.md) | Severity, precedence, why AI cannot decide |
| [05](docs/05-ai-design.md) | Where AI is used, prompts, model choice |
| [06](docs/06-demo-scenarios.md) | The four scenarios and the demo order |
| [07](docs/07-architecture.md) | Modules, boundaries, request flow |
| [08](docs/08-ui-and-demo.md) | Screens and the demo script |
| [10](docs/10-assumptions-and-scope.md) | Assumptions, scope, security posture |
| [13](docs/13-deployment-plan.md) | Supabase and Vercel |
| [14](docs/14-auth-and-infrastructure.md) | Auth, isolation, storage |
| [15](docs/15-ai-employee.md) | The AI Employee's capabilities |
| [16](docs/16-frontend.md) | The React app |
