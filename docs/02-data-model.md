# 02 · Data Model

## Submission — 14 fields

Admission criterion: every field is contradictable by another field or by a document. Fields that cannot be contradicted were cut.

| # | Field | Type | Required | Used by |
|---|---|---|---|---|
| 1 | `legal_entity_name` | str | yes | R09, R12 |
| 2 | `entity_type` | enum | yes | R07 |
| 3 | `country_of_incorporation` | enum | yes | rule applicability |
| 4 | `registered_address_state` | str | yes | R08 |
| 5 | `contact_name` | str | yes | R01 |
| 6 | `contact_email` | str | yes | R01 |
| 7 | `contact_phone` | str | yes | R01 |
| 8 | `tax_id_type` | enum | yes | rule applicability |
| 9 | `gstin` | str | if `tax_id_type == GSTIN` | R04, R06, R07, R08 |
| 10 | `pan` | str | if India | R03, R06, R07 |
| 11 | `account_holder_name` | str | yes | **R09** |
| 12 | `account_number` | str | yes | R10 |
| 13 | `ifsc` | str | if India | R05, R10 |
| 14 | `bank_name` | str | yes | context on the run page |

**Enums**

- `entity_type` — `Private Limited` · `LLP` · `Partnership` · `Proprietorship` · `Foreign Corporation`
- `country_of_incorporation` — `IN` · `US`
- `tax_id_type` — `GSTIN` · `EIN`

**Rule applicability.** R04, R06, R07, R08 run only when `tax_id_type == "GSTIN"`. R03 runs only when a PAN is expected. R05 and the IFSC half of R10 run only for `IN`. This is how "India plus one Western jurisdiction" is supported without a second rule set — see `10-assumptions-and-scope.md`.

## Documents — 3 required

| Key | Document | Extracted into | Used by |
|---|---|---|---|
| `incorporation_certificate` | Certificate of Incorporation | `IncorporationDoc` | R02, R12 |
| `bank_proof` | Cancelled cheque or bank letter | `BankProofDoc` | R02, **R09**, R10 |
| `insurance_certificate` | Certificate of Insurance | `InsuranceDoc` | R02, R11 |

Three, not four. A GST registration certificate was cut: R06 already proves the GSTIN against the PAN arithmetically, so the certificate would be a second, weaker check on something already settled.

## Extracted document schemas

All fields nullable — `null` means "not present in the document", which is a finding input, not an error. Enforced with structured outputs (`output_config.format`), so no defensive JSON parsing.

```jsonc
// IncorporationDoc
{ "legal_name": "string|null",
  "registration_number": "string|null",
  "incorporation_date": "YYYY-MM-DD|null" }

// BankProofDoc
{ "account_holder_name": "string|null",
  "account_number": "string|null",
  "ifsc": "string|null",
  "bank_name": "string|null" }

// InsuranceDoc
{ "insured_name": "string|null",
  "policy_number": "string|null",
  "valid_until": "YYYY-MM-DD|null" }
```

Stored as one JSON object on the run:

```jsonc
extracted = {
  "incorporation_certificate": { ... } | null,   // null = not attached
  "bank_proof":                { ... } | null,
  "insurance_certificate":     { ... } | null
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
lets completeness stay at stage 2, ahead of extraction: a vendor should not wait on three API
calls to be told they forgot an attachment.

`None` for the whole map (rather than a dict of `None`s) means the submission carried no
documents *as a channel* at all — the JSON-only API path. Extraction is skipped and R02 stays
silent. A multipart submit always creates the upload directory, even with zero files, so every
missing document is reported.

**Skip semantics.** A rule whose inputs are absent emits **nothing**. The absence is already reported by R01/R02; a rule must never double-report it. Example: if `incorporation_certificate` is missing, R02 fires and R12 stays silent.

## Persistence — 3 tables (SQLite)

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

`followup_draft` holds the vendor-facing text and is NULL unless a draft was created.
`followup_sent_at` is NULL until a human clicks send — **that column is the entire human gate**,
and it is what makes "drafted" and "sent" distinguishable in the export.

`submission_json` is the input snapshot and is never mutated. Extraction writes `extracted_json` once; stages 4–6 only read. That is what makes re-running a decision free, instant, and deterministic.

## Event detail payloads

| `event_type` | `detail_json` carries |
|---|---|
| `stage_started` / `stage_completed` | `{ "findings_added": n }` |
| `stage_failed` | `{ "error": "...", "traceback_head": "..." }` |
| `ai_call` | `{ "purpose", "model", "input_summary", "raw_response", "usage": {...} }` |
| `decision` | `{ "status", "block_count", "fix_count", "rule_ids": [...] }` |
| `followup_sent` | `{ "actor", "edited": bool }` — actor also on the event's `actor` column as `user:<name>` |
| `internal_note` | `{ "reason", "blocking_rules": [...], "note" }` — written for REJECTED instead of a draft |
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
