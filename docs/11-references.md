# 11 · References

Sources that shaped a specific design decision. Each entry states what it supports. Nothing here is background reading.

## Process model

**[Sumsub — KYB Verification Guide](https://sumsub.com/blog/kyb-guide/)**
The six-step KYB spine: collect company information, verify documents, extract and cross-check, verify stakeholders, screen, decide. Our 7-stage pipeline is a deliberate subset — stakeholder/UBO verification and AML screening are the two steps we consciously dropped. Also the source of the standard document list (certificate of incorporation, proof of address, registry extract).
→ Supports `01-solution-overview.md`, `10-assumptions-and-scope.md`

**[SAP Ariba — Approving or denying a supplier registration questionnaire](https://help.sap.com/docs/strategic-sourcing/managing-suppliers-and-supplier-lifecycles/approving-or-denying-supplier-registration-questionnaire)**
Status machine: `Pending Approval` → `Registered`, and **one denial denies the whole registration**. That is precedence-of-block-over-fix, already proven in a shipping enterprise product. Our `decide()` is the same rule in three lines.
→ Supports `04-decision-engine.md`

## Why bank verification is the control that matters

**[PaymentWorks — What your vendor payment software can't protect you from](https://www.paymentworks.com/2026/07/31/what-your-vendor-payment-software-cant-protect-you-from-and-what-fills-the-gap/)**
> "Vendor payment software assumes the vendor data it processes has already been verified — it hasn't. Banking information is collected at onboarding and updated on request, often with minimal independent confirmation."

The clearest statement of the gap this engine targets. Justifies R09 and R10 being BLOCK rather than FIX.
→ Supports `03-validation-rules.md`, `06-demo-scenarios.md`

**[Corpay — Business Email Compromise: how AP automation stops vendor fraud](https://www.corpay.com/resources/blog/business-email-compromise-ap)**
AP is targeted because it is the one department whose ordinary job is moving money to external parties on written instruction, at volume, on a schedule.
→ Supports EC-3's framing

**[First Business Bank — Vendor & BEC fraud: real case studies](https://firstbusiness.bank/resource-center/business-email-compromise-fraud-real-case-studies/)**
The documented failure mode: the payment cleared because **the account name matched the vendor's business name** but the account did not belong to them. This is exactly EC-3, and it is why "complete ≠ legitimate" is the line for that scenario.
→ Supports `06-demo-scenarios.md` EC-3

## Format vs. existence — the scope boundary

**[Fonoa — Tax ID validation: best practices and tools](https://www.fonoa.com/resources/blog/tax-id-validation-best-practices-tools)**
> "Many tax IDs can pass a format check yet be unissued or canceled. Best practice is to combine format validation with live database validation."

The single sentence that justifies our entire scope boundary: we do format and checksum offline, and defer existence to a named integration seam.
→ Supports `10-assumptions-and-scope.md` (assumption A5)

## Identifier mechanics — the source of R06, R07, R08

**[IndiaFilings — Decoding the GST registration number](https://www.indiafilings.com/learn/decoding-the-gst-registration-number)**
GSTIN structure: 2-digit state code, 10-character PAN at positions 3–12, registration count, literal `Z`, mod-36 checksum.
→ Supports R04, R06, R08

**[GimBooks — How to find PAN from GSTIN](https://www.gimbooks.com/blog/how-to-find-pan-from-gstin/)**
PAN extraction from GSTIN, and the PAN 4th-character entity-type code (`C` company, `F` firm/LLP, `P` individual, `T` trust, `H` HUF).
→ Supports R06, R07

These two references between them produce the strongest demo moment in the build: three independent contradictions derived from one 15-character string with zero external calls.

## Supporting the deferred list

**[HyperVerge — What is penny drop verification](https://hyperverge.co/blog/what-is-penny-drop/)**
₹1 IMPS transfer returning the bank's registered beneficiary name, then name-matched with a confidence score. This is what R09 becomes when it gets a real input source.
→ Supports `10-assumptions-and-scope.md` deferred table

**[Sirion — Preventing duplicate vendor requests: SOP and key controls](https://www.sirion.ai/library/contract-insights/prevent-duplicate-vendor-requests/)**
Two suppliers sharing a bank account are either duplicates or fraud. The rule we cut, and the reason it needs seeded master data to exist.
→ Supports the deferred duplicate-detection entry

**[Ivalua — Vendor master data management](https://www.ivalua.com/blog/vendor-master-data-management/)**
Composite matching across name, tax ID, and address for high-confidence duplicate identification. Relevant only to the deferred feature.
→ Supports the deferred duplicate-detection entry

## Primary source

`asa-case-study-candidate.pdf` — *AI Solutions Associate · Case Study · 2026*, problem statement **PS-2 · Operations · Vendor onboarding — from submission to approval**. Extracted in `00-case-study-requirements.md`.
