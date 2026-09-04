# 03 · Validation Rules

**12 rules. No more.** Severity is `BLOCK` (credibility failure, leads to Rejected) or `FIX` (recoverable, leads to Pending). There is no third severity and no weighting.

Every rule is a pure function in `rules.py`: `(submission, extracted) -> Finding | None`. No I/O, no network, no clock reads except a `today` value injected as a parameter.

## Rule table

| ID | Description | Sev | Inputs | Expected finding | Demo |
|---|---|---|---|---|---|
| **R01** | A required submission field is empty or missing | FIX | all 14 fields | `Required field 'contact_phone' is missing` | **EC-2** |
| **R02** | A required document was not attached | FIX | the 3 doc slots | `Required document 'Certificate of Incorporation' was not attached` | **EC-2** |
| **R03** | PAN does not match `^[A-Z]{5}[0-9]{4}[A-Z]$` | FIX | `pan` | `PAN format is invalid` · expected `AAAAA9999A` · actual `ABCD1234F` | unit test |
| **R04** | GSTIN fails the 15-char pattern or the **mod-36 checksum** | FIX | `gstin` | `GSTIN checksum is invalid — this is not an issued GST number` | unit test |
| **R05** | IFSC does not match `^[A-Z]{4}0[A-Z0-9]{6}$` | FIX | `ifsc` | `IFSC format is invalid` · expected `AAAA0999999` | unit test |
| **R06** | **GSTIN characters 3–12 differ from the submitted PAN** | **BLOCK** | `gstin`, `pan` | `GSTIN-embedded PAN does not match the submitted PAN` · expected `ABCDE1234F` · actual `ABCDE1234K` | **EC-4** |
| **R07** | **PAN 4th character contradicts the declared entity type** | **BLOCK** | `pan`, `entity_type` | `PAN encodes entity type 'Firm/LLP' but the submission declares 'Proprietorship'` | **EC-4** |
| **R08** | GSTIN state code differs from the registered address state | FIX | `gstin`, `registered_address_state` | `GSTIN is registered in Karnataka but the address is in Maharashtra` | **EC-4** |
| **R09** | **Bank account holder name differs from the legal entity name** | **BLOCK** | `legal_entity_name`, `bank_proof.account_holder_name` | `Bank account is held in a different name than the vendor entity` | **EC-3** |
| **R10** | Bank proof account number or IFSC differs from the submitted values | **BLOCK** | `account_number`, `ifsc`, `bank_proof` | `Account number on the bank document does not match the submitted account number` | unit test |
| **R11** | A document's `valid_until` is before today | FIX | `insurance_certificate.valid_until` | `Certificate of Insurance expired on 21 July 2026` | **EC-2** |
| **R12** | Incorporation certificate legal name differs from the submitted legal name | FIX | `legal_entity_name`, `incorporation_certificate.legal_name` | `Legal name on the incorporation certificate differs from the submitted name` | unit test |

## Stage assignment

| Stage | Rules |
|---|---|
| 2 · Completeness | R01, R02 |
| 4 · Format & checksum | R03, R04, R05 |
| 5 · Consistency | R06, R07, R08, R09, R10, R11, R12 |

## Notes on specific rules

### R03–R05 fire in no demo scenario. They stay anyway.

They are the **input-validation trust boundary**. A malformed GSTIN reaching R06's string slicing is a crash, not a finding. Three lines each, and they make every rule downstream of them safe to write naively. Cheap guards at a trust boundary are not what "keep the MVP small" means.

### R06 and R07 — the two that carry the grade

A GSTIN is 15 characters: `[2 state code][10 PAN][1 registration count][Z][1 mod-36 checksum]`.

```
2 9 A B C D E 1 2 3 4 F 1 Z 5
|-|  |-----------------|  | |  |
 |            |           | |  +-- checksum (mod 36)
 |            |           | +----- always 'Z'
 |            |           +------- registration count for this PAN in this state
 |            +------------------- the PAN, positions 3-12
 +-------------------------------- state code (29 = Karnataka)
```

And within the PAN, the **4th character encodes legal form**:

| Char | Entity form | Accepted `entity_type` |
|---|---|---|
| `C` | Company | `Private Limited` |
| `F` | Firm / LLP | `LLP`, `Partnership` |
| `P` | Individual | `Proprietorship` |
| `T` | Trust | out of MVP scope |
| `H` `A` `B` `G` `L` `J` | HUF, AOP, BOI, Government, Local Authority, Artificial Juridical Person | out of MVP scope |

So one 15-character string yields the vendor's **state**, **PAN**, and **legal form** — three things contradictable against what they typed, with **zero external calls**. Invisible to a human skimming a form; provable on camera in five seconds.

If the PAN's 4th character is outside the MVP mapping, R07 emits nothing (skip semantics) rather than guessing.

### R08 is FIX, not BLOCK — deliberately

A vendor legitimately registered in one state can have a head office in another. That is a question, not a fraud signal. R06 and R07 are BLOCK because they are *arithmetically impossible* if both values are real; R08 is merely *unusual*. Getting this distinction right is the difference between a rule set and a hostile rule set.

### R09 and R12 share a comparator, with different severities

Both compare two entity names using `matching.names_match()` (see `05-ai-design.md`). But:

- A name variation on an **incorporation certificate** is usually a formatting or transcription difference — **FIX**.
- A name mismatch on a **bank account** is the single most common vendor-payment fraud pattern — **BLOCK**.

Same code, different context, justified by consequence. Worth 15 seconds of the demo.

### R11 uses an injected `today`

`check_r11(submission, extracted, today)` — never `date.today()` inside the rule. Otherwise the test suite breaks the day the sample data expires, and the demo silently changes behaviour over time.

## Coverage map

| Rule | Covered by |
|---|---|
| R01, R02, R11 | EC-2 (demo) + unit test |
| R06, R07, R08 | EC-4 (demo) + unit test |
| R09 | EC-3 (demo) + unit test |
| R03, R04, R05, R10, R12 | unit test only |
| all 12 pass | EC-1 (demo) |

Stating plainly which rules are demoed and which are unit-tested is more credible than implying all 12 appear on screen.

## Adding a rule later

1. Write a pure function in `rules.py`.
2. Append it to the stage list in `pipeline.py`.
3. Add one assert to `test_rules.py`.

Nothing else changes. `decide()` is untouched by construction. That is the payoff of findings-then-decide.
