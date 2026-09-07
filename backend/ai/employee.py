"""The Onboarding Assistant — one worker, several capabilities, one outcome.

    Vendor submission
          v
    Document validation
          v
    Onboarding Assistant   extract · review · assess · summarise · recommend
          v
    Deterministic decision engine      <- authoritative, never AI
          v
    Human review
          v
    Complete / correction requested

This is an orchestration layer, not an agent. It has no database handle, no shell,
no arbitrary tool use: it can only call the capabilities defined below, each of
which takes plain data and returns plain data. Everything it produces is advisory
— `rules.decide()` remains the only thing that sets a status.

Capability cost note: `review_extraction`, `review_findings`, `summarize_vendor`
and `recommend_next_action` are facets of a single structured model call
(`review()`), because four separate round trips would add ~10s per run for no
extra signal. Each is still individually addressable and individually tested.
"""

import json
import time
from dataclasses import asdict, dataclass, field

from ai import extract
from engine import rules

MODEL = extract.MODEL

CAPABILITIES = (
    "extract_document",
    "review_extraction",
    "review_findings",
    "assess_risk",
    "summarize_vendor",
    "recommend_next_action",
)

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "Low", "Medium", "High"


@dataclass
class Review:
    """Everything the Onboarding Assistant has to say about one run. Advisory only."""

    risk: str = RISK_LOW
    risk_rationale: str = ""
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    recommended_action: str = ""
    extraction_notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "risk_rationale": {"type": "string"},
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "recommended_action": {"type": "string"},
        "extraction_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["risk_rationale", "summary", "key_points",
                 "recommended_action", "extraction_notes"],
    "additionalProperties": False,
}

REVIEW_PROMPT = """You are an onboarding analyst reviewing a vendor submission \
that has ALREADY been decided by a deterministic rule engine.

Vendor: {vendor}
Decision: {status}          (final — you cannot change it)
Risk level: {risk}          (derived from finding severity — do not restate it as a number)

Checks that failed:
{findings}

What the documents said, beside what the vendor typed:
{comparison}

Answers to questions this form asks that the rule engine has no rule for:
{custom}

Write a short internal briefing for the reviewer who has to act on this.

- `summary`: two sentences at most. What was submitted and what the outcome means.
- `key_points`: one short line per *failed check*, in plain language, in the same
  order as the list above. No rule identifiers. Do not add lines for checks that
  passed — a reviewer needs the exceptions, not the confirmations. Empty list if
  there are no findings.
- `recommended_action`: one sentence. What the reviewer should do next. If the
  decision is REJECTED, recommend not proceeding. If APPROVED, say no further
  action is required. If PENDING, say what to request.
- `risk_rationale`: one sentence explaining why the risk sits at {risk}.
  Where a form-specific answer above looks inconsistent or worth a human's
  attention, say so here — but never treat it as a failed check, because no
  rule examined it.
- `extraction_notes`: anything about the *document reading* that a reviewer
  should double-check — a value that looked ambiguous, a field that was blank.
  Empty list if the extraction looked clean.

Never contradict the decision. Never suggest overriding it. Never invent a check \
that is not listed. This is an internal note, not a message to the vendor."""


# --- capability 1: extraction ------------------------------------------------

def extract_document(path, doc_type: str):
    """Read one document into structured fields. Delegates to extract.py."""
    return extract.extract_document(path, doc_type)


# --- capability 2: risk (deterministic) --------------------------------------

def assess_risk(findings) -> str:
    """Risk follows finding severity, not model judgement.

    A model that could talk risk down would be a model that could talk a
    rejection down. The narrative around this level is AI-written; the level
    itself is arithmetic.
    """
    severities = {f["severity"] if isinstance(f, dict) else f.severity
                  for f in findings}
    if rules.BLOCK in severities:
        return RISK_HIGH
    if rules.FIX in severities:
        return RISK_MEDIUM
    return RISK_LOW


# --- capabilities 3-6: one structured call -----------------------------------

def review(vendor_name: str, status: str, findings, comparisons=None,
           client=None, custom_answers=None, schema=None) -> tuple[Review, dict]:
    """Review the decided run. Returns (Review, ai_call metadata).

    Covers review_extraction, review_findings, summarize_vendor and
    recommend_next_action in one round trip.
    """
    risk = assess_risk(findings)
    rendered_findings = _render_findings(findings)
    rendered_comparison = _render_comparisons(comparisons or [])

    resp = (client or extract.client()).messages.create(
        model=MODEL,
        max_tokens=1200,
        output_config={"format": {"type": "json_schema",
                                 "schema": REVIEW_SCHEMA}},
        messages=[{"role": "user", "content": REVIEW_PROMPT.format(
            vendor=vendor_name or "this vendor", status=status, risk=risk,
            findings=rendered_findings, comparison=rendered_comparison,
            custom=_render_custom(custom_answers, schema))}],
    )
    raw = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(raw)

    result = Review(
        risk=risk,
        risk_rationale=data["risk_rationale"].strip(),
        summary=data["summary"].strip(),
        key_points=[p.strip() for p in data["key_points"] if p.strip()],
        recommended_action=data["recommended_action"].strip(),
        extraction_notes=[n.strip() for n in data["extraction_notes"] if n.strip()],
    )
    meta = {
        "purpose": "ai_employee:review",
        "capability": "review_findings+summarize_vendor+recommend_next_action"
                      "+review_extraction",
        "model": MODEL,
        "input_summary": f"{status}, {len(list(findings))} finding(s) for {vendor_name}",
        "usage": {"input_tokens": resp.usage.input_tokens,
                  "output_tokens": resp.usage.output_tokens},
    }
    return result, meta


# Named facets. The work happens once in review(); these make each capability
# individually addressable without a second round trip.

def review_extraction(result: Review) -> list[str]:
    return result.extraction_notes


def review_findings(result: Review) -> list[str]:
    return result.key_points


def summarize_vendor(result: Review) -> str:
    return result.summary


def recommend_next_action(result: Review) -> str:
    return result.recommended_action


# --- rendering helpers (no I/O, no model) ------------------------------------

def _render_findings(findings) -> str:
    lines = []
    for f in findings:
        severity = f["severity"] if isinstance(f, dict) else f.severity
        message = f["message"] if isinstance(f, dict) else f.message
        expected = f.get("expected") if isinstance(f, dict) else f.expected
        actual = f.get("actual") if isinstance(f, dict) else f.actual
        detail = f" (expected {expected}, found {actual})" if expected and actual else ""
        lines.append(f"- [{severity}] {message}{detail}")
    return "\n".join(lines) or "- none; every check passed"


def _render_custom(answers, schema) -> str:
    """Form-specific answers, labelled. No rule looked at any of these."""
    if not answers:
        return "- none; this form has only standard fields"
    labels = {}
    for section in (schema or {}).get("sections", []):
        for entry in section.get("fields", []):
            labels[entry["id"]] = entry.get("label", entry["id"])
    lines = [f"- {labels.get(key, key)}: {value or '(blank)'}"
             for key, value in answers.items()]
    return "\n".join(lines) or "- none"


def _render_comparisons(comparisons) -> str:
    lines = []
    for group in comparisons:
        if not group.get("attached"):
            continue
        for row in group.get("rows", []):
            if row.get("form") or row.get("doc"):
                flag = "  <-- differs" if row.get("mismatch") else ""
                lines.append(f"- {group['label']} / {row['label']}: "
                             f"submitted {row.get('form') or '-'}, "
                             f"document {row.get('doc') or '-'}{flag}")
    return "\n".join(lines) or "- no documents were read"


# --- orchestration -----------------------------------------------------------

def run_review(run_id: str, vendor_name: str, status: str, findings,
               comparisons=None, on_capability=None, review_fn=None,
               custom_answers=None, schema=None) -> Review | None:
    """Perform the post-decision review and report the capability call.

    Never raises: the decision is already durable, and losing an advisory
    briefing must not turn a good run into an error.
    """
    started = time.perf_counter()
    try:
        reviewer = review_fn or review
        try:
            result, meta = reviewer(vendor_name, status, findings, comparisons,
                                    custom_answers=custom_answers, schema=schema)
        except TypeError:
            # A simpler injected reviewer (tests, custom integrations) may take
            # only the four core arguments.
            result, meta = reviewer(vendor_name, status, findings, comparisons)
    except Exception as exc:
        if on_capability:
            on_capability({"purpose": "ai_employee:review",
                           "capability": "review",
                           "model": MODEL,
                           "error": type(exc).__name__,
                           "duration_ms": int((time.perf_counter() - started) * 1000)},
                          failed=True)
        return None

    meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
    meta["risk"] = result.risk
    if on_capability:
        on_capability(meta, failed=False)
    return result
