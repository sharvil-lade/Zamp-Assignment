# 10 · Assumptions and Scope

The case study says: *"treat ambiguity as part of the exercise. Make an assumption, note it somewhere you can reference in the live pitch, and move on."* This file is that note.

## Explicit assumptions

| # | Assumption | Why it's reasonable |
|---|---|---|
| A1 | The vendor submits through a **web form we control**, not by email | The brief says "you decide what the submission looks like". A structured form is what every real supplier portal does. |
| A2 | **India is the primary jurisdiction**, with the US as a second country | Indian identifiers (GSTIN, PAN, IFSC) are self-describing and support genuine cross-field checks with no external calls. That is the strongest available demonstration in a week. |
| A3 | Sample documents are **synthetic PDFs we generate** | The FAQ explicitly permits this: *"create vendor submission forms or JSON"*. |
| A4 | **One reviewer, no authentication** | No multi-user requirement in the brief. Identity would be a text field if needed. |
| A5 | Format validity is checked; **existence is not** | A tax ID can pass a checksum and still be unissued or cancelled. Existence needs a live registry call — deliberately deferred, see below. |
| A6 | The follow-up email is **drafted, not sent** | The human-in-the-loop gate is a product decision, and it removes SMTP from scope. |
| A7 | `expected_annual_spend`, payment terms, and commercial fields are **not collected** | They fed a risk-tier feature that was cut. Uncollected rather than unused. |
| A8 | Documents are **single-page, English, digital or clean scans** | Multi-page and multi-language extraction is a real problem; it is not this problem. |
| A9 | A **~400 ms per-stage pause** is intentional | Demo legibility. Marked in code as removable. |
| A10 | Thresholds 0.92 / 0.75 / 0.70 are **tuned once against the sample set** | Four numbers nobody will change do not need a config system. |
| A12 | A **refused attachment counts as "not attached"** | The brief names "vendors attach the wrong documents" as a normal case, so it must be a fixable finding (R02), not a failed run. |
| A13 | **Reviewer identity is a free-text field** | Follows from A4. Real auth is the first thing production adds — see `13-deployment-plan.md`. |
| A11 | The **PDF is the source of truth over the form** where they disagree | A document is harder to fabricate casually than a text input. This is why R09/R10/R12 compare extracted values against typed ones and not the reverse. |

## MVP scope — what ships

- 14-field submission form + 3 document uploads
- 7-stage pipeline with visible per-stage execution
- **12 deterministic rules**, BLOCK/FIX severity
- **3-line `decide()`** — the only thing that sets a status
- AI in exactly 3 places: extraction, ambiguous name matching, follow-up drafting
- **4 demo scenarios**: happy path, missing/expired documents, bank beneficiary mismatch, cross-field identity contradiction
- Live run view, dashboard, extracted-vs-form panel, audit timeline, JSON export
- Human send gate on the follow-up
- `/reset`
- One test file

## Deferred — named, not built

These are real features of real systems. Each is out of scope on purpose, and each has a stated seam.

| Deferred | Seam |
|---|---|
| **Sanctions / PEP screening** | A stage-5 rule against a name list. One function + a data source. |
| **Duplicate vendor + reused bank account detection** | A stage-5 rule against a vendor master table. Needs seeded state — which is exactly what its removal deleted. |
| **Penny-drop bank verification** | Replaces R09's document comparison with a bank-returned beneficiary name. Same rule, better input. |
| **Live tax ID existence checks** (VIES, GST portal) | A new rule alongside R04. Format stays offline; existence becomes a call. |
| **Reviewer queue, override-with-note, maker-checker** | A new event type on the existing append-only log. `decide()` unchanged. |
| **Risk scoring / tiering** | Explicitly rejected, not merely deferred — see `04-decision-engine.md`. |
| **Ongoing monitoring, re-screening, UBO traversal** | A scheduler over stored runs. |
| **ERP / AP system sync** | A write step after `APPROVED`. |
| **Email sending, vendor reply threading, resubmission linking** | SMTP plus a `parent_run_id` column. |
| **Multi-page, multi-language document extraction** | A prompt and schema change in `extract.py`. |
| **Additional countries** | Fixtures and an enum entry, not new rule logic. |

## External integrations intentionally NOT implemented

No live GST portal lookup. No VIES. No penny drop. No OFAC or sanctions feed. No corporate registry API. No credit bureau. No email provider.

**The reasoning, ready for the interview:** every one of these is an auth flow, a rate limit, a network dependency, and a live-demo failure mode. Wiring a real KYB vendor into a case study spends a day of a six-day budget proving something nobody asked to see. The checks that shipped need **zero external dependencies** and still catch the fraud pattern the brief names.

If asked *"where's sanctions screening?"* the answer is: **"Deliberately out of the MVP. It's a stage-5 rule against a name list — a function and a data source. I spent the time on the cross-field checks that need no external dependency, because those are the ones that catch the case the brief actually describes."**

## Technical tradeoffs

| Chose | Over | Because |
|---|---|---|
| HTMX polling every 700 ms | SSE / WebSockets | One HTML attribute vs. streaming code and reconnect logic. Nothing here needs sub-second latency. |
| SQLite | Postgres | Single file, zero setup, no second process to fail at demo time. |
| Raw `sqlite3` | SQLAlchemy | Three tables and about ten queries. An ORM is more code, not less. |
| Server-rendered Jinja | React | No build step, no bundler, no npm. Four pages. |
| Claude native PDF input | Tesseract / OCR pipeline | Replaces a day of preprocessing with one API parameter, and removes the digital-vs-scanned branch entirely. |
| `difflib` (stdlib) | `rapidfuzz`, `fuzzywuzzy` | Already installed. A dependency for one ratio call is not worth it. |
| Module constants | A config file | Four numbers, tuned once. |
| `BackgroundTasks` | Celery + Redis | One reviewer, a handful of runs. Two extra processes for nothing. |
| Findings-then-decide | Rules returning statuses directly | Makes reasoning visible for free, and makes `decide()` untouchable by rule changes. |
| Deterministic decision | LLM-as-judge | Reproducible on camera, testable without mocking, explainable to a non-technical buyer. |

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
| **Error surfaces** | Browsers get a readable page, API clients get JSON. No traceback ever reaches a response |

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
| P1 | **Authentication and RBAC** | Single reviewer on one machine (A4). `/reset` is unauthenticated and destructive — it exists for demo repeatability. |
| P2 | **Encryption at rest for `ai_call` payloads** | They hold account numbers by design; that *is* the auditable record. Today it is a plain column. |
| P3 | **Durable storage and a real database** | SQLite and a local `uploads/` directory do not survive serverless. One module (`store.py`) plus a file adapter. |
| P4 | **A real job runner** | `BackgroundTasks` does not outlive a serverless response. The live run view needs no change — it reads persisted events. |
| P5 | **Concurrency safety** | `_next_run_id` reads `MAX(run_id)`; correct for one process, racy under load. A sequence fixes it. |
| P6 | **Rate limiting, CSRF, retention policy** | Standard middleware and policy; no design work outstanding. |
| P7 | **Observability** | Structured logs, error tracking, per-stage latency and token spend. |
| P8 | **Resubmission linking** | A `parent_run_id` column so a corrected submission points at the run it replaces. |

## Known limitations — state these before being asked

1. **No existence verification.** A structurally perfect but unissued GSTIN passes. Mitigation is a registry call; it is deferred, not overlooked.
2. **Extraction is unverified against a second source.** If the model misreads an account number, R10 fires a false positive. The extracted-vs-form panel exists precisely so a human can catch this.
3. **No duplicate detection.** The same vendor submitted twice produces two independent Approvals.
4. **Single-jurisdiction depth.** The cross-field checks are India-specific. The US path validates format only.
5. **No persistence of uploaded documents beyond the run.** `/reset` deletes them.
6. **No authentication.** Anyone who can reach the app can submit, review, and reset. Single
   reviewer, single machine — assumption A4.
7. **No rate limiting or CSRF protection.** Out of scope for a local demo; both are standard
   middleware if this were ever exposed.
8. **`/reset` is unauthenticated and destructive.** It exists for demo repeatability and is
   confirm-gated in the UI only.

Volunteering these is stronger than being caught by them. The brief says a strong submission handles edge cases *deliberately* — knowing precisely where your boundary is counts as deliberate.
