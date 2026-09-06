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

# The five documents this engine accepts, in the order the vendor form asks for
# them. Adding a sixth means a schema, a prompt, and rules that read it —
# nothing here is generic enough to pretend otherwise.
DOC_TYPES = ("pan_card", "gst_certificate", "incorporation_certificate",
             "bank_proof", "address_proof")

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


# `document_type` is on every schema so a rule can check the vendor attached the
# document to the right slot. It is still transcription — "what does this paper
# call itself" — and the rule, not the model, decides whether that is a problem.
SCHEMAS = {
    "pan_card": _nullable(
        "document_type", "pan", "name"),
    "gst_certificate": _nullable(
        "document_type", "gstin", "legal_name", "trade_name"),
    "incorporation_certificate": _nullable(
        "document_type", "legal_name", "registration_number", "incorporation_date"),
    "bank_proof": _nullable(
        "document_type", "account_holder_name", "account_number", "ifsc", "bank_name"),
    "address_proof": _nullable(
        "document_type", "name", "address", "state"),
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

# Every prompt asks for `document_type` first: the vendor may have attached the
# wrong file, and the answer to "what is this?" must not be coloured by what we
# were hoping to find. So the prompts say what we *expect*, never what to assume.
_WHAT_IS_IT = (
    "First, say what kind of document this actually is, in a few plain lowercase "
    "words taken from its own heading — for example 'pan card', 'gst registration "
    "certificate', 'certificate of incorporation', 'cancelled cheque', "
    "'electricity bill'. Report what the document says it is even if that is not "
    "what was expected. "
)

PROMPTS = {
    "pan_card":
        "This should be an Indian PAN card. " + _WHAT_IS_IT +
        "Then extract the 10-character PAN and the name of the cardholder exactly "
        "as printed. " + _TRANSCRIBER_RULES,
    "gst_certificate":
        "This should be an Indian GST registration certificate. " + _WHAT_IS_IT +
        "Then extract the 15-character GSTIN, the legal name of the business, and "
        "the trade name if one is shown. " + _TRANSCRIBER_RULES,
    "incorporation_certificate":
        "This should be a certificate of incorporation or business registration. "
        + _WHAT_IS_IT +
        "Then extract the legal entity name, the registration or identification "
        "number (CIN, LLPIN or registration number) exactly as printed, and the "
        "date of incorporation as YYYY-MM-DD. " + _TRANSCRIBER_RULES,
    "bank_proof":
        "This should be a cancelled cheque or bank confirmation letter. "
        + _WHAT_IS_IT +
        "Then extract the account holder name exactly as printed, the account "
        "number, the IFSC code, and the bank name. " + _TRANSCRIBER_RULES,
    "address_proof":
        "This should be proof of the business address — a utility bill, bank "
        "statement, lease or similar. " + _WHAT_IS_IT +
        "Then extract the name the document is addressed to, the full address as "
        "one line, and the state or province it is in. " + _TRANSCRIBER_RULES,
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
