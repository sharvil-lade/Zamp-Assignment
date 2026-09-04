"""The 12 deterministic validation rules and the decision function.

PURE MODULE. Imports nothing outside the standard library — no anthropic, no
httpx, no sqlite3, no clock reads. That constraint is what structurally prevents
AI from reaching the decision (docs/04-decision-engine.md, docs/05-ai-design.md).

Every rule has the signature  (submission, extracted, ctx) -> list[Finding]
and returns [] when its inputs are absent (skip semantics, docs/02-data-model.md).
"""

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Callable

# --- types ------------------------------------------------------------------

BLOCK = "BLOCK"
FIX = "FIX"

STAGE_COMPLETENESS = "completeness"
STAGE_FORMAT = "format"
STAGE_CONSISTENCY = "consistency"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    stage: str
    message: str
    expected: str | None = None
    actual: str | None = None
    tag: str | None = None


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule needs from the outside world, injected.

    `today` is passed in rather than read, so tests are stable and the demo does
    not silently change behaviour over time. `names_match` defaults to the pure
    stdlib comparator below; Part 4 injects matching.names_match, which adds the
    ambiguous-band AI escalation. The default is deterministic, so AI is an
    opt-in refinement rather than a dependency.
    """

    today: date
    names_match: Callable[[str, str], bool] = None  # set in __post_init__

    def __post_init__(self):
        if self.names_match is None:
            object.__setattr__(self, "names_match", default_names_match)


# --- name comparison (stdlib fallback; matching.py builds on this in Part 4) --

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalize_name(name: str) -> str:
    """Uppercase, drop punctuation, collapse whitespace."""
    return _NON_ALNUM.sub(" ", (name or "").upper()).strip()


def default_names_match(a: str, b: str) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.92


# --- reference data ---------------------------------------------------------

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# PAN 4th character -> legal form, and which declared entity_types are consistent.
PAN_ENTITY_CODE = {
    "C": ("Company", {"Private Limited"}),
    "F": ("Firm/LLP", {"LLP", "Partnership"}),
    "P": ("Individual", {"Proprietorship"}),
}

GST_STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands",
    "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}

DOCUMENT_LABELS = {
    "incorporation_certificate": "Certificate of Incorporation",
    "bank_proof": "Cancelled cheque or bank letter",
    "insurance_certificate": "Certificate of Insurance",
}

ALWAYS_REQUIRED = (
    "legal_entity_name", "entity_type", "country_of_incorporation",
    "registered_address_state", "contact_name", "contact_email",
    "contact_phone", "tax_id_type", "account_holder_name",
    "account_number", "bank_name",
)


# --- helpers ----------------------------------------------------------------

def _val(submission: dict, key: str) -> str:
    v = submission.get(key)
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def _doc(extracted, key: str) -> dict | None:
    if not extracted:
        return None
    doc = extracted.get(key)
    return doc if isinstance(doc, dict) else None


def _is_india(submission: dict) -> bool:
    return _val(submission, "country_of_incorporation").upper() == "IN"


def _uses_gstin(submission: dict) -> bool:
    return _val(submission, "tax_id_type").upper() == "GSTIN"


def _fmt_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def gstin_checksum(first14: str) -> str:
    """The 15th character of a GSTIN, by the standard Luhn mod-36 algorithm.

    Fixtures must be generated with this — never hand-write a GSTIN
    (docs/06-demo-scenarios.md).
    """
    if len(first14) != 14:
        raise ValueError("gstin_checksum expects the first 14 characters")
    total, factor = 0, 2
    for ch in reversed(first14.upper()):
        product = GSTIN_CHARSET.index(ch) * factor
        factor = 1 if factor == 2 else 2
        total += product // 36 + product % 36
    return GSTIN_CHARSET[(36 - total % 36) % 36]


# --- stage 2: completeness --------------------------------------------------

def r01_required_fields(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    required = list(ALWAYS_REQUIRED)
    if _uses_gstin(submission):
        required.append("gstin")
    if _is_india(submission):
        required += ["pan", "ifsc"]
    return [
        Finding("R01", FIX, STAGE_COMPLETENESS,
                f"Required field '{name}' is missing")
        for name in required if not _val(submission, name)
    ]


def r02_required_documents(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    if extracted is None:          # extraction has not run — nothing to report
        return []
    return [
        Finding("R02", FIX, STAGE_COMPLETENESS,
                f"Required document '{label}' was not attached")
        for key, label in DOCUMENT_LABELS.items() if _doc(extracted, key) is None
    ]


# --- stage 4: format and checksum -------------------------------------------

def r03_pan_format(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    pan = _val(submission, "pan").upper()
    if not pan or not _is_india(submission):
        return []
    if PAN_RE.match(pan):
        return []
    return [Finding("R03", FIX, STAGE_FORMAT, "PAN format is invalid",
                    expected="AAAAA9999A", actual=pan)]


def r04_gstin_format_and_checksum(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    gstin = _val(submission, "gstin").upper()
    if not gstin or not _uses_gstin(submission):
        return []
    if not GSTIN_RE.match(gstin):
        return [Finding("R04", FIX, STAGE_FORMAT, "GSTIN format is invalid",
                        expected="99AAAAA9999A9ZA", actual=gstin)]
    expected_check = gstin_checksum(gstin[:14])
    if gstin[14] != expected_check:
        return [Finding(
            "R04", FIX, STAGE_FORMAT,
            "GSTIN checksum is invalid — this is not an issued GST number",
            expected=gstin[:14] + expected_check, actual=gstin)]
    return []


def r05_ifsc_format(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    ifsc = _val(submission, "ifsc").upper()
    if not ifsc or not _is_india(submission):
        return []
    if IFSC_RE.match(ifsc):
        return []
    return [Finding("R05", FIX, STAGE_FORMAT, "IFSC format is invalid",
                    expected="AAAA0999999", actual=ifsc)]


# --- stage 5: consistency ---------------------------------------------------

def _valid_gstin(submission: dict) -> str | None:
    """The GSTIN only if it is structurally sound — R04 already reported it if not."""
    gstin = _val(submission, "gstin").upper()
    return gstin if gstin and GSTIN_RE.match(gstin) else None


def _valid_pan(submission: dict) -> str | None:
    pan = _val(submission, "pan").upper()
    return pan if pan and PAN_RE.match(pan) else None


def r06_gstin_pan_match(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    gstin, pan = _valid_gstin(submission), _valid_pan(submission)
    if not gstin or not pan:
        return []
    embedded = gstin[2:12]
    if embedded == pan:
        return []
    return [Finding("R06", BLOCK, STAGE_CONSISTENCY,
                    "GSTIN-embedded PAN does not match the submitted PAN",
                    expected=embedded, actual=pan)]


def r07_pan_entity_type(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    pan = _valid_pan(submission)
    declared = _val(submission, "entity_type")
    if not pan or not declared:
        return []
    encoded = PAN_ENTITY_CODE.get(pan[3])
    if encoded is None:            # entity form outside MVP scope — do not guess
        return []
    form, accepted = encoded
    if declared in accepted:
        return []
    return [Finding("R07", BLOCK, STAGE_CONSISTENCY,
                    f"PAN encodes entity type '{form}' but the submission "
                    f"declares '{declared}'",
                    expected=form, actual=declared)]


def r08_gstin_state(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    gstin = _valid_gstin(submission)
    declared = _val(submission, "registered_address_state")
    if not gstin or not declared:
        return []
    code = gstin[:2]
    state = GST_STATE_CODES.get(code)
    if state is None:              # unknown code — do not guess
        return []
    if state.casefold() == declared.casefold():
        return []
    return [Finding("R08", FIX, STAGE_CONSISTENCY,
                    f"GSTIN is registered in {state} (state code {code}) but the "
                    f"registered address is in {declared}",
                    expected=state, actual=declared)]


def r09_bank_holder_name(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    bank = _doc(extracted, "bank_proof")
    entity = _val(submission, "legal_entity_name")
    holder = (bank or {}).get("account_holder_name")
    if not bank or not holder or not entity:
        return []
    if ctx.names_match(entity, holder):
        return []
    return [Finding("R09", BLOCK, STAGE_CONSISTENCY,
                    "Bank account is held in a different name than the vendor entity",
                    expected=entity, actual=holder)]


def r10_bank_details_match(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    bank = _doc(extracted, "bank_proof")
    if not bank:
        return []
    findings = []
    doc_account = (bank.get("account_number") or "").strip()
    form_account = _val(submission, "account_number")
    if doc_account and form_account and doc_account != form_account:
        findings.append(Finding(
            "R10", BLOCK, STAGE_CONSISTENCY,
            "Account number on the bank document does not match the submitted "
            "account number",
            expected=doc_account, actual=form_account))
    if _is_india(submission):
        doc_ifsc = (bank.get("ifsc") or "").strip().upper()
        form_ifsc = _val(submission, "ifsc").upper()
        if doc_ifsc and form_ifsc and doc_ifsc != form_ifsc:
            findings.append(Finding(
                "R10", BLOCK, STAGE_CONSISTENCY,
                "IFSC on the bank document does not match the submitted IFSC",
                expected=doc_ifsc, actual=form_ifsc))
    return findings


def r11_document_expiry(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    doc = _doc(extracted, "insurance_certificate")
    raw = (doc or {}).get("valid_until")
    if not doc or not raw:
        return []
    try:
        valid_until = date.fromisoformat(raw)
    except ValueError:             # unparseable date — extraction's problem, not a rule's
        return []
    if valid_until >= ctx.today:
        return []
    label = DOCUMENT_LABELS["insurance_certificate"]
    return [Finding("R11", FIX, STAGE_CONSISTENCY,
                    f"{label} expired on {_fmt_date(valid_until)}",
                    expected=f"valid on or after {_fmt_date(ctx.today)}",
                    actual=raw)]


def r12_incorporation_name(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    doc = _doc(extracted, "incorporation_certificate")
    entity = _val(submission, "legal_entity_name")
    doc_name = (doc or {}).get("legal_name")
    if not doc or not doc_name or not entity:
        return []
    if ctx.names_match(entity, doc_name):
        return []
    return [Finding("R12", FIX, STAGE_CONSISTENCY,
                    "Legal name on the incorporation certificate differs from the "
                    "submitted name",
                    expected=doc_name, actual=entity)]


# --- rule sets by stage -----------------------------------------------------

COMPLETENESS_RULES = (r01_required_fields, r02_required_documents)
FORMAT_RULES = (r03_pan_format, r04_gstin_format_and_checksum, r05_ifsc_format)
CONSISTENCY_RULES = (
    r06_gstin_pan_match, r07_pan_entity_type, r08_gstin_state,
    r09_bank_holder_name, r10_bank_details_match, r11_document_expiry,
    r12_incorporation_name,
)
ALL_RULES = COMPLETENESS_RULES + FORMAT_RULES + CONSISTENCY_RULES


def apply(rules, submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    return [f for rule in rules for f in rule(submission, extracted, ctx)]


# --- the decision -----------------------------------------------------------

def decide(findings: list[Finding]) -> str:
    """The entire decision layer. Sees findings and nothing else."""
    if any(f.severity == BLOCK for f in findings):
        return "REJECTED"
    if any(f.severity == FIX for f in findings):
        return "PENDING"
    return "APPROVED"
