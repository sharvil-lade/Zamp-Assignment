# 15 · The AI Employee

One worker, several capabilities, one outcome. Not a team of agents.

```
Vendor submission
      v
Document validation
      v
AI Employee   extract · review · assess · summarise · recommend · communicate
      v
Deterministic decision engine        <- authoritative, never AI
      v
Human review
      v
Complete / communicate
```

`ai_employee.py` is an **orchestration layer, not an agent**. It has no database
handle, no shell, no arbitrary tool use. Its imports are asserted by a test:

```python
imported <= {"json", "time", "dataclasses", "extract", "rules"}
```

No `store`, no `psycopg`, no `subprocess`, no `os`. It receives plain data and
returns plain data; the pipeline is the only thing that writes anything down.

## Capabilities

| Capability | Kind | Notes |
|---|---|---|
| `extract_document()` | AI | Delegates to `extract.py`. One call per attached document, up to five per run. |
| `assess_risk()` | **deterministic** | BLOCK → High, FIX → Medium, none → Low |
| `review_extraction()` | AI | Values worth a second look |
| `review_findings()` | AI | The failed checks, in plain language |
| `summarize_vendor()` | AI | Two sentences for a reviewer |
| `recommend_next_action()` | AI | One sentence, actionable |
| `draft_communication()` | AI | Delegates to `extract.py`; human-gated |

**Why risk is not an AI judgement.** A model that could talk risk down is a model
that could talk a rejection down. The *level* is arithmetic over finding
severity; only the *rationale* is written. A test feeds a reassuring narrative to
a BLOCK case and asserts the level still comes back `High`.

**Why four capabilities share one call.** `review_extraction`, `review_findings`,
`summarize_vendor` and `recommend_next_action` are facets of a single structured
call (`review()`). Four round trips would add roughly ten seconds per run for no
extra signal. Each remains individually addressable and individually tested.

## The decision is never the AI's

`rules.decide()` runs and its result is **persisted** before the review stage
starts — a test asserts the event ordering. The AI Employee receives the decision
as a fact and is instructed to treat it as one:

> Decision: {status}          (final — you cannot change it)
> …
> Never contradict the decision. Never suggest overriding it.

`test_ai_employee_cannot_override_a_block` runs EC-3 with a review that says
*"Everything is in order. Recommend APPROVED."* and asserts the run is still
`REJECTED` with `R09`.

**If the AI fails, the decision stands.** `run_review()` never raises. A failure
writes a `capability_failed` event, the stage completes with
`outcome: "unavailable"`, and the status is untouched. The run page then says so
rather than showing an empty panel.

## Where it sits in the pipeline

Stage 3 extracts; stage 7 reviews. Eight stages in total:

```
1 Submission received     5 Cross-checking information
2 Checking completeness   6 Decision
3 Extracting documents    7 AI Employee review          [AI]
4 Validating formats      8 Preparing communication     [AI]
```

Stage labels come from `pipeline.STAGES`; the UI renders real backend state and
never invents a status.

## Auditability

Every operation writes an event with `actor="ai_employee"`:

| Event | Carries |
|---|---|
| `ai_call` | purpose, capability, model, input summary, raw response, usage, duration, risk |
| `ai_summary` | the full `Review` — risk, rationale, summary, key points, recommendation, extraction notes |
| `capability_failed` | capability, error type, duration — never a payload |

Timestamp, `run_id` and actor come from the events table itself. The existing
secret and token redaction applies unchanged; a test asserts no key or password
reaches the audit trail.

## The summary is employee-only

Returned in the `GET /api/runs/{run_id}` payload and rendered by `RunDetail`
directly under the status banner. It shows decision, risk, summary, key findings,
recommended action and communication state.

**It is never rendered on a vendor page.** `GET /api/vendor/onboard/{token}`
returns the vendor's own form and nothing internal — no status, findings, risk or
AI output — and the vendor portal is routed outside `<Layout>`, so there is no
component path that could reach it. A backend test seeds a high-risk summary and
asserts the vendor payload carries none of it; `vendor.test.jsx` asserts the same
against the rendered page.

## Human-in-the-loop, unchanged

The AI Employee prepares; the employee decides.

```
AI Employee -> draft -> employee reviews -> Send
```

`followup_sent_at` stays NULL until a human clicks. Nothing is ever emailed
automatically. Rejected runs get an internal note instead of a vendor draft.
