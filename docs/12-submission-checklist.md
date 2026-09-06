# 12 · Submission Checklist

Verified against a clean checkout on 2026-09-06. Re-run before sending.

## Functional requirements (from the brief)

| Requirement | Where | Status |
|---|---|---|
| Takes a vendor submission as input | Vendor portal at `/vendor/onboard/<token>` — the only intake | done |
| Produces **Approved / Pending / Rejected** | `rules.decide()` | done |
| **Reasoning is visible** | Finding cards with rule id, severity, expected vs actual | done |
| **Communicates back what's needed** | Drafted follow-up on PENDING, human-gated | done |
| **2–4 non-trivial edge cases** | EC-2, EC-3, EC-4 (plus EC-1 happy path) | done |
| **Live run view**, each stage as it executes | 8 stages; `RunDetail` polls `GET /api/runs/{id}` every 1200 ms | done |
| **Dashboard** — history, status, outputs | `/dashboard` with tiles + status filter | done |
| Runs live, repeatably, on demand | `python -m uvicorn app:app --reload --app-dir backend --port 8000`; clear local state between takes | done |
| Explainable to a non-technical buyer | Plain-language findings; no scores | done |

## Beyond the brief

| Addition | Where | Status |
|---|---|---|
| Employee authentication | One shared password (`APP_PASSWORD`) + HMAC-signed session token | done |
| Secure single-use vendor links | `onboarding_cases`; only the token hash is stored | done |
| **Configurable onboarding forms**, the standard one generated from the rule engine | `backend/forms.py`, `form_templates`, and the per-case schema snapshot | done |
| **REST API** | `backend/routes/`, one router per domain, all under `/api` | done |
| **React 18 front end** | `frontend/`, the only UI; served at the root from `frontend/dist` | done |
| Postgres + object storage backends | `store.py`, `storage.py`, selected by env | done, unverified live |

## Demo scenarios

| Scenario | Expected | Verified |
|---|---|---|
| EC-1 Happy path | `APPROVED`, no findings, no draft | yes |
| EC-2 Incomplete submission | `PENDING`, R01 R02, itemised draft | yes |
| EC-3 Bank beneficiary mismatch | `REJECTED`, R09, internal note, **no model call for matching** | yes |
| EC-4 Cross-field contradiction | `REJECTED`, R06 R07 R08 R15 R16 R18 — and **not** R17 | yes |

- [x] All four are fixtures in `backend/samples/`, submitted through the vendor portal
- [x] Fixture GSTINs carry a computed checksum (asserted by test)
- [x] Every fixture PDF is headed the way its real issuer would head it, so R13 does not fire spuriously
- [x] No demo scenario escalates to the ambiguous-band model call (demo stability)
- [x] Run order rehearsed: EC-1 → EC-2 → EC-3 → EC-4 → dashboard → export

## Testing

Two suites. Both are fully offline.

- [x] `python -m pytest tests -q` from the repo root — `test_rules.py` the
      deterministic core and the pipeline, `test_auth.py` passwords and access
      tokens, `test_forms.py` forms and the vendor portal, `test_api.py` the REST
      layer and the bundle catch-all
- [x] `npm run test` in `frontend/` — one file per concern, listed in `docs/16`
- [x] Backend passes with `ANTHROPIC_API_KEY=""`; no network, no mocking framework
- [x] Frontend runs in jsdom with `src/api.js` mocked; no running backend needed
- [x] All 17 rules unit-tested (pass, fail and skip-semantics)
- [x] Decision precedence, GSTIN checksum, matching thresholds
- [x] AI failure → `ERROR`; AI uncertainty → `PENDING`; never a silent approval
- [x] Bad uploads, missing fields, invalid data, extraction and drafting failure
- [x] `draft ≠ sent`, duplicate send → 409, unknown run → 404
- [x] Reset, audit persistence, export completeness, stage ordering
- [x] Form schema validation; a case snapshots the schema it was created against, so an edit
      cannot change what a vendor was asked
- [x] The standard form takes its fields, options and `required` flags from `rules.py`, and
      `seed_standard_template()` rebuilds it if it ever stops validating or falls behind the
      engine — while preserving a form that has merely been edited
- [x] A case keeps its version **and** its schema snapshot; a draft cannot onboard
- [x] Canonical fields reach the rules unchanged; custom fields get no invented rule
- [x] A vendor cannot choose a template; a case id is not a vendor credential
- [x] Structural test: `rules.py` imports nothing outside the stdlib
- [x] Structural test: **no SQL and no database driver anywhere in `backend/routes/`**
- [x] Every route the suite drives is a real one: `/api/*` through `TestClient`,
      the React routes through vitest. There is no server-rendered layer left to cover

## Documentation

- [x] `README.md` — problem, workflow, architecture, stack, repository layout,
      exact backend and frontend setup, env vars, both test suites, four
      scenarios, AI vs deterministic, limitations
- [x] `docs/00`–`docs/16` present and internally consistent
- [x] Field counts, rule IDs, stage names, table names and statuses agree across all docs
- [x] Route paths in the docs match the routers (`backend/routes/*.py`) and the
      React route table (`frontend/src/App.jsx`)
- [x] Every doc link in the README resolves
- [x] Deviations from the original design recorded **in** the docs, with reasons

## Security and hygiene

- [x] `.gitignore` covers `.env`, `vendor.db*`, `uploads/`, caches, `server.log`;
      `frontend/.gitignore` covers `node_modules/` and `dist/`
- [x] `.env.example` committed with blank values; `.env` never committed
- [x] No API key in any shipped module or fixture (asserted by test)
- [x] Upload allow-list by extension **and** magic bytes; 10 MB cap
- [x] Bad attachment → `PENDING` + R02, never a crashed run
- [x] Path traversal impossible — filename discarded, run-id format enforced;
      the SPA catch-all resolves paths against the build directory before serving
- [x] Vendor token: 32 random bytes, hash-only storage, single use, redacted in logs
- [x] Vendor portal routed outside `<Layout>`, the session-gated employee shell —
      asserted against the rendered output
- [x] CORS uses an explicit origin list, never a wildcard; `allow_credentials=False`,
      because the credential is a header rather than something the browser attaches
- [x] Session token: HMAC-SHA256 over the expiry, signed with the password, verified on
      decode, read from the `Authorization` header only — never a query parameter
- [x] Provider errors redacted (`sk-...` → `sk-***REDACTED***`) before persistence
- [x] Every failure is JSON from one of two handlers in `app.py`; React turns it into one `ApiError`

## Git status

- [x] Working tree clean apart from intended files
- [x] `vendor.db`, `uploads/`, `__pycache__/`, `.pytest_cache/`, `.env`,
      `frontend/node_modules/`, `frontend/dist/` untracked
- [x] Sample PDFs and fixture JSONs committed (the demo must work on a fresh clone)
- [x] `git status --porcelain` shows no ignored artefact

## Fresh-environment check

- [x] `pip install -r requirements.txt` from a clean interpreter
- [x] `python -m uvicorn app:app --reload --app-dir backend --port 8000` starts with no errors
- [x] `vendor.db` created automatically on first start
- [x] The standard form is seeded on first start, and rebuilt from the engine if it falls behind it
- [x] `/login` and `/dashboard` render sensible empty states; `GET /api/forms/templates`
      already lists the seeded standard template
- [x] `cd frontend && npm install && npm run dev` serves the app on `:5173`
- [x] `npm run build` writes `frontend/dist`; the backend then serves it at the root
- [x] Starting the backend with no `frontend/dist` still starts — start-up logs a
      warning naming the fix and the catch-all answers 503 with the same instruction
- [x] README commands work verbatim

## Known gaps, stated rather than discovered

- [x] `vercel.json` builds `frontend/` and routes both `/api/(.*)` and `/(.*)` to
      `backend/app.py`.
- [ ] Never run against a live Supabase project — see `13-deployment-plan.md`.
- [ ] No RBAC: every signed-in employee can edit any form and regenerate any vendor link.
- [ ] No rate limiting or CSRF protection.

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
| 0 | Empty dashboard | 10 s | "Submission in, explained decision out." |
| 1 | EC-1 live run | 60 s | "Eight stages. Three use AI — badged. One decision function." |
| 2 | EC-2 → draft | 90 s | "Recoverable, so it writes the chase-up. A human sends it." |
| 3 | EC-3 → R09 | 90 s | "Everything's complete. Complete is not the same as legitimate." |
| 4 | EC-4 → R06/R07/R08/R15/R16/R18 | 90 s | "Three typed edits, six contradictions, no lookups." |
| 5 | Dashboard + Export JSON | 30 s | "This replaces 'whatever's in someone's inbox'." |

## Questions to have rehearsed

| Question | Answer lives in |
|---|---|
| "Why isn't the AI making the decision?" | `04-decision-engine.md` |
| "Where's sanctions screening?" | `10-assumptions-and-scope.md` |
| "What if the model is wrong?" | `05-ai-design.md` — uncertainty → Pending, never Rejected |
| "How would you deploy this?" | `13-deployment-plan.md` |
| "Why delete the server-rendered pages instead of keeping them as a fallback?" | `10-assumptions-and-scope.md` — frontend scope calls |
| "What happens to a form that's already been sent when you edit it?" | `02-data-model.md` — the per-case schema snapshot |
| "Why don't you check the address against the address proof?" | `10-assumptions-and-scope.md` — *The address is collected, not matched* |
| "What would you build next?" | `10-assumptions-and-scope.md` deferred table |
