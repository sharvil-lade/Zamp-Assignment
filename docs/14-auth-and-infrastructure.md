# 14 · Authentication and Infrastructure

Phase 3/4: employee login, Employee/Vendor isolation, Supabase Postgres, Supabase
Storage, and a Vercel-compatible shape. The PS-2 decision engine is unchanged.

```
                    ┌─────────────────┐
                    │    Employee     │
                    │   Authenticated │
                    └────────┬────────┘
                             │  Authorization: Bearer <token>
                             ▼
                    ┌─────────────────┐
                    │   FastAPI App   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
       Supabase DB    Supabase Storage   PS-2 Engine
              │              │              │
              └──────────────┼──────────────┘
                             │
                             ▼
                       AI Employee
                      (future phase)

Vendor ──secure token──> Vendor Portal ──> Submission ──> PS-2 Pipeline
```

## Authentication

**Approach: one shared password behind a signed session token.** No user table,
no directory, no roles, no OAuth, no password reset — this is a door, not an
identity system. The employee side is internal; the thing that actually needs
careful access control is the *vendor* side, and that has its own one-time link
token.

`POST /api/login` exchanges the password for a session token. Every later request
presents it in one place and one place only:

```
Authorization: Bearer <expires_at>.<signature>
```

Nothing is kept server-side. There is no session table, no session middleware and
no cookie — the token *is* the session, and it carries its own expiry.

| Piece | Where |
|---|---|
| The password | `APP_PASSWORD` env var. Falls back to `gozamp` so a fresh clone runs |
| Comparison | `auth.check_password` — `hmac.compare_digest`, constant time, exact bytes (no trimming, no case folding) |
| Session token | `auth.issue_token` — `<expires_at>.<HMAC-SHA256(expires_at, password)>`, standard library only |
| Lifetime | `auth.SESSION_TTL_SECONDS`, 12 hours. A constant, not a setting: one shared password has no operator who needs to tune it |
| Verification | `auth.valid_token` — signature checked with `compare_digest`, then the expiry |
| Transport | `auth.token_from` reads the `Authorization` header and nothing else — never a query parameter, which would land a working credential in access logs, browser history and referrer headers |
| Dependency | `auth.require_session` on every employee route |

The login response is `{access_token, token_type: "bearer", expires_in}` — no user
object, because there is no user. A failed login says *"That password was not
recognised."*

### The signature key is the password itself

There is no separate `SECRET_KEY` to manage. Signing the token with the password
means **changing `APP_PASSWORD` invalidates every existing session at once**,
which is the entire revocation story and costs nothing to operate.

The expiry is inside the signature, so pushing it out invalidates the token. A
forged, tampered, expired or foreign token is simply not valid — `valid_token`
returns `False` and `require_session` raises.

`config.verify()` refuses to start a production process still using the default
password. That is the one mistake this shape of login makes easy, so it is a hard
stop rather than a warning.

### There is no logout endpoint

A signed token cannot be recalled: the server holds nothing to delete, and the
signature stays valid until it expires regardless of what any endpoint claims.
Signing out is therefore the client discarding the token — `api.logout()` clears
it from memory and from `localStorage`, and `useSession.signOut` clears the
session flag with it.

The honest consequence is that a stolen token remains usable until it expires,
or until the password is changed. That trade-off is recorded in
`10-assumptions-and-scope.md`.

### Unauthenticated behaviour

`require_session` raises `NotAuthenticated`, and one handler in `app.py` turns it
into `401 {"detail": "authentication required"}`. There is no redirect and no
server-rendered error page: React owns navigation, so an unauthenticated API call
is a status code and the client decides to show the login screen. `Layout`
remembers where the visitor was headed and login sends them back there.

`GET /api/session` is the read-only counterpart, and the only thing an anonymous
caller may ask. React calls it on boot to find out whether a token left in
`localStorage` is still valid; it answers `{authenticated: false}` for no token
and for an expired one alike, which is the only distinction the client needs.

### The actor in the audit trail

| Action | Recorded as |
|---|---|
| Case created | `onboarding_cases.created_by_employee = user:operator` |
| Follow-up sent | `events.actor = user:operator`, detail carries `actor` and `edited` |

**A shared password cannot identify a person, so the trail does not pretend to.**
It records what is true — an authenticated operator acted — rather than inventing
a name. The `actor` still comes only from the session and never from the request
body; a test asserts a request body cannot spoof it.

This is a deliberate trade against the previous per-person attribution. Restoring
*who* did something means restoring a user directory, and the seam for that is
`auth.py` alone.

## Isolation

| | Employee | Vendor |
|---|---|---|
| Credential | one shared password, exchanged for a bearer session token | one 32-byte URL token |
| API routes | everything under `/api` except the two vendor routes | `/api/vendor/onboard/{token}` |
| Client routes | `/dashboard`, `/onboardings/*`, `/forms/*`, `/run/*` | `/vendor/onboard/{token}` |
| Sees | every case, run, finding, event, AI output | its own case, its own form, a confirmation |
| Where it renders | inside `<Layout>`, the session-gated employee shell | outside it — no nav, no links inward |

The vendor's token is the **only** authorisation input. `store.get_case_by_token`
hashes and looks up; there is no code path that accepts a case id or run id from
a vendor request. Holding a valid token grants nothing on the internal app —
asserted by the vendor isolation tests in both suites.

## Database

Two backends behind `store.py`, selected by `DATABASE_URL`:

| | Development | Production |
|---|---|---|
| Engine | SQLite (`vendor.db`) | Supabase Postgres via `psycopg` 3 |
| Placeholders | `?` | `%s`, translated by `store._q` |
| Integer keys | `INTEGER PRIMARY KEY` | `BIGINT GENERATED ALWAYS AS IDENTITY` |

Everything else is identical: same six tables, same columns, same types, same
semantics. Timestamps stay ISO-8601 `TEXT` and JSON payloads stay `TEXT`
deliberately — see the deviation note in `13-deployment-plan.md`.

**Nothing outside `store.py` contains SQL** — asserted by `test_no_sql_lives_outside_store`.

### Verifying the Postgres SQL without a server

`pglast` embeds the actual PostgreSQL parser. Every statement `store.py` would
send is parsed under the real grammar in `test_every_postgres_statement_parses_under_the_real_pg_grammar`,
and the DDL is asserted to create exactly the documented tables.

## Storage

Two backends behind `storage.py`, selected by `SUPABASE_URL` +
`SUPABASE_SERVICE_ROLE_KEY`:

| | Development | Production |
|---|---|---|
| Target | `./uploads/` | Supabase Storage, private bucket |
| Read | the file itself | downloaded to a temp file |

**Object keys are derived, never supplied.**
`onboarding/CASE-0001/bank_proof.pdf`. Every component is validated
(`storage.object_key`): the prefix must match `(onboarding|runs)/(CASE|VS)-\d{4,}`,
the document type must be one of ours, and the extension must be in the
allow-list. The vendor's filename is used for nothing but its extension, which
`extract.check_upload` has already validated by magic bytes.

**The pipeline is untouched.** `store.saved_documents()` returns `Path` objects
exactly as before; the Supabase backend materialises objects into a temp file
first. `extract.py` never learned that storage changed.

**Both prefixes are read.** A run is created before its case is bound, so
documents can legitimately land under either `runs/VS-nnnn` or
`onboarding/CASE-nnnn`. `saved_documents` reads both, most-specific wins — without
that, attaching a case silently orphaned documents already uploaded.

## Configuration

`config.py` is the only module that reads `os.environ`. Backends are selected
purely by which variables are present, and **`config.verify()` refuses to start a
production process that has fallen back to a development backend**:

```
APP_ENV=production without DATABASE_URL
  -> RuntimeError: Refusing to start: DATABASE_URL is not set —
     refusing to use SQLite in production
```

`config.summary()` reports which backend is active and is safe to log; it never
contains a credential.

One variable belongs to authentication: `APP_PASSWORD`. It is both the password
and the key the session token is signed with, so there is no second secret to
rotate. It is read once, in `config.py`; `auth.py` reads it from there rather
than reaching for the environment itself.

## Vercel

`vercel.json` builds the frontend and routes everything to the ASGI app. The build
command is `cd frontend && npm ci && npm run build` with `frontend/dist` as the
output directory; `backend/app.py` is the `@vercel/python` entrypoint; both
`/api/(.*)` and `/(.*)` are dispatched to it. One origin, one deployment.

Two things matter:

1. **`PIPELINE_MODE=inline`.** A `BackgroundTasks` job does not outlive the
   response on serverless. Inline runs the pipeline before responding — the
   vendor waits a few seconds, and the live run view simply finds the run already
   finished. `background` stays the local default because it is what makes the
   stage-by-stage view worth watching.
2. **No filesystem persistence.** SQLite and `./uploads` are development-only;
   both are refused in production by `config.verify()`.

Authentication needs no second thought on serverless: there is no server-side
session to share between invocations, which is one of the reasons a bearer token
is the right shape here.

A real job queue is the next infrastructure step, not this one — see
`13-deployment-plan.md`.
