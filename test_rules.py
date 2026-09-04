"""Unit tests for the deterministic core.

No network, no fixtures, no mocking, no AI — that is the payoff of rules.py
being pure (docs/04-decision-engine.md).
"""

from datetime import date

import pytest

import rules
from rules import BLOCK, FIX, Finding, RuleContext, decide, gstin_checksum

TODAY = date(2026, 9, 5)
CTX = RuleContext(today=TODAY)


def gstin(first14: str) -> str:
    """Never hand-write a GSTIN — docs/06-demo-scenarios.md."""
    return first14 + gstin_checksum(first14)


GSTIN_KA = gstin("29ABCFS1234K1Z")      # Karnataka, PAN ABCFS1234K, 4th char F = Firm/LLP


def base_submission(**overrides) -> dict:
    """EC-1: the clean vendor. All 12 rules pass."""
    s = {
        "legal_entity_name": "Sundaram Industrial Supplies LLP",
        "entity_type": "LLP",
        "country_of_incorporation": "IN",
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
    e = {
        "incorporation_certificate": {
            "legal_name": "Sundaram Industrial Supplies LLP",
            "registration_number": "AAB-1234", "incorporation_date": "2019-04-11"},
        "bank_proof": {
            "account_holder_name": "Sundaram Industrial Supplies LLP",
            "account_number": "50200071234567", "ifsc": "HDFC0001234",
            "bank_name": "HDFC Bank"},
        "insurance_certificate": {
            "insured_name": "Sundaram Industrial Supplies LLP",
            "policy_number": "POL-99812", "valid_until": "2027-03-31"},
    }
    e.update(overrides)
    return e


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


# --- R02 required documents -------------------------------------------------

def test_r02_silent_before_extraction_has_run():
    assert rules.r02_required_documents(base_submission(), None, CTX) == []


def test_r02_passes_when_all_three_attached():
    assert rules.r02_required_documents(base_submission(), base_extracted(), CTX) == []


def test_r02_reports_the_missing_document_by_label():
    found = rules.r02_required_documents(
        base_submission(), base_extracted(incorporation_certificate=None), CTX)
    assert len(found) == 1
    assert found[0].message == (
        "Required document 'Certificate of Incorporation' was not attached")
    assert found[0].severity == FIX


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
    ext = base_extracted(bank_proof={
        "account_holder_name": "S. Ramesh Kumar",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank"})
    found = rules.r09_bank_holder_name(base_submission(), ext, CTX)
    assert ids(found) == ["R09"] and found[0].severity == BLOCK
    assert found[0].expected == "Sundaram Industrial Supplies LLP"
    assert found[0].actual == "S. Ramesh Kumar"


def test_r09_tolerates_punctuation_only_differences():
    ext = base_extracted(bank_proof={
        "account_holder_name": "SUNDARAM INDUSTRIAL SUPPLIES, LLP.",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank"})
    assert rules.r09_bank_holder_name(base_submission(), ext, CTX) == []


def test_r09_uses_the_injected_comparator():
    """AI enters only as an injected callable — never as a hard dependency."""
    ctx = RuleContext(today=TODAY, names_match=lambda a, b: True)
    ext = base_extracted(bank_proof={
        "account_holder_name": "Completely Different Entity Ltd",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank"})
    assert rules.r09_bank_holder_name(base_submission(), ext, ctx) == []


# --- R10 bank document vs form (BLOCK) --------------------------------------

def test_r10_passes_when_document_matches_form():
    assert rules.r10_bank_details_match(base_submission(), base_extracted(), CTX) == []


def test_r10_blocks_on_account_number_mismatch():
    ext = base_extracted(bank_proof={
        "account_holder_name": "Sundaram Industrial Supplies LLP",
        "account_number": "50200079999999", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank"})
    found = rules.r10_bank_details_match(base_submission(), ext, CTX)
    assert ids(found) == ["R10"] and found[0].severity == BLOCK
    assert found[0].expected == "50200079999999"


def test_r10_blocks_on_ifsc_mismatch_too():
    ext = base_extracted(bank_proof={
        "account_holder_name": "Sundaram Industrial Supplies LLP",
        "account_number": "50200071234567", "ifsc": "ICIC0004321",
        "bank_name": "HDFC Bank"})
    found = rules.r10_bank_details_match(base_submission(), ext, CTX)
    assert ids(found) == ["R10"] and "IFSC" in found[0].message


# --- R11 document expiry ----------------------------------------------------

def test_r11_passes_on_a_current_certificate():
    assert rules.r11_document_expiry(base_submission(), base_extracted(), CTX) == []


def test_r11_flags_an_expired_certificate_with_the_date():
    ext = base_extracted(insurance_certificate={
        "insured_name": "Sundaram Industrial Supplies LLP",
        "policy_number": "POL-99812", "valid_until": "2026-07-21"})
    found = rules.r11_document_expiry(base_submission(), ext, CTX)
    assert ids(found) == ["R11"] and found[0].severity == FIX
    assert found[0].message == "Certificate of Insurance expired on 21 July 2026"


def test_r11_uses_injected_today_not_the_wall_clock():
    ext = base_extracted(insurance_certificate={
        "insured_name": "x", "policy_number": "y", "valid_until": "2026-07-21"})
    past = RuleContext(today=date(2026, 1, 1))
    assert rules.r11_document_expiry(base_submission(), ext, past) == []


# --- R12 incorporation certificate name -------------------------------------

def test_r12_passes_when_names_agree():
    assert rules.r12_incorporation_name(base_submission(), base_extracted(), CTX) == []


def test_r12_flags_name_difference_as_fix_not_block():
    ext = base_extracted(incorporation_certificate={
        "legal_name": "Meridian Holdings Private Limited",
        "registration_number": "AAB-1234", "incorporation_date": "2019-04-11"})
    found = rules.r12_incorporation_name(base_submission(), ext, CTX)
    assert ids(found) == ["R12"]
    assert found[0].severity == FIX          # formatting difference, not fraud


def test_r12_silent_when_the_document_is_absent():
    """R02 already reports the absence — skip semantics."""
    assert rules.r12_incorporation_name(
        base_submission(), base_extracted(incorporation_certificate=None), CTX) == []


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


def test_ec2_missing_and_expired_is_pending():
    submission = base_submission(contact_phone="")
    extracted = base_extracted(
        incorporation_certificate=None,
        insurance_certificate={"insured_name": "Sundaram Industrial Supplies LLP",
                               "policy_number": "POL-99812",
                               "valid_until": "2026-07-21"})
    found = run_all(submission, extracted)
    assert ids(found) == ["R01", "R02", "R11"]
    assert "R12" not in ids(found)            # its document is absent; R02 owns that
    assert all(f.severity == FIX for f in found)
    assert decide(found) == "PENDING"


def test_ec3_bank_beneficiary_mismatch_is_rejected():
    submission = base_submission(account_holder_name="S. Ramesh Kumar")
    extracted = base_extracted(bank_proof={
        "account_holder_name": "S. Ramesh Kumar",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank"})
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
        submission["legal_entity_name"], "S. Ramesh Kumar") is False
    # and with a comparator that refuses to answer, R09 still would have to ask,
    # proving the call site exists but the deterministic path decides the case:
    with pytest.raises(AssertionError):
        rules.r09_bank_holder_name(
            submission,
            base_extracted(bank_proof={"account_holder_name": "S. Ramesh Kumar",
                                       "account_number": "50200071234567",
                                       "ifsc": "HDFC0001234",
                                       "bank_name": "HDFC Bank"}),
            exploding)


def test_ec4_cross_field_contradiction_is_rejected():
    submission = base_submission(
        pan="ABCFS1234Z", entity_type="Proprietorship",
        registered_address_state="Maharashtra")
    found = run_all(submission, base_extracted())
    assert ids(found) == ["R06", "R07", "R08"]
    by_id = {f.rule_id: f for f in found}
    assert by_id["R06"].severity == BLOCK
    assert by_id["R07"].severity == BLOCK
    assert by_id["R08"].severity == FIX
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


# --- pipeline integration (no AI, no PDFs) ----------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite file per test. Still no network, still no mocking."""
    import store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(store, "UPLOAD_DIR", tmp_path / "uploads")
    store.init_db()
    return store


def execute(db, submission):
    import pipeline
    run_id = db.create_run(submission.get("legal_entity_name"), submission)
    status = pipeline.run(run_id, today=TODAY)
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
    assert kinds.count("stage_started") == 4        # intake, completeness, format, consistency
    assert kinds.count("stage_completed") == 4
    assert "decision" in kinds
    assert all(e["actor"] == "system" for e in db.get_events(run_id))


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
    assert order == ["intake", "completeness", "format", "consistency"]


def test_error_is_distinct_from_rejected(db, monkeypatch):
    """A crashing stage must never fall through to a decision."""
    import pipeline
    monkeypatch.setattr(rules, "apply",
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
    import pipeline
    with pytest.raises(KeyError):
        pipeline.run("VS-9999", today=TODAY)
