# 05 · AI Design

AI appears in exactly **three** places. Each is justified by a rule not being able to do the job. Everything else is deterministic.

## 1. Document extraction — stage 3

**Job:** arbitrary PDF or scanned image to structured JSON.

**Why AI:** vendors format documents however they like. No rule reads an arbitrary cancelled cheque. This is the one genuinely unsolvable-by-rules step in the whole process.

**Implementation** (`extract.py`):

```python
resp = client.messages.create(
    model="claude-opus-5",
    max_tokens=2000,
    output_config={
        "effort": "low",
        "format": {"type": "json_schema", "schema": BANK_PROOF_SCHEMA},
    },
    messages=[{"role": "user", "content": [
        {"type": "document", "source": {"type": "base64",
                                        "media_type": "application/pdf", "data": b64}},
        {"type": "text", "text": "Extract account holder name, account number, IFSC, "
                                 "and bank name. Use null for anything not present. "
                                 "Do not infer or correct values."},
    ]}],
)
```

Design notes:

- **No OCR library.** Claude takes PDFs natively as `document` blocks. No Tesseract, no poppler, no `pdf2image`, no preprocessing. Scanned images use an identical call with an `image` block, so "scanned vs. digital" never becomes a branch in our code.
- **Structured outputs** (`output_config.format`) guarantee the shape — no defensive JSON parsing, no retry-on-malformed-JSON loop.
- **`effort: "low"`** — extraction is transcription, not reasoning. The cost lever applied where it costs nothing.
- **"Do not infer or correct values"** is load-bearing. If the model silently normalises `ABCDE1234K` to the PAN it saw elsewhere, R06 stops working. The extractor must be a faithful transcriber, not a helpful one.
- **`null` is a valid answer**, not an error. Absence is a finding input. Verified against a
  deliberately blank field on `samples/pdfs/bank_proof_illegible.pdf`: the model returns
  `"ifsc": null` rather than borrowing the value from another document in the same run.
- **A null never becomes a finding.** Missing data is missing data, not a contradiction — the
  consistency rules skip absent inputs rather than treating them as mismatches.
- **Extract once.** Results persist to `runs.extracted_json` at stage 3. Stages 4–6 only read. Re-deciding is free and deterministic.

**Failure handling:** an API error or timeout raises, the run ends `ERROR` with a `stage_failed` event. It never silently produces a status. Demonstrated in Part 7.

## 2. Ambiguous name matching — stage 5

**Job:** are these two strings the same legal entity?

**Why AI, but only sometimes:** `Acme Technologies Pvt Ltd` vs `Acme Tech Private Limited` is the same entity. `Acme Technologies Pvt Ltd` vs `Acme Holdings LLC` is not. Most pairs are obvious; a narrow band is not.

**Three-band design** (`matching.py`):

```
normalize()  -> uppercase, strip punctuation, expand PVT LTD -> PRIVATE LIMITED,
                LLP/LIMITED/CO suffix handling, collapse whitespace
score        =  difflib.SequenceMatcher ratio on normalized token sets   # stdlib

score >= 0.92     -> MATCH        deterministic, no model call
score <= 0.75     -> MISMATCH     deterministic, no model call
0.75 < s < 0.92   -> escalate to Claude -> {same_entity: bool,
                                            confidence: float,
                                            reason: str}
```

Roughly 90% of comparisons never reach a model. When asked *"why AI here?"* the answer is: **"only inside the band where the deterministic score is genuinely ambiguous — and I can show you where that band starts and why."**

**Low-confidence escalation.** If the model returns `confidence < 0.7`, it does **not** decide. The rule emits a `FIX` finding tagged `ai_uncertain`, routing to a human:

> `R09 · FIX · ai_uncertain · Could not confidently determine whether 'Sundaram Industrial LLP' and 'Sundaram Inds. LLP' are the same entity — human review required`

This is the honest handling of an unsure model, and it is one of the two human-in-the-loop points. Note the severity downgrade: an uncertain match becomes Pending (ask a human), never Rejected.

**The comparator returns a verdict, not a bool.** `NameVerdict(match, reason, score)` where
`match` is `True` / `False` / **`None` = uncertain**. A bool cannot express "ask a human", and
that third answer is the whole point of the low-confidence path. `rules.py` owns the type (it is
pure), `matching.py` returns it, and a bare `bool` from a simpler injected comparator is still
accepted.

**Model calls are memoised per run.** R09 and R12 frequently compare the same two strings; asking
twice costs a second call and puts a duplicate row in the audit trail. A cache hit emits no
`ai_call` event — only real calls belong in the record.

**Thresholds are constants at the top of `matching.py`**, not config. Tuned once against the
sample set in Part 4, then left alone:

| Pair | Score | Band |
|---|---|---|
| `Sundaram Industrial Supplies LLP` / `SUNDARAM INDUSTRIAL SUPPLIES, LLP.` | 1.000 | match |
| `Acme Technologies Pvt Ltd` / `Acme Tech Private Limited` | 1.000 | match |
| `Krishna Auto Components Pvt Ltd` / `Krishna Auto Component Pvt Ltd` | 0.987 | match |
| `Global Marine Services LLP` / `Global Marine Supplies LLP` | 0.846 | **ask** |
| `Sundaram Industrial Supplies LLP` / `Sundaram Industrial Enterprises LLP` | 0.806 | **ask** |
| `Deccan Packaging Industries` / `Deccan Packaging Ind. Pvt Ltd` | 0.771 | **ask** |
| `Meridian Logistics LLP` / `Meridian Logistics Private Limited` | 0.714 | mismatch |
| `Sundaram Industrial Supplies LLP` / `S. Ramesh Kumar` | 0.348 | mismatch |

Legal-form suffixes are **expanded, never stripped**: dropping them would make
`Meridian Logistics LLP` and `Meridian Logistics Private Limited` identical, and those are
different companies.

**None of the four demo scenarios reaches the model for name matching** — a deliberate
demo-stability property, asserted by a test. EC-3's fraud case sits at 0.348.

## 3. Follow-up email drafting — stage 7

**Job:** turn a list of findings into a message a vendor can act on.

**Why AI:** deterministic input, natural-language output. Pure upside, zero risk surface — it runs *after* the decision and cannot influence it.

**Implementation:** the prompt receives only the vendor name and the **FIX findings** (rule id, message, expected, actual). It is instructed to be specific and dated — *"your Certificate of Insurance expired on 21 July 2026"*, not *"your submission is incomplete"* — and told not to invent requirements beyond the findings given.

**Runs only for `PENDING`.** A `REJECTED` run produces an internal note instead. Telling a suspected fraudster exactly which check caught them is a real-world anti-pattern; that asymmetry is deliberate and worth saying out loud in the demo.

**Human-gated.** The draft is displayed, editable, and requires a click. Nothing auto-sends. The click writes a `followup_sent` event with the actor. This is also why we never build SMTP — "Copy to clipboard" is the send button. See `10-assumptions-and-scope.md`.

## What AI must NEVER do

| Never | Why |
|---|---|
| **Determine the final status** | The status is `decide(findings)`. The model cannot see findings. |
| Validate a format or checksum | Exactly one right answer; a regex is correct, free, and instant |
| Compare two exact values (account numbers, GSTIN vs PAN) | String equality is not a judgment call |
| Do date arithmetic | `date < today` |
| Decide a severity | Severity is a constant on the rule |
| Override or soften a deterministic finding | Findings are immutable once emitted |
| Correct or normalise an extracted value | Silently repairing input destroys R06, R09, and R10 |
| Read the vendor's submission during drafting | Stage 7 receives findings only, not the raw submission |

## Structural enforcement

Not a policy — a property of the code:

- `rules.py` has **no network imports** and no I/O.
- `decide(findings) -> str` takes a list of findings and nothing else.
- `extract.py` and `matching.py` are the only modules importing the Anthropic SDK.
- The model's only path into a decision is **as a finding** that a rule chose to emit, with a severity the rule chose.

If someone deleted every AI call, the pipeline would still run and still decide — it would simply have no document data and no ambiguous-band resolution. That is the correct dependency direction.

## Model and cost

`claude-opus-5`, 1M context, $5 / $25 per MTok. Extraction runs at `effort: "low"`.

Demo volume is roughly 50 extractions across the whole build week, so cost is negligible. `claude-sonnet-5` ($2 / $10) is the available step-down if wanted; not taken by default.
