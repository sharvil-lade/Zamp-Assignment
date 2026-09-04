# 06 · Demo Scenarios

Four scenarios. Each is a saved input loadable from a dropdown on the submit screen — no typing during the demo.

> **Sample data note.** The GSTIN literals below are illustrative and are written as `29ABCDE1234F1Z<ck>`. Real sample fixtures are generated in Part 4 using `rules.gstin_checksum()` so that the 15th character is genuinely valid — otherwise R04 would fire in every scenario and mask the intended findings. **Never hand-write a GSTIN into a fixture.**

---

## EC-1 · Happy path — **APPROVED**

**Vendor:** `Sundaram Industrial Supplies LLP`

| Field | Value |
|---|---|
| entity_type | `LLP` |
| country_of_incorporation | `IN` |
| registered_address_state | `Karnataka` |
| tax_id_type / gstin | `GSTIN` / `29ABCDE1234F1Z<ck>` |
| pan | `ABCDE1234F` |
| account_holder_name | `Sundaram Industrial Supplies LLP` |
| ifsc | `HDFC0001234` |
| documents | all 3 attached, insurance valid until 2027-03-31 |

**Internal consistency:** state code `29` = Karnataka ✓ · embedded PAN `ABCDE1234F` = submitted PAN ✓ · PAN 4th char `F` = Firm/LLP = declared `LLP` ✓ · bank holder name = entity name ✓

**Rules triggered:** none. All 12 pass.

**Status:** `APPROVED`

**Output:** no findings. The run page shows 7 green stages, an Approved banner, and the extracted-vs-form panel with every pair matching. No follow-up email is drafted.

**Runtime:** ~6 s with the deliberate per-stage pause.

---

## EC-2 · Missing and expired documents — **PENDING**

Same vendor as EC-1, with three degradations:

1. `insurance_certificate` valid until **2026-07-21** (expired)
2. `incorporation_certificate` **not attached**
3. `contact_phone` **blank**

**Rules triggered:**

| Rule | Sev | Finding |
|---|---|---|
| R11 | FIX | `Certificate of Insurance expired on 21 July 2026` |
| R02 | FIX | `Required document 'Certificate of Incorporation' was not attached` |
| R01 | FIX | `Required field 'contact_phone' is missing` |

R12 stays **silent** — its input document is absent and R02 already reports that (skip semantics, `02-data-model.md`).

**Status:** `PENDING` (3 FIX, 0 BLOCK)

**Expected output — the drafted follow-up:**

> Subject: Additional information needed for your vendor registration (VS-0002)
>
> Hi Priya,
>
> Thanks for submitting your registration for Sundaram Industrial Supplies LLP. Three items need attention before we can complete onboarding:
>
> 1. Your Certificate of Insurance expired on **21 July 2026**. Please attach a currently valid certificate.
> 2. We did not receive a **Certificate of Incorporation**. Please attach it.
> 3. The **contact phone number** was left blank on the form.
>
> Once you send these over we'll pick the review straight back up.

**Why this scenario exists:** it is the **only** one that exercises the brief's *"communicate back what's needed"* requirement, and the only one where a vendor can resubmit. Specific and dated, not "your submission is incomplete."

---

## EC-3 · Bank beneficiary mismatch — **REJECTED**

Same vendor as EC-1. Everything is complete, every format is valid, every cross-field check passes.

**The one difference:** the cancelled cheque reads `S. Ramesh Kumar`. The submitted `account_holder_name` also reads `S. Ramesh Kumar`, so form and document agree — the form is internally consistent. What it does not match is the **legal entity**.

**Rules triggered:**

| Rule | Sev | Finding |
|---|---|---|
| R09 | **BLOCK** | `Bank account is held in a different name than the vendor entity` · expected `Sundaram Industrial Supplies LLP` · actual `S. Ramesh Kumar` |

Name score is roughly 0.11, a deterministic MISMATCH well below the 0.75 floor. No model call. Worth pointing out on camera: **the fraud case does not need the AI.**

R10 does **not** fire here — the account number and IFSC on the document match the form. R10 is covered by unit test.

**Status:** `REJECTED`

**Output:** no vendor-facing email. An internal note only.

**Why this scenario exists:** it is the brief's own example (*"the name on the submission doesn't match the name on the bank account"*) and the single most common vendor-payment fraud pattern. **A completeness-only checker approves this and the money is gone.** The sentence for this one is: **complete is not the same as legitimate.**

---

## EC-4 · Cross-field identity contradiction — **REJECTED**

All documents present and current. All formats valid. Nothing looks wrong on the form.

| Field | Value | |
|---|---|---|
| gstin | `29ABCDE1234F1Z<ck>` | |
| pan | `ABCDE1234K` | changed |
| entity_type | `Proprietorship` | changed |
| registered_address_state | `Maharashtra` | changed |

**Rules triggered:**

| Rule | Sev | Finding |
|---|---|---|
| R06 | **BLOCK** | `GSTIN-embedded PAN does not match the submitted PAN` · expected `ABCDE1234F` · actual `ABCDE1234K` |
| R07 | **BLOCK** | `PAN encodes entity type 'Firm/LLP' but the submission declares 'Proprietorship'` |
| R08 | FIX | `GSTIN is registered in Karnataka (state code 29) but the registered address is in Maharashtra` |

**Status:** `REJECTED`

**Why this scenario exists:** three independent contradictions extracted from **one 15-character string**, with **zero external API calls**. It is genuinely invisible to a human reviewer skimming a form, and it is the direct answer to the brief's *"subtle inconsistencies that only become visible when you cross-reference the fields."*

This is the one that makes an interviewer sit up. Build the demo to close on it.

---

## Demo sequence

| # | Scenario | Time | The line to say |
|---|---|---|---|
| 1 | EC-1 happy path | 60 s | "Seven stages, three of them AI, one decision function." |
| 2 | EC-2 pending | 90 s | "Recoverable — so it writes the chase-up email, and a human sends it." |
| 3 | EC-3 bank mismatch | 90 s | "Complete is not the same as legitimate." |
| 4 | EC-4 cross-field | 90 s | "Three contradictions out of one string, no external lookups." |
| 5 | Dashboard + export JSON | 30 s | "This is what replaces 'whatever's in someone's inbox'." |

Total ~4:40, inside the 5-minute video limit. Click `/reset` between takes.

## Slack, if the week runs short

Cut in this order: **EC-2 last** — it is the only scenario exercising communicate-back. Drop R12 or R08 first; both are single findings and neither is load-bearing. Three well-handled scenarios still satisfies the brief's "2–4".
