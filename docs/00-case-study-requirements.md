# 00 · Case Study Requirements (PS-2)

Extracted verbatim-in-substance from `asa-case-study-candidate.pdf` (AI Solutions Associate — Case Study, 2026). Nothing in this file is invented; anything we decided ourselves lives in `10-assumptions-and-scope.md`.

## The chosen problem — PS-2 · Operations

> **Vendor onboarding — from submission to approval**

**Problem text (source, condensed to its load-bearing claims):**

- Before a company can pay a vendor, someone has to verify they're legitimate: collecting company details, banking info, tax registration, compliance documents — and checking that it is **complete, consistent, and credible**.
- Vendors submit incomplete forms. They attach the wrong documents.
- **The name on the submission doesn't match the name on the bank account.**
- **The tax ID format is wrong for the country they claim to be in.**
- Some submissions look fine on the surface but have **subtle inconsistencies that only become visible when you cross-reference the fields**.
- When something's missing, someone has to track down the vendor, explain what's needed, and wait.
- Procurement teams deal with this across dozens of new vendors per quarter. **The review is manual, the follow-up is manual, and the only audit trail is whatever's in someone's inbox.**
- A bad onboarding — a vendor who slips through with inconsistent details — can cause **payment fraud, compliance issues, or both**.

**The build instruction (source):**

> Build a process that takes a vendor submission as input and produces a clear status as output — **approved, pending, or rejected** — with the **reasoning visible**. For anything not approved, the process should **communicate back what's needed**. You decide what the submission looks like, what rules to apply, and how the output is structured.

**Edge cases (source):**

> Design and build **2–4 edge cases of your own**. They should be realistic scenarios where the process has to behave differently from the happy path — situations that reveal how flexible and deliberate your logic is. **Don't pick trivial ones.** The edge cases you choose tell us how well you understand the problem.

## The core question being asked

> Given a real operational problem, a week, and access to any AI tools you want — can you build a process that **actually runs**, handles **real inputs**, and deals with **edge cases gracefully**?

## Deliverables

**Deliverable 1 — a working automated process, live and runnable**
- Must actually execute. Not a mockup, not a dashboard that simulates one.
- Accepts real inputs (a PDF, a form submission, a CSV row, a prospect name), runs the logic, produces a real output.
- Any stack: n8n, Make, Zapier, Cursor, Lovable, plain Python, Retool, custom code.
- Must run live and be demonstrable in the interview.
- **"The UI is part of the grade — we expect an intuitive, well-designed interface with a live run view (showing each stage as it executes) and a dashboard (showing history, status, and outputs across runs)."**

**Deliverable 2 — a 5-minute demo video**
- Loom or any screen recording. Five minutes max.
- Show the **happy path running live**, then **at least one edge case**.
- Narrate what's happening and why — what the process does at each step, the decisions it makes, anything interesting about the build.
- No editing needed, no slides.

**After submission — live demo in the follow-up interview**
- You run the process live: happy path plus the edge cases you designed.
- Watched executing in real time, then questioned on decisions. No slides.

## What is graded

Stated explicitly in the source:
1. **Whether the process actually works.**
2. **The judgment behind your design choices.**
3. **How you explain it to a non-technical buyer.**

"What a strong submission looks like": a process that actually runs — takes a real input, executes the logic, produces a clear output — **with an intuitive UI and dashboard you can point to**; edge cases handled **deliberately, not ignored**; a demo video that shows it running and explains the thinking; and a live demo where you can talk fluently about every decision.

## Guidance — how to approach the week (source, 5 points)

1. **Map the process on paper before you build anything.** Every step: input, each stage, decision points, output. Candidates who skip this end up with something that works on the happy path and falls apart on anything unexpected.
2. **Get the happy path working first, then add edge cases.** One at a time. *"A process that handles 3 scenarios well beats one that half-handles ten."*
3. **Use AI tools — and be deliberate about it.** No extra credit for doing things manually. What matters is that the process works, you understand why each tool is there, and you can speak to it fluently.
4. **Prepare your demo runs before the interview.** Test inputs ready and rehearsed; know exactly what you'll run and in what order. A demo that breaks live, even on a minor thing, is hard to recover from.
5. **Submit something that runs, even if it's not perfect.** A process that executes end-to-end beats one that's 80% built and can't run.

## Schedule

| Day | Requirement |
|---|---|
| 1 | Pick a problem. Reply to hiring coordinator — subject: `ASA Case Study — [Your Name] — PS-2` |
| 2–6 | Build. Happy path and defined edge cases. The process should actually run. |
| 7 | Submit: link to the live/runnable process + link to the 5-minute demo video |

## Constraints from the FAQ

- **Ambiguity is part of the exercise.** Make an assumption, note it somewhere you can reference in the live pitch, and move on.
- **Real data is not required.** *"For PS-2, create vendor submission forms or JSON."* Inputs must be realistic enough that the process logic is meaningful.
- **Any tools, any model** — ChatGPT, Claude, Gemini, any API. **Only requirement: the process must actually run live during the interview.**
- **No Zamp or Pace.** Use tools that exist independently.
- **UI floor vs. UI bar.** The FAQ states you need "something to show run status and outputs — a simple dashboard, a structured log, even a clean console output is fine." Deliverable 1 states the UI is part of the grade and expects a live run view and a dashboard. **We build to the Deliverable 1 bar; the FAQ is treated as the floor.** (Noted as a resolved tension, not an invented requirement.)
- If more time is needed, email the coordinator before the deadline rather than going silent.

## Requirements checklist we must satisfy

- [ ] Accepts a real vendor submission as input (form + documents)
- [ ] Produces exactly one of: **Approved / Pending / Rejected**
- [ ] **Reasoning is visible** for every decision
- [ ] **Communicates back what's needed** for anything not approved
- [ ] **2–4 non-trivial edge cases**, deliberately handled
- [ ] **Live run view** showing each stage as it executes
- [ ] **Dashboard** showing history, status, and outputs across runs
- [ ] Runs live, end to end, repeatably, on demand
- [ ] Explainable to a non-technical buyer
