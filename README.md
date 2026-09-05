# Vendor Onboarding Decision Engine

A vendor submission goes in. An explained decision comes out.

Built for **PS-2 · Operations — Vendor onboarding: from submission to approval**
(AI Solutions Associate case study, 2026).

**One line:** *AI reads documents, rules decide, humans send.*

---

## The problem

Before a company can pay a vendor, someone has to verify they are legitimate:
collect company details, banking information, tax registration and compliance
documents, then check that all of it is **complete, consistent and credible**.

Today that review is manual, chasing missing information is manual, and the only
record of why a vendor was approved is somebody's inbox.

The expensive failure is not an incomplete form. It is a submission that *looks*
complete and is internally inconsistent — a bank account in a different name, a
tax ID that contradicts the declared jurisdiction or legal form. Those pass a
completeness check and cause payment fraud.

## The solution

A form and three documents in; **Approved / Pending / Rejected** out, with every
reason visible, a drafted follow-up for anything recoverable, and an immutable
audit record of how the decision was reached.

It is a **decision engine**, not a KYB platform. It does not maintain vendor
master data, monitor vendors over time, or screen against live sanctions feeds.
Those boundaries are deliberate and documented in
[docs/10-assumptions-and-scope.md](docs/10-assumptions-and-scope.md).

---

## Two ways in

**Vendor portal (the product flow).** An employee creates a case at `/onboardings/new`,
copies the generated secure link, and sends it to the vendor. The vendor opens
`/vendor/onboard/<token>`, fills in their own details, uploads their documents, and submits —
which hands straight to the pipeline below. The employee watches it on `/dashboard`.

```
Employee -> Create case -> Secure link -> Vendor -> Submit -> PS-2 pipeline -> Dashboard
```

The token is 32 random bytes; only its SHA-256 is stored, it is never written to the logs, and
it works exactly once. A vendor sees their own company name and nothing internal — no status,
no findings, no rules, no run id.

**Direct submit (`/`).** The original internal form with the demo-sample dropdown. Still there,
still what the four scenarios run through.

## End-to-end workflow

```
  Vendor submission (14 fields + 3 PDFs)
            |
        [1] Intake ---------------------> run created, status=RUNNING
        [2] Completeness ---------------> findings[]        R01 R02
        [3] Extraction  (Claude) -------> extracted{} persisted
        [4] Format & checksum ----------> findings[]        R03 R04 R05
        [5] Consistency ----------------> findings[]        R06 ... R12
            |     +-- ambiguous name pair? -> Claude -> {same_entity, confidence, reason}
        [6] Decision  decide(findings) -> APPROVED | PENDING | REJECTED
        [7] Communicate (Claude) -------> draft email --> human clicks send
            |
   every stage appends to events[]  ->  live run view · dashboard · JSON export
```

**The load-bearing idea:** stages 2, 4 and 5 decide nothing. They only emit
**findings**. Stage 6 derives the status mechanically:

```python
def decide(findings):
    if any(f.severity == "BLOCK" for f in findings): return "REJECTED"
    if any(f.severity == "FIX"   for f in findings): return "PENDING"
    return "APPROVED"
```

Three lines. That inversion is why the reasoning is visible without building an
explainability feature, why adding a rule can never break the decision, and why
the audit trail is a byproduct rather than a feature.

## AI vs deterministic responsibilities

**AI does exactly three things:**

| Where | Why it must be AI |
|---|---|
| **Document extraction** (stage 3) | Vendors format documents however they like. No rule reads an arbitrary cancelled cheque. |
| **Ambiguous name matching** (stage 5) | Only inside a measured band — `score ≥ 0.92` matches and `≤ 0.75` mismatches are settled by `difflib`, so ~90% of comparisons never reach a model. |
| **Follow-up drafting** (stage 7) | Deterministic input (the findings), natural-language output, runs *after* the decision. |

**Deterministic does everything else** — regex, checksums, string slicing, date
arithmetic, exact comparison, **and the decision itself**.

**The boundary is structural, not a policy.** `rules.py` imports nothing outside
the standard library, and `decide()` takes a list of findings and nothing else.
The model *cannot* reach the decision — that is a property of the file layout,
enforced by a test that walks the module's imports.

If the model is unsure (`confidence < 0.7`) it does not decide: the rule emits a
`FIX` tagged `ai_uncertain` and a human reviews. Uncertainty becomes **Pending**,
never Rejected — and never a silent approval.

---

## Architecture

```
app.py            FastAPI routes + rendering
pipeline.py       the 7 stages; event emission; error containment
rules.py          the 12 rules + decide(); pure, stdlib only, zero I/O
extract.py        Claude document extraction + follow-up drafting
matching.py       name normalisation, difflib scoring, ambiguous-band escalation
store.py          sqlite3; 4 tables (runs, findings, events, onboarding_cases)
templates/        employee: base · submit · run · _run_body · dashboard · error
                  employee: onboarding_new · onboarding_created
                  vendor:   vendor_base · vendor_form · vendor_submitted
samples/          4 scenario fixtures + 6 generated PDFs + their generators
test_rules.py     224 tests, no network required
docs/             00–13, the design record
```

Six Python modules, eleven templates. Full detail in
[docs/07-architecture.md](docs/07-architecture.md).

**Data flows one way.** The submission snapshot is never mutated; extraction
writes once at stage 3; stages 4–6 only read. Re-running a decision is free,
instant and deterministic.

### Tech stack

| Layer | Choice | Why |
|---|---|---|
| Web | FastAPI | One process, background tasks built in |
| Storage | SQLite (`sqlite3`, stdlib) | Single file, zero setup, no ORM |
| Templates | Jinja2 | Server-rendered, no build step |
| Interactivity | HTMX (CDN) | Live run view in one HTML attribute |
| Styling | Tailwind (CDN) | Looks deliberate in ~40 lines of classes |
| AI | Anthropic SDK, `claude-opus-5` | Native PDF input, structured outputs |
| Tests | pytest | One file, fully offline |

No npm, no build step, no bundler, no Docker, no queue, no ORM, no separate
frontend. Under live-demo conditions that is not a preference, it is risk
management: every extra process is one more thing that can fail on camera.

---

## Local setup

Requires **Python 3.11+**.

```bash
git clone <repo> && cd vendor-onboarding
pip install -r requirements.txt
```

### Environment variables

One variable. Copy the template and fill it in:

```bash
cp .env.example .env
```

| Variable | Required | Used for |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Document extraction, ambiguous name matching, follow-up drafting |

An exported shell variable works equally well. If it is missing you get a clear
message rather than a stack trace, and the test suite still passes — it never
touches the network.

### Running the app

```bash
python -m uvicorn app:app --reload --port 8000
```

Open **http://127.0.0.1:8000**. That is the whole application — there is no
separate frontend to start. Internet access is needed on first page load so the
Tailwind and HTMX CDN scripts fetch.

`vendor.db` is created automatically on startup.

### Running the tests

```bash
python -m pytest test_rules.py -q      # 224 passed
```

No API key, no network, no fixtures, no mocking framework — the payoff of
`rules.py` being pure. Verify that yourself with:

```bash
ANTHROPIC_API_KEY="" python -m pytest test_rules.py -q
```

### Regenerating the fixtures

Committed, so this is optional (`pip install reportlab` first):

```bash
python samples/make_pdfs.py        # the 6 sample documents
python samples/make_fixtures.py    # the 4 scenario JSONs, GSTIN checksum computed
```

---

## The four demo scenarios

Pick one from the **Load sample** dropdown on the submit page; it fills all 14
fields and attaches the fixture PDFs. Then **Run onboarding checks**.

### EC-1 · Happy path → `APPROVED`
`Sundaram Industrial Supplies LLP`, everything consistent. All 12 rules pass, no
findings, no follow-up drafted.

### EC-2 · Missing and expired documents → `PENDING`
Insurance expired 21 July 2026, incorporation certificate not attached, contact
phone blank. Fires **R01, R02, R11** — and drafts a dated, itemised follow-up.
The only scenario that exercises *"communicate back what's needed"*.

### EC-3 · Bank beneficiary mismatch → `REJECTED`
Everything is complete and every format is valid; the cheque reads
`S. Ramesh Kumar`. Fires **R09 (BLOCK)**. Name score 0.348 — settled
deterministically, **no model call**. The line for this one: *complete is not the
same as legitimate.*

### EC-4 · Cross-field identity contradiction → `REJECTED`
Nothing looks wrong on the form. Fires **R06 + R07 (BLOCK)** and **R08 (FIX)**:

```
R06  GSTIN-embedded PAN does not match the submitted PAN
       expected ABCFS1234K   actual ABCFS1234Z
R07  PAN encodes entity type 'Firm/LLP' but the submission declares 'Proprietorship'
R08  GSTIN is registered in Karnataka (state code 29) but the address is in Maharashtra
```

Three independent contradictions from **one 15-character string**, with zero
external API calls. A GSTIN is `[2 state][10 PAN][1 count][Z][1 checksum]`, and
the PAN's 4th character encodes legal form.

Rejected runs get an **internal note**, never a vendor-facing message — telling a
suspected fraudster which check caught them is deliberate policy.

Full detail: [docs/06-demo-scenarios.md](docs/06-demo-scenarios.md).

---

## Known limitations

Stated up front rather than discovered:

1. **No existence verification.** A structurally perfect but unissued GSTIN
   passes. Format is checked offline; existence needs a registry call —
   deferred, not overlooked.
2. **No duplicate detection.** The same vendor submitted twice produces two
   independent approvals.
3. **No sanctions or PEP screening.** A stage-5 rule against a name list; the
   seam exists, the data source does not.
4. **Extraction is not cross-verified.** A misread account number would produce a
   false positive — which is exactly why the extracted-vs-submitted panel exists.
5. **Single-jurisdiction depth.** Cross-field checks are India-specific; the US
   path validates format only.
6. **No authentication, CSRF protection or rate limiting.** Single reviewer,
   single machine. `/reset` is unauthenticated and destructive.
7. **Document contents live in the audit trail by design.** `ai_call` events
   store the model's raw response — that *is* the auditable record. Production
   would need encryption at rest.
8. **No concurrency control.** Many simultaneous submissions would each spawn
   background extraction with no queue.

Full reasoning, and what would change first in production:
[docs/10-assumptions-and-scope.md](docs/10-assumptions-and-scope.md).

## Future deployment

Vercel + FastAPI + Supabase Postgres + Supabase Storage + Anthropic API.
Planned and costed, **not implemented** — the local SQLite MVP is intentionally
intact. See [docs/13-deployment-plan.md](docs/13-deployment-plan.md).

## Documentation

| Doc | Contents |
|---|---|
| [00](docs/00-case-study-requirements.md) | PS-2 requirements, extracted from the brief |
| [01](docs/01-solution-overview.md) | Positioning, 7-stage workflow, AI vs deterministic |
| [02](docs/02-data-model.md) | 14 fields, 3 documents, schemas, 3 tables |
| [03](docs/03-validation-rules.md) | All 12 rules with IDs, severities and inputs |
| [04](docs/04-decision-engine.md) | `decide()` and why it stays deterministic |
| [05](docs/05-ai-design.md) | The three AI uses and what AI must never do |
| [06](docs/06-demo-scenarios.md) | EC-1 to EC-4 in full |
| [07](docs/07-architecture.md) | Modules, responsibilities, data flow, routes |
| [08](docs/08-ui-and-demo.md) | Screens and the demo script |
| [09](docs/09-implementation-plan.md) | The 8 build parts |
| [10](docs/10-assumptions-and-scope.md) | Assumptions, scope, security posture, tradeoffs |
| [11](docs/11-references.md) | Research sources and what each supports |
| [12](docs/12-submission-checklist.md) | Pre-submission verification |
| [13](docs/13-deployment-plan.md) | Future production architecture |
