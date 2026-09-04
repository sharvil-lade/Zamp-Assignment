# 08 · UI and Demo

The brief states the UI is part of the grade and names two things explicitly: **a live run view showing each stage as it executes**, and **a dashboard showing history, status, and outputs across runs**. Both are built. Three screens total.

## Screen 1 · Submit

**Route:** `GET /` then `POST /submit` (multipart → background run → `303` to the run view)

- The 14 fields, grouped: Entity · Contact · Tax · Banking
- Three file inputs: Certificate of Incorporation · Cancelled cheque or bank letter · Certificate of Insurance
- **"Load sample" dropdown** — the four scenarios. It fills the 14 fields from
  `/samples/{name}` and names the fixture PDFs in a hidden `sample` field; a browser cannot
  pre-fill a file input, so the server attaches those documents itself. Two clicks, no typing,
  no fumbling an upload on camera.

The dropdown is the most important control on this screen. In the interview you pick a scenario and hit Run: no typing, no typos, no dead air, no chance of fat-fingering a GSTIN on camera.

Submitting redirects straight to the live run view.

## Screen 2 · Live run — *the graded screen*

**Route:** `GET /run/{id}`; the polled fragment is `GET /run/{id}/stages`, swapped into
`#run-body` every 700 ms.

The fragment is the **whole run body**, not just the stage list — one swap updates stages,
findings, communication, comparison and timeline together, so nothing needs a page reload when
the run completes. Polling stops because the finished fragment simply does not re-render the
`hx-trigger` attribute.

**It polls on the `run_finished` event, not on the status.** The status is persisted *before*
stage 7 so the decision is durable the instant it is made — which means a terminal status does
not mean the pipeline has finished. Polling on status stops the view while the follow-up is
still being drafted, and the draft never appears.

**Above the fold — the seven stages**, vertically, each in one of three states:

As implemented, with real timings from an EC-2 run:

```
[x] 1  Intake                                      400 ms
[x] 2  Completeness          2 finding(s)          406 ms
[x] 3  Extraction        AI  2 document(s)        7 784 ms
[x] 4  Format & checksum                           400 ms
[x] 5  Consistency           1 finding(s)          412 ms
[x] 6  Decision              PENDING
[x] 7  Communicate       AI  draft created       4 714 ms
```

A stage that never ran because the submission carried no documents renders as
`skipped` ("no documents"), not as pending — extraction is the only stage that
can legitimately be skipped.

Stages carrying an `AI` badge are visibly marked. That badge is the fastest way to communicate
the architecture without saying a word. Extraction and Communicate always carry it; **Consistency
carries it only when that run actually escalated a name comparison** — so on all four demo
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

1. **Extracted vs. form** — one table per document: form value, document value, and **the rule
   that compares them**. A highlighted row therefore reads as "this is why R09 fired", not just
   "these differ". The rows mirror the rules rather than the fields, which is what makes EC-3
   legible: its form and cheque agree with each other, so the pair worth showing is
   *legal entity name* against *account holder on the document*.
2. **Audit timeline** — every event with timestamp, stage, actor, duration. AI events expand to show model, response, and token usage.
3. **Follow-up draft** (PENDING only) — editable textarea, **Copy** button, an actor
   field and **Mark as sent**. The badge reads *Drafted — not sent* in amber, then
   *Sent &lt;timestamp&gt;* in green, after which the textarea is read-only and the send
   button is gone. This is the human gate; nothing auto-sends. A REJECTED run shows
   an **Internal note** here instead, naming the blocking rules.
4. **Rejected attachments** — if a file was refused at upload (wrong type, empty,
   corrupt, oversized) an amber panel names the file and the reason. The document
   counts as not attached, so R02 reports it and the run is Pending, not Errored.
5. **Export JSON** — `/run/{id}/export`, the entire run as one file.

Spend disproportionate build time here. Everything before Part 6 was plumbing; this screen is what gets pointed at.

## Screen 3 · Dashboard

**Route:** `GET /dashboard`

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
2. **Export JSON** — machine-readable and complete. `GET /run/{id}/export` returns seven
   sections: `run` · `submission` · `extracted` · `findings` · `communication` · `events` ·
   `exported_at`. Event details are decoded, so the file is readable without unpacking JSON
   strings. `communication` reports `draft_exists`, `draft`, `sent`, `sent_at`, `sent_by`, and
   the `internal_note` for rejected runs.

Spend 30 seconds of the demo on the export button. It quotes the brief's own pain statement — *"the only audit trail is whatever's in someone's inbox"* — back at them.

## `/reset`

`POST /reset` wipes runs, findings, events, and uploads. Ten minutes to build.

Use it between video takes and again before the live interview. Leftover state changing a result mid-demo is the most common live-demo failure, and this removes the category.

## Visual language

Tailwind via CDN. Neutral greys, one accent, semantic colour reserved for status only — green Approved, amber Pending, red Rejected, slate Error. Findings inherit their severity colour. Nothing else on the page is coloured, so status reads instantly.

Monospace for identifiers (GSTIN, PAN, IFSC, account numbers) so a character-level mismatch is visible at a glance. This matters specifically for EC-4, where the entire point is one character differing between two ten-character strings.

## Demo sequence

| # | Screen / action | Time | Say |
|---|---|---|---|
| 0 | `/reset`, dashboard empty | 10 s | "Vendor onboarding decision engine — submission in, explained decision out." |
| 1 | Submit, EC-1, live run | 60 s | "Seven stages. Three use AI — badged. One decision function." |
| 2 | EC-2, Pending, the drafted email | 90 s | "Recoverable, so it writes the chase-up. Specific and dated. A human sends it." |
| 3 | EC-3, Rejected, the R09 card | 90 s | "Everything's complete. Complete is not the same as legitimate." |
| 4 | EC-4, Rejected, R06/R07/R08 | 90 s | "Three contradictions out of one 15-character string. No external lookups." |
| 5 | Dashboard, then Export JSON | 30 s | "Four runs, full history. This replaces 'whatever's in someone's inbox'." |

About 4:40. Inside the 5-minute limit with room to breathe.

**For the live interview:** same order, `/reset` first, and be ready to be interrupted. The two questions to have rehearsed answers for are *"why isn't the AI making the decision?"* (`04-decision-engine.md`) and *"where's sanctions screening?"* (`10-assumptions-and-scope.md`).
