# 02 · Data Model

## Submission — 15 fields

Admission criterion: every field is either contradictable by another field or by a document, or is
what makes a document legible to the reviewer. Fields that were neither were cut.

| # | Field | Type | Required | Used by |
|---|---|---|---|---|
| 1 | `legal_entity_name` | str | yes | R09, R12, R15, R16, R18 |
| 2 | `entity_type` | enum | yes | R07, R17 |
| 3 | `country_of_incorporation` | enum | yes | rule applicability |
| 4 | `registered_address` | str | yes | R01 only — displayed beside the address proof, never matched |
| 5 | `registered_address_state` | str | yes | R08, R18 |
| 6 | `contact_name` | str | yes | R01 |
| 7 | `contact_email` | str | yes | R01 |
| 8 | `contact_phone` | str | yes | R01 |
| 9 | `tax_id_type` | enum | yes | rule applicability |
| 10 | `gstin` | str | if `tax_id_type == GSTIN` | R04, R06, R07, R08, R16 |
| 11 | `pan` | str | if India | R03, R06, R07, R15, R16 |
| 12 | `account_holder_name` | str | yes | **R09** |
| 13 | `account_number` | str | yes | R10 |
| 14 | `ifsc` | str | if India | R05, R10 |
| 15 | `bank_name` | str | yes | context on the run page |

**`registered_address` is the one field collected and deliberately not matched.** It is asked for
because an address proof means nothing unless you know which address it is meant to prove, and it
is shown to the reviewer beside the address printed on that document. Nothing compares the two.
A street address is written a dozen defensible ways — `42 Industrial Layout, Koramangala` against
`#42, Industrial Layout, 2nd Cross, Koramangala 560034` — and a fuzzy comparison over that would
manufacture findings faster than it caught anything real. R18 checks the addressee name and the
state instead, the two parts of an address that do have one correct answer. The full reasoning is
in `10-assumptions-and-scope.md`; the field is required by R01 like any other, so a blank one is
still reported.

**Enums**

- `entity_type` — `Private Limited` · `LLP` · `Partnership` · `Proprietorship` · `Foreign Corporation`
- `country_of_incorporation` — `IN` · `US`
- `tax_id_type` — `GSTIN` · `EIN`

**Rule applicability.** R04, R06, R07, R08 run only when `tax_id_type == "GSTIN"`. R03 runs only when a PAN is expected. R05 and the IFSC half of R10 run only for `IN`. The same two conditions decide which *documents* R02 asks for, so the field rules and the document rules never disagree about whether this vendor is an Indian GST registrant. R15 and R16 need their document attached at all, so they are self-limiting: a submission with no PAN card reaches R15 and it emits nothing. This is how "India plus one Western jurisdiction" is supported without a second rule set — see `10-assumptions-and-scope.md`.

**These 15 are the *canonical* fields, not the whole form.** Since forms became configurable, a template may collect anything else as well; a schema field maps onto one of these by carrying `canonical:`. Only the canonical ones reach the rules below. See *Configurable forms* further down.

## Documents — 5 slots

| Key | Document | Extracted into | Used by |
|---|---|---|---|
| `pan_card` | PAN card | `PanCardDoc` | R02, R13, R14, **R15** |
| `gst_certificate` | GST registration certificate | `GstCertificateDoc` | R02, R13, R14, **R16** |
| `incorporation_certificate` | Certificate of incorporation or business registration | `IncorporationDoc` | R02, R13, R14, R17, R12 |
| `bank_proof` | Cancelled cheque or bank letter | `BankProofDoc` | R02, R13, R14, **R09**, R10 |
| `address_proof` | Address proof | `AddressProofDoc` | R02, R13, R14, R18 |

**Five slots, but not five per submission.** `rules.required_documents(submission)` decides what a
given vendor is actually chased for, mirroring R01's conditions on the corresponding fields:

| Document | Required when |
|---|---|
| `pan_card` | `country_of_incorporation == "IN"` |
| `gst_certificate` | `tax_id_type == "GSTIN"` |
| `incorporation_certificate` · `bank_proof` · `address_proof` | always |

A US vendor on an EIN is asked for three documents, an Indian vendor on a GSTIN for five. The
extractor still knows all five slots; only R02's expectations move.

**The PAN card and the GST certificate earn their place by being contradictable.** An earlier
draft cut the GST certificate on the grounds that R06 already proves the GSTIN against the PAN
arithmetically. That reasoning was wrong in one specific way: R06 proves the two *typed* values
agree with each other, and proves nothing about whether the vendor holds either. R16 compares the
GSTIN printed on the certificate against the one typed, and compares the PAN embedded in that
printed GSTIN against the PAN typed — which catches a real certificate belonging to a different
legal person. R15 does the same job for the PAN card. Both still run entirely offline.

There is **no insurance certificate**. It was the only document nothing could contradict — its
policy number and insured name were checked against nothing, and its expiry date was the sole
reason R11 existed. A document that can only ever tell you about itself is a filing requirement,
not a validation input, so the document and its rule were removed together.

## Extracted document schemas

All fields nullable — `null` means "not present in the document", which is a finding input, not an error. Enforced with structured outputs (`output_config.format`), so no defensive JSON parsing.

```jsonc
// PanCardDoc
{ "document_type": "string|null",
  "pan": "string|null",
  "name": "string|null" }

// GstCertificateDoc
{ "document_type": "string|null",
  "gstin": "string|null",
  "legal_name": "string|null",
  "trade_name": "string|null" }

// IncorporationDoc
{ "document_type": "string|null",
  "legal_name": "string|null",
  "registration_number": "string|null",
  "incorporation_date": "YYYY-MM-DD|null" }

// BankProofDoc
{ "document_type": "string|null",
  "account_holder_name": "string|null",
  "account_number": "string|null",
  "ifsc": "string|null",
  "bank_name": "string|null" }

// AddressProofDoc
{ "document_type": "string|null",
  "name": "string|null",
  "address": "string|null",
  "state": "string|null" }
```

**`document_type` is on every schema, and it is still transcription.** The model is asked what the
paper calls itself, in a few plain words taken from its own heading — `pan card`,
`electricity bill`, `certificate of incorporation`. It is not asked whether that is *correct*.
R13 owns that judgment, and it compares the transcribed string against the slot the file was
uploaded into. Keeping the two apart is the same discipline as everywhere else here: the model
reports, the rule decides.

**Required versus useful.** `rules.DOCUMENT_REQUIRED_FIELDS` names the two values per document
that R14 insists on — `(pan, name)`, `(gstin, legal_name)`,
`(legal_name, registration_number)`, `(account_holder_name, account_number)`, `(name, address)`.
Everything else on a schema is useful context for the reviewer and its absence raises nothing.
`address_proof.address` is the clearest case: it is extracted and shown beside the
`registered_address` the vendor typed, and neither R14 nor R18 judges either one. The pair is
there to be read by a person, not scored by the engine.

Stored as one JSON object on the run:

```jsonc
extracted = {
  "pan_card":                  { ... } | null,   // null = not attached
  "gst_certificate":           { ... } | null,
  "incorporation_certificate": { ... } | null,
  "bank_proof":                { ... } | null,
  "address_proof":             { ... } | null
}
```

## Finding

The unit of reasoning. Immutable, emitted by rules, consumed only by `decide()` and the UI.

```python
@dataclass(frozen=True)
class Finding:                     # rules return list[Finding]; [] means "rule passed"
    rule_id:  str                  # "R06"
    severity: str                  # "BLOCK" | "FIX"
    stage:    str                  # "consistency"
    message:  str                  # human sentence, buyer-readable
    expected: str | None = None    # what should have been there
    actual:   str | None = None    # what was there
    tag:      str | None = None    # e.g. "ai_uncertain"
```

`expected` and `actual` exist so the UI can render **both sides of the contradiction**. That is what turns a verdict into an explanation:

> `R06 · BLOCK · GSTIN-embedded PAN does not match submitted PAN`
> expected `ABCFS1234K` · actual `ABCFS1234Z`

**Document presence vs. document contents.** R02 asks "was this attached?", which is a
directory listing, not an extraction result. So stage 2 is handed a **presence map** with the
same shape as `extracted` — `{}` for attached, `None` for not — and stage 4 onward get the real
extracted values. R02's code is identical either way and never sees model output. This is what
lets completeness stay at stage 2, ahead of extraction: a vendor should not wait on five API
calls to be told they forgot an attachment.

`None` for the whole map (rather than a dict of `None`s) means the submission carried no
documents *as a channel* at all — the JSON-only API path. Extraction is skipped and R02 stays
silent. A multipart submit always creates the upload directory, even with zero files, so every
missing document is reported.

**Skip semantics.** A rule whose inputs are absent emits **nothing**. The absence is already reported by R01/R02; a rule must never double-report it. Example: if `incorporation_certificate` is missing, R02 fires and R12 stays silent.

## Persistence — 6 tables (SQLite in dev, Supabase Postgres in production)

```sql
CREATE TABLE runs (
  run_id           TEXT PRIMARY KEY,   -- 'VS-0007'
  created_at       TEXT NOT NULL,
  vendor_name      TEXT,
  submission_json  TEXT NOT NULL,      -- input snapshot, never mutated
  extracted_json   TEXT,               -- filled at stage 3
  status           TEXT NOT NULL,      -- RUNNING|APPROVED|PENDING|REJECTED|ERROR
  followup_draft   TEXT,
  followup_sent_at TEXT,
  duration_ms      INTEGER
);

CREATE TABLE findings (
  id       INTEGER PRIMARY KEY,          -- no AUTOINCREMENT: it would create
                                         -- a 4th internal table (sqlite_sequence)
                                         -- and we never need never-reused ids
  run_id   TEXT NOT NULL REFERENCES runs(run_id),
  rule_id  TEXT NOT NULL,
  severity TEXT NOT NULL,
  stage    TEXT NOT NULL,
  message  TEXT NOT NULL,
  expected TEXT,
  actual   TEXT,
  tag      TEXT
);

CREATE TABLE events (            -- append-only. never UPDATE, never DELETE.
  id          INTEGER PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(run_id),
  ts          TEXT NOT NULL,
  stage       TEXT NOT NULL,
  event_type  TEXT NOT NULL,     -- stage_started|stage_completed|stage_failed
                                 -- |ai_call|decision|followup_sent
  actor       TEXT NOT NULL,     -- 'system' | 'user:<name>'
  detail_json TEXT,
  duration_ms INTEGER
);
```

```sql
CREATE TABLE onboarding_cases (   -- added by the vendor-portal layer
  id                  TEXT PRIMARY KEY,   -- 'CASE-0001'
  run_id              TEXT REFERENCES runs(run_id),  -- NULL until the vendor submits
  vendor_name         TEXT NOT NULL,
  contact_name        TEXT,
  contact_email       TEXT,
  token_hash          TEXT NOT NULL UNIQUE,  -- sha256 of the link token, never the token
  status              TEXT NOT NULL,      -- AWAITING_VENDOR | PROCESSING
  created_at          TEXT NOT NULL,
  submitted_at        TEXT,
  created_by_employee TEXT,               -- the signed-in employee who created it
  form_id             TEXT,               -- which form this case was created from
  form_schema_json    TEXT                -- a snapshot of what that form said at the time
);
```

**A case exists before a run does.** The employee creates the case; the vendor fills it in later.
`run_id` is therefore NULL until submission, at which point the existing pipeline takes over
completely unchanged. `runs`, `findings` and `events` were not modified.

## Configurable forms — 1 table

An onboarding form is no longer hardcoded. A form is a name, a description and a schema, and it is
edited in place. There are no versions: a case takes a **snapshot** of the schema it was created
against, which is what a version would have been for, at a fraction of the machinery.

```sql
CREATE TABLE form_templates (
  id          TEXT PRIMARY KEY,   -- 'FORM-0001'
  name        TEXT NOT NULL,      -- 'Standard Vendor Onboarding'
  description TEXT,
  schema_json TEXT NOT NULL,      -- the form definition
  created_by  TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT
);
```

### The form schema

One JSON object, validated structurally by `forms.validate_schema()`:

```jsonc
{
  "sections": [
    { "title": "Bank account",
      "description": "optional",
      "fields": [
        { "id": "account_number",       // ^[a-z][a-z0-9_]{0,60}$, unique in the form
          "label": "Account number",
          "type": "text",               // text textarea email phone number date
                                        // boolean select document
          "required": true,
          "options": ["…"],             // select only, at least one
          "help": "optional",
          "canonical": "account_number" // optional — see below
        }
      ]
    }
  ]
}
```

### Canonical fields versus custom fields

This is the split that keeps PS-2 intact.

| | Carries `canonical:` | Does not |
|---|---|---|
| Where the value goes | Into the canonical submission dict, under the canonical name | Into `submission._custom`, keyed by field id |
| What checks it | **The existing deterministic rules, unchanged** — R01–R18 | Schema validation only: required, type, allowed options |
| Findings it can raise | Any rule that reads that field | `R01` (missing), `R02` (missing document), `R03` (type) |
| Who else reads it | The whole pipeline | The AI Employee, told explicitly that it carries no rule |

A `canonical` value must be one of the 15 PS-2 submission fields or one of the five document
types, or the schema is rejected. `forms.normalize_submission()` performs the split at intake, so
`rules.py` receives exactly the dict it has always received and knows nothing about forms.

**Custom fields get no invented business rules.** Completeness and type sanity are objective; a
made-up rule for an arbitrary field would be a guess dressed as a decision. Anything subtler is
the AI Employee's job, and a human's.

### The snapshot, and why it replaced versioning

**A case snapshots the schema it was created against.** `onboarding_cases.form_id` records *which*
form, and `form_schema_json` records *what it said at the time*. The vendor is rendered and
validated against the snapshot, so a form cannot shift under someone mid-way through, and a
submission stays readable against the questions actually asked even after the form is edited or
deleted. That is the whole guarantee an immutable published version was buying, and the snapshot
buys it on its own — so the version table, the DRAFT/PUBLISHED status and the publish step were
all removed. `store._drop_form_versioning()` migrates a database that predates the change, carrying
each form's newest version schema up onto the form itself before the old table goes.

The form is chosen by the signed-in employee when the case is created. A vendor never gets to say
which form they are filling in — they present a token, and the case decides everything else.

### The standard form is generated from the engine, and kept that way

**It is seeded, not typed in.** `forms.standard_schema()` builds *Standard Vendor Onboarding* out
of `rules.FIELD_LABELS`, `rules.ENTITY_TYPES`, `rules.COUNTRIES`, `rules.TAX_ID_TYPES`,
`rules.DOCUMENT_LABELS` and `extract.DOC_TYPES`, so the default form cannot ask for a field the
engine does not read, or offer an option the engine will not accept.

**And it takes `required` from the engine too, which is the part that matters.** A field carries
the browser's `required` attribute only if it appears in `rules.ALWAYS_REQUIRED`, and a document
only if it appears in `rules.required_documents({})`. So `gstin`, `pan`, `ifsc`, the PAN card and
the GST certificate are *not* marked required, even though an Indian vendor owes every one of them.
Those five are conditional — R01 and R02 decide them from the country and the tax type the vendor
actually picks — and marking them required in the form would stop a US vendor on an EIN from
submitting at all. Reading both lists from the engine is what stops the form contradicting the rule
that judges what it collects.

**Sections and help text are written for the vendor, not for us.** The five sections are *Your
company*, *Who we should contact*, *Tax registration*, *Bank account* and *Documents*. Every
document slot and most of the fields carry a `help` line, and the line says why we are asking:
*"Exactly as printed on your incorporation certificate — not a trading or brand name"*, *"Form GST
REG-06, the certificate issued when you registered"*. Nearly every one of these values is
cross-checked against a document, and a vendor who knows that fills the field in more carefully.
The handful with no help line are the ones where the label is already the whole instruction.

**Seeding runs on every start-up, and it does not clobber edits.**
`store.seed_standard_template()` leaves an existing standard form alone — reordered, reworded, an
extra custom question added, all deliberate and all kept. It rebuilds from the engine in exactly
two cases, both of which mean the form can no longer do its job:

- it **no longer validates** against `forms.validate_schema()`, or
- it has **fallen behind the engine** — `forms.missing_engine_fields()` reports a canonical field
  or a document type that the rules read and the form no longer asks for.

That second condition is the one `registered_address` exercised: the moment the field entered
`rules.SUBMISSION_FIELDS`, every existing standard form was one question short of the engine and
was rebuilt on the next boot. A vendor filling in a form that cannot satisfy the engine is a worse
outcome than a lost edit. Anyone who wants a genuinely different form **duplicates** this one — the
copy is an ordinary form and is never rebuilt.

**Columns were added after the first release.** `CREATE TABLE IF NOT EXISTS` will not add a column
to a table that already exists, so `store.init_db()` probes the live columns and issues
`ALTER TABLE … ADD COLUMN` for `onboarding_cases.form_id`, `onboarding_cases.form_schema_json`,
`form_templates.schema_json` and `form_templates.updated_at` when they are absent. An existing
database upgrades in place; a case created before the change has `form_id` NULL and falls back to
the standard form.

**Two dialects, one schema.** `store.py` selects Postgres when `DATABASE_URL` is set and SQLite
otherwise. The only DDL difference is the integer key — `INTEGER PRIMARY KEY` in SQLite,
`BIGINT GENERATED ALWAYS AS IDENTITY` in Postgres. Placeholders are written `?` once and
translated to `%s` by `store._q`. Timestamps stay ISO-8601 `TEXT` and JSON payloads stay `TEXT` in
both, so semantics are byte-identical across the migration (see `13-deployment-plan.md`).

**Only the token hash is stored.** A database leak yields no working links, and the raw token is
returned to the employee exactly once — on the page that mints it.

`followup_draft` holds the vendor-facing text and is NULL unless a draft was created.
`followup_sent_at` is NULL until a human clicks send — **that column is the entire human gate**,
and it is what makes "drafted" and "sent" distinguishable in the export.

`submission_json` is the input snapshot and is never mutated. Extraction writes `extracted_json` once; stages 4–6 only read. That is what makes re-running a decision free, instant, and deterministic. It holds the 15 canonical fields at the top level, plus a `_custom` object carrying the answers to any fields the form added beyond them.

## Event detail payloads

| `event_type` | `detail_json` carries |
|---|---|
| `stage_started` / `stage_completed` | `{ "findings_added": n }` |
| `stage_failed` | `{ "error": "...", "traceback_head": "..." }` |
| `ai_call` | `{ "purpose", "model", "input_summary", "raw_response", "usage": {...} }` |
| `decision` | `{ "status", "block_count", "fix_count", "rule_ids": [...] }` |
| `followup_sent` | `{ "actor", "edited": bool }` — actor also on the event's `actor` column as `user:<name>` |
| `internal_note` | `{ "reason", "blocking_rules": [...], "note" }` — written for REJECTED instead of a draft |
| `vendor_submitted` | `{ "case_id", "form_id" }` — stage `intake`, actor `vendor`. Records which form the submission was made against; what that form actually said is on the case, as its schema snapshot. |
| `upload_rejected` | `{ "document", "filename", "reason", "bytes" }` — stage `intake`. The file was refused at the boundary and never saved, so R02 reports the document as missing. A wrong attachment is a fixable finding, never a crashed run. |
| `run_finished` | `{ "status" }` — the terminal marker, stage `run`. The status is persisted *before* stage 7, so "status is terminal" is not the same as "the pipeline has finished"; the live run view polls until this event exists. |

The `ai_call` event is what makes an AI-assisted decision auditable six months later: the exact model, the exact response, the token usage.

## Run status values

| Status | Meaning |
|---|---|
| `RUNNING` | Pipeline in flight — the live run view polls on this |
| `APPROVED` | No findings. Payable. |
| `PENDING` | At least one FIX, no BLOCK. Recoverable; follow-up drafted. |
| `REJECTED` | At least one BLOCK. Credibility failure; no vendor-facing message. |
| `ERROR` | The pipeline itself failed (see `stage_failed`). Distinct from a vendor problem. |

`ERROR` is separate from `REJECTED` on purpose. "Our extractor crashed" and "this vendor is not credible" are different facts and must never be conflated on a dashboard.
