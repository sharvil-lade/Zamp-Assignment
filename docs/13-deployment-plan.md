# 13 · Deployment Plan (not implemented)

**Status: planned only.** No Vercel configuration, no Postgres, no Supabase code
exists in the repository. The local SQLite MVP is intentionally intact and is
what gets demonstrated. This document exists so the question *"how would you ship
this?"* has a concrete answer rather than an improvised one.

## Target architecture

**Vercel + FastAPI + Supabase PostgreSQL + Supabase Storage + Anthropic API**

```
        browser
           |  HTTPS
           v
   +-------------------+        Vercel (serverless functions, Python runtime)
   |  FastAPI (ASGI)   |        - routes, Jinja rendering, HTMX fragments
   |  app.py           |        - stateless: no local disk, no in-process state
   +---+-----------+---+
       |           |
       |           +------------------> Anthropic API
       |                                extraction · name matching · drafting
       |
       +--> Supabase PostgreSQL         runs · findings · events
       |
       +--> Supabase Storage            uploaded documents (private bucket)
```

## What changes, and what does not

| Layer | Local (today) | Production | Blast radius |
|---|---|---|---|
| Web | uvicorn, one process | Vercel serverless function | none — same ASGI app |
| DB | `sqlite3`, `vendor.db` | Supabase Postgres | **`store.py` only** |
| Files | `uploads/<run_id>/` | Supabase Storage bucket | `store.upload_dir` / `saved_documents`, `app._save_uploads` |
| Background work | `BackgroundTasks` | queue or step function | `app.submit` |
| Secrets | `.env` | Vercel env vars | none |
| Rules / decision | pure functions | **unchanged** | none |
| AI modules | Anthropic SDK | **unchanged** | none |

**`rules.py`, `matching.py`, `extract.py`, `pipeline.py` and every template are
untouched by this migration.** That is the payoff of putting all SQL behind
`store.py` and all business logic behind pure functions — the storage swap is one
module and a file adapter.

## The four things that actually need work

### 1. SQLite → Postgres (`store.py`)

Same three tables. Real changes:

- `INTEGER PRIMARY KEY` → `BIGSERIAL`; `TEXT` timestamps → `TIMESTAMPTZ`.
- `submission_json` / `extracted_json` / `detail_json` → `JSONB` (queryable, so
  "every run where R06 fired" becomes a real query).
- Parameter style `?` → `%s`, connections via a pooler (`psycopg` + Supabase's
  pgBouncer — serverless makes connection count the first thing to bite).
- `_next_run_id` currently reads `MAX(run_id)` and increments. That is safe for
  one process and **racy under concurrency** — replace with a sequence.

### 2. Local disk → Supabase Storage

Serverless functions have no durable filesystem, and `/tmp` does not survive
between invocations. `store.upload_dir()` and `saved_documents()` become
bucket operations; `extract._content_block` reads bytes from Storage instead of a
`Path`. The upload validation in `extract.check_upload` is unchanged — it already
takes `(filename, bytes)`.

The bucket must be **private**, with signed URLs for reviewer download only.

### 3. Background execution

`BackgroundTasks` does not survive a serverless response returning. Options in
increasing order of effort:

- **Run synchronously** and lose the live stage view (unacceptable — it is graded).
- **Supabase queue / pg_cron worker** polling for `RUNNING` runs.
- **Vercel background functions** or a small always-on worker.

The live run view needs no change either way: it polls `/run/{id}/stages`, which
reads persisted events. Whatever executes the pipeline just has to write them.

### 4. Auth and the destructive routes

`/reset` is unauthenticated and wipes everything. That cannot ship. Minimum:
Supabase Auth in front of the reviewer routes, and `/reset` removed or restricted
to a non-production environment.

## What production would need beyond a lift-and-shift

Not migration work — genuine gaps, in the order I would close them:

1. **Auth + RBAC** (submitter vs reviewer), and a reviewer identity that is not a
   free-text field.
2. **Encryption at rest for `ai_call` payloads.** They contain account numbers by
   design; today that is a plain column.
3. **Retention policy** for documents and audit events.
4. **Rate limiting and CSRF** on the submit and send routes.
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

## Why none of this is built

The brief asks for a process that **runs live** during an interview. Every item
above adds a network dependency, an auth flow, or a cold start between the
demonstrator and a working demo. A local single-process app with a file database
starts in one command and cannot fail for reasons outside the room.

The migration is real work but it is *known* work, and it touches one module plus
a file adapter. Knowing precisely where the seam is counts for more than having
crossed it.
