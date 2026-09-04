# 08 · UI and Demo

The brief states the UI is part of the grade and names two things explicitly: **a live run view showing each stage as it executes**, and **a dashboard showing history, status, and outputs across runs**. Both are built. Three screens total.

## Screen 1 · Submit

**Route:** `GET /` then `POST /submit`

- The 14 fields, grouped: Entity · Contact · Tax · Banking
- Three file inputs: Certificate of Incorporation · Cancelled cheque or bank letter · Certificate of Insurance
- **"Load sample" dropdown** — the four scenarios, pre-filled including attachments

The dropdown is the most important control on this screen. In the interview you pick a scenario and hit Run: no typing, no typos, no dead air, no chance of fat-fingering a GSTIN on camera.

Submitting redirects straight to the live run view.

## Screen 2 · Live run — *the graded screen*

**Route:** `GET /run/{id}`, fragment at `GET /run/{id}/stages` polled every 700 ms.

**Above the fold — the seven stages**, vertically, each in one of three states:

```
[x] 1  Intake                                     12 ms
[x] 2  Completeness                                3 ms    0 findings
[x] 3  Extraction            [AI]              1 840 ms    3 documents read
[x] 4  Format & checksum                           2 ms    0 findings
[~] 5  Consistency                                         <- spinner, active
[ ] 6  Decision
[ ] 7  Communicate           [AI]
```

Stages carrying an `[AI]` badge are visibly marked. That badge is the fastest way to communicate the architecture without saying a word.

**Finding cards** appear under their stage as they fire, severity-coloured (BLOCK red, FIX amber), each showing **both sides of the contradiction**:

```
+----------------------------------------------------------+
| R06   BLOCK   Consistency                                |
| GSTIN-embedded PAN does not match the submitted PAN      |
|   expected  ABCDE1234F                                   |
|   actual    ABCDE1234K                                   |
+----------------------------------------------------------+
```

Showing both values is what turns a verdict into an explanation. A card that says only "PAN mismatch" is a worse product.

**Status banner** at the top once stage 6 completes: green Approved / amber Pending / red Rejected, with the count of BLOCK and FIX findings.

**Below the fold, four sections:**

1. **Extracted vs. form** — a two-column table per document, form value beside extracted value, mismatched rows highlighted. This is where the AI's output is made inspectable rather than trusted.
2. **Audit timeline** — every event with timestamp, stage, actor, duration. AI events expand to show model, response, and token usage.
3. **Follow-up draft** (PENDING only) — editable textarea, **Copy** button, **Mark as sent** button. This is the human gate; nothing auto-sends.
4. **Export JSON** — the entire run as one file.

Spend disproportionate build time here. Everything before Part 6 was plumbing; this screen is what gets pointed at.

## Screen 3 · Dashboard

**Route:** `GET /dashboard`

- Four stat tiles: Total · Approved · Pending · Rejected
- Table: `Run ID · Vendor · Status · Findings · Submitted · Duration`, newest first
- Status filter (All / Approved / Pending / Rejected / Error)
- `ERROR` runs styled distinctly from `REJECTED` — different failure, different colour
- Row click opens the run detail

This is the brief's *"history, status, and outputs across runs"*, literally.

## Audit trail in the UI

Two surfaces, no third:

1. **Timeline** on the run page — human-readable, expandable.
2. **Export JSON** — machine-readable and complete: submission snapshot, extracted values, all findings, all events, final status.

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
