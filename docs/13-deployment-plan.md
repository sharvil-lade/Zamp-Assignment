# 13 · Deployment Plan

**Status: implemented in Phase 4** — see `14-auth-and-infrastructure.md` for the
shipped design. `vercel.json`, the Postgres dialect in `store.py` and the Supabase
Storage backend in `storage.py` all exist. SQLite and local disk remain the
**development** backends and are refused in production by `config.verify()`.

**Not yet verified against a live Supabase project.** Every Postgres statement is
parsed under the real PostgreSQL grammar by `pglast` in the test suite, and the
storage backend is exercised through its local twin — but no connection to a
running Supabase instance has been made. What remains is configuration, not code:
the checklist below.

## Target architecture

**Vercel + FastAPI + Supabase PostgreSQL + Supabase Storage + Anthropic API**

```
        browser
           |  HTTPS
           v
   +-------------------+        Vercel (serverless functions, Python runtime)
   |  FastAPI (ASGI)   |        - REST API at /api
   |  backend/app.py   |        - the built React bundle at every other path
   +---+-----------+---+        - stateless: no local disk, no in-process state
       |           |
       |           +------------------> Anthropic API
       |                                extraction · name matching · drafting
       |
       +--> Supabase PostgreSQL         runs · findings · events · cases
       |                                form_templates
       |
       +--> Supabase Storage            uploaded documents (private bucket)
```

**One origin, one deployment.** The React app is not a separate hosting target: `npm run build`
writes `frontend/dist`, `app.py` serves it at the root, and the bundle calls `/api` on the same
origin. No second project, no proxy rules, no CORS in production.

## Supabase setup checklist (the remaining work)

1. **Create the project.** Put it in a region near your Vercel region.
2. **Database.** Project Settings -> Database -> Connection string -> URI. Use the
   **connection pooler** host (port 6543) on serverless; a direct connection per
   invocation exhausts Postgres connections quickly. Set it as `DATABASE_URL`.
3. **Tables.** `store.init_db()` creates all six with `CREATE TABLE IF NOT EXISTS`
   on first start, adds the two columns `onboarding_cases` gained later with
   `ALTER TABLE … ADD COLUMN`, and seeds the standard form template. All of it is
   idempotent, so no migration tool is needed for the initial deploy or for
   upgrading a database created before configurable forms existed.
4. **Storage bucket.** Storage -> New bucket -> `onboarding-documents`, with
   **Public switched off**. Objects are read server-side with the service-role
   key, so no RLS policy is required for that path — and none should be added
   that would let an anon key read the bucket.
5. **Keys.** Project Settings -> API -> `SUPABASE_URL` and the **service-role**
   key. The anon key is not used by this application.
6. **Password.** Set `APP_PASSWORD` to something that is not the documented
   default — the app refuses to start in production while it still is. It also
   signs the session tokens, so changing it signs everyone out.
7. **Frontend.** `cd frontend && npm install && npm run build`. That writes
   `frontend/dist`, which must be present in the deployed tree — it is the only
   UI, and without it every non-`/api` path answers 503. Leave `VITE_API_BASE`
   unset (or blank) for the build: a production bundle calls `/api` on its own
   origin.
8. **Vercel.** Set all of the above plus `ANTHROPIC_API_KEY`, `APP_ENV=production`
   and `PIPELINE_MODE=inline`. Deploy.

A missing variable stops the app at startup and names the variable, rather than
quietly running on SQLite.

**`vercel.json` covers both halves.** `buildCommand` is
`cd frontend && npm ci && npm run build` with `outputDirectory` `frontend/dist`;
the `@vercel/python` build points at `backend/app.py`; and both `/api/(.*)` and
`/(.*)` route to it, so the same function answers the API and serves the bundle.

## Environment variables the frontend introduced

| Variable | Read by | Local | Production |
|---|---|---|---|
| `VITE_API_BASE` | Vite, baked into the bundle at **build** time | `http://localhost:8000` | blank — same origin |
| `CORS_ORIGINS` | `backend/config.py`, at **run** time | defaults to `http://localhost:5173,http://127.0.0.1:5173` | irrelevant; leave the default |

`VITE_API_BASE` is a build-time constant, not a runtime setting: changing it means
rebuilding. It exists only because the Vite dev server and the API live on
different ports during development.

`CORS_ORIGINS` is the other half of the same problem. The dev server is a
different origin, so the browser will not let it call `/api` unless the server
says that origin is allowed. The credential is an `Authorization` header the
client attaches itself, not a cookie the browser sends on its own, so
`allow_credentials` stays `False` — but the origin list is still explicit, never a
wildcard. In production the bundle is served from the deployment's own origin and
the middleware never matches.

## The frontend and `PIPELINE_MODE=inline`

`PIPELINE_MODE=inline` runs the pipeline before the response is written, because a
background task does not outlive a serverless invocation. That interacts with the
React run view in exactly one way, and it is benign:

- `POST /api/vendor/onboard/{token}` returns only after the
  run has finished. The submitter waits the few seconds extraction takes.
- `RunDetail` opens on a run that already reports `finished: true`, so its first
  poll is also its last. The stage-by-stage animation is a local-only effect.
- Nothing about the page changes. It renders persisted events either way, which is
  why neither front end needed a code change for serverless.

`background` stays the local default so the live run view is worth watching.

## What changes, and what does not

| Layer | Local (today) | Production | Blast radius |
|---|---|---|---|
| Web | uvicorn, one process | Vercel serverless function | none — same ASGI app |
| DB | `sqlite3`, `vendor.db` | Supabase Postgres | **`store.py` only** |
| Files | `uploads/<prefix>/` | Supabase Storage bucket | **`storage.py` only** |
| Background work | `BackgroundTasks` | `PIPELINE_MODE=inline`; a queue is the next step | `api/submit.start_pipeline` |
| Frontend | Vite dev server on `:5173`, cross-origin | `frontend/dist` served at the root, same origin | `VITE_API_BASE` at build time |
| Secrets | `.env` | Vercel env vars | none |
| Auth | bearer session token, signed with the shared password | **unchanged** — nothing to share between invocations | none |
| REST API | `/api`, same process | **unchanged** | none |
| Rules / decision | pure functions | **unchanged** | none |
| AI modules | Anthropic SDK | **unchanged** | none |

**`rules.py`, `matching.py` and `extract.py` are untouched by this migration.**
`pipeline.py` needed exactly two lines — the
document-channel signal, which could not survive the move to object storage. That
is the payoff of putting all SQL behind `store.py` and all business logic behind
pure functions.

## The four things that actually need work

### 1. SQLite → Postgres (`store.py`) — done

Six tables now, on the same two dialects. What shipped, and where it differs from
this plan:

- `INTEGER PRIMARY KEY` → `BIGINT GENERATED ALWAYS AS IDENTITY` (the SQL-standard
  form; `BIGSERIAL` is the older spelling).
- Parameter style `?` → `%s` via `store._q`; `psycopg` 3 with `dict_row`.
- **Timestamps stayed `TEXT` and JSON payloads stayed `TEXT`.** This plan proposed
  `TIMESTAMPTZ` and `JSONB`. The migration brief was to *preserve semantics*, and
  every read path already does `json.loads` or ISO-8601 string comparison.
  Converting the types would have meant changing serialisation in code the
  migration was meant to leave alone, for a query capability nothing currently
  uses. `JSONB` is still the right call the first time someone wants to query
  inside `detail_json`, and it is one `ALTER TABLE ... USING` away.
- **`_next_run_id` still reads `MAX(run_id)` and increments — still racy.** Two
  simultaneous submissions could collide. Unchanged from the SQLite behaviour,
  and still the top correctness item; a Postgres sequence fixes it.

### 2. Local disk → Supabase Storage — done

`storage.py` holds both backends. `extract._content_block` was **not** changed:
the Supabase backend downloads to a temp file, so `saved_documents()` still hands
the pipeline a `Path`. Upload validation is untouched — it already took
`(filename, bytes)`.

The bucket is private and read server-side with the service-role key. Signed URLs
for reviewer download are not implemented; the reviewer sees extracted values
rather than the original file.

**One change was required in `pipeline.py`** (two lines). The "did this submission
carry documents" signal was `upload_dir().exists()`, and object storage has no
empty folders. It is now a `documents_expected` intake event, read through
`store.has_document_channel()`.

### 3. Background execution — deferred, with a working stopgap

`PIPELINE_MODE=inline` runs the pipeline before responding. The vendor waits the
few seconds extraction takes, and the live run view finds the run already
finished. Correct, but it loses the stage-by-stage animation, so `background`
remains the local default.

**This is the next infrastructure step**, genuinely deferred rather than solved: a
Supabase queue or `pg_cron` worker polling for `RUNNING` runs, or a small
always-on worker. The live run view needs no change either way — it polls
`GET /api/runs/{id}`, which reads persisted events.

### 4. Auth and the destructive routes — done

Every internal route requires a valid session token. Supabase Auth was not used:
for an internal tool a single shared password has fewer moving parts and no
network dependency on the login path. The seam is `auth.py` alone.

The token is stateless, which is what makes it a good fit for serverless — there
is no session store for a second invocation to miss — and also what makes it
irrevocable before `exp`. That trade-off is recorded in
`10-assumptions-and-scope.md`, and a deny-list or a shorter TTL is the answer if
it ever stops being acceptable.

There is no destructive route to restrict: nothing over HTTP can delete a run, a
finding or an audit event, in any environment.

## What production would need beyond a lift-and-shift

Not migration work — genuine gaps, in the order I would close them:

1. **Per-person identity, then RBAC.** Access is one shared password, so the
   audit trail records `user:operator` rather than a name and everyone can do
   everything. Restoring *who* did something is the first step; roles are the
   second. The seam for both is `auth.py` alone.
2. **Encryption at rest for `ai_call` payloads.** They contain account numbers by
   design; today that is a plain column.
3. **Retention policy** for documents and audit events.
4. **Rate limiting** on `/api/login`, submit and send.
5. **The deferred rules** — sanctions screening, duplicate/reused bank account
   detection, live tax ID existence checks (see `10-assumptions-and-scope.md`).
6. **Observability** — structured logs, error tracking, per-stage latency and
   token-spend metrics.
7. **Idempotency** on resubmission, with `parent_run_id` linking a corrected
   submission to the run it replaces.

## Cost sketch

Anthropic is the only per-run cost: roughly 3 extraction calls at ~2.2k in / ~60
out, plus a drafting call on PENDING runs. At `claude-opus-5` rates that is a
fraction of a cent per vendor. Vercel and Supabase free tiers comfortably cover
demo and pilot volume; "dozens of new vendors a quarter" is not a scaling problem.

## The local demo is unchanged

The brief asks for a process that **runs live** during an interview. With no
Supabase variables set, the app behaves exactly as before: one process, a file
database, local documents, one command to start. The production backends are
opt-in by configuration, so the demo cannot fail for reasons outside the room.

**The one thing the demo does need is a build.** React is the whole UI now, so
`npm run build` has to have been run at least once before the interview; a backend
started without `frontend/dist` logs a warning at start-up and answers 503 with
the same instruction, rather than failing in a way anyone has to debug on camera.
Running `npm run dev` alongside the backend works too, and is what the build-free
path costs: two processes instead of one.
