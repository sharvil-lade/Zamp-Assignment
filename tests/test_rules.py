"""Unit tests for the deterministic core.

No network, no fixtures, no mocking, no AI — that is the payoff of rules.py
being pure (docs/04-decision-engine.md).
"""

import io
import json
from datetime import date

import pytest

from conftest import TEST_PASSWORD, backend, repo

from engine import rules
from engine.rules import BLOCK, FIX, Finding, RuleContext, decide, gstin_checksum

TODAY = date(2026, 9, 5)
CTX = RuleContext(today=TODAY)


def gstin(first14: str) -> str:
    """Never hand-write a GSTIN — docs/06-demo-scenarios.md."""
    return first14 + gstin_checksum(first14)


GSTIN_KA = gstin("29ABCFS1234K1Z")      # Karnataka, PAN ABCFS1234K, 4th char F = Firm/LLP
# Same state, same 4th-character entity code, a *different* PAN inside. R16 uses
# it to prove a certificate can be well-formed and still belong to someone else.
GSTIN_OTHER_PAN = gstin("29ZZZFS9999Z1Z")

# A CIN is 21 characters and an LLPIN is AAA-9999. Both are spelled out here so a
# test that swaps one for the other reads as the mix-up R17 exists to catch.
CIN = "U74999KA2019PTC123456"
LLPIN = "AAB-1234"


def base_submission(**overrides) -> dict:
    """EC-1: the clean vendor. Every rule passes."""
    s = {
        "legal_entity_name": "Sundaram Industrial Supplies LLP",
        "entity_type": "LLP",
        "country_of_incorporation": "IN",
        "registered_address": "42 Industrial Layout, Koramangala, Bengaluru",
        "registered_address_state": "Karnataka",
        "contact_name": "Priya Raghavan",
        "contact_email": "priya@sundaramsupplies.in",
        "contact_phone": "+91 80 4123 7788",
        "tax_id_type": "GSTIN",
        "gstin": GSTIN_KA,
        "pan": "ABCFS1234K",
        "account_holder_name": "Sundaram Industrial Supplies LLP",
        "account_number": "50200071234567",
        "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank",
    }
    s.update(overrides)
    return s


def base_extracted(**overrides) -> dict:
    """The five documents, read cleanly. `document_type` is on every one of them
    because R13 judges what a document calls itself, not what slot it arrived in."""
    e = {
        "pan_card": {
            "document_type": "pan card", "pan": "ABCFS1234K",
            "name": "Sundaram Industrial Supplies LLP"},
        "gst_certificate": {
            "document_type": "gst registration certificate", "gstin": GSTIN_KA,
            "legal_name": "Sundaram Industrial Supplies LLP",
            "trade_name": "Sundaram Supplies"},
        "incorporation_certificate": {
            "document_type": "certificate of incorporation",
            "legal_name": "Sundaram Industrial Supplies LLP",
            "registration_number": LLPIN, "incorporation_date": "2019-04-11"},
        "bank_proof": {
            "document_type": "cancelled cheque",
            "account_holder_name": "Sundaram Industrial Supplies LLP",
            "account_number": "50200071234567", "ifsc": "HDFC0001234",
            "bank_name": "HDFC Bank"},
        "address_proof": {
            "document_type": "electricity bill",
            "name": "Sundaram Industrial Supplies LLP",
            "address": "12 Industrial Layout, Peenya, Bengaluru 560058",
            "state": "Karnataka"},
    }
    e.update(overrides)
    return e


def doc(key: str, **overrides) -> dict:
    """One document from the clean set with a field or two changed.

    Saves every mismatch test from restating four fields it does not care about,
    which is how a stray typo used to make a test pass for the wrong reason.
    """
    return dict(base_extracted()[key], **overrides)


def ids(findings) -> list[str]:
    return sorted(f.rule_id for f in findings)


def run_all(submission, extracted=None, ctx=CTX):
    return rules.apply(rules.ALL_RULES, submission, extracted, ctx)


# --- GSTIN checksum ---------------------------------------------------------

@pytest.mark.parametrize("valid", ["27AAPFU0939F1ZV", "24AAACC1206D1ZM"])
def test_gstin_checksum_reproduces_published_check_digits(valid):
    assert gstin_checksum(valid[:14]) == valid[14]


def test_gstin_checksum_detects_any_single_character_change():
    good = gstin("29ABCFS1234K1Z")
    for i in range(14):
        swapped = "B" if good[i] != "B" else "C"
        mutated = good[:i] + swapped + good[i + 1:]
        assert gstin_checksum(mutated[:14]) != good[14] or mutated[:14] == good[:14]


def test_gstin_checksum_rejects_wrong_length():
    with pytest.raises(ValueError):
        gstin_checksum("29ABCDE1234F1Z5")


# --- R01 required fields ----------------------------------------------------

def test_r01_passes_on_complete_submission():
    assert rules.r01_required_fields(base_submission(), None, CTX) == []


def test_r01_reports_each_blank_field_separately():
    found = rules.r01_required_fields(
        base_submission(contact_phone="", contact_name="   "), None, CTX)
    assert len(found) == 2
    assert all(f.severity == FIX and f.stage == "completeness" for f in found)
    assert "Required field 'contact_phone' is missing" in [f.message for f in found]


def test_r01_requires_pan_and_ifsc_only_for_india():
    us = base_submission(country_of_incorporation="US", tax_id_type="EIN",
                         gstin="", pan="", ifsc="")
    assert rules.r01_required_fields(us, None, CTX) == []


def test_r01_requires_the_registered_address():
    """It is on the form to be read beside the address proof; a blank one leaves
    the reviewer nothing to read against, so it is chased like any other."""
    found = rules.r01_required_fields(
        base_submission(registered_address=""), None, CTX)
    assert [f.message for f in found] == [
        "Required field 'registered_address' is missing"]
    assert found[0].severity == FIX and found[0].stage == "completeness"


def test_the_registered_address_is_required_of_every_country():
    """Unlike PAN and IFSC it has no India condition — everyone has an address."""
    us = base_submission(country_of_incorporation="US", tax_id_type="EIN",
                         gstin="", pan="", ifsc="", registered_address="")
    assert ids(rules.r01_required_fields(us, None, CTX)) == ["R01"]


# --- R02 required documents -------------------------------------------------

def test_r02_silent_before_extraction_has_run():
    assert rules.r02_required_documents(base_submission(), None, CTX) == []


def test_r02_passes_when_every_required_document_is_attached():
    assert rules.r02_required_documents(base_submission(), base_extracted(), CTX) == []


def test_r02_reports_the_missing_document_by_label():
    found = rules.r02_required_documents(
        base_submission(), base_extracted(incorporation_certificate=None), CTX)
    assert len(found) == 1
    assert found[0].message == (
        "Required document 'Certificate of incorporation or business "
        "registration' was not attached")
    assert found[0].severity == FIX


# --- R02: which documents a submission actually needs -------------------------

# R02 mirrors R01 exactly: a document is only chased when the submission claims
# the thing it proves. A US vendor is never asked for an Indian PAN card.
ALWAYS = ["incorporation_certificate", "bank_proof", "address_proof"]


@pytest.mark.parametrize("country,tax_id_type,expected", [
    ("IN", "GSTIN", ["pan_card", "gst_certificate"] + ALWAYS),
    ("IN", "EIN", ["pan_card"] + ALWAYS),
    ("US", "EIN", ALWAYS),
    ("US", "GSTIN", ["gst_certificate"] + ALWAYS),
])
def test_required_documents_follows_what_the_submission_claims(country, tax_id_type,
                                                               expected):
    assert rules.required_documents(base_submission(
        country_of_incorporation=country, tax_id_type=tax_id_type)) == expected


def test_r02_never_chases_a_us_vendor_for_a_pan_card():
    """The exact regression: a foreign vendor being told to send Indian tax papers."""
    us = base_submission(country_of_incorporation="US", tax_id_type="EIN",
                         gstin="", pan="", ifsc="")
    found = rules.r02_required_documents(
        us, {k: base_extracted()[k] for k in ALWAYS}, CTX)
    assert found == []


def test_r02_asks_a_gstin_vendor_for_the_gst_certificate():
    found = rules.r02_required_documents(
        base_submission(), base_extracted(gst_certificate=None), CTX)
    assert [f.rule_id for f in found] == ["R02"]
    assert "GST registration certificate" in found[0].message


# --- R03 PAN format ---------------------------------------------------------

def test_r03_passes_on_valid_pan():
    assert rules.r03_pan_format(base_submission(), None, CTX) == []


def test_r03_flags_malformed_pan():
    found = rules.r03_pan_format(base_submission(pan="ABCD1234F"), None, CTX)
    assert ids(found) == ["R03"]
    assert found[0].severity == FIX
    assert found[0].expected == "AAAAA9999A" and found[0].actual == "ABCD1234F"


# --- R04 GSTIN format and checksum ------------------------------------------

def test_r04_passes_on_checksum_valid_gstin():
    assert rules.r04_gstin_format_and_checksum(base_submission(), None, CTX) == []


def test_r04_flags_bad_checksum():
    wrong = GSTIN_KA[:14] + ("A" if GSTIN_KA[14] != "A" else "B")
    found = rules.r04_gstin_format_and_checksum(
        base_submission(gstin=wrong), None, CTX)
    assert ids(found) == ["R04"]
    assert "checksum is invalid" in found[0].message
    assert found[0].expected == GSTIN_KA


def test_r04_flags_bad_pattern():
    found = rules.r04_gstin_format_and_checksum(
        base_submission(gstin="29ABCDE1234F1X5"), None, CTX)
    assert ids(found) == ["R04"] and "format is invalid" in found[0].message


# --- R05 IFSC format --------------------------------------------------------

def test_r05_passes_on_valid_ifsc():
    assert rules.r05_ifsc_format(base_submission(), None, CTX) == []


def test_r05_flags_malformed_ifsc():
    found = rules.r05_ifsc_format(base_submission(ifsc="HDFC1001234"), None, CTX)
    assert ids(found) == ["R05"] and found[0].expected == "AAAA0999999"


# --- R06 GSTIN-embedded PAN (BLOCK) -----------------------------------------

def test_r06_passes_when_embedded_pan_matches():
    assert rules.r06_gstin_pan_match(base_submission(), None, CTX) == []


def test_r06_blocks_on_embedded_pan_mismatch():
    found = rules.r06_gstin_pan_match(base_submission(pan="ABCFS1234Z"), None, CTX)
    assert ids(found) == ["R06"]
    assert found[0].severity == BLOCK
    assert found[0].expected == "ABCFS1234K" and found[0].actual == "ABCFS1234Z"


def test_r06_skips_when_gstin_is_malformed():
    """R04 already reported it — a rule must never double-report."""
    assert rules.r06_gstin_pan_match(base_submission(gstin="nonsense"), None, CTX) == []


# --- R07 PAN entity-type code (BLOCK) ---------------------------------------

def test_r07_passes_when_pan_code_matches_declared_type():
    assert rules.r07_pan_entity_type(base_submission(), None, CTX) == []


def test_r07_blocks_firm_pan_declared_as_proprietorship():
    found = rules.r07_pan_entity_type(
        base_submission(entity_type="Proprietorship"), None, CTX)
    assert ids(found) == ["R07"] and found[0].severity == BLOCK
    assert found[0].message == (
        "PAN encodes entity type 'Firm/LLP' but the submission declares "
        "'Proprietorship'")


def test_r07_accepts_partnership_for_firm_pan():
    assert rules.r07_pan_entity_type(
        base_submission(entity_type="Partnership"), None, CTX) == []


def test_r07_skips_entity_forms_outside_mvp_scope():
    """PAN 4th char 'T' (Trust) is unmapped — skip rather than guess."""
    s = base_submission(pan="ABCTS1234K", gstin=gstin("29ABCTS1234K1Z"))
    assert rules.r07_pan_entity_type(s, None, CTX) == []


# --- R08 GSTIN state code ---------------------------------------------------

def test_r08_passes_when_state_matches():
    assert rules.r08_gstin_state(base_submission(), None, CTX) == []


def test_r08_flags_state_mismatch_as_fix_not_block():
    found = rules.r08_gstin_state(
        base_submission(registered_address_state="Maharashtra"), None, CTX)
    assert ids(found) == ["R08"]
    assert found[0].severity == FIX          # legitimate multi-state registration
    assert found[0].message == (
        "GSTIN is registered in Karnataka (state code 29) but the registered "
        "address is in Maharashtra")


# --- R09 bank beneficiary name (BLOCK) --------------------------------------

def test_r09_silent_before_extraction_has_run():
    assert rules.r09_bank_holder_name(base_submission(), None, CTX) == []


def test_r09_passes_when_holder_is_the_entity():
    assert rules.r09_bank_holder_name(
        base_submission(), base_extracted(), CTX) == []


def test_r09_blocks_when_account_is_in_a_personal_name():
    ext = base_extracted(bank_proof=doc("bank_proof",
                                        account_holder_name="S. Ramesh Kumar"))
    found = rules.r09_bank_holder_name(base_submission(), ext, CTX)
    assert ids(found) == ["R09"] and found[0].severity == BLOCK
    # `expected` always holds the evidence and `actual` what the vendor typed,
    # so the run page can label a side-by-side from the rule alone.
    assert found[0].expected == "S. Ramesh Kumar"
    assert found[0].actual == "Sundaram Industrial Supplies LLP"


def test_r09_tolerates_punctuation_only_differences():
    ext = base_extracted(bank_proof=doc(
        "bank_proof", account_holder_name="SUNDARAM INDUSTRIAL SUPPLIES, LLP."))
    assert rules.r09_bank_holder_name(base_submission(), ext, CTX) == []


def test_r09_uses_the_injected_comparator():
    """AI enters only as an injected callable — never as a hard dependency."""
    ctx = RuleContext(today=TODAY, names_match=lambda a, b: True)
    ext = base_extracted(bank_proof=doc(
        "bank_proof", account_holder_name="Completely Different Entity Ltd"))
    assert rules.r09_bank_holder_name(base_submission(), ext, ctx) == []


# --- R10 bank document vs form (BLOCK) --------------------------------------

def test_r10_passes_when_document_matches_form():
    assert rules.r10_bank_details_match(base_submission(), base_extracted(), CTX) == []


def test_r10_blocks_on_account_number_mismatch():
    ext = base_extracted(bank_proof=doc("bank_proof",
                                        account_number="50200079999999"))
    found = rules.r10_bank_details_match(base_submission(), ext, CTX)
    assert ids(found) == ["R10"] and found[0].severity == BLOCK
    assert found[0].expected == "50200079999999"


def test_r10_blocks_on_ifsc_mismatch_too():
    ext = base_extracted(bank_proof=doc("bank_proof", ifsc="ICIC0004321"))
    found = rules.r10_bank_details_match(base_submission(), ext, CTX)
    assert ids(found) == ["R10"] and "IFSC" in found[0].message


# --- R12 incorporation certificate name -------------------------------------

def test_r12_passes_when_names_agree():
    assert rules.r12_incorporation_name(base_submission(), base_extracted(), CTX) == []


def test_r12_flags_name_difference_as_fix_not_block():
    ext = base_extracted(incorporation_certificate=doc(
        "incorporation_certificate", legal_name="Meridian Holdings Private Limited"))
    found = rules.r12_incorporation_name(base_submission(), ext, CTX)
    assert ids(found) == ["R12"]
    assert found[0].severity == FIX          # formatting difference, not fraud


def test_r12_silent_when_the_document_is_absent():
    """R02 already reports the absence — skip semantics."""
    assert rules.r12_incorporation_name(
        base_submission(), base_extracted(incorporation_certificate=None), CTX) == []


# --- R13 document type ------------------------------------------------------

def test_r13_passes_when_every_document_is_what_it_was_uploaded_as():
    assert rules.r13_document_type(base_submission(), base_extracted(), CTX) == []


def test_r13_flags_a_bank_statement_uploaded_as_a_pan_card():
    """The vendor gets one legible finding instead of every later PAN check
    failing for reasons nobody can trace back to the wrong attachment."""
    ext = base_extracted(pan_card=doc("pan_card",
                                      document_type="savings account statement"))
    found = rules.r13_document_type(base_submission(), ext, CTX)
    assert ids(found) == ["R13"]
    assert found[0].severity == FIX and found[0].stage == "format"
    assert found[0].expected == "PAN card"
    assert found[0].actual == "savings account statement"


@pytest.mark.parametrize("key,stated", [
    ("pan_card", "Permanent Account Number Card"),
    ("gst_certificate", "Certificate of Registration - Goods and Services Tax"),
    ("incorporation_certificate", "LLP Agreement"),
    ("incorporation_certificate", "Udyam Registration Certificate"),
    ("bank_proof", "Bank Account Confirmation Letter"),
    ("address_proof", "Tenancy Agreement"),
    ("address_proof", "Aadhaar Card"),
])
def test_r13_accepts_the_many_shapes_a_real_document_takes(key, stated):
    """One slot, several legitimate papers. Matching is on a keyword, not on an
    exact title, or half of honest India would be rejected at the door."""
    ext = base_extracted(**{key: doc(key, document_type=stated)})
    assert rules.r13_document_type(base_submission(), ext, CTX) == []


def test_r13_is_silent_when_the_document_never_says_what_it_is():
    """Nothing claimed is a readability problem — R14's, not R13's."""
    ext = base_extracted(bank_proof=doc("bank_proof", document_type=None))
    assert rules.r13_document_type(base_submission(), ext, CTX) == []


def test_r13_skips_documents_that_were_not_attached():
    assert rules.r13_document_type(
        base_submission(), base_extracted(address_proof=None), CTX) == []


# --- R14 readability --------------------------------------------------------

def test_r14_passes_when_every_required_field_was_read():
    assert rules.r14_document_is_readable(base_submission(), base_extracted(),
                                          CTX) == []


def test_r14_reports_a_wholly_unreadable_document_once():
    """A blank scan is one problem — re-send the page — not two missing fields."""
    ext = base_extracted(pan_card=doc("pan_card", pan=None, name=None))
    found = rules.r14_document_is_readable(base_submission(), ext, CTX)
    assert len(found) == 1
    assert found[0].rule_id == "R14" and found[0].severity == FIX
    assert found[0].stage == "format"
    assert found[0].message == (
        "Nothing could be read from the PAN card — it may be blurred, cropped, "
        "or a scan of the wrong page")


def test_r14_reports_a_partly_readable_document_field_by_field():
    """Half a document is a different conversation: name the field that is gone."""
    ext = base_extracted(bank_proof=doc("bank_proof", account_number="  "))
    found = rules.r14_document_is_readable(base_submission(), ext, CTX)
    assert len(found) == 1
    assert found[0].message == (
        "The account number could not be read from the Cancelled cheque or "
        "bank letter")


def test_r14_ignores_fields_a_document_does_not_have_to_carry():
    """A cheque with no printed bank name is still a usable cheque."""
    ext = base_extracted(bank_proof=doc("bank_proof", bank_name=None, ifsc=None))
    assert rules.r14_document_is_readable(base_submission(), ext, CTX) == []


def test_r14_says_nothing_about_a_document_that_was_never_attached():
    """R02 owns absence; reporting it twice would double-charge the vendor."""
    assert rules.r14_document_is_readable(
        base_submission(), base_extracted(gst_certificate=None), CTX) == []


def test_r14_names_the_field_in_words_a_vendor_would_recognise():
    """`account_holder_name` is our vocabulary. The email is not for us."""
    ext = base_extracted(address_proof=doc("address_proof", address=None))
    found = rules.r14_document_is_readable(base_submission(), ext, CTX)
    assert found[0].message == "The address could not be read from the Address proof"
    assert "address_proof" not in found[0].message


# --- R17 registration number ------------------------------------------------

def test_r17_accepts_an_llpin_from_an_llp():
    assert rules.r17_registration_number(base_submission(), base_extracted(),
                                         CTX) == []


def test_r17_accepts_a_cin_from_a_private_limited():
    s = base_submission(entity_type="Private Limited", pan="ABCCS1234K",
                        gstin=gstin("29ABCCS1234K1Z"))
    ext = base_extracted(incorporation_certificate=doc(
        "incorporation_certificate", registration_number=CIN))
    assert rules.r17_registration_number(s, ext, CTX) == []


def test_r17_flags_an_llp_showing_a_cin():
    ext = base_extracted(incorporation_certificate=doc(
        "incorporation_certificate", registration_number=CIN))
    found = rules.r17_registration_number(base_submission(), ext, CTX)
    assert ids(found) == ["R17"]
    assert found[0].severity == FIX and found[0].stage == "format"
    assert found[0].message == (
        "The number on the incorporation certificate is not a valid LLPIN, "
        "which is the format required for LLP")
    assert found[0].expected == "LLPIN format" and found[0].actual == CIN


def test_r17_flags_a_private_limited_showing_an_llpin():
    # PAN 4th char C = Company, so R07 stays quiet and R17 is the only rule under
    # test here — getting that character wrong is how this test used to lie.
    s = base_submission(entity_type="Private Limited", pan="ABCCS1234K",
                        gstin=gstin("29ABCCS1234K1Z"))
    found = rules.r17_registration_number(s, base_extracted(), CTX)
    assert ids(found) == ["R17"] and "CIN" in found[0].message


@pytest.mark.parametrize("entity_type,number", [
    ("Partnership", "REG/2019/44821"),
    ("Proprietorship", "UDYAM-KR-03-0001234"),
    ("Foreign Corporation", "5901234"),
])
def test_r17_accepts_any_number_where_no_national_format_exists(entity_type, number):
    """Partnerships and proprietorships register in ways with no standard shape.
    Inventing a pattern for them would reject honest vendors."""
    ext = base_extracted(incorporation_certificate=doc(
        "incorporation_certificate", registration_number=number))
    assert rules.r17_registration_number(
        base_submission(entity_type=entity_type), ext, CTX) == []


def test_r17_is_silent_when_the_number_could_not_be_read():
    """Absence is R14's finding. Two rules on one blank field is one too many."""
    ext = base_extracted(incorporation_certificate=doc(
        "incorporation_certificate", registration_number=None))
    assert rules.r17_registration_number(base_submission(), ext, CTX) == []


def test_r17_lowercases_are_still_a_valid_number():
    ext = base_extracted(incorporation_certificate=doc(
        "incorporation_certificate", registration_number="aab-1234"))
    assert rules.r17_registration_number(base_submission(), ext, CTX) == []


# --- R15 the PAN card -------------------------------------------------------

def test_r15_passes_when_the_card_agrees_with_the_form():
    assert rules.r15_pan_card(base_submission(), base_extracted(), CTX) == []


def test_r15_blocks_a_different_pan_on_the_card():
    """A different PAN is a contradiction about who this vendor is, not a typo."""
    ext = base_extracted(pan_card=doc("pan_card", pan="ZZZFS9999Z"))
    found = rules.r15_pan_card(base_submission(), ext, CTX)
    assert ids(found) == ["R15"]
    assert found[0].severity == BLOCK and found[0].stage == "consistency"
    assert found[0].expected == "ZZZFS9999Z" and found[0].actual == "ABCFS1234K"


def test_r15_flags_a_name_difference_as_fix_not_block():
    ext = base_extracted(pan_card=doc("pan_card",
                                      name="Meridian Holdings Private Limited"))
    found = rules.r15_pan_card(base_submission(), ext, CTX)
    assert ids(found) == ["R15"] and found[0].severity == FIX
    assert found[0].expected == "Meridian Holdings Private Limited"   # the card
    assert found[0].actual == "Sundaram Industrial Supplies LLP"      # the form


def test_r15_tolerates_punctuation_only_name_differences():
    """The same three-band matching every other name here goes through."""
    ext = base_extracted(pan_card=doc(
        "pan_card", name="SUNDARAM INDUSTRIAL SUPPLIES, LLP."))
    assert rules.r15_pan_card(base_submission(), ext, CTX) == []


def test_r15_an_unsure_comparator_asks_a_human_rather_than_rejecting():
    ctx = RuleContext(today=TODAY, names_match=uncertain)
    ext = base_extracted(pan_card=doc("pan_card", name="Sundaram Inds. Supplies"))
    found = rules.r15_pan_card(base_submission(), ext, ctx)
    assert len(found) == 1
    assert found[0].severity == FIX and found[0].tag == "ai_uncertain"


def test_r15_reports_a_wrong_pan_and_a_wrong_name_separately():
    ext = base_extracted(pan_card=doc("pan_card", pan="ZZZFS9999Z",
                                      name="Meridian Holdings Private Limited"))
    found = rules.r15_pan_card(base_submission(), ext, CTX)
    assert [f.severity for f in found] == [BLOCK, FIX]


def test_r15_silent_when_the_card_was_not_attached():
    assert rules.r15_pan_card(
        base_submission(), base_extracted(pan_card=None), CTX) == []


# --- R16 the GST certificate -------------------------------------------------

def test_r16_passes_when_the_certificate_agrees_with_the_form():
    assert rules.r16_gst_certificate(base_submission(), base_extracted(), CTX) == []


def test_r16_blocks_a_different_gstin_on_the_certificate():
    ext = base_extracted(gst_certificate=doc("gst_certificate",
                                             gstin=GSTIN_OTHER_PAN))
    found = rules.r16_gst_certificate(base_submission(), ext, CTX)
    assert [f.severity for f in found] == [BLOCK, BLOCK]   # wrong GSTIN, wrong PAN
    assert "does not match the submitted GSTIN" in found[0].message


def test_r16_blocks_a_certificate_that_belongs_to_a_different_pan():
    """The PAN lives inside characters 3-12 of the GSTIN, so a certificate issued
    to another legal person is caught without calling anything external."""
    s = base_submission(gstin=GSTIN_OTHER_PAN)          # form and cert agree...
    ext = base_extracted(gst_certificate=doc("gst_certificate",
                                             gstin=GSTIN_OTHER_PAN))
    found = rules.r16_gst_certificate(s, ext, CTX)      # ...but the PAN does not
    assert ids(found) == ["R16"] and found[0].severity == BLOCK
    assert found[0].expected == "ZZZFS9999Z" and found[0].actual == "ABCFS1234K"


def test_r16_flags_a_legal_name_difference_as_fix_not_block():
    ext = base_extracted(gst_certificate=doc(
        "gst_certificate", legal_name="Meridian Holdings Private Limited"))
    found = rules.r16_gst_certificate(base_submission(), ext, CTX)
    assert ids(found) == ["R16"] and found[0].severity == FIX
    assert found[0].stage == "consistency"


def test_r16_never_compares_the_trade_name():
    """A trade name is legitimately different from a legal name; it is shown to
    the reviewer, never judged."""
    ext = base_extracted(gst_certificate=doc("gst_certificate",
                                             trade_name="Sundaram Tools"))
    assert rules.r16_gst_certificate(base_submission(), ext, CTX) == []


def test_r16_an_unsure_comparator_asks_a_human_rather_than_rejecting():
    ctx = RuleContext(today=TODAY, names_match=uncertain)
    ext = base_extracted(gst_certificate=doc("gst_certificate",
                                             legal_name="Sundaram Inds. Supplies"))
    found = rules.r16_gst_certificate(base_submission(), ext, ctx)
    assert len(found) == 1 and found[0].tag == "ai_uncertain"
    assert found[0].severity == FIX


def test_r16_silent_when_the_certificate_was_not_attached():
    assert rules.r16_gst_certificate(
        base_submission(), base_extracted(gst_certificate=None), CTX) == []


# --- R18 the address proof ---------------------------------------------------

def test_r18_passes_when_the_bill_matches_the_registered_address():
    assert rules.r18_address_proof(base_submission(), base_extracted(), CTX) == []


def test_r18_flags_a_bill_addressed_to_someone_else():
    ext = base_extracted(address_proof=doc("address_proof", name="S. Ramesh Kumar"))
    found = rules.r18_address_proof(base_submission(), ext, CTX)
    assert ids(found) == ["R18"] and found[0].severity == FIX
    assert found[0].stage == "consistency"
    assert found[0].expected == "S. Ramesh Kumar"


def test_r18_flags_a_bill_for_a_different_state():
    ext = base_extracted(address_proof=doc("address_proof", state="Maharashtra"))
    found = rules.r18_address_proof(base_submission(), ext, CTX)
    assert ids(found) == ["R18"] and found[0].severity == FIX
    assert found[0].expected == "Maharashtra" and found[0].actual == "Karnataka"


def test_r18_compares_the_state_case_insensitively():
    ext = base_extracted(address_proof=doc("address_proof", state="karnataka"))
    assert rules.r18_address_proof(base_submission(), ext, CTX) == []


def test_r18_never_compares_the_street_address():
    """The form asks for a state, not a street. The full address is extracted for
    a reviewer to read, never matched against a field that does not exist."""
    ext = base_extracted(address_proof=doc(
        "address_proof", address="Plot 9, MIDC Andheri, Mumbai 400093"))
    assert rules.r18_address_proof(base_submission(), ext, CTX) == []


def test_r18_reports_both_a_wrong_addressee_and_a_wrong_state():
    ext = base_extracted(address_proof=doc("address_proof", name="S. Ramesh Kumar",
                                           state="Maharashtra"))
    found = rules.r18_address_proof(base_submission(), ext, CTX)
    assert len(found) == 2 and all(f.severity == FIX for f in found)


def test_r18_silent_when_the_proof_was_not_attached():
    assert rules.r18_address_proof(
        base_submission(), base_extracted(address_proof=None), CTX) == []


# --- the rule sets ----------------------------------------------------------

def test_every_rule_is_wired_into_exactly_one_stage():
    """A rule not in a stage set never runs, and one in two sets reports twice.
    Both are silent failures — nothing crashes, the engine just stops checking."""
    sets = (rules.COMPLETENESS_RULES, rules.FORMAT_RULES, rules.CONSISTENCY_RULES)
    assert len(rules.ALL_RULES) == len(set(rules.ALL_RULES))
    assert set(rules.ALL_RULES) == set().union(*sets)
    assert sum(len(s) for s in sets) == len(rules.ALL_RULES)


@pytest.mark.parametrize("rule_set,stage", [
    ("COMPLETENESS_RULES", "completeness"),
    ("FORMAT_RULES", "format"),
    ("CONSISTENCY_RULES", "consistency"),
])
def test_a_stages_rules_only_ever_stamp_that_stage(rule_set, stage):
    """The run page groups findings by stage. A rule filed under one stage that
    stamps another scatters its findings somewhere nobody is looking."""
    broken = base_submission(pan="nonsense", gstin="nonsense", ifsc="nonsense",
                             contact_phone="")
    ext = base_extracted(
        pan_card=doc("pan_card", document_type="salary slip", pan="ZZZFS9999Z"),
        gst_certificate=doc("gst_certificate", legal_name=None),
        incorporation_certificate=doc("incorporation_certificate",
                                      registration_number="whatever"),
        bank_proof=doc("bank_proof", account_holder_name="S. Ramesh Kumar",
                       account_number="1"),
        address_proof=doc("address_proof", name="Someone Else", state="Goa"))
    found = rules.apply(getattr(rules, rule_set), broken, ext, CTX)
    assert found, "the broken submission should trip this stage"
    assert {f.stage for f in found} == {stage}


# --- decide(): precedence ---------------------------------------------------

def f(sev):
    return Finding("Rxx", sev, "consistency", "m")


def test_decide_approves_on_no_findings():
    assert decide([]) == "APPROVED"


def test_decide_pends_on_fix_only():
    assert decide([f(FIX), f(FIX)]) == "PENDING"


def test_decide_rejects_on_any_block():
    assert decide([f(BLOCK)]) == "REJECTED"


def test_decide_block_outranks_fix():
    """One fraud signal plus four missing fields is Rejected, not Pending."""
    assert decide([f(FIX), f(FIX), f(BLOCK), f(FIX), f(FIX)]) == "REJECTED"


def test_decide_sees_only_findings():
    import inspect
    assert list(inspect.signature(decide).parameters) == ["findings"]


# --- the four demo scenarios (docs/06) --------------------------------------

def test_ec1_happy_path_is_approved():
    found = run_all(base_submission(), base_extracted())
    assert found == []
    assert decide(found) == "APPROVED"


def test_ec2_missing_and_unreadable_documents_are_pending():
    """What the expiry case used to cover: a document-level FIX that pends a run
    and gives the vendor something concrete to re-send."""
    submission = base_submission(contact_phone="")
    extracted = base_extracted(
        incorporation_certificate=None,
        address_proof=doc("address_proof", name=None, address=None, state=None))
    found = run_all(submission, extracted)
    assert ids(found) == ["R01", "R02", "R14"]
    assert "R12" not in ids(found)            # its document is absent; R02 owns that
    assert "R17" not in ids(found)            # likewise the registration number
    assert "R18" not in ids(found)            # nothing was read, so nothing to compare
    assert all(f.severity == FIX for f in found)
    assert decide(found) == "PENDING"


def test_ec3_bank_beneficiary_mismatch_is_rejected():
    submission = base_submission(account_holder_name="S. Ramesh Kumar")
    extracted = base_extracted(bank_proof=doc("bank_proof",
                                              account_holder_name="S. Ramesh Kumar"))
    found = run_all(submission, extracted)
    assert ids(found) == ["R09"]              # R10 does not fire: details agree
    assert decide(found) == "REJECTED"


def test_ec3_resolves_without_the_ai_comparator():
    """The fraud case is deterministic — the score is far below the band."""
    exploding = RuleContext(
        today=TODAY,
        names_match=lambda a, b: (_ for _ in ()).throw(
            AssertionError("AI must not be consulted here")))
    submission = base_submission(account_holder_name="S. Ramesh Kumar")
    assert rules.default_names_match(
        submission["legal_entity_name"], "S. Ramesh Kumar").match is False
    # and with a comparator that refuses to answer, R09 still would have to ask,
    # proving the call site exists but the deterministic path decides the case:
    with pytest.raises(AssertionError):
        rules.r09_bank_holder_name(
            submission,
            base_extracted(bank_proof=doc("bank_proof",
                                          account_holder_name="S. Ramesh Kumar")),
            exploding)


def test_ec4_cross_field_contradiction_is_rejected():
    """The form contradicts itself, and now the documents contradict the form too:
    the PAN card and the GST certificate both carry the PAN that was not typed."""
    submission = base_submission(
        pan="ABCFS1234Z", entity_type="Proprietorship",
        registered_address_state="Maharashtra")
    found = run_all(submission, base_extracted())
    assert ids(found) == ["R06", "R07", "R08", "R15", "R16", "R18"]
    by_id = {f.rule_id: f for f in found}
    assert by_id["R06"].severity == BLOCK
    assert by_id["R07"].severity == BLOCK
    assert by_id["R08"].severity == FIX
    assert by_id["R15"].severity == BLOCK    # the card says ABCFS1234K
    assert by_id["R16"].severity == BLOCK    # so does the GSTIN on the certificate
    assert by_id["R18"].severity == FIX      # the bill is still for Karnataka
    assert decide(found) == "REJECTED"


def test_ec4_needs_no_document_to_reach_the_same_verdict():
    """The cross-field contradiction is settled by the form alone. The documents
    corroborate it; they are not what makes it a rejection."""
    submission = base_submission(
        pan="ABCFS1234Z", entity_type="Proprietorship",
        registered_address_state="Maharashtra")
    found = run_all(submission, None)
    assert ids(found) == ["R06", "R07", "R08"]
    assert decide(found) == "REJECTED"


# --- purity guard -----------------------------------------------------------

def test_rules_module_imports_nothing_outside_the_stdlib():
    """The structural guarantee that AI cannot reach the decision."""
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(rules.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"re", "dataclasses", "datetime", "difflib", "typing"}
    assert "anthropic" not in imported and "sqlite3" not in imported


def test_no_rule_reads_the_wall_clock():
    """What the expiry rule's injected-`today` test used to protect: a rule that
    called date.today() would make the demo drift and the suite flake by the
    calendar. `today` stays on RuleContext for the next dated rule to use."""
    src = backend("engine/rules.py").read_text(encoding="utf-8")
    assert "date.today()" not in src
    assert "today" in inspect_params(RuleContext.__init__)


# --- pipeline integration (no AI, no PDFs) ----------------------------------

def execute(db, submission):
    from engine import pipeline
    run_id = db.create_run(submission.get("legal_entity_name"), submission)
    status = pipeline.run(run_id, today=TODAY,
                          review_fn=fake_reviewer())
    return run_id, status


def test_pipeline_approves_a_clean_submission(db):
    run_id, status = execute(db, base_submission())
    assert status == "APPROVED"
    assert db.get_findings(run_id) == []
    assert db.get_run(run_id)["status"] == "APPROVED"


def test_pipeline_pends_on_a_blank_required_field(db):
    run_id, status = execute(db, base_submission(contact_phone=""))
    assert status == "PENDING"
    found = db.get_findings(run_id)
    assert [f["rule_id"] for f in found] == ["R01"]
    assert found[0]["message"] == "Required field 'contact_phone' is missing"


def test_pipeline_rejects_a_blocking_contradiction(db):
    run_id, status = execute(db, base_submission(
        pan="ABCFS1234Z", entity_type="Proprietorship",
        registered_address_state="Maharashtra"))
    assert status == "REJECTED"
    assert sorted(f["rule_id"] for f in db.get_findings(run_id)) == ["R06", "R07", "R08"]


def test_pipeline_persists_findings_and_events(db):
    run_id, _ = execute(db, base_submission(contact_phone=""))
    assert len(db.get_findings(run_id)) == 1
    kinds = [e["event_type"] for e in db.get_events(run_id)]
    # intake, completeness, format, consistency, review, communicate
    # (no documents -> no extraction stage)
    assert kinds.count("stage_started") == 6
    assert kinds.count("stage_completed") == 6
    assert "decision" in kinds
    actors = {e["actor"] for e in db.get_events(run_id)}
    assert actors <= {"system", "ai_employee"}


def test_pipeline_records_the_decision_event_detail(db):
    run_id, _ = execute(db, base_submission(
        pan="ABCFS1234Z", entity_type="Proprietorship"))
    import json
    detail = next(json.loads(e["detail_json"]) for e in db.get_events(run_id)
                  if e["event_type"] == "decision")
    assert detail["status"] == "REJECTED"
    assert detail["block_count"] == 2 and detail["fix_count"] == 0
    assert detail["rule_ids"] == ["R06", "R07"]


def test_pipeline_runs_every_stage_in_order(db):
    """Guards against argument-order and wiring bugs the unit tests cannot see."""
    run_id, _ = execute(db, base_submission())
    order = [e["stage"] for e in db.get_events(run_id)
             if e["event_type"] == "stage_completed"]
    assert order == ["intake", "completeness", "format", "consistency",
                     "review", "communicate"]


def test_error_is_distinct_from_rejected(db, monkeypatch):
    """A crashing stage must never fall through to a decision."""
    from engine import pipeline
    monkeypatch.setattr(rules, "evaluate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    run_id = db.create_run("Crashy Corp", base_submission())
    assert pipeline.run(run_id, today=TODAY) == "ERROR"

    run = db.get_run(run_id)
    assert run["status"] == "ERROR"
    assert run["status"] != "REJECTED"
    failed = [e for e in db.get_events(run_id) if e["event_type"] == "stage_failed"]
    assert len(failed) == 1 and "boom" in failed[0]["detail_json"]
    assert not any(e["event_type"] == "decision" for e in db.get_events(run_id))


def test_unknown_run_raises(db):
    from engine import pipeline
    with pytest.raises(KeyError):
        pipeline.run("VS-9999", today=TODAY)


# --- stage 3 extraction wiring (offline: the extractor is injected) ---------

# What the injected extractor returns for each slot. Same five shapes the real
# schemas declare, `document_type` included — a fake that dropped it would make
# every R13 test in this file pass against a stub that cannot exist in production.
FAKE_DOCS = dict(base_extracted(),
                 bank_proof=doc("bank_proof", bank_name="HDFC Bank Limited"))


def fake_extractor(calls=None, overrides=None):
    def _fn(path, doc_type):
        if calls is not None:
            calls.append((path.name, doc_type))
        data = dict((overrides or {}).get(doc_type, FAKE_DOCS[doc_type]))
        return data, {"purpose": f"extract:{doc_type}", "model": "fake-model",
                      "input_summary": path.name,
                      "usage": {"input_tokens": 100, "output_tokens": 20}}
    return _fn


def attach(db, run_id, *doc_types):
    """Store stub documents for a run and mark the document channel as used."""
    db.add_event(run_id, "intake", "documents_expected")
    for t in doc_types:
        db.save_document(run_id, t, ".pdf", b"%PDF-1.4 stub")


def execute_with_docs(db, submission, doc_types, overrides=None, calls=None):
    from engine import pipeline
    run_id = db.create_run(submission.get("legal_entity_name"), submission)
    attach(db, run_id, *doc_types)
    status = pipeline.run(run_id, today=TODAY,
                          review_fn=fake_reviewer(),
                          extract_fn=fake_extractor(calls, overrides))
    return run_id, status


def test_extraction_persists_results_to_the_run(db):
    run_id, status = execute_with_docs(db, base_submission(), FAKE_DOCS.keys())
    extracted = db.get_run(run_id)["extracted"]
    assert set(extracted) == set(FAKE_DOCS)
    assert extracted["bank_proof"]["ifsc"] == "HDFC0001234"
    assert status == "APPROVED"


def test_extraction_writes_one_ai_call_event_per_document(db):
    run_id, _ = execute_with_docs(db, base_submission(), FAKE_DOCS.keys())
    ai = [e for e in db.get_events(run_id) if e["event_type"] == "ai_call"
          and e["stage"] == "extraction"]
    assert len(ai) == 5
    for e in ai:
        detail = json.loads(e["detail_json"])
        assert detail["purpose"].startswith("extract:")
        assert detail["model"] and detail["input_summary"]
        assert detail["usage"]["input_tokens"] > 0
        assert e["stage"] == "extraction"


def test_extraction_is_skipped_when_no_documents_were_submitted(db):
    """JSON-only submissions carry no documents, so R02 must stay silent."""
    run_id, status = execute(db, base_submission())
    assert db.get_run(run_id)["extracted"] is None
    assert not any(e["event_type"] == "ai_call" and e["stage"] == "extraction"
                   for e in db.get_events(run_id))
    assert status == "APPROVED"


def test_missing_attachment_is_reported_without_calling_the_model(db):
    attached = [d for d in FAKE_DOCS if d != "incorporation_certificate"]
    calls = []
    run_id, status = execute_with_docs(db, base_submission(), attached, calls=calls)
    assert [c[1] for c in calls] == attached      # the absent one costs no tokens
    assert db.get_run(run_id)["extracted"]["incorporation_certificate"] is None
    found = [f["rule_id"] for f in db.get_findings(run_id)]
    assert found == ["R02"]
    assert status == "PENDING"


def test_a_null_extracted_field_never_becomes_a_finding(db):
    """An honest null is missing data, not a contradiction.

    Only for fields a document does not have to carry — R14 deliberately reports
    a *required* field that could not be read, and that is a different thing.
    """
    partial = dict(FAKE_DOCS["bank_proof"], ifsc=None, bank_name=None)
    run_id, status = execute_with_docs(
        db, base_submission(), FAKE_DOCS.keys(), overrides={"bank_proof": partial})
    assert db.get_findings(run_id) == []
    assert status == "APPROVED"


def test_extraction_failure_yields_error_not_a_status(db):
    from engine import pipeline
    run_id = db.create_run("Boom Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)

    def exploding(path, doc_type):
        raise RuntimeError("api timeout")

    assert pipeline.run(run_id, today=TODAY, extract_fn=exploding,
                        review_fn=fake_reviewer()) == "ERROR"
    run = db.get_run(run_id)
    assert run["status"] == "ERROR" and run["extracted"] is None
    failed = [e for e in db.get_events(run_id) if e["event_type"] == "stage_failed"]
    assert len(failed) == 1 and failed[0]["stage"] == "extraction"
    assert not any(e["event_type"] == "decision" for e in db.get_events(run_id))


def test_stage_order_matches_the_documented_pipeline(db):
    run_id, _ = execute_with_docs(db, base_submission(), FAKE_DOCS.keys())
    order = [e["stage"] for e in db.get_events(run_id)
             if e["event_type"] == "stage_completed"]
    assert order == ["intake", "completeness", "extraction", "format",
                     "consistency", "review", "communicate"]


def test_extracted_values_reach_the_consistency_rules(db):
    """EC-3 through the real pipeline: the cheque is in a personal name."""
    mismatch = dict(FAKE_DOCS["bank_proof"], account_holder_name="S. Ramesh Kumar")
    run_id, status = execute_with_docs(
        db, base_submission(account_holder_name="S. Ramesh Kumar"),
        FAKE_DOCS.keys(), overrides={"bank_proof": mismatch})
    found = db.get_findings(run_id)
    assert [f["rule_id"] for f in found] == ["R09"]
    assert found[0]["severity"] == "BLOCK"
    assert status == "REJECTED"


def test_the_format_stage_can_see_the_extracted_documents(db):
    """Format runs *after* extraction now, which is the whole reason R13/R14/R17
    can exist. If it ran first it would be handed None and go silent."""
    wrong_slot = dict(FAKE_DOCS["pan_card"], document_type="savings account statement")
    run_id, status = execute_with_docs(
        db, base_submission(), FAKE_DOCS.keys(),
        overrides={"pan_card": wrong_slot})
    found = db.get_findings(run_id)
    assert [f["rule_id"] for f in found] == ["R13"]
    assert found[0]["stage"] == "format"
    assert status == "PENDING"


def test_an_unreadable_document_pends_the_run_rather_than_approving_it(db):
    """A scan nothing could be read from is not a clean submission."""
    blank = dict(FAKE_DOCS["address_proof"], name=None, address=None, state=None)
    run_id, status = execute_with_docs(
        db, base_submission(), FAKE_DOCS.keys(), overrides={"address_proof": blank})
    found = db.get_findings(run_id)
    assert [f["rule_id"] for f in found] == ["R14"]
    assert found[0]["severity"] == "FIX"
    assert status == "PENDING"


def test_a_foreign_vendor_is_approved_on_three_documents(db):
    """The conditional requirement, end to end: no PAN card and no GST
    certificate are asked for, so a US vendor who sent the other three is done."""
    us = base_submission(country_of_incorporation="US", tax_id_type="EIN",
                         gstin="", pan="", ifsc="",
                         entity_type="Foreign Corporation",
                         registered_address_state="Delaware")
    calls = []
    run_id, status = execute_with_docs(
        db, us, ["incorporation_certificate", "bank_proof", "address_proof"],
        overrides={"address_proof": doc(
            "address_proof", state="Delaware",
            address="1209 Orange Street, Wilmington DE 19801")},
        calls=calls)
    assert db.get_findings(run_id) == []
    assert status == "APPROVED"
    assert "pan_card" not in [c[1] for c in calls]     # never even read for
    assert "gst_certificate" not in [c[1] for c in calls]


def test_extraction_model_cannot_set_a_status(db):
    """Whatever the extractor returns, the status comes from decide()."""
    from engine import pipeline
    run_id = db.create_run("Sneaky Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)

    def liar(path, doc_type):
        data = dict(FAKE_DOCS[doc_type])
        data["status"] = "APPROVED"          # ignored: not in the schema's fields
        return data, {"purpose": "x", "model": "m", "input_summary": "i",
                      "usage": {"input_tokens": 1, "output_tokens": 1}}
    assert pipeline.run(run_id, today=TODAY, extract_fn=liar) == "APPROVED"
    from engine import rules as r
    assert list(inspect_params(r.decide)) == ["findings"]


def inspect_params(fn):
    import inspect
    return inspect.signature(fn).parameters


# --- extract.py schema contract (no network) --------------------------------

def test_extract_schemas_match_the_documented_shapes():
    from ai import extract
    assert set(extract.DOC_TYPES) == set(extract.SCHEMAS) == set(extract.PROMPTS)
    expected = {
        "pan_card": {"document_type", "pan", "name"},
        "gst_certificate": {"document_type", "gstin", "legal_name", "trade_name"},
        "incorporation_certificate": {"document_type", "legal_name",
                                      "registration_number", "incorporation_date"},
        "bank_proof": {"document_type", "account_holder_name", "account_number",
                       "ifsc", "bank_name"},
        "address_proof": {"document_type", "name", "address", "state"},
    }
    assert set(expected) == set(extract.DOC_TYPES)
    for doc_type, fields in expected.items():
        schema = extract.SCHEMAS[doc_type]
        assert set(schema["properties"]) == fields
        assert set(schema["required"]) == fields
        assert schema["additionalProperties"] is False
        for spec in schema["properties"].values():
            assert spec["type"] == ["string", "null"]   # every field nullable


def test_every_document_is_asked_what_it_is():
    """R13 has nothing to judge unless every schema carries `document_type`, and
    the prompt must ask for it without hinting at the answer we hope for."""
    from ai import extract
    for doc_type in extract.DOC_TYPES:
        assert "document_type" in extract.SCHEMAS[doc_type]["properties"], doc_type
        assert "even if that is not what was expected" in extract.PROMPTS[doc_type]


def test_every_rule_that_reads_a_document_has_a_schema_to_read_it_from():
    """The two lists are edited in different files; a document dropped from one
    and not the other is exactly how a rule goes quiet without anyone noticing."""
    from ai import extract
    assert set(rules.DOCUMENT_LABELS) == set(extract.DOC_TYPES)
    assert set(rules.DOCUMENT_REQUIRED_FIELDS) == set(extract.DOC_TYPES)
    assert set(rules.DOCUMENT_TYPE_KEYWORDS) == set(extract.DOC_TYPES)
    assert {key for key, _ in rules.DOCUMENT_COMPARISONS} == set(extract.DOC_TYPES)
    for key, fields in rules.DOCUMENT_COMPARISONS:
        for _, _, doc_field, _ in fields:
            assert doc_field in extract.SCHEMAS[key]["properties"], (key, doc_field)


def test_a_document_only_row_is_shown_but_never_judged():
    """A registration number has no form field to differ from, so the reviewer
    sees it and no rule can flag it."""
    groups = {g["key"]: g for g in rules.compare_documents(
        base_submission(), base_extracted())}
    registration = next(r for r in groups["incorporation_certificate"]["rows"]
                        if r["label"] == "Registration number")
    assert registration["form"] is None and registration["doc"]
    assert registration["mismatch"] is False
    assert all(not r["mismatch"] for g in groups.values() for r in g["rows"])


def test_the_registered_address_is_shown_beside_the_proof_but_never_matched():
    """Both values are on screen so a human can read them together, but the row
    owns no rule: a street address is written a dozen legitimate ways and a fuzzy
    comparison would invent findings out of formatting."""
    submitted = "42 Industrial Layout, Koramangala, Bengaluru"
    on_proof = "No. 42, Industrial Layout, Koramangala, Bengaluru - 560095"
    groups = {g["key"]: g for g in rules.compare_documents(
        base_submission(registered_address=submitted),
        base_extracted(address_proof=doc("address_proof", address=on_proof)))}

    address = next(r for r in groups["address_proof"]["rows"]
                   if r["label"] == "Address")
    assert address["form"] == submitted and address["doc"] == on_proof
    assert address["rule"] is None                 # nothing owns the comparison
    assert address["mismatch"] is False            # so nothing highlights it


def test_the_address_proof_is_still_judged_on_addressee_and_state():
    """Not matching the street address does not make the document unchecked —
    R18 still owns the two rows either side of it."""
    rows = {r["label"]: r for g in rules.compare_documents(
        base_submission(), base_extracted()) if g["key"] == "address_proof"
        for r in g["rows"]}
    assert rows["Addressed to — vs legal entity"]["rule"] == "R18"
    assert rows["State"]["rule"] == "R18"


def test_a_real_mismatch_is_highlighted_only_where_a_rule_compares_the_pair():
    ext = base_extracted(pan_card=doc("pan_card", pan="ZZZFS9999Z"),
                         gst_certificate=doc("gst_certificate",
                                             trade_name="Something Else Entirely"))
    groups = {g["key"]: g for g in rules.compare_documents(base_submission(), ext)}
    flagged = [r["label"] for g in groups.values() for r in g["rows"] if r["mismatch"]]
    assert flagged == ["PAN"]                 # the trade name has no rule, so no flag


def test_compare_documents_reports_which_documents_are_missing():
    groups = {g["key"]: g for g in rules.compare_documents(
        base_submission(), base_extracted(gst_certificate=None))}
    assert groups["gst_certificate"]["attached"] is False
    assert groups["pan_card"]["attached"] is True
    assert all(r["doc"] is None for r in groups["gst_certificate"]["rows"])


def test_every_prompt_forbids_inventing_values():
    from ai import extract
    for prompt in extract.PROMPTS.values():
        assert "null" in prompt
        assert "Never infer, correct, complete, normalise or guess" in prompt


def test_unsupported_file_type_is_rejected():
    from ai import extract
    with pytest.raises(ValueError):
        extract._content_block(pathlib_Path("x.docx"))


def pathlib_Path(name):
    import pathlib
    return pathlib.Path(name)


# --- matching.py: normalization and the three bands -------------------------

def boom_ask(a, b, on_ai_call=None):
    raise AssertionError("the model must not be consulted for this pair")


def test_normalize_expands_legal_form_abbreviations():
    from ai import matching
    assert matching.normalize("Acme Tech Pvt. Ltd.") == "ACME TECHNOLOGIES PRIVATE LIMITED"
    assert matching.normalize("acme  technologies,  private limited") == \
        "ACME TECHNOLOGIES PRIVATE LIMITED"


def test_similarity_is_symmetric_and_bounded():
    from ai import matching
    a, b = "Sundaram Industrial Supplies LLP", "Sundaram Inds. Supplies LLP"
    assert matching.similarity(a, b) == matching.similarity(b, a)
    assert 0.0 <= matching.similarity(a, b) <= 1.0
    assert matching.similarity(a, a) == 1.0
    assert matching.similarity("", "anything") == 0.0


def test_different_legal_form_is_not_the_same_entity():
    """Suffixes are expanded, never stripped: an LLP is not a Private Limited."""
    from ai import matching
    v = matching.names_match("Meridian Logistics LLP",
                             "Meridian Logistics Private Limited", ask=boom_ask)
    assert v.match is False


@pytest.mark.parametrize("a,b", [
    ("Sundaram Industrial Supplies LLP", "Sundaram Industrial Supplies LLP"),
    ("Sundaram Industrial Supplies LLP", "SUNDARAM INDUSTRIAL SUPPLIES, LLP."),
    ("Acme Technologies Pvt Ltd", "Acme Tech Private Limited"),
])
def test_obvious_matches_never_reach_the_model(a, b):
    from ai import matching
    v = matching.names_match(a, b, ask=boom_ask)
    assert v.match is True and v.score >= matching.MATCH_THRESHOLD


@pytest.mark.parametrize("a,b", [
    ("Sundaram Industrial Supplies LLP", "S. Ramesh Kumar"),
    ("Acme Technologies Pvt Ltd", "Acme Holdings LLC"),
])
def test_obvious_mismatches_never_reach_the_model(a, b):
    from ai import matching
    v = matching.names_match(a, b, ask=boom_ask)
    assert v.match is False and v.score <= matching.MISMATCH_THRESHOLD


def test_only_the_ambiguous_band_escalates():
    from ai import matching
    asked = []

    def ask(a, b, on_ai_call=None):
        asked.append((a, b))
        return rules.NameVerdict(True, reason="stub")

    v = matching.names_match("Global Marine Services LLP",
                             "Global Marine Supplies LLP", ask=ask)
    assert len(asked) == 1
    assert matching.MISMATCH_THRESHOLD < v.score < matching.MATCH_THRESHOLD
    assert v.match is True


def test_low_model_confidence_becomes_uncertain_not_a_decision(monkeypatch):
    import anthropic
    from ai import matching

    payload = json.dumps({"same_entity": True, "confidence": 0.41,
                          "reason": "genuinely unclear"})
    block = type("B", (), {"type": "text", "text": payload})()
    resp = type("R", (), {"content": [block],
                          "usage": type("U", (), {"input_tokens": 10,
                                                  "output_tokens": 5})()})()
    fake = type("C", (), {"messages": type("M", (), {
        "create": staticmethod(lambda **kw: resp)})()})
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: fake)

    v = matching._ask_claude("A Industries LLP", "A Industrial LLP")
    assert v.match is None                     # uncertain, not True
    assert "0.41" in v.reason


# --- the ai_uncertain path through the rules --------------------------------

def uncertain(a, b):
    return rules.NameVerdict(None, reason="model confidence 0.40: unclear")


def test_uncertain_name_match_downgrades_r09_to_fix():
    ctx = RuleContext(today=TODAY, names_match=uncertain)
    ext = base_extracted(bank_proof={
        "account_holder_name": "Sundaram Inds. Supplies LLP",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank"})
    found = rules.r09_bank_holder_name(base_submission(), ext, ctx)
    assert len(found) == 1
    f = found[0]
    assert f.rule_id == "R09"
    assert f.severity == FIX                   # never REJECTED on uncertainty
    assert f.tag == "ai_uncertain"
    assert "human review required" in f.message


def test_uncertain_name_match_never_silently_approves(db):
    from engine import pipeline
    run_id = db.create_run("Ambiguous Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    status = pipeline.run(run_id, today=TODAY, names_match=uncertain,
                          extract_fn=fake_extractor(),
                          review_fn=fake_reviewer())
    assert status == "PENDING"                 # not APPROVED, not REJECTED
    assert "ai_uncertain" in [f["tag"] for f in db.get_findings(run_id)]


def test_name_matching_failure_yields_error_not_approval(db):
    """An AI outage must not become a silent approval."""
    from engine import pipeline

    def exploding(a, b):
        raise RuntimeError("anthropic timeout")

    run_id = db.create_run("Outage Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    assert pipeline.run(run_id, today=TODAY, names_match=exploding,
                        extract_fn=fake_extractor(),
                        review_fn=fake_reviewer()) == "ERROR"
    assert db.get_run(run_id)["status"] == "ERROR"
    assert not any(e["event_type"] == "decision" for e in db.get_events(run_id))


# --- the four fixtures, end to end (offline) --------------------------------

# What the real extractor read off each source PDF, transcribed from the headings
# and rows samples/make_pdfs.py actually prints. `document_type` is whatever the
# paper calls itself — "permanent account number card", not "pan_card" — because
# that is the string R13 judges.
_BANK = {"document_type": "bank account confirmation letter",
         "account_holder_name": "Sundaram Industrial Supplies LLP",
         "account_number": "50200071234567", "ifsc": "HDFC0001234",
         "bank_name": "HDFC Bank Limited"}

FIXTURE_EXTRACTIONS = {
    "pan_card.pdf": {
        "document_type": "permanent account number card",
        "pan": "ABCFS1234K", "name": "Sundaram Industrial Supplies LLP"},
    "gst_certificate.pdf": {
        "document_type": "gst registration certificate", "gstin": GSTIN_KA,
        "legal_name": "Sundaram Industrial Supplies LLP",
        "trade_name": "Sundaram Supplies"},
    "incorporation_certificate.pdf": {
        "document_type": "certificate of incorporation",
        "legal_name": "Sundaram Industrial Supplies LLP",
        "registration_number": "AAB-1234", "incorporation_date": "2019-04-11"},
    "bank_proof.pdf": _BANK,
    "bank_proof_mismatch.pdf": dict(_BANK, account_holder_name="S. Ramesh Kumar"),
    "address_proof.pdf": {
        "document_type": "electricity bill",
        "name": "Sundaram Industrial Supplies LLP",
        "address": "42 Industrial Layout, Koramangala, Bengaluru 560095",
        "state": "Karnataka"},
}


def fixture_extractor(path, doc_type):
    """Replays what the real extractor returned for that source PDF in Part 3."""
    source = path.read_text(encoding="utf-8").split(":", 1)[1]
    data = dict(FIXTURE_EXTRACTIONS[source])
    return data, {"purpose": "extract:" + doc_type, "model": "replay",
                  "input_summary": source,
                  "fields_read": sorted(k for k, v in data.items() if v),
                  "usage": {"input_tokens": 1, "output_tokens": 1}}


def load_scenario(name):
    import pathlib
    return json.loads(backend("samples", name + ".json").read_text("utf-8"))


def run_scenario(db, name, calls):
    from ai import matching
    from engine import pipeline
    sc = load_scenario(name)
    run_id = db.create_run(sc["submission"]["legal_entity_name"], sc["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in sc["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())

    def recording_ask(a, b, on_ai_call=None):
        calls.append((a, b))
        return rules.NameVerdict(False, reason="escalated")

    def counting_match(a, b):
        return matching.names_match(a, b, ask=recording_ask)

    status = pipeline.run(run_id, today=TODAY, names_match=counting_match,
                          extract_fn=fixture_extractor,
                          review_fn=fake_reviewer())
    return sc, run_id, status


SCENARIO_NAMES = ["ec1_happy", "ec2_incomplete", "ec3_bank_mismatch", "ec4_crossfield"]


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_fixture_produces_its_documented_status_and_findings(db, name):
    calls = []
    sc, run_id, status = run_scenario(db, name, calls)
    assert status == sc["expected_status"], sc["label"]
    found = sorted({f["rule_id"] for f in db.get_findings(run_id)})
    assert found == sc["expected_rules"], sc["label"]


def test_ec3_makes_no_model_call_for_name_matching(db):
    """The fraud case is settled deterministically — the score is far below the band."""
    calls = []
    _, run_id, status = run_scenario(db, "ec3_bank_mismatch", calls)
    assert calls == []
    assert status == "REJECTED"
    ai = [e for e in db.get_events(run_id) if e["event_type"] == "ai_call"
          and e["stage"] == "consistency"]
    assert ai == []          # no name-match escalation for the fraud case


def test_no_fixture_needs_the_name_comparator(db):
    """All four demos resolve without escalation — a demo-stability property."""
    for name in SCENARIO_NAMES:
        calls = []
        run_scenario(db, name, calls)
        assert calls == [], name + " escalated to the model"


def test_fixture_gstins_carry_a_valid_checksum():
    for name in SCENARIO_NAMES:
        g = load_scenario(name)["submission"]["gstin"]
        assert gstin_checksum(g[:14]) == g[14], name + " has a hand-written GSTIN"


def test_every_fixture_attaches_only_documents_the_engine_can_read():
    """A scenario naming a slot that no longer exists would be silently ignored by
    the pipeline and the demo would show the wrong outcome for the wrong reason."""
    from ai import extract
    for name in SCENARIO_NAMES:
        sc = load_scenario(name)
        assert set(sc["documents"]) <= set(extract.DOC_TYPES), name
        for source in sc["documents"].values():
            assert source in FIXTURE_EXTRACTIONS, f"{name}: no replay for {source}"
            assert backend("samples", "pdfs", source).exists(), source


def test_every_replayed_extraction_matches_its_real_schema():
    """The replay stands in for the model. If it invents a field the schema does
    not have — or drops one it does — these tests stop testing production."""
    from ai import extract
    by_source = {source: key for name in SCENARIO_NAMES
                 for key, source in load_scenario(name)["documents"].items()}
    for source, key in by_source.items():
        assert set(FIXTURE_EXTRACTIONS[source]) == \
            set(extract.SCHEMAS[key]["properties"]), source


def test_matching_module_never_emits_findings_or_statuses():
    import pathlib
    src = backend("ai/matching.py").read_text(encoding="utf-8")
    assert "Finding(" not in src
    for word in ("APPROVED", "PENDING", "REJECTED"):
        assert word not in src, "matching.py must not mention " + word


# --- stage 7: communication -------------------------------------------------

def fake_reviewer(calls=None, risk=None):
    """Stands in for the AI Employee's single review call."""
    def _fn(vendor_name, status, findings, comparisons=None):
        from ai import employee as ai_employee
        if calls is not None:
            calls.append({"vendor": vendor_name, "status": status,
                          "findings": list(findings)})
        result = ai_employee.Review(
            risk=risk or ai_employee.assess_risk(findings),
            risk_rationale="stub rationale",
            summary=f"{vendor_name} was {status.lower()}.",
            key_points=[f["message"] for f in findings][:3],
            recommended_action="stub action",
            extraction_notes=[])
        return result, {"purpose": "ai_employee:review", "capability": "review",
                        "model": "fake-model", "input_summary": status,
                        "usage": {"input_tokens": 10, "output_tokens": 20}}
    return _fn


def run_scenario_offline(db, name):
    from ai import matching
    from engine import pipeline
    sc = load_scenario(name)
    run_id = db.create_run(sc["submission"]["legal_entity_name"], sc["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in sc["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())

    def no_escalation(a, b, on_ai_call=None):
        raise AssertionError("no fixture should escalate")

    status = pipeline.run(
        run_id, today=TODAY, extract_fn=fixture_extractor,
        names_match=lambda a, b: matching.names_match(a, b, ask=no_escalation),
        review_fn=fake_reviewer())
    return sc, run_id, status


@pytest.mark.parametrize("name", ["ec1_happy", "ec3_bank_mismatch", "ec4_crossfield"])
def test_approved_and_rejected_never_get_a_draft(db, name):
    drafts = []
    sc, run_id, status = run_scenario_offline(db, name)
    assert status in ("APPROVED", "REJECTED")
    assert not [e for e in db.get_events(run_id)
                if e["event_type"] == "correction_requested"]
    assert drafts == []


def test_rejected_records_an_internal_note_instead(db):
    _, run_id, status = run_scenario_offline(db, "ec3_bank_mismatch")
    assert status == "REJECTED"
    note = [e for e in db.get_events(run_id) if e["event_type"] == "internal_note"]
    assert len(note) == 1
    detail = json.loads(note[0]["detail_json"])
    assert detail["blocking_rules"] == ["R09"]
    assert "different name" in detail["note"]
    assert not [e for e in db.get_events(run_id)
                if e["event_type"] == "correction_requested"]


def test_approved_run_has_no_communication_output(db):
    _, run_id, _ = run_scenario_offline(db, "ec1_happy")
    outcomes = [json.loads(e["detail_json"])["outcome"]
                for e in db.get_events(run_id)
                if e["event_type"] == "stage_completed" and e["stage"] == "communicate"]
    assert outcomes == ["no_communication_needed"]


def test_vendor_wording_replaces_raw_field_names():
    """A vendor has never seen our field names; a reviewer needs them."""
    f = Finding("R01", FIX, "completeness",
                "Required field 'contact_phone' is missing")
    assert rules.correction_request(f) == "Please provide the contact phone number."
    assert "contact_phone" not in rules.correction_request(f)


def test_reviewer_wording_is_unchanged():
    found = rules.r01_required_fields(base_submission(contact_phone=""), None, CTX)
    assert found[0].message == "Required field 'contact_phone' is missing"


def test_every_kind_of_finding_becomes_something_the_vendor_can_do():
    """Not a restatement of the problem - an instruction. A vendor reading
    "document type invalid" does not know what to upload."""
    for f, verb in ((Finding("R02", FIX, "completeness",
                             "Required document 'PAN card' was not attached"),
                     "upload"),
                    (Finding("R14", FIX, "format",
                             "The account number could not be read from the "
                             "Cancelled cheque or bank letter"),
                     "re-upload")):
        text = rules.correction_request(f)
        assert text.lower().startswith("please")
        assert verb in text.lower()
        assert f.rule_id not in text


def test_every_submission_field_has_a_human_label():
    assert set(rules.FIELD_LABELS) == set(rules.SUBMISSION_FIELDS)
    assert all("_" not in label for label in rules.FIELD_LABELS.values())


# --- upload validation ------------------------------------------------------

GOOD_PDF = b"%PDF-1.4\n% real enough\n"
GOOD_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
GOOD_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40


@pytest.mark.parametrize("filename,data", [
    ("cheque.pdf", GOOD_PDF), ("scan.PDF", GOOD_PDF),
    ("scan.png", GOOD_PNG), ("photo.jpg", GOOD_JPG), ("photo.jpeg", GOOD_JPG),
])
def test_valid_uploads_are_accepted(filename, data):
    from ai import extract
    assert extract.check_upload(filename, data) is None


@pytest.mark.parametrize("filename,data,expect", [
    ("notes.txt", b"hello", "not a supported file type"),
    ("payload.exe", b"MZ\x90\x00", "not a supported file type"),
    ("doc.docx", GOOD_PDF, "not a supported file type"),
    ("noext", GOOD_PDF, "not a supported file type"),
    ("empty.pdf", b"", "the file is empty"),
    ("renamed.pdf", b"MZ\x90\x00 this is an exe", "does not look like a valid PDF"),
    ("truncated.png", b"\x89PNGbroken", "does not look like a valid PNG"),
])
def test_bad_uploads_are_rejected_with_a_readable_reason(filename, data, expect):
    from ai import extract
    reason = extract.check_upload(filename, data)
    assert reason is not None and expect in reason


def test_oversized_upload_is_rejected():
    from ai import extract
    big = GOOD_PDF + b"\x00" * (extract.MAX_UPLOAD_BYTES + 1)
    reason = extract.check_upload("huge.pdf", big)
    assert reason and "limit" in reason


def test_upload_rejection_reason_names_the_allowed_types():
    from ai import extract
    reason = extract.check_upload("x.txt", b"hi")
    for ext in (".pdf", ".png", ".jpg"):
        assert ext in reason


# --- a bad attachment must not crash the run --------------------------------

class FakeUpload:
    def __init__(self, filename, data):
        self.filename = filename
        self.file = io.BytesIO(data)


def save_uploads(run_id: str, files: dict) -> None:
    """Store attachments the way the vendor portal does.

    That is the only intake path now, and it is where `extract.check_upload`
    and the storage-key validation actually run.
    """
    from engine import forms
    from routes import vendor
    vendor._save_documents(run_id, dict(files), forms.standard_schema())


def good_uploads(bad: str = None, bad_name: str = "cheque.exe",
                 bad_data: bytes = b"MZ\x90\x00") -> dict:
    """A valid PDF in every document slot, optionally with one slot poisoned.

    Named per slot so a five-document form does not have to be retyped in every
    upload test — and so adding a sixth document breaks one helper, not twelve.
    """
    from ai import extract
    return {t: FakeUpload(bad_name, bad_data) if t == bad
            else FakeUpload(f"{t}.pdf", GOOD_PDF)
            for t in extract.DOC_TYPES}


def test_rejected_upload_is_reported_not_saved(db):
    run_id = db.create_run("Wrong File Ltd", base_submission())
    save_uploads(run_id, {"bank_proof": FakeUpload("x.exe", b"MZ")})

    assert db.saved_documents(run_id) == {}
    rejected = [e for e in db.get_events(run_id) if e["event_type"] == "upload_rejected"]
    assert len(rejected) == 1
    detail = json.loads(rejected[0]["detail_json"])
    assert detail["document"] == "bank_proof"
    assert detail["filename"] == "x.exe"
    assert "not a supported file type" in detail["reason"]


def test_bad_attachment_yields_pending_not_error(db):
    """A vendor attaching the wrong file is a fixable problem, not a crash."""
    from engine import pipeline
    run_id = db.create_run("Wrong File Ltd", base_submission())
    save_uploads(run_id, good_uploads(bad="bank_proof"))

    status = pipeline.run(run_id, today=TODAY,
                          extract_fn=lambda p, t: (dict(FAKE_DOCS[t]), {
                              "purpose": "extract:" + t, "model": "fake",
                              "input_summary": p.name,
                              "usage": {"input_tokens": 1, "output_tokens": 1}}))
    assert status == "PENDING"                      # not ERROR
    assert [f["rule_id"] for f in db.get_findings(run_id)] == ["R02"]


def test_run_detail_reports_a_rejected_upload(db, client):
    """The rejection must survive the view model, not just persistence."""
    from engine import pipeline

    run_id = db.create_run("Wrong File Ltd", base_submission())
    save_uploads(run_id, good_uploads(bad="bank_proof", bad_data=b"MZ-not-a-pdf"))
    pipeline.run(run_id, today=TODAY,
                 extract_fn=fake_extractor())

    page = client.get(f"/api/runs/{run_id}")
    assert page.status_code == 200
    rejected = page.json()["rejected_uploads"]
    assert [r["filename"] for r in rejected] == ["cheque.exe"]
    assert "not a supported file type" in rejected[0]["reason"]


def test_a_traversal_filename_cannot_escape_the_upload_directory(db):
    run_id = db.create_run("Sneaky Ltd", base_submission())
    save_uploads(run_id, {"bank_proof": FakeUpload("../../../../evil.pdf", GOOD_PDF)})
    saved = db.saved_documents(run_id)
    assert list(saved) == ["bank_proof"]
    assert saved["bank_proof"].name == "bank_proof.pdf"     # filename discarded


@pytest.mark.parametrize("bad_id", ["../etc", "VS-0001/../..", "..", "", "VS-x"])
def test_storage_prefix_rejects_ids_that_are_not_run_ids(bad_id):
    from data import store
    with pytest.raises(ValueError):
        store.storage_prefix(bad_id)


@pytest.mark.parametrize("prefix,doc,ext", [
    ("../../etc", "bank_proof", ".pdf"),
    ("onboarding/CASE-0001", "../../evil", ".pdf"),
    ("onboarding/CASE-0001", "bank_proof", "/../../x.pdf"),
    ("onboarding/CASE-0001", "bank_proof", ".exe"),
    ("runs/VS-x", "bank_proof", ".pdf"),
])
def test_object_keys_cannot_be_escaped(prefix, doc, ext):
    """Every component of the key is validated, whatever the caller passes."""
    from data import storage
    with pytest.raises(ValueError):
        storage.object_key(prefix, doc, ext)


def test_object_key_has_the_documented_shape():
    from data import storage
    assert storage.object_key("onboarding/CASE-0007", "bank_proof", ".pdf") ==         "onboarding/CASE-0007/bank_proof.pdf"


# --- secrets never reach the audit trail ------------------------------------

def test_error_details_redact_anything_key_shaped():
    from engine import pipeline
    exc = RuntimeError("auth failed for sk-ant-api03-AbC123secretKEYvalue-xyz")
    safe = pipeline._safe_error(exc)
    assert "sk-ant-api03-AbC123secretKEYvalue-xyz" not in safe
    assert "REDACTED" in safe


def test_persisted_stage_failure_contains_no_key(db):
    from engine import pipeline

    def exploding(path, doc_type):
        raise RuntimeError("401 from provider, key sk-ant-api03-LEAKEDKEY0000")

    run_id = db.create_run("Leaky Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=exploding,
                 review_fn=fake_reviewer())

    blob = json.dumps([dict(e) for e in db.get_events(run_id)])
    assert "sk-ant-api03-LEAKEDKEY0000" not in blob
    assert "REDACTED" in blob


APP_MODULES = ("app.py", "engine/pipeline.py", "engine/rules.py",
               "data/store.py", "ai/extract.py", "ai/matching.py")


def frontend_sources() -> list:
    """Everything the browser is actually shipped, minus the frontend's own tests.

    Replaces the old template glob: the UI is a React bundle now, but it is still
    the surface a secret would leak through if one were ever inlined.
    """
    return [p for p in repo("frontend", "src").rglob("*")
            if p.suffix in (".js", ".jsx", ".css") and "__tests__" not in p.parts]


def test_no_api_key_appears_anywhere_in_application_source():
    """Scans shipped code. Test fixtures deliberately contain fake key strings."""
    import pathlib
    import re
    pattern = re.compile(r"sk-ant-[A-Za-z0-9]")
    targets = [backend(m) for m in APP_MODULES]
    targets += list(backend("routes").glob("*.py"))
    targets += frontend_sources()
    targets += list(backend("samples").glob("*.py"))
    targets += list(backend("samples").glob("*.json"))
    for path in targets:
        assert not pattern.search(path.read_text(encoding="utf-8")), path


# The only values .env.example is allowed to ship: blank, or a default that is
# public by nature (an environment name, a bucket name, a localhost origin, a
# token lifetime). Anything else in the template would be a committed secret.
NON_SECRET_DEFAULTS = ("", "development", "onboarding-documents", "background",
                       "http://localhost:5173,http://127.0.0.1:5173",
                       # The default password is documented on purpose; production
                       # refuses to start while it is still in use.
                       "gozamp")


def test_env_example_holds_no_actual_secrets():
    """Every value is blank or a non-secret default."""
    import pathlib
    text = repo(".env.example").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=" in text
    for line in text.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        assert value.strip() in NON_SECRET_DEFAULTS, line


def test_missing_api_key_is_a_clear_error_not_a_stack_trace(monkeypatch):
    from ai import extract
    monkeypatch.setattr(extract, "_client", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        extract.client()
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert ".env" in str(exc.value)


def test_gitignore_covers_secrets_and_runtime_state():
    import pathlib
    text = repo(".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "vendor.db", "uploads/", "__pycache__/", ".pytest_cache/"):
        assert entry in text, entry
    assert "!.env.example" in text          # the template stays committed


# --- one failed stage must not corrupt persisted state ----------------------

def test_a_failed_stage_leaves_earlier_state_intact(db):
    """Findings written before the failure survive; nothing is half-written."""
    from engine import pipeline

    def exploding(path, doc_type):
        raise RuntimeError("extraction died")

    run_id = db.create_run("Half Ltd", base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    assert pipeline.run(run_id, today=TODAY, extract_fn=exploding,
                        review_fn=fake_reviewer()) == "ERROR"

    run = db.get_run(run_id)
    assert run["status"] == "ERROR"
    assert run["submission"]["legal_entity_name"] == "Sundaram Industrial Supplies LLP"
    assert run["extracted"] is None                 # never half-written
    assert not [e for e in db.get_events(run_id)
                if e["event_type"] == "correction_requested"]
    assert [f["rule_id"] for f in db.get_findings(run_id)] == ["R01"]
    assert run["duration_ms"] is not None


def test_error_run_still_emits_the_terminal_marker(db):
    from engine import pipeline
    run_id = db.create_run("Boom Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY,
                 review_fn=fake_reviewer(),
                 extract_fn=lambda p, t: (_ for _ in ()).throw(RuntimeError("x")))
    finished = [e for e in db.get_events(run_id) if e["event_type"] == "run_finished"]
    assert len(finished) == 1
    assert json.loads(finished[0]["detail_json"])["status"] == "ERROR"


def test_a_run_after_a_failure_works_normally(db):
    """A crashed run must not poison the next one."""
    from engine import pipeline
    bad = db.create_run("Boom Ltd", base_submission())
    attach(db, bad, *FAKE_DOCS)
    pipeline.run(bad, today=TODAY, review_fn=fake_reviewer(),
                 extract_fn=lambda p, t: (_ for _ in ()).throw(RuntimeError("x")))

    good = db.create_run("Fine Ltd", base_submission())
    attach(db, good, *FAKE_DOCS)
    assert pipeline.run(good, today=TODAY, extract_fn=fake_extractor(),
                        review_fn=fake_reviewer()) == "APPROVED"
    assert db.get_run(bad)["status"] == "ERROR"     # unchanged


def test_the_audit_trail_is_append_only_in_practice(db):
    """Nothing in the codebase ever updates or deletes an event.

    Stronger than it used to be: the demo reset was the one thing that deleted
    events, and it is gone, so the audit trail is now append-only outright.
    """
    src = backend("data/store.py").read_text(encoding="utf-8")
    assert "UPDATE events" not in src
    assert "DELETE FROM events" not in src


def test_every_stage_emits_start_and_end(db):
    _, run_id, _ = run_scenario_offline(db, "ec2_incomplete")
    events = db.get_events(run_id)
    started = {e["stage"] for e in events if e["event_type"] == "stage_started"}
    ended = {e["stage"] for e in events
             if e["event_type"] in ("stage_completed", "stage_failed")}
    assert started == ended


# --- AI cannot determine the final status -----------------------------------

def test_decide_is_reachable_only_from_findings():
    import inspect
    src = inspect.getsource(rules.decide)
    assert "findings" in src
    for word in ("extract", "matching", "anthropic", "client", "submission"):
        assert word not in src


def test_no_ai_module_can_reach_the_decision():
    import pathlib
    for module in ("ai/extract.py", "ai/matching.py"):
        src = backend(module).read_text(encoding="utf-8")
        assert "decide(" not in src
        assert ("from engine import rules" not in src
                or module.endswith("matching.py"))


def test_matching_imports_only_pure_helpers_from_rules():
    """It may borrow normalisation; it may never call the decision function."""
    import pathlib
    src = backend("ai/matching.py").read_text(encoding="utf-8")
    assert "from engine.rules import NameVerdict, normalize_name" in src
    assert "decide(" not in src and "rules.decide" not in src


# ============================================================================
# Onboarding cases — employee creates, vendor submits via secure link
# ============================================================================

def make_case(db, vendor="Sundaram Industrial Supplies LLP",
              contact="Priya Raghavan", email="priya@sundaramsupplies.in"):
    token = db.new_token()
    case_id = db.create_case(vendor, contact, email, token)
    return case_id, token


# --- token generation and storage -------------------------------------------

def test_tokens_are_long_and_unique():
    from data import store
    tokens = {store.new_token() for _ in range(500)}
    assert len(tokens) == 500
    assert all(len(t) >= 40 for t in tokens)


def test_token_generation_uses_the_secure_generator():
    import inspect
    from data import store
    src = inspect.getsource(store.new_token)
    assert "secrets." in src
    assert "random." not in src              # never the predictable module


def test_the_token_and_its_hash_are_both_stored(db):
    """The hash is the lookup key; the token is what an employee copies.

    Hashing it alone protected nothing worth protecting: the link opens a form
    prefilled from `runs.submission_json`, which sits in plaintext two tables
    away, so the breach that leaks a token already has the bank details. What it
    did cost was a link you could lose by closing a tab.
    """
    token = db.new_token()
    case_id = db.create_case("Sundaram", "Priya", "p@e.in", token)
    case = db.get_case(case_id)
    assert case["token"] == token
    assert case["token_hash"] == db.hash_token(token)
    assert case["token_hash"] != token


def test_a_case_is_still_only_reachable_by_its_token(db):
    """Storing it changes nothing about how a vendor is authorised."""
    token = db.new_token()
    case_id = db.create_case("Sundaram", "Priya", "p@e.in", token)
    assert db.get_case_by_token(token)["id"] == case_id
    assert db.get_case_by_token(db.hash_token(token)) is None
    assert db.get_case_by_token(case_id) is None


def test_lookup_by_token_finds_only_its_own_case(db):
    a_id, a_token = make_case(db, "Alpha LLP")
    b_id, b_token = make_case(db, "Beta LLP")
    assert db.get_case_by_token(a_token)["id"] == a_id
    assert db.get_case_by_token(b_token)["id"] == b_id
    assert db.get_case_by_token(a_token)["vendor_name"] == "Alpha LLP"


@pytest.mark.parametrize("bad", ["", "nope", "CASE-0001", "x" * 43, None])
def test_unknown_tokens_resolve_to_nothing(db, bad):
    make_case(db)
    assert db.get_case_by_token(bad) is None


def test_a_new_case_starts_awaiting_the_vendor(db):
    case_id, _ = make_case(db)
    case = db.get_case(case_id)
    assert case["status"] == db.AWAITING_VENDOR
    assert case["run_id"] is None and case["submitted_at"] is None


def test_attaching_a_run_moves_the_case_to_processing(db):
    case_id, _ = make_case(db)
    run_id = db.create_run("Sundaram", base_submission())
    db.attach_run_to_case(case_id, run_id)
    case = db.get_case(case_id)
    assert case["run_id"] == run_id
    assert case["status"] == db.PROCESSING
    assert case["submitted_at"] is not None


def test_case_ids_are_sequential(db):
    assert make_case(db)[0] == "CASE-0001"
    assert make_case(db)[0] == "CASE-0002"


def test_access_logs_redact_the_token_but_keep_the_route():
    """The token travels in the URL, so the access log would otherwise record a
    working credential on every request."""
    import logging
    import app
    _, token = "x", "AbCdEf0123456789_-QwErTyUiOpAsDfGhJkLzXcVbN"
    rec = logging.LogRecord("uvicorn.access", 20, "", 0,
                            '%s - "%s %s HTTP/%s" %d %s',
                            ("127.0.0.1:1", "GET", f"/vendor/onboard/{token}",
                             "1.1", 200, "OK"), None)
    app._RedactVendorToken().filter(rec)
    line = rec.getMessage()
    assert token not in line
    assert "/vendor/onboard/<redacted>" in line      # route still legible


def test_redaction_leaves_other_paths_alone():
    import app
    assert app._redact("GET /dashboard") == "GET /dashboard"
    assert app._redact("GET /run/VS-0001") == "GET /run/VS-0001"


# --- employee: create a case over HTTP ---------------------------------------

def token_from(vendor_url: str) -> str:
    """The raw token out of the one-time link the API just handed back."""
    return vendor_url.rsplit("/", 1)[-1]


def test_the_case_endpoint_takes_the_vendor_and_contact_fields(client, db):
    """There is no new-case page any more — creating a case is one JSON call, so
    the fields the form used to render are the fields this body accepts."""
    from routes import onboardings
    assert {"vendor_name", "contact_name", "contact_email"} <= \
        set(onboardings.CaseIn.model_fields)

    r = client.post("/api/onboardings", json={
        "vendor_name": "Sundaram Industrial Supplies LLP",
        "contact_name": "Priya Raghavan",
        "contact_email": "priya@sundaramsupplies.in"})
    assert r.status_code == 201, r.text
    stored = db.get_case(r.json()["case_id"])
    assert stored["contact_name"] == "Priya Raghavan"
    assert stored["contact_email"] == "priya@sundaramsupplies.in"


def test_employee_creates_a_case_and_gets_a_copyable_link(client, db):
    r = client.post("/api/onboardings", json={
        "vendor_name": "Sundaram Industrial Supplies LLP",
        "contact_name": "Priya Raghavan",
        "contact_email": "priya@sundaramsupplies.in"})
    assert r.status_code == 201
    body = r.json()
    assert body["case_id"] == "CASE-0001"
    assert "/vendor/onboard/" in body["vendor_url"]      # the link to copy
    assert body["form"]["template_name"]

    cases = db.list_cases()
    assert len(cases) == 1
    assert cases[0]["vendor_name"] == "Sundaram Industrial Supplies LLP"
    assert cases[0]["status"] == db.AWAITING_VENDOR


def test_creating_a_case_without_a_vendor_name_is_rejected(client, db):
    assert client.post("/api/onboardings",
                       json={"vendor_name": "  "}).status_code == 400
    assert db.list_cases() == []


def test_the_generated_link_actually_works(client, db):
    r = client.post("/api/onboardings",
                    json={"vendor_name": "Sundaram Industrial Supplies LLP"})
    token = token_from(r.json()["vendor_url"])
    assert client.get(f"/api/vendor/onboard/{token}").status_code == 200


# --- vendor portal: access control -------------------------------------------

def schema_field_ids(schema: dict) -> set[str]:
    return {field["id"] for section in schema["sections"]
            for field in section["fields"]}


def test_vendor_link_opens_the_form(client, db):
    _, token = make_case(db)
    r = client.get(f"/api/vendor/onboard/{token}")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "open"
    assert body["vendor_name"] == "Sundaram Industrial Supplies LLP"
    ids = schema_field_ids(body["schema"])
    assert set(rules.SUBMISSION_FIELDS) <= ids
    from ai import extract
    for doc in extract.DOC_TYPES:
        assert doc in ids


@pytest.mark.parametrize("bad", [
    "deadbeef", "CASE-0001", "x" * 43, "' OR 1=1 --",
    "%2e%2e%2f%2e%2e%2fdashboard", "..%2F..%2Fdashboard", "....//dashboard",
])
def test_an_invalid_token_is_404(client, db, bad):
    """Covers guesses, case ids, injection and every traversal form that actually
    reaches the server. A literal `../../x` is resolved by the HTTP client before
    the request is sent, so it never reaches this route at all."""
    make_case(db)
    assert client.get(f"/api/vendor/onboard/{bad}").status_code == 404


def test_a_vendor_token_reaches_only_its_own_case(client, db):
    make_case(db, "Alpha Industries LLP")
    _, b_token = make_case(db, "Beta Logistics LLP")
    body = client.get(f"/api/vendor/onboard/{b_token}").text
    assert "Beta Logistics LLP" in body
    assert "Alpha Industries LLP" not in body


def test_case_ids_are_never_accepted_as_authorisation(client, db):
    """The only key is the token. A known case id must open nothing."""
    case_id, _ = make_case(db)
    assert client.get(f"/api/vendor/onboard/{case_id}").status_code == 404
    assert client.post(f"/api/vendor/onboard/{case_id}", data={}).status_code == 404


def test_the_vendor_payload_never_exposes_internal_surfaces(client, db):
    _, token = make_case(db)
    body = client.get(f"/api/vendor/onboard/{token}").text
    for leak in ("/dashboard", "/api/runs/", "Decision Engine",
                 "Finding", "BLOCK", "R01", "Audit"):
        assert leak not in body, leak


# --- vendor submission --------------------------------------------------------

def vendor_post(client, token, overrides=None, files=None):
    data = dict(base_submission())
    data.update(overrides or {})
    return client.post(f"/api/vendor/onboard/{token}", data=data,
                       files=files or {})


def test_vendor_submits_and_the_link_then_reports_it_as_submitted(client, db,
                                                                  monkeypatch):
    """There is no confirmation page to redirect to any more: the POST says the
    submission was received, and the same GET the vendor already has reports the
    link as spent."""
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    _, token = make_case(db)

    r = vendor_post(client, token)
    assert r.status_code == 200
    assert r.json() == {"state": "received"}

    assert client.get(f"/api/vendor/onboard/{token}").json()["state"] == "submitted"


def test_submission_creates_a_run_and_binds_it_to_the_case(client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    case_id, token = make_case(db)
    vendor_post(client, token)

    case = db.get_case(case_id)
    assert case["status"] == db.PROCESSING
    assert case["run_id"] is not None
    run = db.get_run(case["run_id"])
    assert run["submission"]["gstin"] == base_submission()["gstin"]
    assert run["submission"]["pan"] == base_submission()["pan"]


def test_submission_triggers_the_existing_pipeline(client, db, monkeypatch):
    from engine import pipeline
    called = []
    monkeypatch.setattr(pipeline, "run",
                        lambda run_id, **k: called.append(run_id) or "APPROVED")
    case_id, token = make_case(db)
    vendor_post(client, token)
    assert called == [db.get_case(case_id)["run_id"]]


def test_submission_records_a_vendor_event_without_the_token(client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    case_id, token = make_case(db)
    vendor_post(client, token)

    events = db.get_events(db.get_case(case_id)["run_id"])
    submitted = [e for e in events if e["event_type"] == "vendor_submitted"]
    assert len(submitted) == 1 and submitted[0]["actor"] == "vendor"
    assert json.loads(submitted[0]["detail_json"])["case_id"] == case_id
    assert token not in json.dumps([dict(e) for e in events])


def test_a_blank_vendor_submission_is_stored_not_500(client, db, monkeypatch):
    """Invalid data becomes findings, exactly as on the internal form."""
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "PENDING")
    case_id, token = make_case(db)
    r = client.post(f"/api/vendor/onboard/{token}",
                    data={f: "" for f in rules.SUBMISSION_FIELDS})
    assert r.status_code == 200
    run = db.get_run(db.get_case(case_id)["run_id"])
    assert run["submission"]["legal_entity_name"] == ""


def test_vendor_uploads_use_the_existing_validation(client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "PENDING")
    case_id, token = make_case(db)
    vendor_post(client, token, files={
        "bank_proof": ("cheque.exe", b"MZ-not-a-pdf", "application/octet-stream"),
        "incorporation_certificate": ("cert.pdf", GOOD_PDF, "application/pdf")})

    run_id = db.get_case(case_id)["run_id"]
    saved = db.saved_documents(run_id)
    assert "bank_proof" not in saved            # refused
    assert "incorporation_certificate" in saved  # accepted
    rejected = [json.loads(e["detail_json"]) for e in db.get_events(run_id)
                if e["event_type"] == "upload_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["document"] == "bank_proof"
    assert "not a supported file type" in rejected[0]["reason"]


def test_a_link_can_only_be_submitted_once(client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    _, token = make_case(db)
    assert vendor_post(client, token).status_code == 200
    assert vendor_post(client, token).status_code == 409
    assert len(db.list_runs()) == 1             # no second run started


def test_revisiting_a_submitted_link_reports_submitted_not_the_form(client, db,
                                                                    monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    _, token = make_case(db)
    vendor_post(client, token)
    body = client.get(f"/api/vendor/onboard/{token}").json()
    assert body["state"] == "submitted"
    assert "schema" not in body                 # nothing left to fill in


def test_the_submitted_state_leaks_no_decision_information(client, db, monkeypatch):
    """The vendor learns their submission arrived. Nothing else."""
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "REJECTED")
    case_id, token = make_case(db)
    vendor_post(client, token)
    db.set_status(db.get_case(case_id)["run_id"], "REJECTED")
    db.add_findings(db.get_case(case_id)["run_id"], [
        {"rule_id": "R09", "severity": "BLOCK", "stage": "consistency",
         "message": "Bank account is held in a different name"}])

    body = client.get(f"/api/vendor/onboard/{token}").text
    for leak in ("REJECTED", "BLOCK", "R09", "Bank account is held",
                 "/api/runs/", "/dashboard", "finding"):
        assert leak not in body, leak


# --- employee monitoring ------------------------------------------------------

def test_dashboard_lists_a_new_case_as_awaiting_vendor(client, db):
    make_case(db, "Sundaram Industrial Supplies LLP")
    case = client.get("/api/dashboard").json()["cases"][0]
    assert case["case_id"] == "CASE-0001"
    assert case["vendor_name"] == "Sundaram Industrial Supplies LLP"
    assert case["status_label"] == "Awaiting vendor" and case["awaiting"] is True


def test_dashboard_shows_the_case_after_submission(client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    case_id, token = make_case(db)
    vendor_post(client, token)
    run_id = db.get_case(case_id)["run_id"]
    db.set_status(run_id, "APPROVED", 4321)

    case = client.get("/api/dashboard").json()["cases"][0]
    assert case["case_id"] == "CASE-0001"
    assert case["run_id"] == run_id             # what the row links through to
    assert case["awaiting"] is False and case["status_label"] != "Awaiting vendor"


def test_list_cases_reports_stage_findings_and_last_activity(db, monkeypatch):
    from engine import pipeline
    case_id, _ = make_case(db)
    run_id = db.create_run("Sundaram", base_submission(contact_phone=""))
    db.attach_run_to_case(case_id, run_id)
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 review_fn=fake_reviewer())

    row = db.list_cases()[0]
    assert row["run_status"] == "PENDING"
    assert row["finding_count"] == 1
    assert row["current_stage"] == "run"        # last event is run_finished
    assert row["last_activity"]


def offline_pipeline(monkeypatch):
    """Let an HTTP submission start a *real* run with no AI reaching the network.

    The route only knows `pipeline.run`, so replacing it with the real function
    plus injected fakes exercises the whole wiring — background task included —
    while every model call stays local.
    """
    from engine import pipeline
    real = pipeline.run
    monkeypatch.setattr(pipeline, "run", lambda run_id, **k: real(
        run_id, today=TODAY, extract_fn=fake_extractor(),
        review_fn=fake_reviewer(), pause=0))


def test_employee_and_vendor_routes_are_separated(anon_client, client, db):
    """The two surfaces share nothing: the employee API needs a bearer token and
    the vendor API needs only its link, and neither answers for the other."""
    _, token = make_case(db)
    for path in EMPLOYEE_ROUTES:
        assert client.get(path).status_code == 200, path
        assert anon_client.get(path).status_code == 401, path
    assert anon_client.get(f"/api/vendor/onboard/{token}").status_code == 200
    assert client.get("/api/vendor/onboard/nope").status_code == 404


# ============================================================================
# Phase 3/4 — employee auth, isolation, Postgres dialect, object storage
# ============================================================================

# The employee surface is JSON now: the pages these used to be are React routes
# that render from exactly these endpoints.
EMPLOYEE_ROUTES = ["/api/dashboard", "/api/forms/templates"]


# --- the shared password ----------------------------------------------------

def test_the_password_is_compared_in_constant_time():
    """A byte-by-byte `==` on a secret leaks its length and prefix by timing."""
    import auth
    src = backend("auth.py").read_text(encoding="utf-8")
    assert "compare_digest" in src
    assert auth.check_password(TEST_PASSWORD) is True
    assert auth.check_password("wrong") is False
    assert auth.check_password("") is False


def test_no_credentials_in_source():
    """Every credential comes from the environment, without exception.

    There used to be one: a documented default password so a fresh clone ran.
    Development now generates one instead, so the rule has no exceptions left
    and this test can be absolute.
    """
    import config
    for name in APP_MODULES + ("config.py", "data/storage.py", "auth.py"):
        src = backend(name).read_text(encoding="utf-8")
        # `app.py` legitimately prints a *template* connection string in its
        # startup help, so the check is for real credential shapes only.
        for leaked in ("ANTHROPIC_API_KEY=", "sk-ant-", "service_role_key ="):
            assert leaked not in src, f"{name} contains {leaked}"
    # Nothing in the source is a usable password, including the one that used
    # to be here.
    assert not hasattr(config, "DEFAULT_PASSWORD")
    for name in APP_MODULES + ("config.py", "data/storage.py", "auth.py"):
        assert "gozamp" not in backend(name).read_text("utf-8"), name


# --- signing in -------------------------------------------------------------

def sign_in(anon_client, password=None, **extra):
    return anon_client.post("/api/login", json={
        "password": TEST_PASSWORD if password is None else password, **extra})


def test_the_session_endpoint_is_public_and_reports_anonymous(anon_client):
    """What the sign-in screen used to be: the one thing a caller with no
    credential may ask, so React knows whether to render the login form."""
    r = anon_client.get("/api/session")
    assert r.status_code == 200
    assert r.json() == {"authenticated": False}


def test_the_right_password_signs_in(anon_client):
    r = sign_in(anon_client)
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] > 0
    assert anon_client.get("/api/dashboard", headers={
        "Authorization": f"Bearer {body['access_token']}"}).status_code == 200


@pytest.mark.parametrize("password", ["wrong password", "", TEST_PASSWORD + " "])
def test_a_wrong_password_is_refused_without_hinting(anon_client, password):
    r = sign_in(anon_client, password)
    assert r.status_code == 401
    assert "not recognised" in r.text            # one message, always
    assert "no such" not in r.text.lower()


def test_the_password_is_compared_exactly(anon_client):
    """No trimming, no case folding: a password is bytes, not a name."""
    assert sign_in(anon_client, password=TEST_PASSWORD.upper()).status_code == 401
    assert sign_in(anon_client, password=f" {TEST_PASSWORD}").status_code == 401
    assert sign_in(anon_client).status_code == 200


def test_signing_out_is_the_client_discarding_the_token(client, anon_client):
    """The token is stateless, so there is nothing on the server to end — and
    deliberately no logout endpoint pretending otherwise."""
    assert client.get("/api/dashboard").status_code == 200
    assert client.post("/api/logout").status_code == 404
    assert anon_client.get("/api/dashboard").status_code == 401


def test_a_refused_request_succeeds_once_the_token_is_presented(anon_client):
    """Replaces `?next=`: nothing redirects any more, so React keeps the route it
    wanted and simply retries it with the token it just obtained."""
    assert anon_client.get("/api/dashboard").status_code == 401
    token = sign_in(anon_client).json()["access_token"]
    assert anon_client.get("/api/dashboard", headers={
        "Authorization": f"Bearer {token}"}).status_code == 200


@pytest.mark.parametrize("evil", ["//evil.example.com", "https://evil.example.com",
                                  "http://evil.example.com/x"])
def test_signing_in_can_never_send_the_caller_off_site(anon_client, evil):
    """The open-redirect this used to guard against no longer has a mechanism:
    signing in returns a token, not a Location. A stray `next` is simply ignored."""
    r = sign_in(anon_client, next=evil)
    assert r.status_code == 200
    assert "location" not in r.headers
    assert evil not in r.text


def test_the_access_token_carries_no_credentials(client, token):
    """Replaces the session cookie: nothing is stored server-side, and the
    token carries an expiry and a signature - never the password itself."""
    assert TEST_PASSWORD not in token
    expiry, _, signature = token.partition(".")
    assert expiry.isdigit() and len(signature) == 64      # HMAC-SHA256 hex
    assert not client.cookies                    # no session cookie at all


@pytest.mark.parametrize("bad", ["", "not-a-token", "a.b.c", "9999999999.deadbeef"])
def test_a_token_that_does_not_verify_authenticates_nobody(anon_client, bad):
    r = anon_client.get("/api/dashboard", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401


# --- protected routes -------------------------------------------------------

@pytest.mark.parametrize("path", EMPLOYEE_ROUTES)
def test_employee_endpoints_refuse_an_anonymous_caller(anon_client, path):
    """Nothing redirects any more: React owns navigation, so a missing token is
    a 401 and the client decides to show the sign-in screen."""
    assert anon_client.get(path).status_code == 401


@pytest.mark.parametrize("path", EMPLOYEE_ROUTES)
def test_employee_endpoints_open_once_signed_in(client, path):
    assert client.get(path).status_code == 200


def test_run_routes_are_protected(anon_client, client, db):
    run_id, _ = execute(db, base_submission())
    for path in (f"/api/runs/{run_id}",):
        assert anon_client.get(path).status_code == 401
        assert client.get(path).status_code == 200


def test_there_is_no_endpoint_that_wipes_the_database(anon_client, client, db):
    """The demo reset was removed: nothing over HTTP can destroy a run."""
    execute(db, base_submission())
    assert anon_client.post("/api/reset").status_code == 404
    assert client.post("/api/reset").status_code == 404
    assert db.list_runs()


def test_case_creation_requires_authentication(anon_client, db):
    r = anon_client.post("/api/onboardings", json={"vendor_name": "Sneaky Ltd"})
    assert r.status_code == 401
    assert db.list_cases() == []


def test_a_refusal_is_a_401_and_never_a_redirect(anon_client):
    r = anon_client.get("/api/dashboard", headers={"Accept": "application/json"})
    assert r.status_code == 401 and r.json()["detail"] == "authentication required"
    assert "location" not in r.headers


# --- employee identity in the audit trail -----------------------------------

def test_the_case_records_that_an_operator_created_it(client, db):
    import auth
    client.post("/api/onboardings",
                json={"vendor_name": "Sundaram Industrial Supplies LLP"})
    assert db.list_cases()[0]["created_by_employee"] == auth.ACTOR


# --- isolation: the vendor surface ------------------------------------------

def test_vendor_routes_need_no_employee_login(anon_client, db):
    """The whole point of the token: a vendor has no account."""
    _, token = make_case(db)
    assert anon_client.get(f"/api/vendor/onboard/{token}").status_code == 200


def test_a_vendor_cannot_reach_employee_routes(anon_client, db):
    """Holding a valid vendor token grants nothing on the internal API."""
    _, token = make_case(db)
    anon_client.get(f"/api/vendor/onboard/{token}")      # exercise the vendor path
    for path in EMPLOYEE_ROUTES:
        assert anon_client.get(path).status_code == 401, path


def test_a_vendor_cannot_reach_a_run(anon_client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    case_id, token = make_case(db)
    vendor_post(anon_client, token)
    run_id = db.get_case(case_id)["run_id"]
    for path in (f"/api/runs/{run_id}",):
        assert anon_client.get(path).status_code == 401
    assert db.list_runs(), "the vendor surface must not destroy anything"


def test_the_vendor_payload_exposes_no_employee_information(anon_client, db):
    _, token = make_case(db)
    body = anon_client.get(f"/api/vendor/onboard/{token}").text
    for leak in ("operator", "Sign out",
                 "/dashboard", "/api/runs/", "/api/login"):
        assert leak not in body, leak


# --- database: dialect correctness ------------------------------------------

def _pg_statements():
    """Every statement store.py would send to Postgres."""
    import config
    from data import store
    original = config.DATABASE_URL
    config.DATABASE_URL = "postgresql://u:p@h:5432/db"     # select the pg dialect
    try:
        ddl = list(store._schema())
        dml = [store._q(sql) for sql in [
            "SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1",
            "INSERT INTO runs (run_id, created_at, vendor_name, submission_json,"
            " status) VALUES (?, ?, ?, ?, 'RUNNING')",
            "SELECT * FROM runs WHERE run_id = ?",
            "UPDATE runs SET status = ?, duration_ms = ? WHERE run_id = ?",
            "UPDATE runs SET extracted_json = ? WHERE run_id = ?",
            "INSERT INTO findings (run_id, rule_id, severity, stage, message,"
            " expected, actual, tag) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            "SELECT * FROM findings WHERE run_id = ? ORDER BY id",
            "SELECT * FROM events WHERE run_id = ? ORDER BY id",
            "INSERT INTO events (run_id, ts, stage, event_type, actor, detail_json,"
            " duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            "SELECT 1 AS present FROM events WHERE run_id = ? AND event_type = ? LIMIT 1",
            "INSERT INTO onboarding_cases (id, vendor_name, contact_name,"
            " contact_email, token_hash, status, created_at, created_by_employee)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            "SELECT * FROM onboarding_cases WHERE token_hash = ?",
            "SELECT * FROM onboarding_cases WHERE id = ?",
            "SELECT * FROM onboarding_cases WHERE run_id = ?",
            "UPDATE onboarding_cases SET run_id = ?, status = ?, submitted_at = ?"
            " WHERE id = ?",
            "DELETE FROM events", "DELETE FROM findings",
            "DELETE FROM onboarding_cases", "DELETE FROM runs",
        ]]
        cases_sql = """
          SELECT c.*, r.status AS run_status, r.duration_ms AS run_duration_ms,
                 (SELECT COUNT(*) FROM findings f WHERE f.run_id = c.run_id)
                   AS finding_count,
                 (SELECT e.stage FROM events e WHERE e.run_id = c.run_id
                   ORDER BY e.id DESC LIMIT 1) AS current_stage,
                 (SELECT e.ts FROM events e WHERE e.run_id = c.run_id
                   ORDER BY e.id DESC LIMIT 1) AS last_activity
          FROM onboarding_cases c
          LEFT JOIN runs r ON r.run_id = c.run_id
          ORDER BY c.id DESC
        """
        return ddl + dml + [cases_sql]
    finally:
        config.DATABASE_URL = original


def _parseable(sql: str) -> str:
    """psycopg's %s is a client-side placeholder, not PostgreSQL syntax."""
    return sql.replace("%s", "$1")


def test_every_postgres_statement_parses_under_the_real_pg_grammar():
    """pglast embeds the actual PostgreSQL parser — no server required."""
    import pglast
    statements = _pg_statements()
    assert len(statements) > 20
    for sql in statements:
        pglast.parse_sql(_parseable(sql))       # raises on invalid Postgres


def test_postgres_ddl_creates_exactly_the_four_documented_tables():
    import pglast
    created = set()
    for sql in _pg_statements():
        for stmt in pglast.parse_sql(_parseable(sql)):
            node = stmt.stmt
            if type(node).__name__ == "CreateStmt":
                created.add(node.relation.relname)
    assert created == set(store_module().TABLES)


def store_module():
    from data import store
    return store


def test_placeholders_are_translated_only_for_postgres():
    import config
    from data import store
    assert store._q("SELECT ? , ?") == "SELECT ? , ?"        # sqlite, unchanged
    original = config.DATABASE_URL
    config.DATABASE_URL = "postgresql://u:p@h/db"
    try:
        assert store._q("SELECT ? , ?") == "SELECT %s , %s"
    finally:
        config.DATABASE_URL = original


def test_no_sql_lives_outside_store():
    """All database access stays behind store.py."""
    import pathlib
    import re
    keywords = re.compile(r"\b(SELECT .* FROM|INSERT INTO|UPDATE \w+ SET|DELETE FROM)\b")
    for name in ("app.py", "engine/pipeline.py", "engine/rules.py",
                 "ai/extract.py", "ai/matching.py", "data/storage.py",
                 "auth.py", "config.py"):
        src = backend(name).read_text(encoding="utf-8")
        assert not keywords.search(src), f"SQL found in {name}"


def test_backend_selection_is_driven_only_by_environment():
    import config
    original = config.DATABASE_URL
    try:
        config.DATABASE_URL = ""
        assert config.database_backend() == "sqlite"
        config.DATABASE_URL = "postgresql://u:p@h/db"
        assert config.database_backend() == "postgres"
    finally:
        config.DATABASE_URL = original


def test_the_only_hard_refusals_are_credentials_that_cannot_work():
    """There is no environment flag left to key off. What remains is refused
    everywhere, because it is broken everywhere."""
    import config
    saved = (config.APP_PASSWORD, config.SUPABASE_URL,
             config.SUPABASE_SERVICE_ROLE_KEY)
    try:
        config.APP_PASSWORD = ""
        assert any("APP_PASSWORD" in p for p in config.verify())

        config.APP_PASSWORD = "set"
        config.SUPABASE_URL = "https://x.supabase.co"
        config.SUPABASE_SERVICE_ROLE_KEY = "sb_publishable_abc"
        assert any("publishable" in p for p in config.verify())

        config.SUPABASE_SERVICE_ROLE_KEY = "sb_secret_abc"
        assert config.verify() == []
    finally:
        (config.APP_PASSWORD, config.SUPABASE_URL,
         config.SUPABASE_SERVICE_ROLE_KEY) = saved


def test_development_config_is_valid_as_is():
    import config
    assert config.verify() == []


def test_config_summary_leaks_no_secrets():
    import config
    saved = config.SUPABASE_SERVICE_ROLE_KEY
    try:
        config.SUPABASE_SERVICE_ROLE_KEY = "super-secret-service-role-key"
        blob = json.dumps(config.summary())
        assert "super-secret-service-role-key" not in blob
    finally:
        config.SUPABASE_SERVICE_ROLE_KEY = saved


# --- database: CRUD and relationships through store.py ----------------------

class _PsycopgShapedConnection:
    """sqlite3 with psycopg 3's narrower Connection surface.

    psycopg exposes `executemany` on the *cursor* only; sqlite3 also offers it on
    the connection. Every SQLite-backed test therefore passes over code that
    would break on Postgres, so this proxy removes the shortcut.
    """

    def __init__(self, real):
        self._real = real

    def execute(self, *a, **k):
        return self._real.execute(*a, **k)

    def cursor(self, *a, **k):
        return self._real.cursor(*a, **k)

    def commit(self):
        return self._real.commit()

    def close(self):
        return self._real.close()


def test_writes_never_use_a_connection_only_shortcut(db, monkeypatch):
    """Catches psycopg/sqlite3 API differences without a Postgres server."""
    import contextlib
    import sqlite3
    from data import store

    @contextlib.contextmanager
    def strict_conn():
        real = sqlite3.connect(store.DB_PATH, check_same_thread=False)
        real.row_factory = sqlite3.Row
        try:
            yield _PsycopgShapedConnection(real)
            real.commit()
        finally:
            real.close()

    monkeypatch.setattr(store, "_conn", strict_conn)

    run_id = store.create_run("Strict Ltd", base_submission())
    store.add_findings(run_id, [
        {"rule_id": "R01", "severity": "FIX", "stage": "completeness", "message": "a"},
        {"rule_id": "R02", "severity": "FIX", "stage": "completeness", "message": "b"}])
    store.add_event(run_id, "intake", "stage_started")
    store.set_status(run_id, "PENDING", 10)
    token = store.new_token()
    case_id = store.create_case("Strict Ltd", "n", "e@x.test", token, "emp@x.test")
    store.attach_run_to_case(case_id, run_id)

    assert len(store.get_findings(run_id)) == 2
    assert store.get_run(run_id)["status"] == "PENDING"
    assert store.get_case_by_token(token)["run_id"] == run_id
    assert store.list_cases()[0]["finding_count"] == 2


def test_crud_and_relationships_survive_a_full_round_trip(db):
    run_id = db.create_run("Relational Ltd", base_submission())
    db.add_findings(run_id, [{"rule_id": "R09", "severity": "BLOCK",
                              "stage": "consistency", "message": "m"}])
    db.add_event(run_id, "intake", "stage_started")
    db.set_extracted(run_id, {"bank_proof": {"ifsc": "HDFC0001234"}})
    db.set_status(run_id, "REJECTED", 1234)

    token = db.new_token()
    case_id = db.create_case("Relational Ltd", "P", "p@r.test", token, "e@zamp.test")
    db.attach_run_to_case(case_id, run_id)

    run = db.get_run(run_id)
    assert run["status"] == "REJECTED" and run["duration_ms"] == 1234
    assert run["extracted"]["bank_proof"]["ifsc"] == "HDFC0001234"
    assert db.get_case_for_run(run_id)["id"] == case_id      # run -> case
    assert db.get_case_by_token(token)["run_id"] == run_id    # case -> run
    row = db.list_cases()[0]
    assert row["finding_count"] == 1 and row["run_status"] == "REJECTED"


# --- storage ----------------------------------------------------------------

def test_documents_are_stored_under_the_case_when_one_exists(db):
    case_id, token = make_case(db)
    run_id = db.create_run("Sundaram", base_submission())
    db.attach_run_to_case(case_id, run_id)
    assert db.storage_prefix(run_id) == f"onboarding/{case_id}"
    key = db.save_document(run_id, "bank_proof", ".pdf", GOOD_PDF)
    assert key == f"onboarding/{case_id}/bank_proof.pdf"


def test_documents_fall_back_to_a_run_prefix_without_a_case(db):
    run_id = db.create_run("Direct Ltd", base_submission())
    assert db.storage_prefix(run_id) == f"runs/{run_id}"


def test_a_stored_document_reads_back_byte_identical(db):
    run_id = db.create_run("Sundaram", base_submission())
    db.save_document(run_id, "bank_proof", ".pdf", GOOD_PDF)
    path = db.saved_documents(run_id)["bank_proof"]
    assert path.read_bytes() == GOOD_PDF


def test_one_case_cannot_see_another_cases_documents(db):
    a_id, a_token = make_case(db, "Alpha LLP")
    b_id, b_token = make_case(db, "Beta LLP")
    a_run = db.create_run("Alpha LLP", base_submission())
    b_run = db.create_run("Beta LLP", base_submission())
    db.attach_run_to_case(a_id, a_run)
    db.attach_run_to_case(b_id, b_run)
    db.save_document(a_run, "bank_proof", ".pdf", b"%PDF-alpha")
    db.save_document(b_run, "bank_proof", ".pdf", b"%PDF-beta")

    assert db.saved_documents(a_run)["bank_proof"].read_bytes() == b"%PDF-alpha"
    assert db.saved_documents(b_run)["bank_proof"].read_bytes() == b"%PDF-beta"
    assert db.storage_prefix(a_run) != db.storage_prefix(b_run)


def test_vendor_upload_lands_under_its_own_case(anon_client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "PENDING")
    case_id, token = make_case(db)
    vendor_post(anon_client, token, files={
        "bank_proof": ("cheque.pdf", GOOD_PDF, "application/pdf")})
    run_id = db.get_case(case_id)["run_id"]
    assert db.storage_prefix(run_id) == f"onboarding/{case_id}"
    assert db.saved_documents(run_id)["bank_proof"].read_bytes() == GOOD_PDF


@pytest.mark.parametrize("filename,data", [
    ("cheque.exe", b"MZ-not-a-pdf"),
    ("cheque.txt", b"plain text"),
    ("cheque.pdf", b"MZ-wrong-magic-bytes"),
    ("cheque.pdf", b""),
])
def test_invalid_vendor_uploads_never_reach_storage(anon_client, db, monkeypatch,
                                                    filename, data):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "PENDING")
    case_id, token = make_case(db)
    vendor_post(anon_client, token, files={
        "bank_proof": (filename, data, "application/octet-stream")})
    run_id = db.get_case(case_id)["run_id"]
    assert "bank_proof" not in db.saved_documents(run_id)
    assert any(e["event_type"] == "upload_rejected" for e in db.get_events(run_id))


def test_oversized_vendor_upload_is_rejected(anon_client, db, monkeypatch):
    from ai import extract
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "PENDING")
    case_id, token = make_case(db)
    huge = b"%PDF-1.4" + b"A" * (extract.MAX_UPLOAD_BYTES + 1)
    vendor_post(anon_client, token, files={
        "bank_proof": ("huge.pdf", huge, "application/pdf")})
    run_id = db.get_case(case_id)["run_id"]
    assert "bank_proof" not in db.saved_documents(run_id)
    reasons = [json.loads(e["detail_json"])["reason"] for e in db.get_events(run_id)
               if e["event_type"] == "upload_rejected"]
    assert any("limit" in r for r in reasons)


def test_storage_backend_is_driven_only_by_environment():
    import config
    saved = (config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    try:
        config.SUPABASE_URL = ""
        assert config.storage_backend() == "local"
        config.SUPABASE_URL = "https://project.supabase.co"
        config.SUPABASE_SERVICE_ROLE_KEY = "service-key"
        assert config.storage_backend() == "supabase"
    finally:
        config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY = saved


def test_the_session_reports_signed_in_and_signing_out_needs_no_server(client):
    body = client.get("/api/session").json()
    assert body["authenticated"] is True
    assert client.post("/api/logout").status_code == 404   # nothing to end


def test_log_records_with_mapping_args_survive_redaction():
    """A %(name)s-style record carries a dict; rebuilding it as a tuple would
    destroy the record and emit a logging traceback."""
    import logging
    import app
    rec = logging.LogRecord("uvicorn.error", 20, "", 0, "config: %(database)s",
                            {"database": "postgres", "storage": "supabase"}, None)
    assert app._RedactVendorToken().filter(rec)
    assert rec.getMessage() == "config: postgres"


def test_the_service_role_key_never_reaches_the_frontend():
    """It is a server-side credential. The templates it must not reach are a
    React bundle now, and the bundle ships to the browser — so the rule is if
    anything stricter."""
    for source in frontend_sources():
        src = source.read_text(encoding="utf-8")
        for forbidden in ("SUPABASE_SERVICE_ROLE_KEY", "service_role",
                          "DATABASE_URL"):
            assert forbidden not in src, f"{source.name} references {forbidden}"
        # Naming the variable on the login screen is fine; shipping its value
        # to the browser is not.
        import config
        assert config.APP_PASSWORD not in src, source.name


def test_supabase_requests_carry_the_key_only_in_headers():
    """The credential belongs in a header, never in a URL that could be logged."""
    import config
    from data import storage
    saved = (config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    try:
        config.SUPABASE_URL = "https://project.supabase.co"
        config.SUPABASE_SERVICE_ROLE_KEY = "service-key-value"
        assert "service-key-value" not in storage._sb_url("object", "bucket", "k")
        headers = storage._sb_headers()
        assert headers["Authorization"] == "Bearer service-key-value"
    finally:
        config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY = saved


# --- deployment shape -------------------------------------------------------

def test_a_submission_is_processed_before_the_response_returns(client, anon_client,
                                                              db, monkeypatch):
    """One execution mode. A background task does not outlive a serverless
    response, so deferring the work would silently lose it once deployed."""
    from engine import pipeline
    calls = []
    monkeypatch.setattr(pipeline, "run", lambda run_id, **k: calls.append(run_id))

    url = client.post("/api/onboardings",
                      json={"vendor_name": "Inline Ltd"}).json()["vendor_url"]
    token = url.rsplit("/", 1)[1]
    anon_client.post(f"/api/vendor/onboard/{token}",
                     data={"legal_entity_name": "Inline Ltd"})
    assert len(calls) == 1, "the pipeline must have run inside the request"


def test_there_is_no_execution_mode_to_configure():
    import config
    from engine import pipeline
    assert not hasattr(config, "PIPELINE_MODE")
    assert not hasattr(pipeline, "DEMO_PAUSE_S")


def test_vercel_is_told_which_asgi_app_to_serve():
    """Vercel's FastAPI detection finds both `backend/app.py` and the shim, and
    refuses to guess. Naming it is what makes the deploy build at all."""
    import tomllib
    cfg = tomllib.load(open(repo("pyproject.toml"), "rb"))
    assert cfg["tool"]["vercel"]["entrypoint"] == "api.index:app"


def test_the_serverless_entrypoint_is_the_same_app_as_local():
    """No second application, no Vercel-only behaviour."""
    src = repo("api", "index.py").read_text(encoding="utf-8")
    assert "from app import app" in src and "backend" in src


def test_the_function_can_outlive_a_default_timeout_and_ships_the_bundle():
    cfg = json.loads(repo("vercel.json").read_text(encoding="utf-8"))
    fn = cfg["functions"]["api/index.py"]
    # The pipeline runs inside the request: five model calls, tens of seconds.
    # Vercel's 10s default would abort every real submission.
    assert fn["maxDuration"] >= 60
    # The React bundle lives outside the entrypoint's tree, so it has to be
    # named explicitly or the function ships without a UI.
    assert fn["includeFiles"].startswith("frontend/dist")
    # `builds` and `functions` are mutually exclusive; having both fails outright.
    assert "builds" not in cfg


def test_the_two_dependency_lists_agree():
    """`pyproject.toml` is what Vercel may install from and `requirements.txt`
    is what a developer installs from. Drift between them is a deployment that
    behaves unlike anything anyone tested."""
    import tomllib
    declared = set(tomllib.load(open(repo("pyproject.toml"), "rb"))
                   ["project"]["dependencies"])
    pinned = {line.strip() for line in
              repo("requirements.txt").read_text(encoding="utf-8").splitlines()
              if line.strip() and not line.startswith("#")}
    assert declared == pinned, declared ^ pinned


def test_every_package_directory_is_a_real_package():
    """A missing `__init__.py` makes a namespace package that imports fine and
    exposes nothing — which is exactly how a gitignore typo took the deployment
    down while every local test passed."""
    for name in ("routes", "ai", "engine", "data"):
        init = backend(name, "__init__.py")
        assert init.exists(), f"backend/{name} has no __init__.py"
        assert not _is_git_ignored(init), f"backend/{name}/__init__.py is gitignored"


def _is_git_ignored(path) -> bool:
    import subprocess
    return subprocess.run(["git", "check-ignore", "-q", str(path)],
                          cwd=repo(), capture_output=True).returncode == 0


def test_no_source_file_is_hidden_from_git():
    """The scratch-script ignore rule must not swallow real source."""
    for path in (backend("routes", "_shared.py"), backend("engine", "rules.py"),
                 repo("api", "index.py")):
        assert not _is_git_ignored(path), f"{path} is gitignored"


def test_env_example_documents_every_required_variable():
    import pathlib
    text = repo(".env.example").read_text(encoding="utf-8")
    for key in ("ANTHROPIC_API_KEY", "DATABASE_URL", "SUPABASE_URL",
                "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_BUCKET", "APP_PASSWORD",
                ):
        assert f"{key}=" in text, key
    # Variables the app no longer reads must not linger in the example either.
    for gone in ("SECRET_KEY", "EMPLOYEES", "ACCESS_TOKEN_TTL_MINUTES"):
        assert gone not in text, f"{gone} is no longer used"
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            assert line.split("=", 1)[1].strip() in NON_SECRET_DEFAULTS, line


def test_env_file_is_loaded_into_config(tmp_path, monkeypatch):
    """A .env is inert unless something reads it — config.py must do so."""
    import importlib
    import config
    env = tmp_path / ".env"
    env.write_text("SUPABASE_BUCKET=from-dotenv", encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.delenv("SUPABASE_BUCKET", raising=False)
    from dotenv import load_dotenv
    load_dotenv(env, override=False)
    importlib.reload(config)
    assert config.SUPABASE_BUCKET == "from-dotenv"
    monkeypatch.delenv("SUPABASE_BUCKET", raising=False)
    importlib.reload(config)


def test_real_environment_wins_over_the_env_file(monkeypatch):
    """A shell export or a Vercel setting must override .env, never the reverse."""
    import inspect
    import config
    src = inspect.getsource(config)
    assert "override=False" in src


def test_a_database_url_with_an_unencoded_at_sign_is_detected():
    """psycopg splits userinfo at the first '@': an unencoded password silently
    becomes part of the hostname and the password comes back empty."""
    import psycopg
    bad = "postgresql://postgres:@Secret123@db.ref.supabase.co:5432/postgres"
    parsed = psycopg.conninfo.conninfo_to_dict(bad)
    assert parsed.get("password") in (None, "")
    assert parsed["host"].startswith("Secret123@")

    good = "postgresql://postgres:%40Secret123@db.ref.supabase.co:5432/postgres"
    parsed = psycopg.conninfo.conninfo_to_dict(good)
    assert parsed["password"] == "@Secret123"
    assert parsed["host"] == "db.ref.supabase.co"


def test_gitignore_still_covers_secrets_and_state():
    import pathlib
    text = repo(".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "vendor.db", "uploads/", "__pycache__/"):
        assert entry in text


# ============================================================================
# Phase 5/6 — AI Employee, dashboard correctness, UI
# ============================================================================

def sample_findings(*severities):
    return [{"rule_id": f"R0{i}", "severity": sev, "stage": "consistency",
             "message": f"finding {i}", "expected": None, "actual": None, "tag": None}
            for i, sev in enumerate(severities, 1)]


# --- capability surface ------------------------------------------------------

def test_the_ai_employee_exposes_exactly_the_documented_capabilities():
    from ai import employee as ai_employee
    assert set(ai_employee.CAPABILITIES) == {
        "extract_document", "review_extraction", "review_findings", "assess_risk",
        "summarize_vendor", "recommend_next_action"}
    for name in ai_employee.CAPABILITIES:
        assert callable(getattr(ai_employee, name)), name


def test_the_ai_employee_has_no_database_or_shell_access():
    """It orchestrates capabilities; it does not reach for tools."""
    import ast
    import pathlib
    tree = ast.parse(backend("ai/employee.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    # `ai` is its own package (extract), `engine` is the rule engine. Neither
    # gives it a database handle, a shell, or arbitrary tool use.
    assert imported <= {"json", "time", "dataclasses", "ai", "engine"}
    for forbidden in ("store", "data", "sqlite3", "psycopg", "subprocess", "os",
                      "eval", "exec"):
        assert forbidden not in imported, forbidden


# --- capability: extraction --------------------------------------------------

def test_extract_document_delegates_to_the_existing_extractor(monkeypatch):
    from ai import employee as ai_employee
    from ai import extract
    seen = []
    monkeypatch.setattr(extract, "extract_document",
                        lambda p, t: seen.append((p, t)) or ({"x": 1}, {"m": 1}))
    data, meta = ai_employee.extract_document("cheque.pdf", "bank_proof")
    assert seen == [("cheque.pdf", "bank_proof")]
    assert data == {"x": 1}


# --- capability: risk (deterministic) ---------------------------------------

@pytest.mark.parametrize("severities,expected", [
    ((), "Low"),
    (("FIX",), "Medium"),
    (("FIX", "FIX"), "Medium"),
    (("BLOCK",), "High"),
    (("FIX", "BLOCK"), "High"),
])
def test_risk_follows_finding_severity(severities, expected):
    from ai import employee as ai_employee
    assert ai_employee.assess_risk(sample_findings(*severities)) == expected


def test_risk_assessment_makes_no_model_call(monkeypatch):
    from ai import employee as ai_employee
    from ai import extract
    monkeypatch.setattr(extract, "client", lambda: (_ for _ in ()).throw(
        AssertionError("risk must be deterministic")))
    assert ai_employee.assess_risk(sample_findings("BLOCK")) == "High"


# --- capabilities: review / summary / recommendation -------------------------

def fake_anthropic_review(payload):
    class Resp:
        content = [type("B", (), {"type": "text", "text": json.dumps(payload)})()]
        usage = type("U", (), {"input_tokens": 100, "output_tokens": 50})()

    return type("C", (), {"messages": type("M", (), {
        "create": staticmethod(lambda **kw: Resp())})()})


REVIEW_PAYLOAD = {
    "risk_rationale": "Two documents are missing.",
    "summary": "Acme submitted a registration with gaps.",
    "key_points": ["Contact phone missing", "Address proof unreadable"],
    "recommended_action": "Request the missing information.",
    "extraction_notes": ["The IFSC was blank on the cheque."],
}


def test_review_produces_every_documented_facet():
    from ai import employee as ai_employee
    result, meta = ai_employee.review(
        "Acme LLP", "PENDING", sample_findings("FIX", "FIX"),
        client=fake_anthropic_review(REVIEW_PAYLOAD))

    assert result.risk == "Medium"
    assert ai_employee.summarize_vendor(result) == "Acme submitted a registration with gaps."
    assert ai_employee.review_findings(result) == ["Contact phone missing",
                                                   "Address proof unreadable"]
    assert ai_employee.recommend_next_action(result) == "Request the missing information."
    assert ai_employee.review_extraction(result) == ["The IFSC was blank on the cheque."]
    assert meta["usage"]["input_tokens"] == 100
    assert meta["model"]


def test_review_risk_is_the_deterministic_one_not_the_models():
    """Even if the narrative sounds reassuring, the level is arithmetic."""
    from ai import employee as ai_employee
    payload = dict(REVIEW_PAYLOAD, risk_rationale="Nothing to worry about.")
    result, _ = ai_employee.review("Acme LLP", "REJECTED", sample_findings("BLOCK"),
                                   client=fake_anthropic_review(payload))
    assert result.risk == "High"


def test_the_review_prompt_forbids_contradicting_the_decision():
    from ai import employee as ai_employee
    p = ai_employee.REVIEW_PROMPT
    assert "you cannot change it" in p
    assert "Never contradict the decision" in p
    assert "Never suggest overriding it" in p
    assert "internal note, not a message to the vendor" in p


def test_review_serialises_for_storage():
    from ai import employee as ai_employee
    result, _ = ai_employee.review("Acme LLP", "PENDING", sample_findings("FIX"),
                                   client=fake_anthropic_review(REVIEW_PAYLOAD))
    blob = json.dumps(result.as_dict())
    assert json.loads(blob)["risk"] == "Medium"


# --- the AI Employee cannot move the decision --------------------------------

def test_ai_employee_cannot_override_a_block(db):
    """R09 rejects. Whatever the AI says, the status stays REJECTED."""
    from engine import pipeline

    def flattering_review(vendor_name, status, findings, comparisons=None):
        from ai import employee as ai_employee
        return ai_employee.Review(
            risk="Low", risk_rationale="Looks fine to me.",
            summary="Everything is in order. Recommend APPROVED.",
            key_points=[], recommended_action="Approve this vendor.",
            extraction_notes=[]), {"purpose": "ai_employee:review",
                                   "model": "fake", "input_summary": "x",
                                   "usage": {"input_tokens": 1, "output_tokens": 1}}

    mismatch = dict(FAKE_DOCS["bank_proof"], account_holder_name="S. Ramesh Kumar")
    run_id = db.create_run("Sundaram Industrial Supplies LLP",
                           base_submission(account_holder_name="S. Ramesh Kumar"))
    attach(db, run_id, *FAKE_DOCS)
    status = pipeline.run(run_id, today=TODAY,
                          review_fn=flattering_review,
                          extract_fn=fake_extractor(overrides={"bank_proof": mismatch}))

    assert status == "REJECTED"
    assert db.get_run(run_id)["status"] == "REJECTED"
    assert [f["rule_id"] for f in db.get_findings(run_id)] == ["R09"]


def test_the_review_runs_after_the_decision_is_persisted(db):
    from engine import pipeline
    run_id, _ = execute(db, base_submission(contact_phone=""))
    kinds = [(e["stage"], e["event_type"]) for e in db.get_events(run_id)]
    decision_at = kinds.index(("decision", "decision"))
    review_at = kinds.index(("review", "stage_started"))
    assert decision_at < review_at


def test_ai_review_failure_leaves_the_decision_intact(db):
    """An advisory briefing is not worth losing a correct decision."""
    from engine import pipeline

    def exploding_review(*a, **k):
        raise RuntimeError("model unavailable")

    run_id = db.create_run("Sundaram Industrial Supplies LLP",
                           base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    status = pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                          review_fn=exploding_review)

    assert status == "PENDING"
    assert db.get_run(run_id)["status"] == "PENDING"
    assert [f["rule_id"] for f in db.get_findings(run_id)] == ["R01"]
    failed = [e for e in db.get_events(run_id)
              if e["event_type"] == "capability_failed"]
    assert len(failed) == 1
    outcome = next(json.loads(e["detail_json"])["outcome"] for e in db.get_events(run_id)
                   if e["stage"] == "review" and e["event_type"] == "stage_completed")
    assert outcome == "unavailable"


# --- auditability ------------------------------------------------------------

def test_every_ai_employee_operation_is_audited(db):
    from engine import pipeline
    run_id = db.create_run("Sundaram Industrial Supplies LLP",
                           base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 review_fn=fake_reviewer())

    events = db.get_events(run_id)
    review_call = next(e for e in events
                       if e["stage"] == "review" and e["event_type"] == "ai_call")
    detail = json.loads(review_call["detail_json"])
    for field in ("purpose", "capability", "model", "input_summary",
                  "usage", "duration_ms", "risk"):
        assert field in detail, field
    assert review_call["actor"] == "ai_employee"
    assert review_call["ts"]
    assert review_call["run_id"] == run_id


def test_the_ai_summary_is_persisted_as_an_event(db):
    from engine import pipeline
    run_id = db.create_run("Sundaram Industrial Supplies LLP",
                           base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 review_fn=fake_reviewer())
    summary = next(json.loads(e["detail_json"]) for e in db.get_events(run_id)
                   if e["event_type"] == "ai_summary")
    assert summary["risk"] == "Medium"
    assert summary["recommended_action"]


def test_ai_employee_audit_never_carries_a_secret(db):
    from engine import pipeline
    run_id = db.create_run("Sundaram Industrial Supplies LLP", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 review_fn=fake_reviewer())
    blob = json.dumps([dict(e) for e in db.get_events(run_id)])
    assert "sk-ant" not in blob
    assert TEST_PASSWORD not in blob


# --- the AI summary is employee-only -----------------------------------------

def test_the_ai_summary_reaches_the_run_detail(client, db):
    from engine import pipeline
    run_id = db.create_run("Sundaram Industrial Supplies LLP",
                           base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 review_fn=fake_reviewer())
    summary = client.get(f"/api/runs/{run_id}").json()["ai_summary"]
    assert summary["summary"]
    assert summary["recommended_action"]
    assert summary["risk"] == "Medium"


def test_the_vendor_never_sees_the_ai_summary(anon_client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "PENDING")
    case_id, token = make_case(db)
    vendor_post(anon_client, token)
    run_id = db.get_case(case_id)["run_id"]
    db.add_event(run_id, "review", "ai_summary", actor="ai_employee", detail={
        "risk": "High", "summary": "Bank account mismatch detected",
        "key_points": ["Bank holder differs"], "recommended_action": "Do not proceed",
        "risk_rationale": "r", "extraction_notes": []})

    # The confirmation page is gone: the same endpoint now reports the spent
    # link, so that one payload is the whole of what a vendor can still read.
    body = anon_client.get(f"/api/vendor/onboard/{token}").text
    for leak in ("AI Employee", "risk", "Risk", "High",
                 "Do not proceed", "Bank account mismatch", "recommended"):
        assert leak not in body, f"{leak} leaked to the vendor"


# --- dashboard: metrics and columns unchanged --------------------------------

# The columns React renders are fields on the row now, not <th> text: the
# backend still decides what a dashboard row consists of.
EXPECTED_METRICS = ["total", "approved", "pending", "rejected"]
EXPECTED_CASE_COLUMNS = ["case_id", "vendor_name", "status_label", "finding_count", "created_at", "last_activity_at"]
EXPECTED_RUN_COLUMNS = ["run_id", "vendor_name", "status_label", "finding_count",
                        "created_at", "duration_ms"]


def test_dashboard_metrics_are_unchanged(client, db):
    execute(db, base_submission())
    stats = client.get("/api/dashboard").json()["stats"]
    for metric in EXPECTED_METRICS:
        assert metric in stats, metric


def test_dashboard_table_columns_are_unchanged(client, db):
    make_case(db)
    execute(db, base_submission())
    body = client.get("/api/dashboard").json()
    for column in EXPECTED_CASE_COLUMNS:
        assert column in body["cases"][0], column
    for column in EXPECTED_RUN_COLUMNS:
        assert column in body["runs"][0], column


def test_dashboard_stats_count_cases_not_runs(client, db):
    """The tiles sit above the case table, so they have to agree with it. A case
    that has been through a correction has two runs and is still one vendor."""
    approved, _ = execute(db, base_submission())
    pending, _ = execute(db, base_submission(contact_phone=""))
    for run_id, vendor in ((approved, "Approved Ltd"), (pending, "Pending Ltd")):
        case_id = db.create_case(vendor, "c", "c@e.in", db.new_token())
        db.attach_run_to_case(case_id, run_id)
    # A second run on the pending case: still one row, still counted once.
    second, _ = execute(db, base_submission(contact_phone=""))
    db.attach_run_to_case(db.list_cases()[0]["id"], second)

    stats = client.get("/api/dashboard").json()["stats"]
    assert len(db.list_runs()) == 3, "three runs..."
    assert stats["total"] == 2, "...but two cases"
    assert stats["approved"] == 1 and stats["pending"] == 1
    assert stats["rejected"] == 0


def test_a_case_with_no_run_counts_as_awaiting_the_vendor(client, db):
    db.create_case("Fresh Ltd", "c", "c@e.in", db.new_token())
    stats = client.get("/api/dashboard").json()["stats"]
    assert stats["total"] == 1 and stats["awaiting_vendor"] == 1


# --- dashboard: created / last activity --------------------------------------

def test_case_without_a_run_reports_creation_as_last_activity(db):
    case_id, _ = make_case(db)
    row = db.list_cases()[0]
    assert row["run_id"] is None
    assert row["created_at"]
    assert row["last_activity_at"] == row["created_at"]


def test_case_with_a_run_reports_the_latest_event(db):
    from engine import pipeline
    case_id, _ = make_case(db)
    run_id = db.create_run("Sundaram", base_submission(contact_phone=""))
    db.attach_run_to_case(case_id, run_id)
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 review_fn=fake_reviewer())

    row = db.list_cases()[0]
    last_event = db.get_events(run_id)[-1]
    assert row["last_activity_at"] == last_event["ts"]
    assert row["last_activity_at"] >= row["created_at"]
    assert row["last_event_type"] == "run_finished"


def test_last_activity_never_precedes_creation(db):
    from engine import pipeline
    for submission in (base_submission(), base_submission(contact_phone="")):
        case_id, _ = make_case(db)
        run_id = db.create_run("v", submission)
        db.attach_run_to_case(case_id, run_id)
        attach(db, run_id, *FAKE_DOCS)
        pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                     review_fn=fake_reviewer())
    for row in db.list_cases():
        assert row["last_activity_at"] >= row["created_at"]


def test_timestamps_are_formatted_by_the_backend():
    from routes._shared import format_timestamp
    assert format_timestamp("2026-09-05T14:14:07+00:00") == "Sep 5, 2026 · 2:14 PM"
    assert format_timestamp("2026-01-09T09:05:00+00:00") == "Jan 9, 2026 · 9:05 AM"
    assert format_timestamp("2026-01-09T00:30:00+00:00") == "Jan 9, 2026 · 12:30 AM"
    assert format_timestamp("2026-01-09T12:00:00+00:00") == "Jan 9, 2026 · 12:00 PM"
    assert format_timestamp(None) == "—"
    assert format_timestamp("not a date") == "—"


def test_no_raw_iso_timestamp_is_ever_handed_over_for_display(client, db):
    """Formatted for reading, server-side. The exact value still travels, but in
    a separate `_iso` field the UI uses for tooltips and sorting — never as the
    thing a human is shown."""
    make_case(db)
    execute(db, base_submission())
    run_id = db.list_runs()[0]["run_id"]

    case = client.get("/api/dashboard").json()["cases"][0]
    run = client.get("/api/dashboard").json()["runs"][0]
    detail = client.get(f"/api/runs/{run_id}").json()
    for shown in (case["created_at"], case["last_activity_at"], run["created_at"],
                  detail["created_at"], detail["events"][0]["ts_display"]):
        assert "+00:00" not in shown, shown
    assert "+00:00" in case["created_at_iso"]              # precision retained
    assert "+00:00" in detail["events"][0]["ts"]


def test_key_points_are_exceptions_not_confirmations():
    from ai import employee as ai_employee
    p = ai_employee.REVIEW_PROMPT
    assert "one short line per *failed check*" in p
    assert "Do not add lines for checks that" in p


def test_dashboard_renders_every_case_state(client, db, monkeypatch):
    from engine import pipeline
    make_case(db, "Awaiting Ltd")                                  # no run

    for name, submission in [("Approved Ltd", base_submission()),
                             ("Pending Ltd", base_submission(contact_phone="")),
                             ("Rejected Ltd", base_submission(pan="ABCFS1234Z"))]:
        case_id, _ = make_case(db, name)
        run_id = db.create_run(name, submission)
        db.attach_run_to_case(case_id, run_id)
        attach(db, run_id, *FAKE_DOCS)
        pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                     review_fn=fake_reviewer())

    cases = client.get("/api/dashboard").json()["cases"]
    assert {c["status_label"] for c in cases} == {"Awaiting vendor", "Approved",
                                                  "Pending", "Rejected"}
    names = {c["vendor_name"] for c in cases}
    assert "Awaiting Ltd" in names and "Rejected Ltd" in names


# --- routing and navigation --------------------------------------------------

def test_the_app_shell_is_public_and_the_dashboard_data_is_not(anon_client, client):
    """Routing moved into React: `/` is the bundle, served to anyone, and the
    only thing worth protecting is the data it then asks for."""
    assert anon_client.get("/").status_code != 401
    assert anon_client.get("/api/dashboard").status_code == 401
    assert set(client.get("/api/dashboard").json()) >= {"stats", "cases", "runs"}


@pytest.mark.parametrize("path", EMPLOYEE_ROUTES)
def test_one_token_opens_every_employee_endpoint(client, path):
    """One password opens every employee screen; there are no roles."""
    assert client.get(path).status_code == 200
    assert client.get("/api/session").json()["authenticated"] is True


def test_the_vendor_payload_shares_nothing_with_the_employee_one(anon_client, db):
    """The separate vendor layout, expressed as data: these keys and no others."""
    _, token = make_case(db)
    body = anon_client.get(f"/api/vendor/onboard/{token}").json()
    assert set(body) == {"state", "correcting", "vendor_name", "contact_name",
                         "schema", "prefill", "max_upload_mb", "accepted_types"}


def test_the_run_detail_carries_what_the_page_links_to(client, db):
    run_id, _ = execute(db, base_submission())
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["run_id"] == run_id
    # A run created directly in the store, with no onboarding case behind it.
    assert body["case_id"] is None



def test_case_creation_offers_the_vendor_link_actions(client, db):
    """Everything the "created" screen needs: a link to copy and open, and the
    form version it was created against."""
    body = client.post("/api/onboardings", json={"vendor_name": "Sundaram LLP"}).json()
    assert body["vendor_url"].startswith("http")
    assert "/vendor/onboard/" in body["vendor_url"]
    assert body["form"]["template_name"]
    # The isolation guarantee the screen states, asserted rather than printed.
    token = token_from(body["vendor_url"])
    assert client.get(f"/api/vendor/onboard/{token}").status_code == 200
    from fastapi.testclient import TestClient
    import app
    anonymous = TestClient(app.app)
    assert anonymous.get("/api/dashboard").status_code == 401


# --- states ------------------------------------------------------------------

def test_empty_states_are_helpful(client):
    """Nothing yet is an answer, not a 404: React needs the empty lists to know
    which invitation to show."""
    body = client.get("/api/dashboard").json()
    assert body["cases"] == [] and body["runs"] == []
    assert body["stats"]["total"] == 0


def test_a_filtered_empty_state_still_says_what_the_filter_was(client, db):
    execute(db, base_submission())               # APPROVED, so REJECTED is empty
    body = client.get("/api/dashboard?status=REJECTED").json()
    assert body["runs"] == []
    assert body["active"] == "REJECTED"          # what to offer clearing
    assert "REJECTED" in body["statuses"]


def test_errors_stay_generic_and_traceback_free(client):
    r = client.get("/api/runs/VS-9999")
    assert r.status_code == 404
    assert "Traceback" not in r.text and "psycopg" not in r.text
    assert r.json()["detail"] == "That run does not exist."


def test_processing_stages_are_real_backend_state(client, db):
    """The stage list comes from the pipeline, not from hardcoded UI strings."""
    from engine import pipeline
    run_id, _ = execute(db, base_submission())
    stages = client.get(f"/api/runs/{run_id}").json()["stages"]
    assert [s["label"] for s in stages] == [label for _, label, _ in pipeline.STAGES]
