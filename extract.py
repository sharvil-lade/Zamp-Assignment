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
