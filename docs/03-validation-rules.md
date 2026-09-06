# 03 · Validation Rules

**17 rules. No more.** Severity is `BLOCK` (credibility failure, leads to Rejected) or `FIX` (recoverable, leads to Pending). There is no third severity and no weighting.

Every rule is a pure function in `rules.py`: `(submission, extracted, ctx) -> list[Finding]`. No I/O, no network, no clock reads — `ctx` (a `RuleContext`) carries the injected `today` and `names_match` comparator.

Rules return a **list**, not `Finding | None`: R01, R02, R10, R13, R14, R15, R16 and R18 can legitimately produce more than one finding in a single pass (three blank fields should be reported as three items, not one at a time across three resubmissions).

**The IDs run R01–R18 with no R11.** R11 checked a document's `valid_until` against `ctx.today`, and it was the only rule that read the insurance certificate. When that document left the set the rule left with it. The gap is deliberate: renumbering would silently change what `R12` means in every audit record already written, and a stable rule id is the whole point of having one.

## Business categories

The 17 rules are grouped for a reviewer by *what they protect*, not by when
they run. Category and execution stage are deliberately different axes: R03
(PAN format) and R13 (document type) both run in the format stage but answer
completely different questions, and a reviewer thinks in the second axis.

The grouping lives on each rule in `rules.RULES`, so the run page, this table
and the engine cannot drift apart.

| Category | Rules | What it protects |
|---|---|---|
| **Completeness** | R01, R02 | That there is enough to judge at all |
| **Document integrity** | R13, R14 | That the evidence is the right kind and legible |
| **Identifier & format validation** | R03, R04, R05, R17 | That core identifiers are well-formed and issued |
| **Identity & tax consistency** | R06, R07, R12, R15, R16 | That the vendor is who they say they are |
| **Banking consistency** | R09, R10 | That the money goes to the vendor and nobody else |
| **Address consistency** | R08, R18 | That the registered address holds up against the evidence |

## Rule → stage → finding → decision impact

Generated from `rules.RULES`. Every rule is registered once, derives its own
stage tuple, and is asserted to be wired into the pipeline by
`tests/test_decision.py::test_every_registered_rule_runs_in_a_stage` — there
is no way for a rule to exist here and not execute.

`Applies when` is what separates a **skipped** check from a **passed** one.
Without it the run page would count a PAN check correctly never run against a
US vendor as a pass, which is the kind of number that quietly destroys trust
in a compliance tool.

| Rule | Name | Stage | Impact | Applies when |
|---|---|---|---|---|
| `R01` | Required fields | completeness | Pending | always |
| `R02` | Required documents | completeness | Pending | extraction has run |
| `R03` | PAN format | format | Pending | Indian vendor with a PAN |
| `R04` | GSTIN format and checksum | format | Pending | vendor registered for GST |
| `R05` | IFSC format | format | Pending | Indian vendor with an IFSC |
| `R13` | Document type | format | Pending | a document says what it is |
| `R14` | Document readability | format | Pending | any document attached |
| `R17` | Registration number format | format | Pending | incorporation number read, entity has a national format |
| `R06` | GSTIN encodes the submitted PAN | consistency | Rejected | PAN and GSTIN both well-formed |
| `R07` | PAN matches the declared entity type | consistency | Rejected | PAN encodes a known entity form |
| `R08` | GST state matches the registered address | consistency | Pending | GSTIN carries a known state code |
| `R09` | Bank account holder is the vendor | consistency | Rejected or Pending | bank document names an account holder |
| `R10` | Bank details match the bank document | consistency | Rejected | bank document carries a number to compare |
| `R12` | Incorporation certificate names the vendor | consistency | Pending | incorporation certificate carries a legal name |
| `R15` | PAN card matches the submission | consistency | Rejected or Pending | PAN card attached |
| `R16` | GST certificate matches the submission | consistency | Rejected or Pending | GST certificate attached |
| `R18` | Address proof matches the submission | consistency | Pending | address proof attached |

## Shared implementation, separate findings

R03, R04, R05 and R17 all validate an identifier's shape, so they share one
implementation — `validate_identifier_format(kind, value)` over the
`IDENTIFIER_FORMATS` table — while still raising four separate findings.
*Unify the duplicated pattern, never the business meaning:* "your PAN is
malformed" and "your GSTIN is malformed" are different things to tell a
vendor, and collapsing them into one generic "identifier invalid" would hide
the reason the check failed.

R04 goes further than shape and verifies the real mod-36 checksum, which is
why a GSTIN that was invented rather than issued is caught with no registry
call. No other identifier here carries a checksum to verify.

## Evidence direction

Every comparison rule stores the **evidence** in `expected` and **what the
vendor typed** in `actual`, and declares where each came from. That single
convention is what lets the run page render a labelled side-by-side without a
special case per rule:

| Source | Value |
|---|---|
| Cancelled cheque or bank letter | `S. Ramesh Kumar` |
| Vendor submission | `Sundaram Industrial Supplies LLP` |

## Rule table

| ID | Description | Sev | Inputs | Expected finding | Demo |
|---|---|---|---|---|---|
| **R01** | A required submission field is empty or missing | FIX | the 12 always-required fields, plus `gstin`, `pan` and `ifsc` when they apply | `Required field 'contact_phone' is missing` | **EC-2** |
| **R02** | A document this submission needs was not attached | FIX | the doc slots this submission requires (presence only) | `Required document 'Address proof' was not attached` | **EC-2** |
| **R03** | PAN does not match `^[A-Z]{5}[0-9]{4}[A-Z]$` | FIX | `pan` | `PAN format is invalid` · expected `AAAAA9999A` · actual `ABCD1234F` | unit test |
| **R04** | GSTIN fails the 15-char pattern or the **mod-36 checksum** | FIX | `gstin` | `GSTIN checksum is invalid — this is not an issued GST number` | unit test |
| **R05** | IFSC does not match `^[A-Z]{4}0[A-Z0-9]{6}$` | FIX | `ifsc` | `IFSC format is invalid` · expected `AAAA0999999` | unit test |
| **R06** | **GSTIN characters 3–12 differ from the submitted PAN** | **BLOCK** | `gstin`, `pan` | `GSTIN-embedded PAN does not match the submitted PAN` · expected `ABCFS1234K` · actual `ABCFS1234Z` | **EC-4** |
| **R07** | **PAN 4th character contradicts the declared entity type** | **BLOCK** | `pan`, `entity_type` | `PAN encodes entity type 'Firm/LLP' but the submission declares 'Proprietorship'` | **EC-4** |
| **R08** | GSTIN state code differs from the registered address state | FIX | `gstin`, `registered_address_state` | `GSTIN is registered in Karnataka (state code 29) but the registered address is in Maharashtra` | **EC-4** |
| **R09** | **Bank account holder name differs from the legal entity name** | **BLOCK** | `legal_entity_name`, `bank_proof.account_holder_name` | `Bank account is held in a different name than the vendor entity` | **EC-3** |
| **R10** | Bank proof account number or IFSC differs from the submitted values | **BLOCK** | `account_number`, `ifsc`, `bank_proof` | `Account number on the bank document does not match the submitted account number` | unit test |
| **R12** | Incorporation certificate legal name differs from the submitted legal name | FIX | `legal_entity_name`, `incorporation_certificate.legal_name` | `Legal name on the incorporation certificate differs from the submitted name` | unit test |
| **R13** | A document does not call itself the kind of document it was uploaded as | FIX | every attached doc's `document_type` | `The file attached as 'PAN card' does not look like that kind of document` · actual `electricity bill` | unit test |
| **R14** | A field that makes a document useful could not be read off it | FIX | every attached doc's required fields | `The account number could not be read from the Cancelled cheque or bank letter` | unit test |
| **R15** | PAN card contradicts the submitted PAN (**BLOCK**) or name (FIX) | **BLOCK** / FIX | `pan`, `legal_entity_name`, `pan_card` | `The PAN on the card does not match the submitted PAN` | **EC-4** |
| **R16** | GST certificate contradicts the submitted GSTIN or its embedded PAN (**BLOCK**), or the legal name (FIX) | **BLOCK** / FIX | `gstin`, `pan`, `legal_entity_name`, `gst_certificate` | `The GSTIN on the certificate belongs to a different PAN than the one submitted` | **EC-4** |
| **R17** | Registration number is not a valid CIN or LLPIN for the declared entity type | FIX | `entity_type`, `incorporation_certificate.registration_number` | `The number on the incorporation certificate is not a valid LLPIN for a LLP` · expected `a LLPIN` | unit test |
| **R18** | Address proof is addressed to a different name, or is for a different state | FIX | `legal_entity_name`, `registered_address_state`, `address_proof` — **not** `registered_address` | `The address proof is for a different state than the registered address` | **EC-4** |

## Stage assignment

| Stage | Rules |
|---|---|
| 2 · Completeness | R01, R02 |
| 4 · Format & checksum | R03, R04, R05, R13, R14, R17 |
| 5 · Consistency | R06, R07, R08, R09, R10, R12, R15, R16, R18 |

**The format stage runs after extraction**, which is what lets R13, R14 and R17 live there. The
split is by *question asked*, not by data source: stage 4 judges each artefact on its own terms —
is this the right kind of document, could it be read, is the identifier on it well formed — and
stage 5 compares what the documents say against what the vendor typed. A vendor who uploaded a
bank statement into the PAN slot is told exactly that at stage 4, instead of watching every
identity check on that slot fail at stage 5 for reasons nobody can read back.

## Notes on specific rules

### R02 asks only for the documents this submission needs

`rules.required_documents(submission)` is the whole rule's opinion, and it mirrors R01 exactly:

| Document | Asked for when |
|---|---|
| PAN card | `country_of_incorporation == "IN"` |
| GST registration certificate | `tax_id_type == "GSTIN"` |
| Certificate of incorporation or business registration | always |
| Cancelled cheque or bank letter | always |
| Address proof | always |

A US vendor is not chased for an Indian PAN card, and a vendor on an EIN is not chased for a GST
certificate. R01 already applies the same two conditions to the `pan` and `gstin` *fields*;
applying different conditions to the *documents* would be the same engine contradicting itself
inside one run.

### R01 splits the 15 fields into 12 unconditional and 3 conditional

`rules.ALWAYS_REQUIRED` names the twelve every vendor owes regardless of where they are
incorporated: `legal_entity_name`, `entity_type`, `country_of_incorporation`, `registered_address`,
`registered_address_state`, `contact_name`, `contact_email`, `contact_phone`, `tax_id_type`,
`account_holder_name`, `account_number` and `bank_name`. The remaining three are conditional on the
answers — `gstin` when `tax_id_type == "GSTIN"`, `pan` and `ifsc` when
`country_of_incorporation == "IN"`.

**The form reads that same list, which is why it is worth naming here.**
`forms.standard_schema()` marks a field browser-required if and only if it is in
`ALWAYS_REQUIRED`, and a document slot if and only if it is in `required_documents({})`. It does
not hard-code either. If it did, a US vendor on an EIN would be blocked by the browser from
submitting a form that R01 would have accepted — the form refusing what the engine allows, which is
the most confusing failure a vendor can hit, because nothing on screen explains it. The conditional
three are left unmarked and R01 decides them from the submitted answers, which is the only place
the condition can honestly be evaluated.

### R03–R05 fire in no demo scenario. They stay anyway.

They are the **input-validation trust boundary**. A malformed GSTIN reaching R06's string slicing is a crash, not a finding. Three lines each, and they make every rule downstream of them safe to write naively. Cheap guards at a trust boundary are not what "keep the MVP small" means.

### R06 and R07 — the two that carry the grade

A GSTIN is 15 characters: `[2 state code][10 PAN][1 registration count][Z][1 mod-36 checksum]`.

```
 2 9  A B C F S 1 2 3 4 K  1  Z  3
 |_|  |_________________|  |  |  |
  |            |           |  |  +-- checksum (mod 36)
  |            |           |  +----- always 'Z'
  |            |           +-------- registration count for this PAN in this state
  |            +-------------------- the PAN, characters 3-12
  +--------------------------------- state code (29 = Karnataka)
                |
                +-- within the PAN, the 4th character (here 'F') encodes legal form
```

**Character positions matter.** The entity-type character is the **4th character of
the PAN**, i.e. index 3 — in `ABCFS1234K` that is `F`, not the trailing `K`. An
earlier draft of this document used `ABCDE1234F` as the sample PAN, whose 4th
character is `D` (unmapped), which could never have demonstrated R07. Corrected
in Part 2 when the tests caught it.

The 4th-character mapping:

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

### One comparator, five rules, two severities

R09, R12, R15, R16 and R18 all compare two entity names through `matching.names_match()` (see
`05-ai-design.md`). The severity is decided by what a mismatch would *mean*, not by the size of
the difference:

- A name variation on an **incorporation certificate**, a **PAN card**, a **GST certificate** or
  an **address proof** is usually a formatting or transcription difference — **FIX**.
- A name mismatch on a **bank account** is the single most common vendor-payment fraud pattern —
  **BLOCK**.

Same code, different context, justified by consequence. Worth 15 seconds of the demo.

**A third outcome: uncertain.** When the comparator returns `match=None` (the model was not
confident enough to decide, see `05-ai-design.md`), all five rules emit a **FIX** tagged
`ai_uncertain` instead of their normal finding. R09 is downgraded from BLOCK: an unsure model
asks a human, it never rejects a vendor. It also never silently passes — the run becomes
PENDING, not APPROVED.

### R13 reads the document, never the filename

R13 compares what the paper **calls itself** — the `document_type` the extractor transcribes off
its own heading — against a keyword set per slot. A vendor renaming `bill.pdf` to
`pan_card.pdf` changes nothing about what is printed on it, and a filename check would be
trivially defeated by exactly the person you most want to catch. If a document claims nothing at
all, R13 stays silent and R14 owns the case.

The keyword sets are deliberately generous, because the slot legitimately accepts a family of
documents rather than one form: an address proof may be an electricity bill, a lease, a bank
statement or a passport, and an incorporation slot takes a partnership deed or a Udyam
registration as readily as a certificate of incorporation. R13 is there to catch a document in
the *wrong slot*, not to insist on one issuer's layout.

### R14 reports unreadable differently from incomplete

`DOCUMENT_REQUIRED_FIELDS` names the two values that make each document worth having. If **all**
of them come back null, that is one finding — the document could not be read at all, which is a
scanning problem and a single sentence to the vendor. If **some** come back null, that is one
finding per field, because "your cheque is blurred" and "the IFSC line on your cheque is blank"
are different conversations. Fields beyond the required two are useful but not disqualifying, so
their absence is never a finding.

R14 covers *attached* documents only. A document that was never attached is R02's, and a rule must never double-report.

### R17 checks the two formats that exist, and no more

| Entity type | Identifier | Shape |
|---|---|---|
| `Private Limited` | CIN | 21 characters, `^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$` |
| `LLP` | LLPIN | `^[A-Z]{3}-\d{4}$` — e.g. `AAB-1234` |
| everything else | registration number | any non-empty value |

Partnerships and proprietorships are registered by state registrars, shops-and-establishments
offices and Udyam under formats with no national shape. Inventing a pattern for them would
reject honest vendors, which is a worse failure than not checking. An absent number is R14's, not
R17's.

### R15, R16 and R18 — what a document proves against a form

These three exist because the earlier rule set trusted the *typed* PAN and GSTIN and never asked
the vendor to evidence them. The severities follow the same test as everywhere else: is the
contradiction a mistake, or is it an impossibility?

| Check | Sev | Why |
|---|---|---|
| PAN on the card ≠ submitted PAN | **BLOCK** | A hard contradiction about who this vendor is |
| Name on the card ≠ legal entity | FIX | Three-band name matching; a variant spelling is not fraud |
| GSTIN on the certificate ≠ submitted GSTIN | **BLOCK** | Same contradiction, different identifier |
| GSTIN on the certificate carries a different PAN | **BLOCK** | The certificate belongs to a different legal person |
| Legal name on the certificate ≠ legal entity | FIX | Three-band name matching |
| Address proof addressed to a different name | FIX | Utility bills sit in a director's or landlord's name often enough |
| Address proof state ≠ registered address state | FIX | Same reasoning as R08 — unusual, not impossible |
| Address proof street address ≠ `registered_address` | *not checked* | Deliberate. See below |

R16's PAN check is worth the extra line: the GSTIN printed on the certificate *contains* the PAN
at characters 3–12, so comparing those ten characters to the submitted PAN catches a certificate
belonging to someone else entirely — still with **zero external calls**. It is R06's arithmetic
pointed at the document instead of the form.

**R18 cross-checks the addressee and the state, and stops there. That is the honest limit.**
The form does collect a full `registered_address` — it has since the field was added — and the
address proof does yield the address printed on it. The two are shown side by side in the
extracted-vs-form panel, and **nothing compares them**. That is a decision, not a gap: a street
address has a dozen defensible spellings (`42 Industrial Layout, Koramangala` and
`#42, Industrial Layout, 2nd Cross, Koramangala 560034` are the same place), and a fuzzy match over
that would manufacture findings on honest vendors far more often than it caught a dishonest one.
A rule that cries wolf on correct data is worse than no rule.

So R18 checks the two parts of an address that do have a single right answer — the name it is
addressed to and the state it is in — and the street address is displayed for a human to read
rather than scored. The row appears in the comparison panel with no rule id and no highlight,
alongside the GST trade name, the bank name and the registration number. Claiming an address match
here would be claiming a check that does not exist; the boundary is written up in
`10-assumptions-and-scope.md`.

### What "verified" does and does not mean here

PAN and GST verification in this system is **format, checksum, internal consistency and document
cross-check**. R03 and R04 prove the identifiers are well formed and, for the GSTIN, arithmetically
issuable. R06, R07 and R08 prove the identifiers agree with each other and with the declared
entity type and state. R15 and R16 prove the documents the vendor attached carry the same
identifiers they typed.

**Nothing here calls an external verification service.** No NSDL PAN lookup, no GST portal API, no
bank penny-drop, no registry search. That is a deliberate scope boundary, not an omission — see
`10-assumptions-and-scope.md`. Every check in this document runs offline, in milliseconds, and
gives the same answer every time. Say so plainly rather than letting "verification" imply more
than it is.

## Coverage map

| Rule | Covered by |
|---|---|
| R01, R02 | EC-2 (demo) + unit test |
| R06, R07, R08, R15, R16, R18 | EC-4 (demo) + unit test |
| R09 | EC-3 (demo) + unit test |
| R03, R04, R05, R10, R12, R13, R14, R17 | unit test only |
| all 17 pass | EC-1 (demo) |

Stating plainly which rules are demoed and which are unit-tested is more credible than implying all 17 appear on screen.

## Adding a rule later

1. Write a pure function in `rules.py`.
2. Append it to the stage list in `pipeline.py`.
3. Add one assert to the rule tests.

Nothing else changes. `decide()` is untouched by construction. That is the payoff of findings-then-decide.
