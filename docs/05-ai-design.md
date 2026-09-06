# 05 · AI Design

AI appears in exactly **three** places. Each is justified by a rule not being able to do the job. Everything else is deterministic.

## 1. Document extraction — stage 3

**Job:** arbitrary PDF or scanned image to structured JSON.

**Why AI:** vendors format documents however they like. No rule reads an arbitrary cancelled cheque. This is the one genuinely unsolvable-by-rules step in the whole process.

**Implementation** (`extract.py`): one schema and one prompt per document type, one call each.

```python
resp = client().messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=2000,
    output_config={
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMAS[doc_type]},
    },
    messages=[{"role": "user", "content": [
        {"type": "document", "source": {"type": "base64",
                                        "media_type": "application/pdf", "data": b64}},
        {"type": "text", "text": PROMPTS[doc_type]},
    ]}],
)
```

`SCHEMAS` and `PROMPTS` are keyed by the same five document types the form collects, and
`_nullable()` builds every schema so that **every field is `["string", "null"]` and every field is
required**. The model must answer for each key; it may answer `null`. That is a different
guarantee from an optional field, and it is the one that makes absence a fact rather than an
omission.

Every prompt is assembled from three pieces, and the order matters:

1. **What we expect** — *"This should be an Indian PAN card."* Stated once, as context.
2. **What is it actually?** — the shared `_WHAT_IS_IT` block, which asks the model to name the
   document in a few plain lowercase words *taken from its own heading*, and says explicitly:
   *"Report what the document says it is even if that is not what was expected."*
3. **The fields**, then the shared `_TRANSCRIBER_RULES`.

Putting `document_type` first is deliberate. If the model is asked to find a PAN before it is
asked what it is looking at, the answer to "what is this?" arrives already coloured by what we
were hoping to find. Asking in the other order costs nothing and keeps R13's input honest.

Design notes:

- **No OCR library.** Claude takes PDFs natively as `document` blocks. No Tesseract, no poppler, no `pdf2image`, no preprocessing. Scanned images use an identical call with an `image` block, so "scanned vs. digital" never becomes a branch in our code.
- **Structured outputs** (`output_config.format`) guarantee the shape — no defensive JSON parsing, no retry-on-malformed-JSON loop.
- **`effort: "low"`** — extraction is transcription, not reasoning. The cost lever applied where it costs nothing.
- **"Never infer, correct, complete, normalise or guess"** is load-bearing, not politeness. If the
  model silently normalises the PAN on a card to the one it saw on the GST certificate in the same
  run, R06 and R15 stop working and the engine starts approving forged submissions. The extractor
  must be a faithful transcriber, not a helpful one — the prompt says outright that *an honest null
  is always better than a plausible guess*.
- **`document_type` is transcription too.** Every schema carries it, and the model is only ever
  asked what the paper calls itself. It is never asked whether the document is in the right slot,
  whether the vendor is trying something, or what should happen next. R13 makes that judgment, in
  `rules.py`, against a keyword set — so "the model noticed" and "the engine decided" stay two
  different events, exactly as they are for every other extracted field.
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

**Model calls are memoised per run.** Five rules now compare a document name against
`legal_entity_name` — R09, R12, R15, R16 and R18 — and on a clean submission they are comparing
the *same two strings* five times over. Asking five times costs five calls and puts four duplicate
rows in the audit trail. A cache hit emits no `ai_call` event: only real calls belong in the
record.

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

**Implementation:** the prompt receives the vendor name, the contact's name, the run reference,
and the **actionable FIX findings** (message, expected, actual only — not rule ids, not
severities, never the submission). Output is structured as `{subject, body}` and stored as
`Subject: ...

<body>`.

**"Actionable" excludes `ai_uncertain`.** Those findings exist because *we* were unsure and routed
the case to a human reviewer — asking the vendor to resolve our uncertainty, and exposing model
confidence to them, is the wrong message. A PENDING run whose only FIX findings are uncertain
gets no draft at all. It is instructed to be specific — *"we did not receive an address proof"*, not *"your submission is incomplete"* — and told not to invent requirements beyond the findings given.

**Runs only for `PENDING`.** A `REJECTED` run produces an internal note instead. Telling a suspected fraudster exactly which check caught them is a real-world anti-pattern; that asymmetry is deliberate and worth saying out loud in the demo.

**Human-gated.** The draft is displayed, editable, and requires a click. Nothing auto-sends. The
gate is a column: `followup_sent_at` stays NULL until `POST /run/{id}/send`, which writes it along
with a `followup_sent` event carrying the actor and whether the text was edited. Sending twice
returns 409; sending a run with no draft returns 400.

**Stage 7 is never fatal.** The decision is persisted *before* communication runs. If drafting
fails, a `stage_failed` event is written and the status stands — losing a convenience email must
not discard a correct, durable decision. This is safe precisely because communication is
downstream of the decision and cannot influence it. This is also why we never build SMTP — "Copy to clipboard" is the send button. See `10-assumptions-and-scope.md`.

## What AI must NEVER do

| Never | Why |
|---|---|
| **Determine the final status** | The status is `decide(findings)`. The model cannot see findings. |
| Validate a format or checksum | Exactly one right answer; a regex is correct, free, and instant |
| Compare two exact values (account numbers, GSTIN vs PAN) | String equality is not a judgment call |
| Do date arithmetic | `date < today` |
| Decide a severity | Severity is a constant on the rule |
| Override or soften a deterministic finding | Findings are immutable once emitted |
| Correct or normalise an extracted value | Silently repairing input destroys R06, R09, R10, R15 and R16 |
| Decide whether a document is the right kind | The model transcribes `document_type`; **R13** judges it |
| Decide whether a document is readable enough | The model returns `null`; **R14** decides what a null costs |
| Read the vendor's submission during drafting | Stage 7 receives findings only, not the raw submission |

## Structural enforcement

Not a policy — a property of the code:

- `rules.py` has **no network imports** and no I/O.
- `decide(findings) -> str` takes a list of findings and nothing else.
- `extract.py` and `matching.py` are the only modules importing the Anthropic SDK.
- The model's only path into a decision is **as a finding** that a rule chose to emit, with a severity the rule chose.

If someone deleted every AI call, the pipeline would still run and still decide — it would simply have no document data and no ambiguous-band resolution. That is the correct dependency direction.

## Model and cost

`claude-haiku-4-5-20251001`. Chosen deliberately for what these calls actually are: extraction is
*transcription* — the prompt forbids inferring, correcting or completing
anything — and every value it reads is then judged by a deterministic rule. The
model is never asked for judgement, so paying for a frontier model's judgement
buys nothing.

Verified rather than assumed: all four demo scenarios produce their documented
status and their exact rule set on this model.

Note `effort` is not passed — Haiku rejects it outright, which is the kind of
thing that only shows up when you actually run it.

A full run is five extractions plus, at most, a handful of ambiguous-band name calls, so demo
volume across the whole build week stays small and cost is negligible. `claude-sonnet-5` ($2 / $10) is the available step-down if wanted; not taken by default.
