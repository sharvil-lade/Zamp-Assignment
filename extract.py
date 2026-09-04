"""Stage 3 — turn a vendor document into structured JSON.

The only genuinely unsolvable-by-rules step in the process: vendors format
documents however they like (docs/05-ai-design.md).

This module transcribes. It does not interpret, validate, or decide — those
belong to rules.py, which cannot import this file.
"""

import base64
import json
import os
from pathlib import Path

import anthropic

MODEL = "claude-opus-5"
MAX_TOKENS = 2000

DOC_TYPES = ("incorporation_certificate", "bank_proof", "insurance_certificate")

MEDIA_TYPES = {
    ".pdf": ("document", "application/pdf"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
}


MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# First bytes each accepted format must start with. A renamed .exe or a truncated
# download is caught here rather than at the API, which turns "vendor attached the
# wrong file" into a fixable finding instead of a crashed run.
MAGIC = {
    "application/pdf": (b"%PDF",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
}


def check_upload(filename: str, data: bytes) -> str | None:
    """Return a human-readable rejection reason, or None if the file is usable."""
    suffix = Path(filename).suffix.lower()
    if suffix not in MEDIA_TYPES:
        allowed = ", ".join(sorted(MEDIA_TYPES))
        return f"'{suffix or filename}' is not a supported file type (allowed: {allowed})"
    if not data:
        return "the file is empty"
    if len(data) > MAX_UPLOAD_BYTES:
        # The caller reads MAX+1 bytes, so len(data) is a floor, not the real
        # size — don't quote a misleading number back at the user.
        return f"the file is larger than the {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit"
    _, media_type = MEDIA_TYPES[suffix]
    if not data.startswith(MAGIC[media_type]):
        return (f"the file does not look like a valid {suffix[1:].upper()} "
                f"(wrong content for its extension)")
    return None


def _nullable(*names: str) -> dict:
    """Every extracted field is nullable: absence is a finding input, not an error."""
    return {
        "type": "object",
        "properties": {n: {"type": ["string", "null"]} for n in names},
        "required": list(names),
        "additionalProperties": False,
    }


SCHEMAS = {
    "incorporation_certificate": _nullable(
        "legal_name", "registration_number", "incorporation_date"),
    "bank_proof": _nullable(
        "account_holder_name", "account_number", "ifsc", "bank_name"),
    "insurance_certificate": _nullable(
        "insured_name", "policy_number", "valid_until"),
}

# "Do not infer or correct" is load-bearing, not politeness. If the model
# silently normalises a PAN to the one it saw on another document, R06 stops
# working and the engine starts approving forged submissions.
_TRANSCRIBER_RULES = (
    "Transcribe only what is printed on this document. "
    "If a value is absent, blank, or not legible, return null for it. "
    "Never infer, correct, complete, normalise or guess a value, and never "
    "take a value from your own knowledge. An honest null is always better "
    "than a plausible guess."
)

PROMPTS = {
    "incorporation_certificate":
        "This is a certificate of incorporation. Extract the legal entity name, "
        "the registration or identification number, and the date of incorporation "
        "as YYYY-MM-DD. " + _TRANSCRIBER_RULES,
    "bank_proof":
        "This is a cancelled cheque or bank confirmation letter. Extract the "
        "account holder name exactly as printed, the account number, the IFSC "
        "code, and the bank name. " + _TRANSCRIBER_RULES,
    "insurance_certificate":
        "This is a certificate of insurance. Extract the insured party's name, "
        "the policy number, and the date the cover is valid until as YYYY-MM-DD. "
        + _TRANSCRIBER_RULES,
}

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["subject", "body"],
    "additionalProperties": False,
}

# The drafter never sees the submission, the status, or the BLOCK findings. It is
# handed a vendor name, a contact name, and a list of things the vendor can fix.
DRAFT_PROMPT = """You write short, plain follow-up emails to vendors on behalf of a \
procurement team.

Vendor: {vendor}
Contact person: {contact}
Reference: {run_id}

These are the only outstanding items. Each one has already been checked; do not \
re-interpret them and do not add any requirement that is not on this list:

{items}

Write the email. Requirements:
- Address the contact by first name.
- One numbered item per outstanding issue, in the order given.
- Be specific. If an item names a date or a document, say the date and the \
document name. Never write a vague summary like "your submission is incomplete".
- End with one clear sentence on what happens next.
- Keep it under 150 words, courteous and matter-of-fact.
- Do not mention internal rule identifiers, scores, systems, or automated checks.
- Do not state or imply an approval decision, and do not promise a timeline.

`subject` must be one line and include the reference."""


def draft_followup(run_id: str, vendor_name: str, contact_name: str,
                   findings) -> tuple[str, dict]:
    """Turn actionable findings into a vendor-facing draft. Returns (text, meta).

    Runs after the decision and cannot influence it. Deterministic input,
    natural-language output — the one place where that is pure upside.
    """
    items = "\n".join(
        f"- {f['message']}" + (
            f" (expected {f['expected']}, found {f['actual']})"
            if f.get("expected") and f.get("actual") else "")
        for f in findings)

    resp = client().messages.create(
        model=MODEL,
        max_tokens=1000,
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
        messages=[{"role": "user", "content": DRAFT_PROMPT.format(
            vendor=vendor_name, contact=contact_name or "there",
            run_id=run_id, items=items)}],
    )

    raw = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(raw)
    text = f"Subject: {data['subject']}\n\n{data['body']}"

    meta = {
        "purpose": "draft_followup",
        "model": MODEL,
        "input_summary": f"{len(findings)} actionable finding(s) for {vendor_name}",
        "raw_response": raw,
        "usage": {"input_tokens": resp.usage.input_tokens,
                  "output_tokens": resp.usage.output_tokens},
    }
    return text, meta


_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — copy .env.example to .env")
        _client = anthropic.Anthropic()
    return _client


def _content_block(path: Path) -> dict:
    """A document block for PDFs, an image block for scans. Same call either way."""
    kind, media_type = MEDIA_TYPES.get(path.suffix.lower(), (None, None))
    if kind is None:
        raise ValueError(f"unsupported document type: {path.suffix}")
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {"type": kind, "source": {"type": "base64",
                                     "media_type": media_type, "data": data}}


def extract_document(path: Path, doc_type: str) -> tuple[dict, dict]:
    """Return (extracted fields, ai_call metadata) for one document.

    Raises on API failure — the pipeline turns that into ERROR, never a status.
    """
    if doc_type not in SCHEMAS:
        raise ValueError(f"unknown document type: {doc_type}")
    path = Path(path)

    resp = client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": SCHEMAS[doc_type]},
        },
        messages=[{"role": "user", "content": [
            _content_block(path),
            {"type": "text", "text": PROMPTS[doc_type]},
        ]}],
    )

    raw = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(raw)          # output_config.format guarantees the shape
    data = {k: (v.strip() if isinstance(v, str) and v.strip() else None)
            for k, v in data.items()}

    meta = {
        "purpose": f"extract:{doc_type}",
        "model": MODEL,
        "input_summary": f"{path.name} ({path.stat().st_size} bytes)",
        "raw_response": raw,
        "usage": {"input_tokens": resp.usage.input_tokens,
                  "output_tokens": resp.usage.output_tokens},
    }
    return data, meta
