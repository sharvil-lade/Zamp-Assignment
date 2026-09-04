# 01 · Solution Overview

## Positioning

**Vendor Onboarding Decision Engine.**

Not a KYB platform. A vendor submission goes in; an explained decision comes out. It deliberately does **not** maintain vendor master data, monitor vendors over time, or screen against live sanctions feeds. Naming that boundary out loud is part of the pitch — see `10-assumptions-and-scope.md`.

**One line:** *AI reads documents, rules decide, humans send.*

## Problem statement

A procurement team receives dozens of new vendor submissions a quarter. Each one has to be checked for three things — **complete** (is everything here?), **consistent** (do the fields agree with each other and with the attached documents?), and **credible** (is this entity who it says it is?). Today that review is manual, the chase-up for missing information is manual, and the only record of why a vendor was approved is somebody's inbox.

The expensive failure is not an incomplete form. It is a submission that looks complete and is internally inconsistent — a bank account in a different name, a tax ID that contradicts the declared jurisdiction or legal form. Those pass a completeness check and cause payment fraud.

**What we build:** a process that takes a submission (form + 3 documents), produces **Approved / Pending / Rejected** with every reason visible, drafts the follow-up for anything recoverable, and leaves an immutable audit record of how it got there.

## The 7-stage workflow

| # | Stage | Does | Nature |
|---|---|---|---|
| 1 | **Intake** | Persist submission + files, assign `VS-####`, snapshot input | Deterministic |
| 2 | **Completeness** | Required fields and documents present (R01, R02) | Deterministic |
| 3 | **Extraction** | Each PDF to structured JSON | **AI** |
| 4 | **Format & checksum** | PAN, GSTIN mod-36, IFSC (R03–R05) | Deterministic |
| 5 | **Consistency** | Field-vs-field and document-vs-form (R06–R12) | Deterministic + **AI at the margin** |
| 6 | **Decision** | Derive status from accumulated findings | Deterministic |
| 7 | **Communicate** | Draft the vendor follow-up | **AI**, human-gated |

## End-to-end flow

```
  Vendor submission (14 fields + 3 PDFs)
            |
        [1] Intake --------------------> run created, status=RUNNING
            |
        [2] Completeness --------------> findings[]
            |
        [3] Extraction  (Claude) ------> extracted{} persisted
            |
        [4] Format & checksum ---------> findings[]
            |
        [5] Consistency ---------------> findings[]
            |     +-- ambiguous name pair? -> Claude -> {same_entity, confidence, reason}
            |
        [6] Decision  decide(findings) -> APPROVED | PENDING | REJECTED
            |
        [7] Communicate (Claude) ------> draft email --+
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

### AI does exactly three things

| Where | Why it must be AI |
|---|---|
| **Document extraction** (stage 3) | Vendors format documents however they like. No rule reads an arbitrary PDF. |
| **Ambiguous name matching** (stage 5) | `Acme Technologies Pvt Ltd` vs `Acme Tech Private Limited` — only inside a measured ambiguity band; ~90% of comparisons never reach a model. |
| **Follow-up drafting** (stage 7) | Deterministic input (the finding list), natural-language output, zero influence on the decision. |

### Deterministic does everything else

Every check with a right answer: regex, checksums, string slicing, set membership, date arithmetic, exact comparison — **and the decision itself**.

A rule is testable, repeatable, instant, free, and its output is a sentence a procurement lead can read. If a GSTIN checksum fails, that is a fact; asking a model to opine on it buys latency, cost, and non-determinism in exchange for nothing. It also means the live demo produces byte-identical output every run.

### The boundary is structural, not a policy

- `rules.py` has **no network imports** and no I/O.
- `decide()` takes **only** a list of findings and returns a string.

The model *cannot* reach the decision. That is a property of the file layout, which is a much stronger answer than "I chose not to." See `05-ai-design.md`.

## What "not approved" produces

- **Pending** — recoverable. Generates a drafted, itemised, dated follow-up email naming exactly what is wrong.
- **Rejected** — credibility failure. Deliberately does **not** generate a vendor-facing "please fix this" message; telling a suspected fraudster which check caught them is a real-world anti-pattern. Produces an internal note instead.
