# 10 · Assumptions and Scope

The case study says: *"treat ambiguity as part of the exercise. Make an assumption, note it somewhere you can reference in the live pitch, and move on."* This file is that note.

## Explicit assumptions

| # | Assumption | Why it's reasonable |
|---|---|---|
| A1 | The vendor submits through a **web form we control**, not by email | The brief says "you decide what the submission looks like". A structured form is what every real supplier portal does. |
| A2 | **India is the primary jurisdiction**, with the US as a second country | Indian identifiers (GSTIN, PAN, IFSC) are self-describing and support genuine cross-field checks with no external calls. That is the strongest available demonstration in a week. |
| A3 | Sample documents are **synthetic PDFs we generate** | The FAQ explicitly permits this: *"create vendor submission forms or JSON"*. |
| A4 | ~~One reviewer, no authentication~~ **Superseded in Phase 3.** Employee routes now require a login; identity is recorded on cases and audit events. | See `14-auth-and-infrastructure.md`. |
| A5 | Format validity is checked; **existence is not** | A tax ID can pass a checksum and still be unissued or cancelled. Existence needs a live registry call — deliberately deferred, see below. |
| A6 | The follow-up email is **drafted, not sent** | The human-in-the-loop gate is a product decision, and it removes SMTP from scope. |
| A7 | `expected_annual_spend`, payment terms, and commercial fields are **not collected** | They fed a risk-tier feature that was cut. Uncollected rather than unused. |
| A8 | Documents are **single-page, English, digital or clean scans** | Multi-page and multi-language extraction is a real problem; it is not this problem. |
| A22 | **Document checking is deterministic and self-contained.** What is checked is type, extractability, format, readability and cross-consistency — and nothing more | Every one of those five questions can be answered offline, in milliseconds, with the same answer every time. Authenticity cannot. See *The document verification boundary* below. |
| A9 | A **~400 ms per-stage pause** is intentional | Demo legibility. Marked in code as removable. |
| A10 | Thresholds 0.92 / 0.75 / 0.70 are **tuned once against the sample set** | Four numbers nobody will change do not need a config system. |
| A12 | A **refused attachment counts as "not attached"** | The brief names "vendors attach the wrong documents" as a normal case, so it must be a fixable finding (R02), not a failed run. |
| A14 | **The vendor link is the whole authentication** for a vendor | No account, no password, no email round-trip. The token is 32 random bytes, single-use, and scoped to one case — appropriate for an MVP where the alternative is a full identity system. Expiry is the obvious next control. |
| A16 | **One shared password in an environment variable**, not a user database | The employee side is an internal tool for a handful of reviewers; the access control that matters guards the *vendor* side, which has its own one-time link token. Supabase Auth is the seam if that changes, and it is confined to `auth.py`. |
| A17 | **One shared password (`APP_PASSWORD`), a 12-hour session, and no per-person identity, refresh token, lockout or MFA** | The employee side is an internal tool behind a single door; the access control that matters is on the *vendor* side, which has its own one-time link token. The cost is that the audit trail records `user:operator` rather than a name — deliberate, and reversible by restoring a directory behind `auth.py`. Rate limiting on `/login` is the first thing to add. |
| A20 | **A stateless access token cannot be revoked before it expires** | Signing out is the client discarding the token — there is no server-side session to delete, and the signature stays valid until `exp` either way. A stolen token is therefore usable for at most the TTL. The seam is a deny-list keyed on `sub` plus `iat`, or a short TTL with refresh; both reintroduce server-side state, which is exactly what this design traded away. |
| A21 | **The token lives in `localStorage`** | It survives a reload, which is the difference between a usable internal tool and one that signs you out every time you open a run in a new tab. The cost is that any script running on this origin could read it, so an XSS bug is a credential leak. An HTTP-only cookie moves the risk to CSRF instead; neither is free, and the token was chosen for the stateless serverless shape. |
| A13 | **Reviewer identity is a free-text field** | Follows from A4. Real auth is the first thing production adds — see `13-deployment-plan.md`. |
| A11 | The **PDF is the source of truth over the form** where they disagree | A document is harder to fabricate casually than a text input. This is why R09, R10, R12, R15, R16 and R18 compare extracted values against typed ones and not the reverse. |
| A18 | A **custom form field gets no business rule** — only completeness, type and allowed options | A rule invented for an arbitrary field would be a guess dressed as a decision. Fields that map onto a PS-2 canonical hit the existing deterministic rules unchanged; everything else is checked structurally and shown to the AI Employee and a human. |
| A19 | A **form is edited in place, and a case keeps a snapshot of what it said** | Editing the live form would otherwise rewrite what a vendor was actually asked, which breaks the audit trail. The snapshot is what an immutable published version was for, without the version table. |
| A23 | The **street address is collected and not matched** | A street address has many correct spellings, so a fuzzy comparison against the address proof would manufacture findings on honest vendors. R18 checks the addressee name and the state instead. See *The address is collected, not matched* below. |
| A24 | The **standard form is regenerated whenever it falls behind the engine**, and preserved whenever it has merely been edited | A vendor filling in a form that cannot satisfy the rules is a worse outcome than a lost edit. Anyone wanting a permanently different form duplicates it. |

## MVP scope — what ships

- 15-field submission form + 5 document slots (PAN card, GST certificate, incorporation or business registration, cancelled cheque or bank letter, address proof), of which a given submission is asked only for the ones it needs
- 7-stage pipeline with visible per-stage execution
- **17 deterministic rules**, BLOCK/FIX severity
- **3-line `decide()`** — the only thing that sets a status
- AI in exactly 3 places: extraction, ambiguous name matching, follow-up drafting
- **4 demo scenarios**: happy path, incomplete submission, bank beneficiary mismatch, cross-field identity contradiction
- Live run view, dashboard, extracted-vs-form panel, audit timeline, JSON export
- Human send gate on the follow-up
- **Configurable onboarding forms** — edited in place, with each case snapshotting the schema it
  was created against; the standard one is generated from the rule engine and rebuilt if it ever
  falls behind it
- **A REST API at `/api`** and a **React 18 front end** served at the root — the
  only UI
- Two test suites: pytest for the backend, vitest for the frontend

## Deferred — named, not built

These are real features of real systems. Each is out of scope on purpose, and each has a stated seam.

| Deferred | Seam |
|---|---|
| **Sanctions / PEP screening** | A stage-5 rule against a name list. One function + a data source. |
| **Duplicate vendor + reused bank account detection** | A stage-5 rule against a vendor master table. Needs seeded state — which is exactly what its removal deleted. |
| **Penny-drop bank verification** | Replaces R09's document comparison with a bank-returned beneficiary name. Same rule, better input. |
| **Live tax ID existence checks** (VIES, GST portal, NSDL PAN) | A new rule alongside R04 and R16. Format, checksum and cross-checks stay offline; existence becomes a call. |
| **Reviewer queue, override-with-note, maker-checker** | A new event type on the existing append-only log. `decide()` unchanged. |
| **Risk scoring / tiering** | Explicitly rejected, not merely deferred — see `04-decision-engine.md`. |
| **Ongoing monitoring, re-screening, UBO traversal** | A scheduler over stored runs. |
| **ERP / AP system sync** | A write step after `APPROVED`. |
| **Email sending and vendor reply threading** | SMTP. The draft is written and a human sends it from their own client; the correction itself comes back through the secure link, not by email. |
| **Multi-page, multi-language document extraction** | A prompt and schema change in `extract.py`. |
| **Document authenticity — signatures, QR codes, tamper forensics** | A new stage-4 rule per signal. Deliberately out of scope, see *The document verification boundary*. |
| **A sixth document type** | A schema, a prompt, a keyword set for R13, a required-field pair for R14, and at least one rule that reads it. The cost is the point: a document nothing can contradict does not earn a slot. |
| **Additional countries** | Fixtures and an enum entry, not new rule logic. |

## The document verification boundary

The user set this boundary explicitly, so it is written down explicitly rather than left to be
inferred from what the code happens to do.

**Document verification in this system is deterministic and self-contained.** For every attached
document the engine asks exactly five questions, and every one of them is answered offline:

| # | Question | Owned by |
|---|---|---|
| 1 | **Type** — is this the kind of document it was uploaded as? | R13, against what the document calls itself |
| 2 | **Extractability** — did the file parse and yield a JSON object at all? | Stage 3, plus the upload guard that rejects a renamed or truncated file at the boundary |
| 3 | **Format** — is the identifier printed on it well formed? | R17 (CIN, LLPIN), and R03/R04/R05 for the typed identifiers |
| 4 | **Readability** — could the fields that make it useful actually be read? | R14 |
| 5 | **Cross-consistency** — does what it says match what the vendor typed and what the other documents say? | R09, R10, R12, R15, R16, R18 |

**And nothing more.** Specifically, and by decision rather than by oversight:

- **No external verification services.** No NSDL or Protean PAN lookup, no GST portal API, no MCA
  registry search, no bank penny-drop, no address or utility-account verification. PAN and GST
  "verification" here means exactly what it meant before the document set changed — format,
  checksum and internal consistency (R03, R04, R06, R07, R08) — plus the new document
  cross-checks (R15, R16). Adding two documents added two more ways to *contradict* the vendor's
  own claims; it added no way to confirm those claims against a third party, and the docs should
  not imply otherwise.
- **No authenticity or tamper detection.** Nothing checks a digital signature, a QR code, an
  embedded verification URL, EXIF or PDF producer metadata, font or layout forensics, or
  copy-paste artefacts in a scan. A cleanly forged PAN card that agrees with a cleanly forged GST
  certificate passes every rule here, and saying so plainly is more useful than a claim that
  cannot survive a follow-up question.
- **No additional document types.** Five slots, fixed. Not because a sixth is hard, but because
  each one costs a schema, a prompt, a keyword set, a required-field pair and at least one rule —
  and a document nothing can contradict earns none of that. The insurance certificate was removed
  for exactly this reason: its expiry date was the only thing about it any rule could read, and
  that made it a filing requirement rather than a validation input.

**Why this is the right boundary for this build.** Every check that ships is instant, free,
reproducible on camera, and explainable in one sentence to a procurement lead. Every check that
does not ship needs an auth flow, a rate limit, a network dependency and a live-demo failure mode.
The honest framing is not "we verify documents" but **"we check that a vendor's documents agree
with their form, with each other, and with themselves."** That is a smaller claim, and it is one
the code actually keeps.

The seam, if it is ever wanted, is a new rule alongside R15 and R16 that calls out and turns the
response into a finding. `decide()` does not change, the stage list does not change, and the
severity question — is an unverifiable identifier a BLOCK or a FIX? — is the only genuinely new
design decision.

## The address is collected, not matched

This sits alongside the boundary above, and for the same reason: it is a limit chosen on purpose,
so it is written down rather than left for someone to discover by reading `DOCUMENT_COMPARISONS`.

The form asks for a full `registered_address`. The address proof yields the address printed on it.
The run page shows the two beside each other. **Nothing compares them, and no rule ever fires on
the pair.**

**Why not.** A street address is not an identifier. A PAN is ten characters with exactly one
correct rendering, and comparing two of them is arithmetic. An address is prose. The same building
is `42 Industrial Layout, Koramangala, Bengaluru`, `#42, Industrial Layout, 2nd Cross, Koramangala,
Bengaluru 560034`, `No. 42, Indl. Layout, Koramangala` and half a dozen more, all of them correct,
none of them equal to another under any string comparison. Every mechanism available here fails on
that:

| Approach | What it would actually do |
|---|---|
| Exact or case-folded match | Fire on nearly every honest vendor. An abbreviation, a PIN code, a missing comma is enough. |
| Token or fuzzy similarity | A threshold with no defensible value. Set it loose and it passes a different building on the same street; set it tight and it is the exact match again. |
| Ask the model "same address?" | Introduces a judgment call, an API call and a non-deterministic finding into the one place where the consequence of a false positive is a vendor being told their utility bill is wrong. |

A rule that cries wolf on correct data is worse than no rule at all: it trains the reviewer to
click past findings, which is exactly the habit this system exists to remove. So the comparison
row carries a `None` rule id in `rules.DOCUMENT_COMPARISONS`, is rendered unhighlighted, and the
reviewer reads it themselves — which is a thing a human is genuinely better at than a string
comparator.

**What is still checked.** R18 takes the two parts of an address that do have one correct answer:
the name the document is addressed to, and the state. R08 independently checks the state against
the GSTIN's state code. So a vendor claiming Maharashtra while their electricity bill is issued in
Karnataka is still caught, twice, from two directions — see EC-4. The street line is the only part
left unjudged.

**Why collect it at all, then.** Because an address proof is meaningless unless you know which
address it is meant to prove, because R01 having the field means a blank one is still reported, and
because the reviewer needs to see what the vendor claims next to what the paper says. Collecting a
value and being explicit that nothing scores it is more honest than either omitting the field or
pretending to check it.

**The seam.** An address-normalisation service — a postal API returning a canonical form for both
strings — turns this into a real comparison, and it would arrive as one more rule id in
`DOCUMENT_COMPARISONS` and one more branch in R18. That it is a one-line change is the point: the
limit is in what can be checked offline, not in the shape of the code.

## External integrations intentionally NOT implemented

No live GST portal lookup. No VIES. No penny drop. No OFAC or sanctions feed. No corporate registry API. No credit bureau. No email provider.

**The reasoning, ready for the interview:** every one of these is an auth flow, a rate limit, a network dependency, and a live-demo failure mode. Wiring a real KYB vendor into a case study spends a day of a six-day budget proving something nobody asked to see. The checks that shipped need **zero external dependencies** and still catch the fraud pattern the brief names.

If asked *"where's sanctions screening?"* the answer is: **"Deliberately out of the MVP. It's a stage-5 rule against a name list — a function and a data source. I spent the time on the cross-field checks that need no external dependency, because those are the ones that catch the case the brief actually describes."**

## Technical tradeoffs

| Chose | Over | Because |
|---|---|---|
| Polling `GET /api/runs/{id}` every 1200 ms | SSE / WebSockets | One `setInterval` vs. streaming code and reconnect logic. Nothing here needs sub-second latency. |
| SQLite | Postgres | Single file, zero setup, no second process to fail at demo time. |
| Raw `sqlite3` | SQLAlchemy | Six tables and a few dozen queries. An ORM is more code, not less. |
| Claude native PDF input | Tesseract / OCR pipeline | Replaces a day of preprocessing with one API parameter, and removes the digital-vs-scanned branch entirely. |
| `difflib` (stdlib) | `rapidfuzz`, `fuzzywuzzy` | Already installed. A dependency for one ratio call is not worth it. |
| Module constants | A config file | Four numbers, tuned once. |
| `BackgroundTasks` | Celery + Redis | One reviewer, a handful of runs. Two extra processes for nothing. |
| Findings-then-decide | Rules returning statuses directly | Makes reasoning visible for free, and makes `decide()` untouchable by rule changes. |
| Deterministic decision | LLM-as-judge | Reproducible on camera, testable without mocking, explainable to a non-technical buyer. |

## Frontend scope calls

A React front end was added after the server-rendered one already worked. Four
decisions shaped it, and each was a deliberate subtraction.

| Chose | Over | Because |
|---|---|---|
| **Plain JSX** | TypeScript | The types would describe payloads that are already defined once, in the API routers that produce them. A second declaration of the same shape in a second language drifts, and nothing here is complex enough to need the compiler to hold it. `api.js` is the only place a payload shape matters, and it is 100 lines. |
| **One plain CSS file** | Tailwind, CSS modules, a component library | Ten screens and one visual language. A PostCSS pipeline would add a toolchain that can break the build in exchange for class names instead of selectors. `styles.css` is hand-written and read top to bottom. |
| **React state + two hooks** | Redux, Zustand, React Query | `useSession` holds the one genuinely global fact — who is signed in. `useResource` holds loading / error / data / reload, which is every page's entire state machine. Ten screens, no shared mutable state between them, no cache to invalidate. |
| **Uncontrolled form inputs + `FormData`** | Controlled inputs, a form library | The forms carry file uploads, so they submit as multipart anyway. `FormData` reads the DOM directly; mirroring every keystroke into React state would be work in service of nothing. |

### The Jinja layer was removed, not kept beside React

This is the scope call most worth defending, because for a while the plan was the
opposite. The server-rendered pages were kept while React was being built — they
carried the behavioural test coverage and needed no build step — and they were
deleted once React covered every route, including the two things that had only
ever existed server-side.

- **Two renderings of the same product is a standing tax.** Every new screen had
  to be built twice or knowingly built once, and the second answer was starting
  to win. Form authoring already existed only in React; keeping the Jinja pages
  meant shipping a product where the fallback quietly did less than the real UI.
- **The coverage moved rather than died.** The route tests were repointed at
  `/api` and the rendering tests were rewritten in vitest against the components.
  The claims each test protects — the send gate, the 409 on a replayed vendor
  link, upload refusal, vendor surface isolation — are still asserted, on the code
  that now serves them.
- **What went with it.** `jinja2` and `itsdangerous` left `requirements.txt`,
  `backend/templates/` was deleted, and `app.py` lost its HTML error page, its
  `303`-to-login redirect and the `Accept`-header branch that chose between them.
  Everything under `/api` answers in JSON; everything else is the bundle.

The honest version is that this cost the demo its build-step-free fallback: a
broken bundle now takes the UI down, where before the root routes would still
render. `npm run build` before the interview, and `frontend/dist` committed to the
deployed tree, is the mitigation — a worse one than a second front end, and still
cheaper than maintaining two.

### Custom form fields get no invented rules

Configurable forms could easily have grown a rule builder. They did not.

A field that carries `canonical:` **is** the PS-2 field it names — it flows into
`rules.py` unchanged and every existing deterministic rule applies. A field that
does not gets three checks only: required, type, allowed options. Nothing else.

The alternative was inferring business meaning from a label, which produces a rule
nobody wrote, that nobody can point at, in a system whose whole argument is that
the reasoning is visible. Where a custom answer genuinely matters, the AI Employee
sees it — labelled explicitly as carrying no rule — and a human decides.

## Security and hygiene posture

Not a security product, but the obvious boundaries are held:

| Control | Where |
|---|---|
| **Upload allow-list** | `.pdf` `.png` `.jpg` `.jpeg` only, checked by extension **and magic bytes** — a renamed `.exe` is refused |
| **Upload size cap** | 10 MB; the request body is read with a bounded `read()`, never unbounded into memory |
| **Empty / corrupt files** | Refused at the boundary with a readable reason |
| **Path traversal** | The uploaded filename is discarded — only its extension is used, and the file is written as `<doc_type><ext>`. `store.upload_dir()` additionally refuses any id that is not `VS-\d{4,}` |
| **Secret redaction** | Provider errors are echoed into the audit trail, so anything matching `sk-[A-Za-z0-9_-]{8,}` is replaced before persistence |
| **No secrets in source** | Asserted by a test that scans every shipped module and template |
| **`.gitignore`** | `.env`, `vendor.db`, `uploads/`, caches and Python artifacts; `.env.example` stays committed |
| **Error surfaces** | Everything answers in JSON through two handlers in `app.py`; React turns any non-2xx into one `ApiError`. No traceback ever reaches a response |
| **Employee authentication** | One shared password in `APP_PASSWORD`, compared with `hmac.compare_digest` in constant time. Never in source; production refuses to start on the default |
| **Session token** | `<expires_at>.<HMAC-SHA256>` signed with the password itself, 12-hour expiry, carried only in the `Authorization` header — never a query parameter, so it cannot land in an access log or a referrer. The expiry is inside the signature, so it cannot be extended. Changing the password invalidates every session at once |
| **Post-login redirect** | Where the visitor was headed is remembered in router state by `Layout`, not in a query parameter, so there is no `next=` for a crafted link to abuse |
| **Service-role key** | Server-side only. It is never sent to the browser, and it travels in an `Authorization` header, never a URL |
| **Production fallback refusal** | `config.verify()` refuses to start with SQLite or local disk when `APP_ENV=production` |
| **Vendor link tokens** | `secrets.token_urlsafe(32)`; only `sha256(token)` is stored. Lookup is by hash — a case id is never accepted as authorisation |
| **Token never logged** | It travels in the URL, so a `logging.Filter` rewrites `/vendor/onboard/<token>` to `/vendor/onboard/<redacted>` in uvicorn's access and error logs |
| **One link, one submission** | A submitted case returns 409 on replay, so a leaked link cannot start a second run |
| **Vendor surface isolation** | `/vendor/onboard/:token` is routed *outside* `<Layout>`, the session-gated employee shell, so the vendor portal is never one render away from employee navigation — no nav, no dashboard link, no Reset. Confirmation shows no status, findings, rules or run id. Asserted by tests that check the rendered page for internal vocabulary, ids and links |
| **CORS** | `allow_credentials=False`, because the credential is a header the client attaches deliberately rather than something the browser sends on its own. `allow_origins` is still an explicit list from `CORS_ORIGINS`, never a wildcard, and defaults to the Vite dev server only |

**A rejected attachment does not fail the run.** "Vendors attach the wrong documents" is in the
problem statement, so it must be a *fixable finding*: the file is not saved, R02 reports the
document as missing, and the reason is shown on the run page and recorded in the audit trail.
Before hardening, every bad upload produced `ERROR`.

**Document contents are in the audit trail by design** — `ai_call` events store the model's raw
response, which is the point of an auditable AI-assisted decision. Nothing is written to stdout or
the server log: verified that account numbers and key-shaped strings appear zero times in
`server.log` across a full demo run.

## Production considerations

What is deliberately absent here but mandatory before this handled real vendors, in the order it
would be closed. Detail and the migration seam: `13-deployment-plan.md`.

| # | Gap | Why it is out of scope now |
|---|---|---|
| P1 | ~~Authentication~~ **Done in Phase 3.** RBAC (reviewer vs admin) is still absent — every employee can do everything. | |
| P2 | **Encryption at rest for `ai_call` payloads** | They hold account numbers by design; that *is* the auditable record. Today it is a plain column. |
| P3 | ~~Durable storage and a real database~~ **Done in Phase 4.** Supabase Postgres + Supabase Storage, both behind their own module. | |
| P4 | **A real job runner** | `BackgroundTasks` does not outlive a serverless response. The live run view needs no change — it reads persisted events. |
| P5 | **Concurrency safety** | `_next_run_id` reads `MAX(run_id)`; correct for one process, racy under load. A sequence fixes it. |
| P6 | **Rate limiting and a retention policy** | Standard middleware and policy; no design work outstanding. CSRF is not on the list — the credential is a header, so a browser never attaches it cross-site. |
| P7 | **Observability** | Structured logs, error tracking, per-stage latency and token spend. |
| P8 | ~~Resubmission linking~~ **Done.** A case owns many runs (`runs.case_id`); a PENDING decision reopens the case, the vendor corrects through the same link, and every previous run keeps its findings and audit trail. |

## Known limitations — state these before being asked

1. **No existence verification.** A structurally perfect but unissued GSTIN passes. Mitigation is a registry call; it is deferred, not overlooked.
2. **Extraction is unverified against a second source.** If the model misreads an account number, R10 fires a false positive. The extracted-vs-form panel exists precisely so a human can catch this.
3. **No duplicate detection.** The same vendor submitted twice produces two independent Approvals.
4. **Single-jurisdiction depth.** The cross-field checks are India-specific. The US path validates format only.
5. **No retention policy for uploaded documents.** They live under the case prefix indefinitely; clearing them is an operator action, not an in-app one.
6. **No identity and no RBAC.** Access is one shared password, so the audit trail
   records `user:operator` rather than a person, and anyone who is in can do
   anything — edit any form, regenerate any vendor link.
7. **No rate limiting.** `/login` in particular is not rate limited, which is the first control
   to add. CSRF is not a live concern — the credential is a header, not a cookie, so a browser
   never attaches it to a cross-site request — but rate limiting is.
8. **Nothing over HTTP can destroy a run.** There is deliberately no reset or delete
   endpoint — an audit trail a signed-in user can wipe is not an audit trail. The cost is
   that clearing demo state is a manual operator action (see `08-ui-and-demo.md`).
9. **A form can only be deleted while nothing has used it.** The standard form is never
   deletable, and neither is one an onboarding was created from — a case must always be able to
   name where its questions came from. There is no archive state, so forms accumulate.

Volunteering these is stronger than being caught by them. The brief says a strong submission handles edge cases *deliberately* — knowing precisely where your boundary is counts as deliberate.
