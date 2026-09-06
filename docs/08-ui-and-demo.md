# 08 · UI and Demo

The brief states the UI is part of the grade and names two things explicitly: **a live run view showing each stage as it executes**, and **a dashboard showing history, status, and outputs across runs**. Both are built. Three screens, plus the vendor's own.

## Screen 1 · The vendor portal — *the only way in*

**Route:** `/vendor/onboard/:token`, which reads `GET /api/vendor/onboard/{token}` and posts
multipart back to the same path → background run → the employee watches it on the dashboard.

There is deliberately **no employee-side submission form**. A vendor's details are entered by
the vendor, once, against a link only they hold. Letting a procurement employee retype a
vendor's bank account into an internal form reintroduces exactly the transcription risk this
engine exists to catch — and it would mean the audit trail could not say who actually
asserted the account number.

- The form is rendered from the **schema snapshot** taken when the case was created, so
  editing the form afterwards cannot change what a vendor is part-way through answering.
- The standard schema is generated from `rules.py`, so a field added to the engine cannot
  leave a stale copy of the list behind in React.
- Five file inputs: PAN card · GST registration certificate · Certificate of incorporation or
  business registration · Cancelled cheque or bank letter · Address proof. An Indian vendor on
  a GSTIN is asked for all five; a US vendor on an EIN is only chased for the last three,
  because R02 asks for what the submission actually needs.
- The vendor sees their own company name and nothing internal — no status, no findings, no
  rule, no run id, and no route back into the employee application.

The employee side of this is `/onboardings/new`: pick the form, create the case, copy the
one-time link. The raw token is shown exactly once.

## The correction cycle

A PENDING decision reopens the case. The run page lists exactly what this run
found — generated from its own FIX findings, so no two Pending runs get the same
message — and offers one button to issue a secure correction link.

```
submission -> PENDING -> corrections listed -> link issued -> vendor corrects
           -> new run on the same case -> new decision
```

The vendor lands on their own form, prefilled with what they sent last time and
with their documents still attached, so they change what was wrong rather than
retyping fifteen fields — which is how a *new* transcription error gets
introduced. Their corrections create a **new run against the same case**: the
previous decision, its findings and its audit trail are untouched, and the run
page shows the rounds as a cycle.

Only a hash of the link token is ever stored, so the draft carries a
placeholder until an employee asks for a link. Nothing is emailed automatically —
sending is still a person clicking, recorded against the session.

## Screen 2 · Live run — *the graded screen*

**Route:** `/run/:runId`, which polls `GET /api/runs/{run_id}` every 1200 ms and aborts the
in-flight request on unmount.

The payload is the **whole run**, not just the stage list — one response updates stages,
findings, communication, comparison and timeline together, so nothing needs a reload when the
run completes. Polling stops when the run reports `finished`.

**It polls on the `run_finished` event, not on the status.** The status is persisted *before*
stage 7 so the decision is durable the instant it is made — which means a terminal status does
not mean the pipeline has finished. Polling on status stops the view while the follow-up is
still being drafted, and the draft never appears.

**Above the fold — the eight stages**, vertically, each in one of three states:

As implemented, with real timings from an EC-2 run:

```
[x] 1  Intake                                      400 ms
[x] 2  Completeness          3 finding(s)          406 ms
[x] 3  Extraction        AI  3 document(s)       11 902 ms
[x] 4  Format & documents                          403 ms
[x] 5  Consistency                                 412 ms
[x] 6  Decision              PENDING
[x] 7  AI Employee review AI  risk · summary      3 190 ms
[x] 8  Communicate       AI  draft created       4 714 ms
```

A stage that never ran because the submission carried no documents renders as
`skipped` ("no documents"), not as pending — extraction is the only stage that
can legitimately be skipped.

Stages carrying an `AI` badge are visibly marked. That badge is the fastest way to communicate
the architecture without saying a word. Extraction, AI Employee review and Communicate always
carry it; **Consistency carries it only when that run actually escalated a name comparison** — so on all four demo
scenarios it stays off, which is itself the point ("the fraud case didn't need the model").

**Finding cards** appear under their stage as they fire, severity-coloured (BLOCK red, FIX amber), each showing **both sides of the contradiction**:

```
+----------------------------------------------------------+
| R06   BLOCK   Consistency                                |
| GSTIN-embedded PAN does not match the submitted PAN      |
|   expected  ABCFS1234K                                   |
|   actual    ABCFS1234Z                                   |
+----------------------------------------------------------+
```

Showing both values is what turns a verdict into an explanation. A card that says only "PAN mismatch" is a worse product.

**Status banner** at the top once stage 6 completes: green Approved / amber Pending / red Rejected, with the count of BLOCK and FIX findings.

**Below the fold, four sections:**

1. **Extracted vs. form** — one table per document, five tables in all: form value, document
   value, and **the rule that compares them**. A highlighted row therefore reads as "this is why
   R09 fired", not just "these differ". The rows mirror the rules rather than the fields, which is
   what makes EC-3 legible: its form and cheque agree with each other, so the pair worth showing is
   *legal entity name* against *account holder on the document*.

   Some rows carry **no rule at all** and are never highlighted — the GST trade name, the bank
   name, the registration number, and the street address on the address proof. They are shown
   because a reviewer wants to see them; they are unhighlighted because nothing compares them.

   The address row is the one worth pointing at. Under the address proof there are now three rows:
   *Addressed to* and *State*, both owned by R18 and both highlightable, and *Address* — the
   `registered_address` the vendor typed beside the address printed on the document, with no rule
   id and no highlight, however far apart the two strings look. That is deliberate: a street
   address has many correct spellings, so a comparison would fire on honest vendors, and R18 checks
   the addressee and the state instead (`10-assumptions-and-scope.md`). A panel that highlighted
   rows nothing checks would be claiming checks the engine does not perform. The whole table is
   built from `rules.DOCUMENT_COMPARISONS`, so it cannot drift away from what the rules actually
   do — the unhighlighted rows are literally the ones whose rule id is `None`.
2. **Audit timeline** — every event with timestamp, stage, actor, duration. AI events expand to show model, response, and token usage.
3. **Follow-up draft** (PENDING only) — editable textarea, **Copy** button and
   **Mark as sent**. The sender is the signed-in employee, taken from the access
   token; it is not a field anyone types into. The badge reads *Drafted — not sent* in amber, then
   *Sent &lt;timestamp&gt;* in green, after which the textarea is read-only and the send
   button is gone. This is the human gate; nothing auto-sends. A REJECTED run shows
   an **Internal note** here instead, naming the blocking rules.
4. **Rejected attachments** — if a file was refused at upload (wrong type, empty,
   corrupt, oversized) an amber panel names the file and the reason. The document
   counts as not attached, so R02 reports it and the run is Pending, not Errored.
5. **Export JSON** — `GET /api/runs/{run_id}/export`, the entire run as one file.

Spend disproportionate build time here. Everything before Part 6 was plumbing; this screen is what gets pointed at.

## Screen 3 · Dashboard

**Route:** `/dashboard`, backed by `GET /api/dashboard?status=`

- Four stat tiles: Total · Approved · Pending · Rejected
- Table: `Run ID · Vendor · Status · Findings · Submitted · Duration`, newest first
- Status filter pills (All / Approved / Pending / Rejected / Error) via `?status=`;
  an unrecognised value falls back to All rather than erroring
- `ERROR` runs styled distinctly from `REJECTED` — different failure, different colour
- Row click opens the run detail

This is the brief's *"history, status, and outputs across runs"*, literally.

## Audit trail in the UI

Two surfaces, no third:

1. **Timeline** on the run page — human-readable, expandable.
2. **Export JSON** — machine-readable and complete. `GET /api/runs/{run_id}/export` returns seven
   sections: `run` · `submission` · `extracted` · `findings` · `communication` · `events` ·
   `exported_at`. Event details are decoded, so the file is readable without unpacking JSON
   strings. `communication` reports `draft_exists`, `draft`, `sent`, `sent_at`, `sent_by`, and
   the `internal_note` for rejected runs.

Spend 30 seconds of the demo on the export button. It quotes the brief's own pain statement — *"the only audit trail is whatever's in someone's inbox"* — back at them.

## Starting a take from an empty dashboard

There is deliberately **no reset endpoint**. Nothing reachable over HTTP can destroy a
run, a finding or an audit event — `test_there_is_no_endpoint_that_wipes_the_database`
locks that in, and the dashboard is asserted to offer no such button. An append-only
audit trail that a logged-in user can wipe is not an audit trail.

Clearing state is therefore an operator action, outside the app. Stop the server and:

```bash
rm -f vendor.db vendor.db-wal vendor.db-shm && rm -rf uploads/
```

Against Supabase, truncate `events`, `findings`, `runs` and `onboarding_cases` from the
SQL editor. Do this between video takes and again before the live interview — leftover
state changing a result mid-demo is the most common live-demo failure.

## Visual language

One hand-written stylesheet, `frontend/src/styles.css` — no framework, no build step beyond
Vite. Neutral greys, one accent, semantic colour reserved for status only — green Approved, amber Pending, red Rejected, slate Error. Findings inherit their severity colour. Nothing else on the page is coloured, so status reads instantly.

Monospace for identifiers (GSTIN, PAN, IFSC, account numbers) so a character-level mismatch is visible at a glance. This matters specifically for EC-4, where the entire point is one character differing between two ten-character strings.

## Demo sequence

| # | Screen / action | Time | Say |
|---|---|---|---|
| 0 | Clear state, dashboard empty | 10 s | "Vendor onboarding decision engine — submission in, explained decision out." |
| 1 | Create a case, copy the link, submit EC-1 as the vendor, live run | 60 s | "Eight stages. Three use AI — badged. One decision function." |
| 2 | EC-2, Pending, the drafted email | 90 s | "Recoverable, so it writes the chase-up. Specific and dated. A human sends it." |
| 3 | EC-3, Rejected, the R09 card | 90 s | "Everything's complete. Complete is not the same as legitimate." |
| 4 | EC-4, Rejected, R06/R07/R08/R15/R16/R18 | 90 s | "Three typed edits, six contradictions — the PAN alone is contradicted four ways, by the GSTIN, the entity type, the PAN card and the GST certificate. No external lookups." |
| 5 | Dashboard, then Export JSON | 30 s | "Four runs, full history. This replaces 'whatever's in someone's inbox'." |

About 4:40. Inside the 5-minute limit with room to breathe.

**Preparing the recording.** Every submission now enters through the vendor portal, so
there is no one-click sample loader any more. Create the four cases *before* you start
recording and keep each vendor link open in its own tab; on camera you fill the form and
attach the fixture PDFs from `backend/samples/pdfs/`. Budget for that, or record each
scenario separately and cut — the alternative is typing a fifteen-character GSTIN live.

**For the live interview:** same order, clear the state first, and be ready to be interrupted. The two questions to have rehearsed answers for are *"why isn't the AI making the decision?"* (`04-decision-engine.md`) and *"where's sanctions screening?"* (`10-assumptions-and-scope.md`).
