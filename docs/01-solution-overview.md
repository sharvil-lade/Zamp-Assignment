# 01 · Solution Overview

## Positioning

**Vendor Onboarding Decision Engine.**

Not a KYB platform. A vendor submission goes in; an explained decision comes out. It deliberately does **not** maintain vendor master data, monitor vendors over time, or screen against live sanctions feeds. Naming that boundary out loud is part of the pitch — see `10-assumptions-and-scope.md`.

**One line:** *AI reads documents, rules decide, humans send.*

## Problem statement

A procurement team receives dozens of new vendor submissions a quarter. Each one has to be checked for three things — **complete** (is everything here?), **consistent** (do the fields agree with each other and with the attached documents?), and **credible** (is this entity who it says it is?). Today that review is manual, the chase-up for missing information is manual, and the only record of why a vendor was approved is somebody's inbox.

The expensive failure is not an incomplete form. It is a submission that looks complete and is internally inconsistent — a bank account in a different name, a tax ID that contradicts the declared jurisdiction or legal form. Those pass a completeness check and cause payment fraud.

**What we build:** a process that takes a submission (form + up to 5 documents), produces **Approved / Pending / Rejected** with every reason visible, drafts the follow-up for anything recoverable, and leaves an immutable audit record of how it got there.

## The 8-stage workflow

| # | Stage | Does | Nature |
|---|---|---|---|
| 1 | **Submission received** | Persist submission + files, assign `VS-####`, snapshot input | Deterministic |
| 2 | **Checking completeness** | Required fields and documents present (R01, R02) | Deterministic |
| 3 | **Extracting documents** | Each PDF to structured JSON | **AI** |
| 4 | **Validating formats** | PAN, GSTIN mod-36, IFSC (R03–R05); and each document as an artefact — right kind, readable, well-formed registration number (R13, R14, R17) | Deterministic |
| 5 | **Cross-checking information** | Field-vs-field and document-vs-form (R06–R10, R12, R15, R16, R18) | Deterministic + **AI at the margin** |
| 6 | **Decision** | Derive status from accumulated findings | Deterministic |
| 7 | **AI Employee review** | Risk, summary, key points, recommendation | **AI**, advisory |
| 8 | **Preparing communication** | Draft the vendor follow-up | **AI**, human-gated |

Stage 7 runs *after* the decision is persisted and cannot change it — see
`15-ai-employee.md`.

## End-to-end flow

```
  Vendor submission (15 fields + up to 5 PDFs)
            |
        [1] Intake --------------------> run created, status=RUNNING
            |
        [2] Completeness --------------> findings[]
            |
        [3] Extraction  (Claude) ------> extracted{} persisted
            |
        [4] Format & documents --------> findings[]
            |
        [5] Consistency ---------------> findings[]
            |     +-- ambiguous name pair? -> Claude -> {same_entity, confidence, reason}
            |
        [6] Decision  decide(findings) -> APPROVED | PENDING | REJECTED
            |                              (persisted before anything AI runs)
        [7] AI Employee review --------> risk · summary · recommendation
            |                            advisory; cannot change the status
        [8] Communicate (Claude) ------> draft email --+
            |                                          |
            v                                          v
    every stage appends to events[]           human clicks Send/Copy
            |
            +-------------> Live run view · Dashboard · Export JSON
```

**The load-bearing inversion:** stages 2, 4, and 5 decide nothing. They only emit **findings**. Stage 6 derives the status mechanically from those findings.

Consequences, all free:

- Reasoning is visible without building an explainability feature — the findings *are* the reasoning.
- Adding or changing a rule can never break the decision logic.
- The audit trail is a byproduct, not a feature.
- The decision is reproducible from stored data without re-reading a single PDF.

## AI vs deterministic responsibilities

### One AI Employee, four places it acts

`ai_employee.py` orchestrates every AI capability. It has no database handle, no
shell and no arbitrary tools — see `15-ai-employee.md`.

| Where | Why it must be AI |
|---|---|
| **Document extraction** (stage 3) | Vendors format documents however they like. No rule reads an arbitrary PDF. |
| **Ambiguous name matching** (stage 5) | `Acme Technologies Pvt Ltd` vs `Acme Tech Private Limited` — only inside a measured ambiguity band; ~90% of comparisons never reach a model. |
| **Review, summary, recommendation** (stage 7) | Reads the already-decided run and briefs the reviewer in plain language. |
| **Follow-up drafting** (stage 8) | Deterministic input (the finding list), natural-language output, zero influence on the decision. |

**Risk is deterministic**, not a model judgement: BLOCK → High, FIX → Medium,
none → Low. Only the rationale around it is written.

### Deterministic does everything else

Every check with a right answer: regex, checksums, string slicing, set membership, date arithmetic, exact comparison — **and the decision itself**.

A rule is testable, repeatable, instant, free, and its output is a sentence a procurement lead can read. If a GSTIN checksum fails, that is a fact; asking a model to opine on it buys latency, cost, and non-determinism in exchange for nothing. It also means the live demo produces byte-identical output every run.

### The boundary is structural, not a policy

- `rules.py` has **no network imports** and no I/O.
- `decide()` takes **only** a list of findings and returns a string.
- `ai_employee.py` imports nothing but `json`, `time`, `dataclasses`, `extract`
  and `rules` — asserted by a test. It cannot reach `store`, a shell, or a driver.
- The decision is persisted *before* the review stage begins.

The model *cannot* reach the decision. That is a property of the file layout, which is a much stronger answer than "I chose not to." See `05-ai-design.md`.

## What "not approved" produces

- **Pending** — recoverable. Generates a drafted, itemised, dated follow-up email naming exactly what is wrong.
- **Rejected** — credibility failure. Deliberately does **not** generate a vendor-facing "please fix this" message; telling a suspected fraudster which check caught them is a real-world anti-pattern. Produces an internal note instead.
