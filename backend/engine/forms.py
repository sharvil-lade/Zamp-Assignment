"""Configurable onboarding forms.

    form  ->  onboarding case (schema snapshot)  ->  submission

A form is edited in place; there are no versions. Each onboarding case keeps a
snapshot of the schema it was created with, so a submission stays readable
against the questions actually asked even after the form is changed.

Two layers of meaning, which is what keeps PS-2 intact:

  canonical fields   a schema field carrying `canonical: "gstin"` is the PS-2
                     GSTIN field. It flows into rules.py exactly as before and
                     the existing deterministic rules apply unchanged.

  custom fields      anything else. Schema validation (required, type, options)
                     and the Onboarding Assistant's review — never invented business rules.

Pure module: no database, no network, no model calls.
"""

import re
from datetime import date

from ai import extract
from engine import rules

FIELD_TYPES = ("text", "textarea", "email", "phone", "number", "date",
               "boolean", "select", "document")

DOCUMENT_TYPE = "document"
STANDARD_TEMPLATE_NAME = "Standard Vendor Onboarding"

FIELD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,60}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- schema validation -------------------------------------------------------

def validate_schema(schema: dict) -> list[str]:
    """Structural problems with a form definition. Empty list means usable."""
    problems: list[str] = []
    if not isinstance(schema, dict):
        return ["schema must be an object"]

    sections = schema.get("sections")
    if not isinstance(sections, list) or not sections:
        return ["schema must contain at least one section"]

    seen: set[str] = set()
    for index, section in enumerate(sections, 1):
        if not isinstance(section, dict) or not str(section.get("title", "")).strip():
            problems.append(f"section {index} needs a title")
            continue
        fields = section.get("fields")
        if not isinstance(fields, list) or not fields:
            problems.append(f"section '{section['title']}' has no fields")
            continue
        for field in fields:
            problems += _validate_field(field, section["title"], seen)
    return problems


def _validate_field(field, section_title: str, seen: set[str]) -> list[str]:
    if not isinstance(field, dict):
        return [f"section '{section_title}' contains a field that is not an object"]

    problems = []
    field_id = field.get("id", "")
    label = str(field.get("label", "")).strip()
    field_type = field.get("type")

    if not FIELD_ID_RE.match(str(field_id)):
        problems.append(f"'{field_id}' is not a valid field id "
                        "(lowercase letters, digits and underscores)")
    elif field_id in seen:
        problems.append(f"duplicate field id '{field_id}'")
    else:
        seen.add(field_id)

    if not label:
        problems.append(f"field '{field_id}' needs a label")
    if field_type not in FIELD_TYPES:
        problems.append(f"field '{field_id}' has an unknown type '{field_type}'")
    if field_type == "select" and not field.get("options"):
        problems.append(f"select field '{field_id}' needs at least one option")

    canonical = field.get("canonical")
    if canonical and canonical not in CANONICAL_FIELDS and canonical not in extract.DOC_TYPES:
        problems.append(f"field '{field_id}' maps to unknown canonical "
                        f"field '{canonical}'")
    return problems


CANONICAL_FIELDS = set(rules.SUBMISSION_FIELDS)


# --- reading a schema --------------------------------------------------------

def iter_fields(schema: dict):
    for section in (schema or {}).get("sections", []):
        for field in section.get("fields", []):
            yield section, field


def document_fields(schema: dict) -> list[dict]:
    return [f for _, f in iter_fields(schema) if f.get("type") == DOCUMENT_TYPE]


def value_fields(schema: dict) -> list[dict]:
    return [f for _, f in iter_fields(schema) if f.get("type") != DOCUMENT_TYPE]


def canonical_document_types(schema: dict) -> dict[str, str]:
    """{field id: canonical document type} for documents PS-2 knows how to read."""
    return {f["id"]: f["canonical"] for f in document_fields(schema)
            if f.get("canonical") in extract.DOC_TYPES}


# --- normalisation -----------------------------------------------------------

def normalize_submission(schema: dict, raw: dict) -> tuple[dict, dict]:
    """Split a submission into (canonical PS-2 fields, custom fields).

    The canonical half is exactly the dict `rules.py` has always received, so
    every existing rule keeps working with no knowledge of forms at all.
    """
    canonical: dict = {name: "" for name in rules.SUBMISSION_FIELDS}
    custom: dict = {}

    for _, field in iter_fields(schema):
        if field.get("type") == DOCUMENT_TYPE:
            continue
        value = raw.get(field["id"], "")
        value = value.strip() if isinstance(value, str) else value
        target = field.get("canonical")
        if target in CANONICAL_FIELDS:
            canonical[target] = value
        else:
            custom[field["id"]] = value
    return canonical, custom


def custom_field_findings(schema: dict, raw: dict, present_documents) -> list:
    """Generic checks for fields PS-2 has no opinion about.

    Deliberately only completeness and type sanity: an invented business rule
    for an arbitrary field would be a guess dressed as a decision. Anything
    subtler is the Onboarding Assistant's job, and a human's.
    """
    found = []
    present = set(present_documents or ())

    for _, field in iter_fields(schema):
        if field.get("canonical"):
            continue                                  # PS-2 already owns this one
        field_id, label = field["id"], field.get("label", field["id"])

        if field.get("type") == DOCUMENT_TYPE:
            if field.get("required") and field_id not in present:
                found.append(rules.Finding(
                    "R02", rules.FIX, rules.STAGE_COMPLETENESS,
                    f"Required document '{label}' was not attached"))
            continue

        value = raw.get(field_id, "")
        value = value.strip() if isinstance(value, str) else value
        if field.get("required") and value in ("", None, []):
            found.append(rules.Finding(
                "R01", rules.FIX, rules.STAGE_COMPLETENESS,
                f"Required field '{label}' is missing"))
            continue
        if value in ("", None, []):
            continue

        problem = _type_problem(field, value)
        if problem:
            found.append(rules.Finding(
                "R03", rules.FIX, rules.STAGE_FORMAT,
                f"{label} {problem}", actual=str(value)[:80]))
    return found


def _type_problem(field: dict, value) -> str | None:
    kind = field.get("type")
    text = str(value)
    if kind == "email" and not EMAIL_RE.match(text):
        return "is not a valid email address"
    if kind == "number":
        try:
            float(text)
        except ValueError:
            return "is not a number"
    if kind == "date":
        try:
            date.fromisoformat(text)
        except ValueError:
            return "is not a valid date (YYYY-MM-DD)"
    if kind == "select" and text not in [str(o) for o in field.get("options", [])]:
        return "is not one of the allowed options"
    return None


# --- the standard PS-2 template ---------------------------------------------

# How the submission fields are grouped for a human. Public because the direct
# submission form renders from it too — a second copy in the frontend is a list
# that silently goes stale the moment a field is added.
FIELD_GROUPS = (
    ("Your company", ["legal_entity_name", "entity_type",
                      "country_of_incorporation", "registered_address",
                      "registered_address_state"]),
    ("Who we should contact", ["contact_name", "contact_email", "contact_phone"]),
    ("Tax registration", ["tax_id_type", "gstin", "pan"]),
    ("Bank account", ["account_holder_name", "account_number", "ifsc", "bank_name"]),
)

_TYPES = {"contact_email": "email", "contact_phone": "phone",
          "registered_address": "textarea"}
_OPTIONS = {"entity_type": rules.ENTITY_TYPES,
            "country_of_incorporation": rules.COUNTRIES,
            "tax_id_type": rules.TAX_ID_TYPES}

# What the vendor needs to know to answer correctly the first time. Each line
# says why we are asking, because every one of these is cross-checked against a
# document and a vendor who knows that fills it in more carefully.
_HELP = {
    "legal_entity_name":
        "Exactly as printed on your incorporation certificate — not a trading "
        "or brand name.",
    "entity_type":
        "This decides which registration number and PAN category we expect.",
    "registered_address":
        "Your registered address, as it appears on the address proof you attach.",
    "registered_address_state":
        "For Indian vendors this must match the state your GSTIN was issued in.",
    "contact_email": "We will send any follow-up here.",
    "tax_id_type": "GSTIN for Indian vendors, EIN for US vendors.",
    "gstin": "15 characters. Required if you are registered for GST.",
    "pan": "10 characters. Required for Indian vendors.",
    "account_holder_name":
        "Must be the name on the account, which we check against your cancelled "
        "cheque or bank letter.",
    "ifsc": "11 characters. Required for Indian bank accounts.",
}

# What counts as each document. Vendors send the wrong thing far more often
# than they send a forged thing.
_DOCUMENT_HELP = {
    "pan_card": "A clear photo or scan of the PAN card itself.",
    "gst_certificate":
        "Form GST REG-06, the certificate issued when you registered.",
    "incorporation_certificate":
        "Certificate of incorporation, LLP agreement, partnership deed or "
        "equivalent business registration.",
    "bank_proof":
        "A cancelled cheque, or a letter from your bank showing the account "
        "number and IFSC.",
    "address_proof":
        "A utility bill, bank statement or lease showing the registered address "
        "above.",
}


def _sentence_case(text: str) -> str:
    return text[:1].upper() + text[1:]


def missing_engine_fields(schema: dict) -> set[str]:
    """Canonical fields and documents the engine reads but this schema never asks for.

    Used to tell a form that has merely been *edited* — reordered, reworded, an
    extra custom question added, all of which are fine — from one that has
    fallen behind the engine and can no longer collect what the rules need.
    """
    asked = {f.get("canonical") for _, f in iter_fields(schema) if f.get("canonical")}
    return (CANONICAL_FIELDS | set(extract.DOC_TYPES)) - asked


def unenforced_requirements(schema: dict) -> set[str]:
    """Engine fields this schema asks for but lets the vendor skip.

    Same purpose as `missing_engine_fields`: a form that collects less than the
    engine reads sends the vendor away and brings them back. Asking for a field
    and then accepting it blank has the same effect, so it counts as the same
    kind of staleness.
    """
    return {f["id"] for _, f in iter_fields(schema)
            if f.get("canonical") and not f.get("required")}


def standard_schema() -> dict:
    """The existing PS-2 form, expressed as a form schema.

    Derived from rules.py rather than retyped, so the default template cannot
    drift away from the fields the deterministic engine actually reads.
    """
    # Every canonical field and every document is required in the browser: a
    # vendor cannot submit a half-filled form and discover an hour later that
    # they had to send five documents, not three.
    #
    # This is stricter than the engine, deliberately. R01 and R02 still decide
    # what is required from the answers — an incomplete submission that arrives
    # by any other route is still judged the same way, so the rules remain the
    # safety layer rather than the only layer.
    #
    # The cost is that the standard form now assumes an Indian vendor: GSTIN,
    # PAN, IFSC, the PAN card and the GST certificate are all insisted on, and
    # a US vendor cannot satisfy it. That is a form-level decision, not an
    # engine one — duplicate this form and relax those five for a US intake.
    sections = []
    for title, field_ids in FIELD_GROUPS:
        fields = []
        for field_id in field_ids:
            field = {
                "id": field_id,
                # Only the first character: `.capitalize()` lowercases the
                # rest, which turns GSTIN into "Gstin" and IFSC into "Ifsc".
                "label": _sentence_case(rules.FIELD_LABELS[field_id]),
                "type": "select" if field_id in _OPTIONS else _TYPES.get(field_id, "text"),
                "required": True,
                "canonical": field_id,
            }
            if field_id in _OPTIONS:
                field["options"] = list(_OPTIONS[field_id])
            if field_id in _HELP:
                field["help"] = _HELP[field_id]
            fields.append(field)
        sections.append({"title": title, "fields": fields})

    sections.append({
        "title": "Documents",
        "description": "PDF, JPG or PNG. Make sure every corner is in shot and "
                       "the text is legible — we read these, we do not just "
                       "file them.",
        "fields": [
            {"id": doc_type, "label": rules.DOCUMENT_LABELS[doc_type],
             "type": DOCUMENT_TYPE, "required": True,
             "canonical": doc_type, "help": _DOCUMENT_HELP[doc_type]}
            for doc_type in extract.DOC_TYPES],
    })

    return {"sections": sections}


STANDARD_DESCRIPTION = ("Everything the decision engine checks: company, "
                        "contact, tax and bank details, and the five documents "
                        "they are cross-checked against.")
