"""Entity-name comparison: deterministic first, Claude only at the margin.

`Acme Technologies Pvt Ltd` and `Acme Tech Private Limited` are the same entity.
`Acme Technologies Pvt Ltd` and `Acme Holdings LLC` are not. Most pairs are
obvious and are settled here with the standard library; only a narrow band is
genuinely ambiguous, and only that band reaches a model (docs/05-ai-design.md).

This module answers a question. It never emits a finding, never picks a
severity, and never sees a status — rules.py does all three, and cannot import
this file.
"""

import json
from difflib import SequenceMatcher

from engine.rules import NameVerdict, normalize_name

# Tuned once against samples/ in Part 4, then left alone. Four numbers nobody
# will change do not need a config file.
MATCH_THRESHOLD = 0.92      # at or above: same entity, no model call
MISMATCH_THRESHOLD = 0.75   # at or below: different entity, no model call
MIN_CONFIDENCE = 0.70       # below this the model does not get to decide

# Only the ambiguous band reaches a model at all, and a low-confidence
# answer is routed to a human rather than acted on.
MODEL = "claude-haiku-4-5-20251001"

# Legal-form spellings that mean the same thing. Expanded, not stripped: dropping
# suffixes entirely would make "Meridian Logistics LLP" and "Meridian Logistics
# Private Limited" identical, which is a different company.
EXPANSIONS = {
    "PVT": "PRIVATE", "PVTLTD": "PRIVATE LIMITED", "LTD": "LIMITED",
    "LIM": "LIMITED", "CO": "COMPANY", "CORP": "CORPORATION",
    "INC": "INCORPORATED", "INDS": "INDUSTRIES", "IND": "INDUSTRIES",
    "TECH": "TECHNOLOGIES", "SVCS": "SERVICES", "SVC": "SERVICES",
    "MFG": "MANUFACTURING", "ENT": "ENTERPRISES", "INTL": "INTERNATIONAL",
    "&": "AND", "AND": "AND",
}

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "same_entity": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["same_entity", "confidence", "reason"],
    "additionalProperties": False,
}

PROMPT = """Two names appear on documents for the same vendor onboarding submission.

  A: {a}
  B: {b}

Are these the same legal entity? Common legitimate differences include \
abbreviated legal suffixes (Pvt Ltd / Private Limited), shortened words, \
punctuation, and word order. A different trading name, a different legal form, \
or a personal name where a company is expected means they are NOT the same entity.

Set `confidence` to how sure you are, from 0 to 1. If you cannot tell, say so \
with a low confidence rather than guessing — an uncertain answer is routed to a \
human reviewer, which is the correct outcome. Keep `reason` to one short sentence."""


def normalize(name: str) -> str:
    """Uppercase, strip punctuation, expand legal-form abbreviations."""
    tokens = normalize_name(name).split()
    return " ".join(EXPANSIONS.get(t, t) for t in tokens)


def similarity(a: str, b: str) -> float:
    """Best of in-order and token-set comparison, so word order does not matter."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    in_order = SequenceMatcher(None, na, nb).ratio()
    token_set = SequenceMatcher(None, " ".join(sorted(na.split())),
                                " ".join(sorted(nb.split()))).ratio()
    return max(in_order, token_set)


def _ask_claude(a: str, b: str, on_ai_call=None) -> NameVerdict:
    import anthropic

    resp = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=500,
        output_config={"format": {"type": "json_schema",
                                 "schema": VERDICT_SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(a=a, b=b)}],
    )
    raw = next(bl.text for bl in resp.content if bl.type == "text")
    data = json.loads(raw)

    if on_ai_call:
        on_ai_call({
            "purpose": "name_match",
            "model": MODEL,
            "input_summary": f"{a!r} vs {b!r}",
            "verdict": bool(data["same_entity"]),
            "confidence": round(float(data["confidence"]), 2),
            "usage": {"input_tokens": resp.usage.input_tokens,
                      "output_tokens": resp.usage.output_tokens},
        })

    confidence = float(data["confidence"])
    reason = data["reason"]
    if confidence < MIN_CONFIDENCE:
        # The model does not get to decide when it is not sure. Uncertain is a
        # third answer, and rules.py turns it into a FIX for a human.
        return NameVerdict(None, reason=f"model confidence {confidence:.2f}: {reason}")
    return NameVerdict(bool(data["same_entity"]), reason=reason)


def names_match(a: str, b: str, *, on_ai_call=None, ask=None) -> NameVerdict:
    """Three bands. Only the middle one costs a model call."""
    score = similarity(a, b)

    if score >= MATCH_THRESHOLD:
        return NameVerdict(True, reason=f"deterministic score {score:.2f}", score=score)
    if score <= MISMATCH_THRESHOLD:
        return NameVerdict(False, reason=f"deterministic score {score:.2f}", score=score)

    verdict = (ask or _ask_claude)(a, b, on_ai_call)
    return NameVerdict(verdict.match, reason=verdict.reason, score=score)
