"""Policy: what a set of findings means, and what to do about it.

    AI interprets documents  ->  rules validate evidence  ->  POLICY decides

`rules.py` answers "what is wrong with this submission?" and produces findings.
This module answers the three questions that follow from them:

    decide()              which status those findings add up to
    assess_risk()         how much attention the run needs
    explain()             the outcome in a reviewer's words
    correction_request()  what to ask the vendor for

The split is one-directional: policy imports rules, and rules imports nothing
from here. That is what keeps `rules.py` standard-library-only, which is in
turn what makes it structurally impossible for a model to reach a decision.

Deterministic throughout. No model is called from this module, and `decide()`
is the only thing in the system that produces a status.
"""

import re
from dataclasses import asdict

from engine import rules
from engine.rules import (BY_ID, CAT_ADDRESS, CAT_BANKING, CAT_COMPLETENESS,
                          CAT_DOCUMENT, CAT_IDENTIFIER, CAT_IDENTITY,
                          CATEGORY_ORDER, FIELD_LABELS)

# Severity stays defined in rules.py, because a rule sets it at the moment it
# builds a Finding and rules.py may not import this module. Re-exported here so
# policy is still the name callers reach for when they mean the vocabulary of a
# decision rather than the mechanics of a check.
BLOCK = rules.BLOCK
FIX = rules.FIX

# --- the decision -----------------------------------------------------------

def decide(findings: list[rules.Finding]) -> str:
    """The entire decision layer. Sees findings and nothing else."""
    if any(f.severity == BLOCK for f in findings):
        return "REJECTED"
    if any(f.severity == FIX for f in findings):
        return "PENDING"
    return "APPROVED"


RISK_LOW, RISK_MEDIUM, RISK_HIGH = "Low", "Medium", "High"


def assess_risk(findings) -> str:
    """Risk follows finding severity, not model judgement.

    A model that could talk risk down would be a model that could talk a
    rejection down. The narrative around this level is AI-written; the level
    itself is arithmetic.
    """
    severities = {f["severity"] if isinstance(f, dict) else f.severity
                  for f in findings}
    if BLOCK in severities:
        return RISK_HIGH
    if FIX in severities:
        return RISK_MEDIUM
    return RISK_LOW


# Why a blocking finding in this category stops an onboarding, in the words a
# reviewer would use to justify it to whoever owns the vendor relationship.
BLOCK_REASONS = {
    CAT_BANKING: "Banking information cannot be verified consistently.",
    CAT_IDENTITY: "The vendor's tax identity cannot be reconciled with the "
                  "documents provided.",
    CAT_ADDRESS: "The registered address cannot be reconciled with the "
                 "documents provided.",
    CAT_DOCUMENT: "The documents provided are not usable as evidence.",
    CAT_IDENTIFIER: "A core identifier is not valid.",
    CAT_COMPLETENESS: "Required information is missing.",
}


def _sentences(messages: list[str], limit: int = 2) -> str:
    """Join finding messages into something readable, without listing twenty."""
    if not messages:
        return ""
    shown = [m.rstrip(".") for m in messages[:limit]]
    text = " and ".join(shown)
    extra = len(messages) - len(shown)
    if extra:
        text += f", and {extra} other item{'s' if extra > 1 else ''}"
    return text + "."


# What a vendor is asked to do about each kind of finding. Deterministic and
# per-finding, so a run only ever asks for what that run actually found - there
# is no generic "your submission is incomplete" message anywhere.
IDENTIFIER_NAMES = {"R03": "PAN", "R04": "GSTIN", "R05": "IFSC code",
                    "R17": "registration number on your incorporation certificate"}


def _quoted(message: str) -> str | None:
    match = re.search(r"'([^']+)'", message)
    return match.group(1) if match else None


def _lower_first(text: str) -> str:
    """Lowercase the first letter, unless the first word is an acronym.

    "GSTIN (submitted)" must not become "gSTIN (submitted)" just because it
    landed mid-sentence.
    """
    if not text:
        return text
    first = text.split(" ", 1)[0].strip("(),.")
    if first.isupper() and len(first) > 1:
        return text
    return text[:1].lower() + text[1:]


def correction_request(finding) -> str:
    """One instruction a vendor can act on. Never mentions a rule id.

    The reviewer's wording and the vendor's wording are different jobs: a
    reviewer needs "R02 - required document not attached"; the vendor needs
    "please upload your certificate of incorporation".
    """
    f = finding if isinstance(finding, dict) else asdict(finding)
    rule_id, message = f["rule_id"], f["message"]
    expected, actual = f.get("expected"), f.get("actual")
    named = _quoted(message)
    rule = BY_ID.get(rule_id)

    if rule_id == "R01":
        label = FIELD_LABELS.get(named, (named or "required field").replace("_", " "))
        return f"Please provide the {label}."

    if rule_id == "R02":
        return f"Please upload your {_lower_first(named or 'missing document')}."

    if rule_id == "R13" and expected and actual:
        return (f"Please upload the correct {_lower_first(expected)} — the file "
                f"attached appears to be {_lower_first(actual)} instead.")

    if rule_id == "R14":
        return f"Please re-upload a clearer copy: {_lower_first(message)}."

    if rule_id in IDENTIFIER_NAMES and expected and actual:
        name = IDENTIFIER_NAMES[rule_id]
        return (f"Please check the {name} — '{actual}' is not valid "
                f"(it should look like {expected}).")

    # A mismatch against a document we read: name both sides, so the vendor can
    # see which one is wrong rather than guessing.
    if rule and rule.evidence and expected and actual:
        return (f"{message.rstrip('.')}. Your {_lower_first(rule.evidence[0])} "
                f"shows '{expected}' while '{actual}' was submitted — please "
                f"correct whichever is wrong.")

    return f"Please review and correct: {_lower_first(message.rstrip('.'))}."


def correction_items(findings) -> list[dict]:
    """Every fixable finding, as something the vendor can act on.

    `ai_uncertain` findings are excluded on purpose: the comparator was unsure
    and routed the case to a *human reviewer*, not to the vendor. Asking a
    vendor to resolve our own uncertainty is the wrong message.
    """
    out = []
    for finding in findings:
        f = finding if isinstance(finding, dict) else asdict(finding)
        if f["severity"] != FIX or f.get("tag") == "ai_uncertain":
            continue
        out.append({"rule_id": f["rule_id"],
                    "category": (BY_ID[f["rule_id"]].category
                                 if f["rule_id"] in BY_ID else CAT_COMPLETENESS),
                    "text": correction_request(f)})
    return out


def explain(status: str, findings) -> dict:
    """The decision in plain English, with the next action. Deterministic.

    No model runs here. The Onboarding Assistant writes its own briefing in stage 7; this
    is the engine stating its own result in its own words, so the page can still
    explain a decision on a run where the model was unavailable.
    """
    # Accept live Findings or the rows they were persisted as, so the API can
    # explain a run it has just read back without rebuilding dataclasses.
    findings = [f if isinstance(f, dict) else asdict(f) for f in findings]
    blocks = [f for f in findings if f["severity"] == BLOCK]
    fixes = [f for f in findings if f["severity"] == FIX]
    uncertain = [f for f in fixes if f.get("tag") == "ai_uncertain"]

    if status in ("RUNNING", "", None):
        return {"headline": "Checks in progress",
                "summary": "The engine is still working through this submission.",
                "reason": "", "next_action": ""}

    if status == "REJECTED":
        n = len(blocks)
        categories = {BY_ID[f["rule_id"]].category
                      for f in blocks if f["rule_id"] in BY_ID}
        return {
            "headline": f"{n} blocking inconsistenc{'y' if n == 1 else 'ies'}",
            "summary": _sentences([f["message"] for f in blocks]),
            "reason": " ".join(BLOCK_REASONS[c] for c in CATEGORY_ORDER
                               if c in categories),
            "next_action": "Do not proceed with onboarding. No vendor-facing "
                           "message was drafted - telling a rejected party which "
                           "check caught them is deliberate policy.",
        }

    if status == "PENDING":
        n = len(fixes)
        actionable = [f for f in fixes if f.get("tag") != "ai_uncertain"]
        if actionable:
            action = ("Issue a correction link and send it to the vendor. It "
                      "reopens their own form, prefilled with what they sent.")
            if uncertain:
                action += (f" {len(uncertain)} comparison(s) also need a human "
                           "judgement and are not on the vendor's list.")
        else:
            action = ("Review the flagged comparisons yourself - the automated "
                      "comparison was not confident enough to decide, and "
                      "nothing was sent to the vendor.")
        return {
            "headline": f"{n} item{'' if n == 1 else 's'} "
                        f"need{'s' if n == 1 else ''} correction",
            "summary": _sentences([f["message"] for f in fixes]),
            "reason": "Nothing contradicts the submission - the evidence is "
                      "incomplete or unclear rather than inconsistent.",
            "next_action": action,
        }

    if status == "APPROVED":
        return {
            "headline": "All required checks passed",
            "summary": "Required information and documents are present, the "
                       "identifiers are valid, and no inconsistency was found "
                       "between what the vendor submitted and what their "
                       "documents say.",
            "reason": "",
            "next_action": "No further action is required. This vendor can be "
                           "onboarded.",
        }

    return {
        "headline": "Processing did not complete",
        "summary": "The run stopped on an error, so no decision was reached. "
                   "This is not a rejection - nothing was judged about this "
                   "vendor.",
        "reason": "",
        "next_action": "Check the audit trail below for the failed stage, then "
                       "resubmit once the cause is fixed.",
    }
