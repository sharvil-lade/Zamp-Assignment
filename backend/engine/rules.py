"""The 17 deterministic validation rules and the decision function.

The ids run R01-R18 with no R11: R11 checked an insurance certificate that is no
longer part of the document set, and reusing its number would silently change
what it means in audit records already written.

PURE MODULE. Imports nothing outside the standard library — no anthropic, no
httpx, no sqlite3, no clock reads. That constraint is what structurally prevents
AI from reaching the decision (docs/04-decision-engine.md, docs/05-ai-design.md).

Every rule has the signature  (submission, extracted, ctx) -> list[Finding]
and returns [] when its inputs are absent (skip semantics, docs/02-data-model.md).
"""

import re
from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Callable

# --- types ------------------------------------------------------------------

BLOCK = "BLOCK"
FIX = "FIX"

STAGE_COMPLETENESS = "completeness"
STAGE_FORMAT = "format"
STAGE_CONSISTENCY = "consistency"

# What a rule did when it ran. A rule that passed and a rule that never applied
# both produce no findings, so without this the run page cannot honestly say
# "14 of 17 checks passed" — it would be counting a PAN check that was correctly
# never run against a US vendor.
PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"

# Business groupings for a reviewer. Deliberately NOT the same axis as stages:
# a stage is when a rule runs, a category is what part of the vendor it protects.
# R13 and R03 both run in the format stage but answer different questions.
CAT_COMPLETENESS = "Completeness"
CAT_DOCUMENT = "Document integrity"
CAT_IDENTIFIER = "Identifier & format validation"
CAT_IDENTITY = "Identity & tax consistency"
CAT_BANKING = "Banking consistency"
CAT_ADDRESS = "Address consistency"

CATEGORY_ORDER = (CAT_COMPLETENESS, CAT_DOCUMENT, CAT_IDENTIFIER,
                  CAT_IDENTITY, CAT_BANKING, CAT_ADDRESS)

# Where a compared value came from. `expected` always holds the evidence and
# `actual` always holds what the vendor typed, so a reviewer reading a
# side-by-side never has to work out which column is which.
SRC_SUBMISSION = "Vendor submission"


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
class Rule:
    """What a rule is, beside the function that runs it.

    The metadata is not decoration: `name`, `category` and `purpose` are what the
    run page shows a reviewer instead of a bare identifier, and `purpose` is the
    answer to "why does this check exist?" living next to the check rather than
    in a document that can drift away from it.
    """

    id: str
    name: str
    category: str
    stage: str
    purpose: str
    impact: tuple                        # severities this rule can produce
    check: Callable
    applies: Callable                    # (submission, extracted) -> bool
    evidence: tuple | None = None        # (source of `expected`, source of `actual`)

    def as_dict(self) -> dict:
        return {"rule_id": self.id, "name": self.name, "category": self.category,
                "stage": self.stage, "purpose": self.purpose,
                "impact": list(self.impact),
                "evidence": list(self.evidence) if self.evidence else None}


@dataclass(frozen=True)
class RuleOutcome:
    """What happened to one rule on one run: passed, failed, or never applied."""

    rule: Rule
    state: str
    findings: tuple = ()

    def as_dict(self) -> dict:
        return {**self.rule.as_dict(), "state": self.state,
                "findings": [asdict(f) for f in self.findings]}

    def as_record(self) -> dict:
        """What is worth persisting: what varied on this run.

        A rule's name, category and purpose are the same on every run and live
        in `RULES`; writing them into each audit event stored the same few
        kilobytes of static text per submission. The view model joins them back
        by id.
        """
        return {"rule_id": self.rule.id, "state": self.state,
                "findings": [asdict(f) for f in self.findings]}


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


@dataclass(frozen=True)
class NameVerdict:
    """`match=None` means *uncertain* — route to a human, never to a rejection."""

    match: bool | None
    reason: str | None = None
    score: float | None = None


def default_names_match(a: str, b: str) -> NameVerdict:
    """Pure stdlib comparator. Never returns uncertain — it has no one to ask."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return NameVerdict(False)
    if na == nb:
        return NameVerdict(True, score=1.0)
    score = SequenceMatcher(None, na, nb).ratio()
    return NameVerdict(score >= 0.92, score=score)


def _verdict(result) -> NameVerdict:
    """Accept a bare bool from a simple injected comparator, or a full verdict."""
    return result if isinstance(result, NameVerdict) else NameVerdict(bool(result))


def _uncertain_finding(rule_id: str, a: str, b: str, verdict: NameVerdict) -> Finding:
    """An unsure model asks a human. It never rejects, and never silently passes.

    `a` is the submitted name and `b` the one read off the document, but they are
    stored the other way round: every comparison finding puts the evidence in
    `expected` and the vendor's own answer in `actual`, so the run page can label
    a side-by-side by source without special-casing any rule.
    """
    reason = f" ({verdict.reason})" if verdict.reason else ""
    return Finding(rule_id, FIX, STAGE_CONSISTENCY,
                   f"Could not confidently determine whether '{a}' and '{b}' are "
                   f"the same entity — human review required{reason}",
                   expected=b, actual=a, tag="ai_uncertain")


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
    "pan_card": "PAN card",
    "gst_certificate": "GST registration certificate",
    "incorporation_certificate": "Certificate of incorporation or business registration",
    "bank_proof": "Cancelled cheque or bank letter",
    "address_proof": "Address proof",
}

# Which documents a given submission has to carry. PAN and GST documents are
# only asked for when the submission claims them, mirroring R01 exactly — a US
# vendor is not chased for an Indian PAN card.
def required_documents(submission: dict) -> list[str]:
    keys = ["incorporation_certificate", "bank_proof", "address_proof"]
    if _is_india(submission):
        keys.insert(0, "pan_card")
    if _uses_gstin(submission):
        keys.insert(1 if _is_india(submission) else 0, "gst_certificate")
    return keys


# Human labels for the values read off a document. R14 reaches a vendor by
# email, and `'account_holder_name'` is our vocabulary, not theirs.
DOCUMENT_FIELD_LABELS = {
    "pan": "PAN",
    "gstin": "GSTIN",
    "name": "name",
    "legal_name": "legal name",
    "trade_name": "trade name",
    "registration_number": "registration number",
    "incorporation_date": "date of incorporation",
    "account_holder_name": "account holder name",
    "account_number": "account number",
    "ifsc": "IFSC code",
    "bank_name": "bank name",
    "address": "address",
    "state": "state",
}

# What each document must yield for it to count as readable. Anything beyond
# this is useful but not disqualifying, so its absence is not a finding.
DOCUMENT_REQUIRED_FIELDS = {
    "pan_card": ("pan", "name"),
    "gst_certificate": ("gstin", "legal_name"),
    "incorporation_certificate": ("legal_name", "registration_number"),
    "bank_proof": ("account_holder_name", "account_number"),
    "address_proof": ("name", "address"),
}

# Words that make a document recognisably the thing it was uploaded as. Matched
# against what the document calls itself, never against the file name — a vendor
# renaming a file changes nothing about what is printed on it.
DOCUMENT_TYPE_KEYWORDS = {
    "pan_card": ("pan", "permanent account number"),
    "gst_certificate": ("gst", "goods and services"),
    "incorporation_certificate": ("incorporation", "business registration",
                                  "certificate of registration", "partnership deed",
                                  "llp agreement", "udyam", "shop"),
    "bank_proof": ("cheque", "check", "bank"),
    "address_proof": ("address", "utility", "electricity", "water", "gas",
                      "telephone", "broadband", "lease", "rent", "tenancy",
                      "bank statement", "aadhaar", "passport", "bill"),
}

# A CIN is 21 characters; an LLPIN is three letters, a hyphen and four digits.
# Anything else is accepted as a plain registration number — partnerships and
# proprietorships have no standard identifier, and inventing a format for them
# would reject honest vendors.
CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
LLPIN_RE = re.compile(r"^[A-Z]{3}-\d{4}$")

# Every identifier this engine knows how to shape-check, in one table: pattern
# plus the mask a human is shown when it does not match. R03, R04, R05 and R17
# all validate through `validate_identifier_format` below — one implementation —
# while still raising their own separate findings, because "your PAN is
# malformed" and "your GSTIN is malformed" are different things to tell a vendor.
IDENTIFIER_FORMATS = {
    "PAN": (PAN_RE, "AAAAA9999A"),
    "GSTIN": (GSTIN_RE, "99AAAAA9999A9ZA"),
    "IFSC": (IFSC_RE, "AAAA0999999"),
    "CIN": (CIN_RE, "U99999AA9999AAA999999"),
    "LLPIN": (LLPIN_RE, "AAA-9999"),
}


def validate_identifier_format(kind: str, value: str) -> str | None:
    """None if `value` has the documented shape of `kind`, else the expected mask.

    Shape only. Whether the identifier is *issued* is a different question —
    R04 answers it for GSTIN with a real checksum, and nothing else can be
    answered without an external registry we deliberately do not call.
    """
    pattern, mask = IDENTIFIER_FORMATS[kind]
    return None if pattern.match((value or "").strip().upper()) else mask


# Which entity types have a registration identifier with a known national shape.
REGISTRATION_FORMATS = {"Private Limited": "CIN", "LLP": "LLPIN"}

# The documented submission fields, in form order (docs/02-data-model.md).
SUBMISSION_FIELDS = (
    "legal_entity_name", "entity_type", "country_of_incorporation",
    "registered_address", "registered_address_state",
    "contact_name", "contact_email", "contact_phone",
    "tax_id_type", "gstin", "pan",
    "account_holder_name", "account_number", "ifsc", "bank_name",
)

# Human labels for the same fields. Rule messages keep the raw field name — that
# is the right level of detail for a reviewer — and the vendor-facing draft
# substitutes the label instead (pipeline._vendor_message).
FIELD_LABELS = {
    "legal_entity_name": "legal entity name",
    "entity_type": "entity type",
    "country_of_incorporation": "country of incorporation",
    "registered_address": "registered address",
    "registered_address_state": "registered address state",
    "contact_name": "contact name",
    "contact_email": "contact email address",
    "contact_phone": "contact phone number",
    "tax_id_type": "tax ID type",
    "gstin": "GSTIN",
    "pan": "PAN",
    "account_holder_name": "bank account holder name",
    "account_number": "bank account number",
    "ifsc": "IFSC code",
    "bank_name": "bank name",
}

ENTITY_TYPES = ("Private Limited", "LLP", "Partnership", "Proprietorship",
                "Foreign Corporation")
COUNTRIES = ("IN", "US")
TAX_ID_TYPES = ("GSTIN", "EIN")

# Which submitted field is compared against which extracted field, and the rule
# that owns the comparison. One definition: the reviewer's table and the AI
# Employee's briefing both build from this rather than restating it.
DOCUMENT_COMPARISONS = (
    ("pan_card", (
        ("PAN", "pan", "pan", "R15"),
        ("Name on card — vs legal entity", "legal_entity_name", "name", "R15"),
    )),
    ("gst_certificate", (
        ("GSTIN", "gstin", "gstin", "R16"),
        ("Legal name", "legal_entity_name", "legal_name", "R16"),
        ("Trade name", "legal_entity_name", "trade_name", None),
    )),
    ("incorporation_certificate", (
        ("Legal entity name", "legal_entity_name", "legal_name", "R12"),
        ("Registration number", None, "registration_number", "R17"),
    )),
    ("bank_proof", (
        ("Account holder — vs legal entity", "legal_entity_name",
         "account_holder_name", "R09"),
        ("Account number", "account_number", "account_number", "R10"),
        ("IFSC", "ifsc", "ifsc", "R10"),
        ("Bank name", "bank_name", "bank_name", None),
    )),
    ("address_proof", (
        ("Addressed to — vs legal entity", "legal_entity_name", "name", "R18"),
        ("State", "registered_address_state", "state", "R18"),
        ("Address", "registered_address", "address", None),
    )),
)


def compare_documents(submission: dict, extracted) -> list[dict]:
    """Submitted values beside document values, grouped by document.

    Pure: no I/O, no model. `mismatch` is only set where a rule actually
    compares the pair, so a highlighted row always corresponds to a real check.
    """
    extracted = extracted or {}
    groups = []
    for key, fields in DOCUMENT_COMPARISONS:
        doc = extracted.get(key)
        rows = []
        for label, form_field, doc_field, rule_id in fields:
            # A row with no form field is document-only — a registration number
            # or a street address the form never asked for. There is nothing to
            # compare it against, so it is shown, not judged.
            form_value = (submission.get(form_field) or None) if form_field else None
            doc_value = (doc or {}).get(doc_field)
            rows.append({
                "label": label,
                "form": form_value,
                "doc": doc_value,
                "rule": rule_id,
                "mismatch": bool(rule_id and form_value and doc_value
                                 and str(form_value).strip().casefold()
                                 != str(doc_value).strip().casefold()),
            })
        groups.append({"label": DOCUMENT_LABELS[key], "key": key,
                       "attached": doc is not None, "rows": rows})
    return groups


ALWAYS_REQUIRED = (
    "legal_entity_name", "entity_type", "country_of_incorporation",
    "registered_address", "registered_address_state",
    "contact_name", "contact_email",
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
                f"Required document '{DOCUMENT_LABELS[key]}' was not attached")
        for key in required_documents(submission) if _doc(extracted, key) is None
    ]


# --- stage 4: format and checksum -------------------------------------------

def r03_pan_format(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    pan = _val(submission, "pan").upper()
    if not pan or not _is_india(submission):
        return []
    mask = validate_identifier_format("PAN", pan)
    if mask is None:
        return []
    return [Finding("R03", FIX, STAGE_FORMAT, "PAN format is invalid",
                    expected=mask, actual=pan)]


def r04_gstin_format_and_checksum(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    gstin = _val(submission, "gstin").upper()
    if not gstin or not _uses_gstin(submission):
        return []
    mask = validate_identifier_format("GSTIN", gstin)
    if mask is not None:
        return [Finding("R04", FIX, STAGE_FORMAT, "GSTIN format is invalid",
                        expected=mask, actual=gstin)]
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
    mask = validate_identifier_format("IFSC", ifsc)
    if mask is None:
        return []
    return [Finding("R05", FIX, STAGE_FORMAT, "IFSC format is invalid",
                    expected=mask, actual=ifsc)]


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
    verdict = _verdict(ctx.names_match(entity, holder))
    if verdict.match is True:
        return []
    if verdict.match is None:                  # unsure: ask a human, do not reject
        return [_uncertain_finding("R09", entity, holder, verdict)]
    return [Finding("R09", BLOCK, STAGE_CONSISTENCY,
                    "Bank account is held in a different name than the vendor entity",
                    expected=holder, actual=entity)]


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


def r12_incorporation_name(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    doc = _doc(extracted, "incorporation_certificate")
    entity = _val(submission, "legal_entity_name")
    doc_name = (doc or {}).get("legal_name")
    if not doc or not doc_name or not entity:
        return []
    verdict = _verdict(ctx.names_match(entity, doc_name))
    if verdict.match is True:
        return []
    if verdict.match is None:
        return [_uncertain_finding("R12", entity, doc_name, verdict)]
    return [Finding("R12", FIX, STAGE_CONSISTENCY,
                    "Legal name on the incorporation certificate differs from the "
                    "submitted name",
                    expected=doc_name, actual=entity)]


# --- stage 4: the documents themselves ---------------------------------------

def r13_document_type(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    """Is each file the kind of document it was attached as?

    Checked against what the document calls itself, not its filename. A vendor
    who uploads a bank statement as a PAN card is told so, rather than having
    every later check on that slot fail for reasons nobody can read.
    """
    findings = []
    for key, keywords in DOCUMENT_TYPE_KEYWORDS.items():
        doc = _doc(extracted, key)
        stated = (doc or {}).get("document_type")
        if not doc or not stated:
            continue                    # nothing claimed — R14 covers readability
        text = str(stated).strip().casefold()
        if any(word in text for word in keywords):
            continue
        findings.append(Finding(
            "R13", FIX, STAGE_FORMAT,
            f"The file attached as '{DOCUMENT_LABELS[key]}' does not look like "
            f"that kind of document",
            expected=DOCUMENT_LABELS[key], actual=str(stated).strip()))
    return findings


def r14_document_is_readable(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    """Could the fields that make a document useful actually be read off it?

    Nothing readable at all is reported once, as an unreadable document — that
    is a scanning problem. A document that gave up some fields but not others is
    reported field by field, because that is a different conversation to have
    with the vendor.
    """
    findings = []
    for key, required in DOCUMENT_REQUIRED_FIELDS.items():
        doc = _doc(extracted, key)
        if not doc:
            continue                    # not attached — R02 owns that
        missing = [f for f in required if not str(doc.get(f) or "").strip()]
        if not missing:
            continue
        label = DOCUMENT_LABELS[key]
        if len(missing) == len(required):
            findings.append(Finding(
                "R14", FIX, STAGE_FORMAT,
                f"Nothing could be read from the {label} — it may be blurred, "
                f"cropped, or a scan of the wrong page"))
        else:
            findings += [
                Finding("R14", FIX, STAGE_FORMAT,
                        f"The {DOCUMENT_FIELD_LABELS.get(field, field)} could "
                        f"not be read from the {label}")
                for field in missing]
    return findings


def r17_registration_number(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    """A company CIN and an LLP LLPIN have known shapes. Nothing else does.

    Partnerships and proprietorships are registered in ways with no national
    format, so any non-empty number is accepted for them. Inventing a pattern
    there would reject honest vendors, which is worse than not checking.
    """
    doc = _doc(extracted, "incorporation_certificate")
    number = str((doc or {}).get("registration_number") or "").strip().upper()
    if not doc or not number:
        return []                       # absence is R14, not this rule
    entity_type = _val(submission, "entity_type")
    kind = REGISTRATION_FORMATS.get(entity_type)
    if kind is None:
        return []
    mask = validate_identifier_format(kind, number)
    if mask is None:
        return []
    return [Finding("R17", FIX, STAGE_FORMAT,
                    f"The number on the incorporation certificate is not a "
                    f"valid {kind}, which is the format required for "
                    f"{entity_type}",
                    expected=f"{kind} format", actual=number)]


# --- stage 5: the documents against the submission ---------------------------

def r15_pan_card(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    """The PAN card against the submitted PAN and the vendor name.

    A different PAN is a hard contradiction about who this vendor is, so it
    blocks. A different *name* goes through the same three-band matching as
    every other name here: a real mismatch is fixable, an unclear one is a
    question for a human.
    """
    doc = _doc(extracted, "pan_card")
    if not doc:
        return []
    findings = []

    doc_pan = str(doc.get("pan") or "").strip().upper()
    form_pan = _val(submission, "pan").upper()
    if doc_pan and form_pan and doc_pan != form_pan:
        findings.append(Finding(
            "R15", BLOCK, STAGE_CONSISTENCY,
            "The PAN on the card does not match the submitted PAN",
            expected=doc_pan, actual=form_pan))

    entity = _val(submission, "legal_entity_name")
    holder = str(doc.get("name") or "").strip()
    if entity and holder:
        verdict = _verdict(ctx.names_match(entity, holder))
        if verdict.match is None:
            findings.append(_uncertain_finding("R15", entity, holder, verdict))
        elif verdict.match is False:
            findings.append(Finding(
                "R15", FIX, STAGE_CONSISTENCY,
                "The name on the PAN card differs from the submitted legal "
                "entity name",
                expected=holder, actual=entity))
    return findings


def r16_gst_certificate(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    """The GST certificate against the submitted GSTIN, PAN and name.

    The GSTIN printed on the certificate carries the PAN inside it (characters
    3 to 12), so this also catches a certificate belonging to a different legal
    person than the PAN the vendor declared — without calling anything external.
    """
    doc = _doc(extracted, "gst_certificate")
    if not doc:
        return []
    findings = []

    doc_gstin = str(doc.get("gstin") or "").strip().upper()
    form_gstin = _val(submission, "gstin").upper()
    if doc_gstin and form_gstin and doc_gstin != form_gstin:
        findings.append(Finding(
            "R16", BLOCK, STAGE_CONSISTENCY,
            "The GSTIN on the certificate does not match the submitted GSTIN",
            expected=doc_gstin, actual=form_gstin))

    form_pan = _val(submission, "pan").upper()
    if GSTIN_RE.match(doc_gstin) and form_pan and doc_gstin[2:12] != form_pan:
        findings.append(Finding(
            "R16", BLOCK, STAGE_CONSISTENCY,
            "The GSTIN on the certificate belongs to a different PAN than the "
            "one submitted",
            expected=doc_gstin[2:12], actual=form_pan))

    entity = _val(submission, "legal_entity_name")
    doc_name = str(doc.get("legal_name") or "").strip()
    if entity and doc_name:
        verdict = _verdict(ctx.names_match(entity, doc_name))
        if verdict.match is None:
            findings.append(_uncertain_finding("R16", entity, doc_name, verdict))
        elif verdict.match is False:
            findings.append(Finding(
                "R16", FIX, STAGE_CONSISTENCY,
                "The legal name on the GST certificate differs from the "
                "submitted legal entity name",
                expected=doc_name, actual=entity))
    return findings


def r18_address_proof(submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    """The address proof against what the form actually collects.

    The form asks for a state, not a street, so the state is what can honestly
    be cross-checked. The full address is extracted and shown to the reviewer
    rather than matched against a field that does not exist.
    """
    doc = _doc(extracted, "address_proof")
    if not doc:
        return []
    findings = []

    entity = _val(submission, "legal_entity_name")
    addressee = str(doc.get("name") or "").strip()
    if entity and addressee:
        verdict = _verdict(ctx.names_match(entity, addressee))
        if verdict.match is None:
            findings.append(_uncertain_finding("R18", entity, addressee, verdict))
        elif verdict.match is False:
            findings.append(Finding(
                "R18", FIX, STAGE_CONSISTENCY,
                "The address proof is addressed to a different name than the "
                "submitted legal entity",
                expected=addressee, actual=entity))

    doc_state = str(doc.get("state") or "").strip()
    form_state = _val(submission, "registered_address_state")
    if doc_state and form_state and doc_state.casefold() != form_state.casefold():
        findings.append(Finding(
            "R18", FIX, STAGE_CONSISTENCY,
            "The address proof is for a different state than the registered "
            "address",
            expected=doc_state, actual=form_state))
    return findings


# --- the rule registry ------------------------------------------------------
#
# One entry per rule. The stage tuples below are DERIVED from this, so a rule
# cannot be registered and then quietly left out of the pipeline, or run without
# anyone being able to say what it is for.
#
# `applies` answers "did this rule have the inputs it needed?". It is the whole
# reason the run page can say *14 of 17 checks passed* honestly: without it a
# rule that passed and a rule that never ran are indistinguishable, and a PAN
# check correctly skipped for a US vendor would be counted as a pass.
#
# `evidence` is (source of `expected`, source of `actual`) for the rules that
# compare two values, and None for the rules that judge a single one.

def _applies_always(submission, extracted) -> bool:
    return True


def _has(extracted, key: str) -> bool:
    return _doc(extracted, key) is not None


def _any_document(extracted) -> bool:
    return any(_has(extracted, key) for key in DOCUMENT_LABELS)


RULES = (
    Rule("R01", "Required fields", CAT_COMPLETENESS, STAGE_COMPLETENESS,
         "A submission missing a field the engine reads cannot be judged on it. "
         "Which fields are required depends on the country and tax type, so an "
         "American vendor is never chased for an Indian PAN.",
         (FIX,), r01_required_fields, _applies_always),

    Rule("R02", "Required documents", CAT_COMPLETENESS, STAGE_COMPLETENESS,
         "Typed details are a claim; the documents are the evidence. Without "
         "them every consistency check below has nothing to compare against.",
         (FIX,), r02_required_documents,
         lambda s, e: e is not None),

    Rule("R03", "PAN format", CAT_IDENTIFIER, STAGE_FORMAT,
         "The PAN is the spine of Indian tax identity - R06, R07, R15 and R16 "
         "all read it. A malformed one silently disables four other checks.",
         (FIX,), r03_pan_format,
         lambda s, e: _is_india(s) and bool(_val(s, "pan"))),

    Rule("R04", "GSTIN format and checksum", CAT_IDENTIFIER, STAGE_FORMAT,
         "The 15th character is a mod-36 checksum, so a GSTIN that was invented "
         "rather than issued is caught without calling any registry.",
         (FIX,), r04_gstin_format_and_checksum,
         lambda s, e: _uses_gstin(s) and bool(_val(s, "gstin"))),

    Rule("R05", "IFSC format", CAT_IDENTIFIER, STAGE_FORMAT,
         "The IFSC routes the payment. A malformed one is money that does not "
         "arrive, discovered after approval rather than before it.",
         (FIX,), r05_ifsc_format,
         lambda s, e: _is_india(s) and bool(_val(s, "ifsc"))),

    Rule("R13", "Document type", CAT_DOCUMENT, STAGE_FORMAT,
         "Judged on what the paper calls itself, never its filename. A bank "
         "statement uploaded as a PAN card is told so once, instead of making "
         "every later check on that slot fail for reasons nobody can read.",
         (FIX,), r13_document_type,
         lambda s, e: any(_doc(e, k) and _doc(e, k).get("document_type")
                          for k in DOCUMENT_TYPE_KEYWORDS)),

    Rule("R14", "Document readability", CAT_DOCUMENT, STAGE_FORMAT,
         "A document nothing can be read from is not evidence. Reported as one "
         "scanning problem when it is wholly unreadable, field by field when it "
         "is partly readable - different conversations with the vendor.",
         (FIX,), r14_document_is_readable, lambda s, e: _any_document(e)),

    Rule("R17", "Registration number format", CAT_IDENTIFIER, STAGE_FORMAT,
         "A company CIN and an LLP LLPIN have known national shapes. Nothing "
         "else does, so any non-empty number is accepted for partnerships and "
         "proprietorships - inventing a format would reject honest vendors.",
         (FIX,), r17_registration_number,
         lambda s, e: (_has(e, "incorporation_certificate")
                       and bool(str((_doc(e, "incorporation_certificate") or {})
                                    .get("registration_number") or "").strip())
                       and _val(s, "entity_type") in REGISTRATION_FORMATS)),

    Rule("R06", "GSTIN encodes the submitted PAN", CAT_IDENTITY,
         STAGE_CONSISTENCY,
         "Characters 3-12 of a GSTIN are the holder's PAN. If they disagree "
         "with the PAN typed on the form, the two identities cannot both be "
         "this vendor - caught with no external lookup at all.",
         (BLOCK,), r06_gstin_pan_match,
         lambda s, e: bool(_valid_gstin(s) and _valid_pan(s)),
         ("GSTIN (submitted)", "PAN (submitted)")),

    Rule("R07", "PAN matches the declared entity type", CAT_IDENTITY,
         STAGE_CONSISTENCY,
         "The 4th character of a PAN encodes the legal form. A firm's PAN "
         "declared as a proprietorship is a contradiction about who is being "
         "paid, not a typo.",
         (BLOCK,), r07_pan_entity_type,
         lambda s, e: bool(_valid_pan(s) and _val(s, "entity_type")
                           and PAN_ENTITY_CODE.get(_valid_pan(s)[3])),
         ("PAN (submitted)", SRC_SUBMISSION)),

    Rule("R08", "GST state matches the registered address", CAT_ADDRESS,
         STAGE_CONSISTENCY,
         "The first two GSTIN characters are the state of registration. A "
         "disagreement is usually a stale address rather than fraud, so it asks "
         "for a correction rather than blocking.",
         (FIX,), r08_gstin_state,
         lambda s, e: bool(_valid_gstin(s) and _val(s, "registered_address_state")
                           and GST_STATE_CODES.get(_valid_gstin(s)[:2])),
         ("GSTIN (submitted)", SRC_SUBMISSION)),

    Rule("R09", "Bank account holder is the vendor", CAT_BANKING,
         STAGE_CONSISTENCY,
         "The single most expensive failure in vendor onboarding: a complete, "
         "plausible submission whose money goes to a personal account. Blocks.",
         (BLOCK, FIX), r09_bank_holder_name,
         lambda s, e: bool(_has(e, "bank_proof") and _val(s, "legal_entity_name")
                           and (_doc(e, "bank_proof") or {}).get("account_holder_name")),
         (DOCUMENT_LABELS["bank_proof"], SRC_SUBMISSION)),

    Rule("R10", "Bank details match the bank document", CAT_BANKING,
         STAGE_CONSISTENCY,
         "A mistyped account number pays a stranger. The cheque is the evidence "
         "and the form is the claim, so any disagreement blocks.",
         (BLOCK,), r10_bank_details_match,
         lambda s, e: bool(_has(e, "bank_proof") and (
             ((_doc(e, "bank_proof") or {}).get("account_number")
              and _val(s, "account_number"))
             or (_is_india(s) and (_doc(e, "bank_proof") or {}).get("ifsc")
                 and _val(s, "ifsc")))),
         (DOCUMENT_LABELS["bank_proof"], SRC_SUBMISSION)),

    Rule("R12", "Incorporation certificate names the vendor", CAT_IDENTITY,
         STAGE_CONSISTENCY,
         "Confirms the entity being onboarded is the entity that was "
         "incorporated. A trading name used on the form is a correction, not a "
         "contradiction, so it asks rather than blocks.",
         (FIX,), r12_incorporation_name,
         lambda s, e: bool(_has(e, "incorporation_certificate")
                           and _val(s, "legal_entity_name")
                           and (_doc(e, "incorporation_certificate")
                                or {}).get("legal_name")),
         (DOCUMENT_LABELS["incorporation_certificate"], SRC_SUBMISSION)),

    Rule("R15", "PAN card matches the submission", CAT_IDENTITY,
         STAGE_CONSISTENCY,
         "A different PAN on the card is a hard contradiction about who this "
         "vendor is and blocks. A different name goes through the same "
         "three-band matching as every other name, so an abbreviation is not "
         "treated as a lie.",
         (BLOCK, FIX), r15_pan_card, lambda s, e: _has(e, "pan_card"),
         (DOCUMENT_LABELS["pan_card"], SRC_SUBMISSION)),

    Rule("R16", "GST certificate matches the submission", CAT_IDENTITY,
         STAGE_CONSISTENCY,
         "The GSTIN printed on the certificate carries a PAN inside it, so this "
         "also catches a certificate belonging to a different legal person than "
         "the PAN declared - again with no external call.",
         (BLOCK, FIX), r16_gst_certificate,
         lambda s, e: _has(e, "gst_certificate"),
         (DOCUMENT_LABELS["gst_certificate"], SRC_SUBMISSION)),

    Rule("R18", "Address proof matches the submission", CAT_ADDRESS,
         STAGE_CONSISTENCY,
         "The form asks for a state, not a street, so the state is what can "
         "honestly be cross-checked. The full address is shown to the reviewer "
         "rather than matched against a field that does not exist.",
         (FIX,), r18_address_proof, lambda s, e: _has(e, "address_proof"),
         (DOCUMENT_LABELS["address_proof"], SRC_SUBMISSION)),
)

BY_ID = {rule.id: rule for rule in RULES}

# R11 is retired: it checked an insurance certificate that is no longer part of
# the document set. Its number is never reused, so audit records already written
# keep meaning what they said.
RETIRED_RULE_IDS = frozenset({"R11"})


def rules_for_stage(stage: str) -> tuple:
    return tuple(rule for rule in RULES if rule.stage == stage)


# The stage sets, derived - the pipeline still receives plain functions, so
# nothing downstream had to learn about the registry.
COMPLETENESS_RULES = tuple(r.check for r in rules_for_stage(STAGE_COMPLETENESS))
# The format stage runs after extraction, so it can judge the documents as
# artefacts - right kind, readable, well-formed identifiers - before the
# consistency stage compares what they say against what the vendor typed.
FORMAT_RULES = tuple(r.check for r in rules_for_stage(STAGE_FORMAT))
CONSISTENCY_RULES = tuple(r.check for r in rules_for_stage(STAGE_CONSISTENCY))
ALL_RULES = COMPLETENESS_RULES + FORMAT_RULES + CONSISTENCY_RULES


def apply(rules, submission: dict, extracted, ctx: RuleContext) -> list[Finding]:
    return [f for rule in rules for f in rule(submission, extracted, ctx)]


def evaluate(rule_set, submission: dict, extracted,
             ctx: RuleContext) -> list[RuleOutcome]:
    """Run each rule and record what *happened* to it, not only what it flagged.

    Same findings as `apply`, plus the passes and the skips - which is what a
    reviewer needs in order to see that a check ran and was satisfied, rather
    than inferring it from an absence.
    """
    outcomes = []
    for rule in rule_set:
        if not rule.applies(submission, extracted):
            outcomes.append(RuleOutcome(rule, SKIPPED))
            continue
        found = tuple(rule.check(submission, extracted, ctx))
        outcomes.append(RuleOutcome(rule, FAILED if found else PASSED, found))
    return outcomes


def summarize(outcomes, findings=None) -> dict:
    """Counts for the decision header.

    Accepts live `RuleOutcome`s or the dicts they were persisted as. Skipped
    checks are reported separately and never folded into the pass count - a
    check that did not run did not pass.

    `findings` overrides the severity counts when given, because a run can also
    carry findings no registered rule produced - a custom form field has no PS-2
    rule behind it, and the header must still count it.
    """
    rows = [o if isinstance(o, dict) else o.as_dict() for o in outcomes]
    if findings is None:
        findings = [f for row in rows for f in row["findings"]]
    findings = [f if isinstance(f, dict) else asdict(f) for f in findings]
    return {
        "total": len(rows),
        "evaluated": sum(r["state"] != SKIPPED for r in rows),
        "passed": sum(r["state"] == PASSED for r in rows),
        "failed": sum(r["state"] == FAILED for r in rows),
        "skipped": sum(r["state"] == SKIPPED for r in rows),
        "blocking": sum(f["severity"] == BLOCK for f in findings),
        "corrections": sum(f["severity"] == FIX for f in findings),
    }
