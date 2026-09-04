"""Unit tests for the deterministic core.

No network, no fixtures, no mocking, no AI — that is the payoff of rules.py
being pure (docs/04-decision-engine.md).
"""

import json
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
        submission["legal_entity_name"], "S. Ramesh Kumar").match is False
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
    status = pipeline.run(run_id, today=TODAY, draft_fn=fake_drafter())
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
    # intake, completeness, format, consistency, communicate (no docs -> no extraction)
    assert kinds.count("stage_started") == 5
    assert kinds.count("stage_completed") == 5
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
    assert order == ["intake", "completeness", "format", "consistency", "communicate"]


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


# --- stage 3 extraction wiring (offline: the extractor is injected) ---------

FAKE_DOCS = {
    "incorporation_certificate": {
        "legal_name": "Sundaram Industrial Supplies LLP",
        "registration_number": "AAB-1234", "incorporation_date": "2019-04-11"},
    "bank_proof": {
        "account_holder_name": "Sundaram Industrial Supplies LLP",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank Limited"},
    "insurance_certificate": {
        "insured_name": "Sundaram Industrial Supplies LLP",
        "policy_number": "POL-99812", "valid_until": "2027-03-31"},
}


def fake_extractor(calls=None, overrides=None):
    def _fn(path, doc_type):
        if calls is not None:
            calls.append((path.name, doc_type))
        data = dict((overrides or {}).get(doc_type, FAKE_DOCS[doc_type]))
        return data, {"purpose": f"extract:{doc_type}", "model": "fake-model",
                      "input_summary": path.name, "raw_response": json.dumps(data),
                      "usage": {"input_tokens": 100, "output_tokens": 20}}
    return _fn


def attach(db, run_id, *doc_types):
    d = db.upload_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    for t in doc_types:
        (d / f"{t}.pdf").write_bytes(b"%PDF-1.4 stub")
    return d


def execute_with_docs(db, submission, doc_types, overrides=None, calls=None):
    import pipeline
    run_id = db.create_run(submission.get("legal_entity_name"), submission)
    attach(db, run_id, *doc_types)
    status = pipeline.run(run_id, today=TODAY, draft_fn=fake_drafter(),
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
    ai = [e for e in db.get_events(run_id) if e["event_type"] == "ai_call"]
    assert len(ai) == 3
    for e in ai:
        detail = json.loads(e["detail_json"])
        assert detail["purpose"].startswith("extract:")
        assert detail["model"] and detail["raw_response"]
        assert detail["usage"]["input_tokens"] > 0
        assert e["stage"] == "extraction"


def test_extraction_is_skipped_when_no_documents_were_submitted(db):
    """JSON-only submissions carry no documents, so R02 must stay silent."""
    run_id, status = execute(db, base_submission())
    assert db.get_run(run_id)["extracted"] is None
    assert not any(e["event_type"] == "ai_call" for e in db.get_events(run_id))
    assert status == "APPROVED"


def test_missing_attachment_is_reported_without_calling_the_model(db):
    calls = []
    run_id, status = execute_with_docs(
        db, base_submission(), ["bank_proof", "insurance_certificate"], calls=calls)
    assert [c[1] for c in calls] == ["bank_proof", "insurance_certificate"]
    assert db.get_run(run_id)["extracted"]["incorporation_certificate"] is None
    found = [f["rule_id"] for f in db.get_findings(run_id)]
    assert found == ["R02"]
    assert status == "PENDING"


def test_a_null_extracted_field_never_becomes_a_finding(db):
    """An honest null is missing data, not a contradiction."""
    partial = dict(FAKE_DOCS["bank_proof"], ifsc=None, account_number=None)
    run_id, status = execute_with_docs(
        db, base_submission(), FAKE_DOCS.keys(), overrides={"bank_proof": partial})
    assert db.get_findings(run_id) == []
    assert status == "APPROVED"


def test_extraction_failure_yields_error_not_a_status(db):
    import pipeline
    run_id = db.create_run("Boom Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)

    def exploding(path, doc_type):
        raise RuntimeError("api timeout")

    assert pipeline.run(run_id, today=TODAY, extract_fn=exploding,
                        draft_fn=fake_drafter()) == "ERROR"
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
                     "consistency", "communicate"]


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


def test_extraction_model_cannot_set_a_status(db):
    """Whatever the extractor returns, the status comes from decide()."""
    import pipeline
    run_id = db.create_run("Sneaky Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)

    def liar(path, doc_type):
        data = dict(FAKE_DOCS[doc_type])
        data["status"] = "APPROVED"          # ignored: not in the schema's fields
        return data, {"purpose": "x", "model": "m", "input_summary": "i",
                      "raw_response": "{}", "usage": {"input_tokens": 1,
                                                      "output_tokens": 1}}
    assert pipeline.run(run_id, today=TODAY, extract_fn=liar) == "APPROVED"
    import rules as r
    assert list(inspect_params(r.decide)) == ["findings"]


def inspect_params(fn):
    import inspect
    return inspect.signature(fn).parameters


# --- extract.py schema contract (no network) --------------------------------

def test_extract_schemas_match_the_documented_shapes():
    import extract
    assert set(extract.DOC_TYPES) == set(extract.SCHEMAS) == set(extract.PROMPTS)
    expected = {
        "incorporation_certificate": {"legal_name", "registration_number",
                                      "incorporation_date"},
        "bank_proof": {"account_holder_name", "account_number", "ifsc", "bank_name"},
        "insurance_certificate": {"insured_name", "policy_number", "valid_until"},
    }
    for doc_type, fields in expected.items():
        schema = extract.SCHEMAS[doc_type]
        assert set(schema["properties"]) == fields
        assert set(schema["required"]) == fields
        assert schema["additionalProperties"] is False
        for spec in schema["properties"].values():
            assert spec["type"] == ["string", "null"]   # every field nullable


def test_every_prompt_forbids_inventing_values():
    import extract
    for prompt in extract.PROMPTS.values():
        assert "null" in prompt
        assert "Never infer, correct, complete, normalise or guess" in prompt


def test_unsupported_file_type_is_rejected():
    import extract
    with pytest.raises(ValueError):
        extract._content_block(pathlib_Path("x.docx"))


def pathlib_Path(name):
    import pathlib
    return pathlib.Path(name)


# --- matching.py: normalization and the three bands -------------------------

def boom_ask(a, b, on_ai_call=None):
    raise AssertionError("the model must not be consulted for this pair")


def test_normalize_expands_legal_form_abbreviations():
    import matching
    assert matching.normalize("Acme Tech Pvt. Ltd.") == "ACME TECHNOLOGIES PRIVATE LIMITED"
    assert matching.normalize("acme  technologies,  private limited") == \
        "ACME TECHNOLOGIES PRIVATE LIMITED"


def test_similarity_is_symmetric_and_bounded():
    import matching
    a, b = "Sundaram Industrial Supplies LLP", "Sundaram Inds. Supplies LLP"
    assert matching.similarity(a, b) == matching.similarity(b, a)
    assert 0.0 <= matching.similarity(a, b) <= 1.0
    assert matching.similarity(a, a) == 1.0
    assert matching.similarity("", "anything") == 0.0


def test_different_legal_form_is_not_the_same_entity():
    """Suffixes are expanded, never stripped: an LLP is not a Private Limited."""
    import matching
    v = matching.names_match("Meridian Logistics LLP",
                             "Meridian Logistics Private Limited", ask=boom_ask)
    assert v.match is False


@pytest.mark.parametrize("a,b", [
    ("Sundaram Industrial Supplies LLP", "Sundaram Industrial Supplies LLP"),
    ("Sundaram Industrial Supplies LLP", "SUNDARAM INDUSTRIAL SUPPLIES, LLP."),
    ("Acme Technologies Pvt Ltd", "Acme Tech Private Limited"),
])
def test_obvious_matches_never_reach_the_model(a, b):
    import matching
    v = matching.names_match(a, b, ask=boom_ask)
    assert v.match is True and v.score >= matching.MATCH_THRESHOLD


@pytest.mark.parametrize("a,b", [
    ("Sundaram Industrial Supplies LLP", "S. Ramesh Kumar"),
    ("Acme Technologies Pvt Ltd", "Acme Holdings LLC"),
])
def test_obvious_mismatches_never_reach_the_model(a, b):
    import matching
    v = matching.names_match(a, b, ask=boom_ask)
    assert v.match is False and v.score <= matching.MISMATCH_THRESHOLD


def test_only_the_ambiguous_band_escalates():
    import matching
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
    import matching

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
    import pipeline
    run_id = db.create_run("Ambiguous Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    status = pipeline.run(run_id, today=TODAY, names_match=uncertain,
                          extract_fn=fake_extractor(), draft_fn=fake_drafter())
    assert status == "PENDING"                 # not APPROVED, not REJECTED
    assert "ai_uncertain" in [f["tag"] for f in db.get_findings(run_id)]


def test_name_matching_failure_yields_error_not_approval(db):
    """An AI outage must not become a silent approval."""
    import pipeline

    def exploding(a, b):
        raise RuntimeError("anthropic timeout")

    run_id = db.create_run("Outage Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    assert pipeline.run(run_id, today=TODAY, names_match=exploding,
                        extract_fn=fake_extractor(), draft_fn=fake_drafter()) == "ERROR"
    assert db.get_run(run_id)["status"] == "ERROR"
    assert not any(e["event_type"] == "decision" for e in db.get_events(run_id))


# --- the four fixtures, end to end (offline) --------------------------------

FIXTURE_EXTRACTIONS = {
    "incorporation_certificate.pdf": {
        "legal_name": "Sundaram Industrial Supplies LLP",
        "registration_number": "AAB-1234", "incorporation_date": "2019-04-11"},
    "bank_proof.pdf": {
        "account_holder_name": "Sundaram Industrial Supplies LLP",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank Limited"},
    "bank_proof_mismatch.pdf": {
        "account_holder_name": "S. Ramesh Kumar",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank Limited"},
    "insurance_certificate.pdf": {
        "insured_name": "Sundaram Industrial Supplies LLP",
        "policy_number": "POL-99812", "valid_until": "2027-03-31"},
    "insurance_certificate_expired.pdf": {
        "insured_name": "Sundaram Industrial Supplies LLP",
        "policy_number": "POL-99812", "valid_until": "2026-07-21"},
}


def fixture_extractor(path, doc_type):
    """Replays what the real extractor returned for that source PDF in Part 3."""
    source = path.read_text(encoding="utf-8").split(":", 1)[1]
    data = dict(FIXTURE_EXTRACTIONS[source])
    return data, {"purpose": "extract:" + doc_type, "model": "replay",
                  "input_summary": source, "raw_response": json.dumps(data),
                  "usage": {"input_tokens": 1, "output_tokens": 1}}


def load_scenario(name):
    import pathlib
    return json.loads((pathlib.Path("samples") / (name + ".json")).read_text("utf-8"))


def run_scenario(db, name, calls):
    import matching
    import pipeline
    sc = load_scenario(name)
    run_id = db.create_run(sc["submission"]["legal_entity_name"], sc["submission"])
    d = db.upload_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    for doc_type, source in sc["documents"].items():
        (d / (doc_type + ".pdf")).write_text("stub:" + source, encoding="utf-8")

    def recording_ask(a, b, on_ai_call=None):
        calls.append((a, b))
        return rules.NameVerdict(False, reason="escalated")

    def counting_match(a, b):
        return matching.names_match(a, b, ask=recording_ask)

    status = pipeline.run(run_id, today=TODAY, names_match=counting_match,
                          extract_fn=fixture_extractor, draft_fn=fake_drafter())
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
    ai = [e for e in db.get_events(run_id) if e["event_type"] == "ai_call"]
    assert ai and all(json.loads(e["detail_json"])["purpose"].startswith("extract:")
                      for e in ai)


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


def test_matching_module_never_emits_findings_or_statuses():
    import pathlib
    src = pathlib.Path("matching.py").read_text(encoding="utf-8")
    assert "Finding(" not in src
    for word in ("APPROVED", "PENDING", "REJECTED"):
        assert word not in src, "matching.py must not mention " + word


# --- stage 7: communication -------------------------------------------------

def fake_drafter(calls=None):
    def _fn(run_id, vendor_name, contact_name, findings):
        if calls is not None:
            calls.append({"run_id": run_id, "vendor": vendor_name,
                          "contact": contact_name, "findings": findings})
        lines = "\n".join("%d. %s" % (i, f["message"])
                          for i, f in enumerate(findings, 1))
        text = ("Subject: Additional information needed (%s)\n\nHi %s,\n\n%s\n\n"
                "Send these over and we'll pick the review back up."
                % (run_id, contact_name.split()[0], lines))
        return text, {"purpose": "draft_followup", "model": "fake-model",
                      "input_summary": "%d finding(s)" % len(findings),
                      "raw_response": text,
                      "usage": {"input_tokens": 50, "output_tokens": 60}}
    return _fn


def run_scenario_with_draft(db, name, drafts=None):
    import matching
    import pipeline
    sc = load_scenario(name)
    run_id = db.create_run(sc["submission"]["legal_entity_name"], sc["submission"])
    d = db.upload_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    for doc_type, source in sc["documents"].items():
        (d / (doc_type + ".pdf")).write_text("stub:" + source, encoding="utf-8")

    def no_escalation(a, b, on_ai_call=None):
        raise AssertionError("no fixture should escalate")

    status = pipeline.run(
        run_id, today=TODAY, extract_fn=fixture_extractor,
        names_match=lambda a, b: matching.names_match(a, b, ask=no_escalation),
        draft_fn=fake_drafter(drafts))
    return sc, run_id, status


def test_ec2_pending_produces_an_itemised_draft(db):
    drafts = []
    sc, run_id, status = run_scenario_with_draft(db, "ec2_incomplete", drafts)
    assert status == "PENDING"

    draft = db.get_run(run_id)["followup_draft"]
    assert draft is not None
    assert draft.startswith("Subject: ")
    assert run_id in draft
    # every actionable finding is itemised, including the date
    assert "21 July 2026" in draft
    assert "Certificate of Incorporation" in draft
    assert "contact phone number" in draft      # human label, not the field name
    assert "contact_phone" not in draft
    assert len(drafts) == 1 and len(drafts[0]["findings"]) == 3


@pytest.mark.parametrize("name", ["ec1_happy", "ec3_bank_mismatch", "ec4_crossfield"])
def test_approved_and_rejected_never_get_a_draft(db, name):
    drafts = []
    sc, run_id, status = run_scenario_with_draft(db, name, drafts)
    assert status in ("APPROVED", "REJECTED")
    assert db.get_run(run_id)["followup_draft"] is None
    assert drafts == []


def test_rejected_records_an_internal_note_instead(db):
    _, run_id, status = run_scenario_with_draft(db, "ec3_bank_mismatch")
    assert status == "REJECTED"
    note = [e for e in db.get_events(run_id) if e["event_type"] == "internal_note"]
    assert len(note) == 1
    detail = json.loads(note[0]["detail_json"])
    assert detail["blocking_rules"] == ["R09"]
    assert "different name" in detail["note"]
    assert db.get_run(run_id)["followup_draft"] is None


def test_approved_run_has_no_communication_output(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec1_happy")
    outcomes = [json.loads(e["detail_json"])["outcome"]
                for e in db.get_events(run_id)
                if e["event_type"] == "stage_completed" and e["stage"] == "communicate"]
    assert outcomes == ["no_communication_needed"]


def test_vendor_wording_replaces_raw_field_names():
    """A vendor has never seen our field names; a reviewer needs them."""
    import pipeline
    f = Finding("R01", FIX, "completeness",
                "Required field 'contact_phone' is missing")
    assert pipeline._vendor_message(f) == (
        "The contact phone number was left blank on the form.")
    assert "contact_phone" not in pipeline._vendor_message(f)


def test_reviewer_wording_is_unchanged():
    found = rules.r01_required_fields(base_submission(contact_phone=""), None, CTX)
    assert found[0].message == "Required field 'contact_phone' is missing"


def test_non_r01_messages_are_passed_through_untouched():
    import pipeline
    for f in (Finding("R02", FIX, "completeness",
                      "Required document 'Certificate of Insurance' was not attached"),
              Finding("R11", FIX, "consistency",
                      "Certificate of Insurance expired on 21 July 2026")):
        assert pipeline._vendor_message(f) == f.message


def test_every_submission_field_has_a_human_label():
    assert set(rules.FIELD_LABELS) == set(rules.SUBMISSION_FIELDS)
    assert all("_" not in label for label in rules.FIELD_LABELS.values())


def test_draft_input_carries_no_raw_field_names(db):
    drafts = []
    run_scenario_with_draft(db, "ec2_incomplete", drafts)
    for item in drafts[0]["findings"]:
        for field in rules.SUBMISSION_FIELDS:
            assert field not in item["message"], field


def test_drafter_never_receives_the_raw_submission(db):
    """It gets a vendor name, a contact name, and findings. Nothing else."""
    drafts = []
    run_scenario_with_draft(db, "ec2_incomplete", drafts)
    passed = drafts[0]
    assert set(passed) == {"run_id", "vendor", "contact", "findings"}
    for f in passed["findings"]:
        assert set(f) <= {"message", "expected", "actual"}
        assert "severity" not in f and "rule_id" not in f


def test_ai_uncertain_findings_are_not_sent_to_the_vendor(db):
    """Our uncertainty is a reviewer's problem, not the vendor's."""
    import pipeline
    drafts = []
    run_id = db.create_run("Ambiguous Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    status = pipeline.run(run_id, today=TODAY, names_match=uncertain,
                          extract_fn=fake_extractor(), draft_fn=fake_drafter(drafts))
    assert status == "PENDING"
    assert "ai_uncertain" in [f["tag"] for f in db.get_findings(run_id)]
    assert drafts == []                                  # nothing actionable
    assert db.get_run(run_id)["followup_draft"] is None


def test_drafting_failure_does_not_destroy_the_decision(db):
    import pipeline

    def exploding(*a, **k):
        raise RuntimeError("anthropic down")

    sc = load_scenario("ec2_incomplete")
    run_id = db.create_run(sc["submission"]["legal_entity_name"], sc["submission"])
    d = db.upload_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    for doc_type, source in sc["documents"].items():
        (d / (doc_type + ".pdf")).write_text("stub:" + source, encoding="utf-8")

    status = pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                          draft_fn=exploding)
    assert status == "PENDING"                           # not ERROR
    assert db.get_run(run_id)["status"] == "PENDING"
    failed = [e for e in db.get_events(run_id) if e["event_type"] == "stage_failed"]
    assert len(failed) == 1 and failed[0]["stage"] == "communicate"


# --- the human send gate ----------------------------------------------------

def test_a_draft_is_not_a_sent_message(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec2_incomplete")
    run = db.get_run(run_id)
    assert run["followup_draft"] is not None
    assert run["followup_sent_at"] is None                # the gate
    assert not any(e["event_type"] == "followup_sent" for e in db.get_events(run_id))


def test_marking_sent_records_actor_and_timestamp(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec2_incomplete")
    ts = db.mark_followup_sent(run_id, db.get_run(run_id)["followup_draft"])
    db.add_event(run_id, "communicate", "followup_sent", actor="user:priya",
                 detail={"actor": "priya", "edited": False})
    run = db.get_run(run_id)
    assert run["followup_sent_at"] == ts
    sent = [e for e in db.get_events(run_id) if e["event_type"] == "followup_sent"]
    assert len(sent) == 1 and sent[0]["actor"] == "user:priya"


def test_editing_before_sending_is_recorded(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec2_incomplete")
    original = db.get_run(run_id)["followup_draft"]
    edited = original + "\n\nPS: please also confirm your GST filing frequency."
    db.mark_followup_sent(run_id, edited)
    assert db.get_run(run_id)["followup_draft"] == edited
    assert db.get_run(run_id)["followup_draft"] != original


# --- export -----------------------------------------------------------------

def build_export(db, run_id):
    import app
    return app.export_run(run_id)


def test_export_contains_every_documented_section(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec2_incomplete")
    ex = build_export(db, run_id)
    assert set(ex) == {"run", "submission", "extracted", "findings",
                       "communication", "events", "exported_at"}
    assert ex["run"]["run_id"] == run_id
    assert ex["run"]["status"] == "PENDING"
    assert ex["submission"]["legal_entity_name"] == "Sundaram Industrial Supplies LLP"
    assert ex["extracted"]["incorporation_certificate"] is None
    assert sorted(f["rule_id"] for f in ex["findings"]) == ["R01", "R02", "R11"]
    assert ex["events"] and all(e["detail_json"] is None for e in ex["events"])
    assert any(e["event_type"] == "decision" for e in ex["events"])


def test_export_distinguishes_drafted_from_sent(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec2_incomplete")
    before = build_export(db, run_id)["communication"]
    assert before["draft_exists"] is True
    assert before["sent"] is False and before["sent_at"] is None

    db.mark_followup_sent(run_id, before["draft"])
    db.add_event(run_id, "communicate", "followup_sent", actor="user:priya",
                 detail={"actor": "priya", "edited": False})
    after = build_export(db, run_id)["communication"]
    assert after["sent"] is True and after["sent_at"]
    assert after["sent_by"] == "priya"


def test_export_of_a_rejected_run_carries_the_internal_note_and_no_draft(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec3_bank_mismatch")
    comm = build_export(db, run_id)["communication"]
    assert comm["draft_exists"] is False and comm["draft"] is None
    assert comm["sent"] is False
    assert comm["internal_note"]["blocking_rules"] == ["R09"]


def test_export_events_are_json_decoded(db):
    _, run_id, _ = run_scenario_with_draft(db, "ec4_crossfield")
    ex = build_export(db, run_id)
    decision = next(e for e in ex["events"] if e["event_type"] == "decision")
    assert decision["detail"]["status"] == "REJECTED"
    assert decision["detail"]["rule_ids"] == ["R06", "R07", "R08"]


def test_export_missing_run_raises_404(db):
    import app
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        app.export_run("VS-9999")
    assert exc.value.status_code == 404


# --- drafting prompt guardrails ---------------------------------------------

def test_draft_prompt_forbids_inventing_requirements():
    import extract
    p = extract.DRAFT_PROMPT
    assert "do not add any requirement that is not on this list" in p
    assert "Do not state or imply an approval decision" in p
    assert "Do not mention internal rule identifiers" in p
    assert "your submission is incomplete" in p          # the anti-example
    assert set(extract.DRAFT_SCHEMA["properties"]) == {"subject", "body"}


# ============================================================================
# Part 7 — hardening
# ============================================================================

import io  # noqa: E402


# --- upload validation ------------------------------------------------------

GOOD_PDF = b"%PDF-1.4\n% real enough\n"
GOOD_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
GOOD_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40


@pytest.mark.parametrize("filename,data", [
    ("cheque.pdf", GOOD_PDF), ("scan.PDF", GOOD_PDF),
    ("scan.png", GOOD_PNG), ("photo.jpg", GOOD_JPG), ("photo.jpeg", GOOD_JPG),
])
def test_valid_uploads_are_accepted(filename, data):
    import extract
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
    import extract
    reason = extract.check_upload(filename, data)
    assert reason is not None and expect in reason


def test_oversized_upload_is_rejected():
    import extract
    big = GOOD_PDF + b"\x00" * (extract.MAX_UPLOAD_BYTES + 1)
    reason = extract.check_upload("huge.pdf", big)
    assert reason and "limit" in reason


def test_upload_rejection_reason_names_the_allowed_types():
    import extract
    reason = extract.check_upload("x.txt", b"hi")
    for ext in (".pdf", ".png", ".jpg"):
        assert ext in reason


# --- a bad attachment must not crash the run --------------------------------

class FakeUpload:
    def __init__(self, filename, data):
        self.filename = filename
        self.file = io.BytesIO(data)


def form_with(files: dict, sample: str = "") -> dict:
    form = {"sample": sample}
    form.update(files)
    return form


def test_rejected_upload_is_reported_not_saved(db):
    import app
    run_id = db.create_run("Wrong File Ltd", base_submission())
    app._save_uploads(run_id, form_with({"bank_proof": FakeUpload("x.exe", b"MZ")}))

    assert db.saved_documents(run_id) == {}
    rejected = [e for e in db.get_events(run_id) if e["event_type"] == "upload_rejected"]
    assert len(rejected) == 1
    detail = json.loads(rejected[0]["detail_json"])
    assert detail["document"] == "bank_proof"
    assert detail["filename"] == "x.exe"
    assert "not a supported file type" in detail["reason"]


def test_bad_attachment_yields_pending_not_error(db):
    """A vendor attaching the wrong file is a fixable problem, not a crash."""
    import app
    import pipeline
    run_id = db.create_run("Wrong File Ltd", base_submission())
    app._save_uploads(run_id, form_with({
        "bank_proof": FakeUpload("cheque.exe", b"MZ\x90\x00"),
        "incorporation_certificate": FakeUpload("cert.pdf", GOOD_PDF),
        "insurance_certificate": FakeUpload("ins.pdf", GOOD_PDF)}))

    status = pipeline.run(run_id, today=TODAY, draft_fn=fake_drafter(),
                          extract_fn=lambda p, t: (dict(FAKE_DOCS[t]), {
                              "purpose": "extract:" + t, "model": "fake",
                              "input_summary": p.name, "raw_response": "{}",
                              "usage": {"input_tokens": 1, "output_tokens": 1}}))
    assert status == "PENDING"                      # not ERROR
    assert [f["rule_id"] for f in db.get_findings(run_id)] == ["R02"]


def test_run_page_and_export_render_a_rejected_upload(db):
    """The rejection must survive rendering, not just persistence."""
    import app
    from fastapi.testclient import TestClient
    import pipeline

    run_id = db.create_run("Wrong File Ltd", base_submission())
    app._save_uploads(run_id, form_with({
        "bank_proof": FakeUpload("cheque.exe", b"MZ-not-a-pdf"),
        "incorporation_certificate": FakeUpload("cert.pdf", GOOD_PDF),
        "insurance_certificate": FakeUpload("ins.pdf", GOOD_PDF)}))
    pipeline.run(run_id, today=TODAY, draft_fn=fake_drafter(),
                 extract_fn=fake_extractor())

    client = TestClient(app.app)
    page = client.get("/run/" + run_id)
    assert page.status_code == 200
    assert "Attachments we could not read" in page.text
    assert "cheque.exe" in page.text
    assert "not a supported file type" in page.text

    ex = client.get("/run/" + run_id + "/export")
    assert ex.status_code == 200                      # regression: this 500'd
    assert any(e["event_type"] == "upload_rejected" for e in ex.json()["events"])


def test_a_traversal_filename_cannot_escape_the_upload_directory(db):
    import app
    run_id = db.create_run("Sneaky Ltd", base_submission())
    app._save_uploads(run_id, form_with({
        "bank_proof": FakeUpload("../../../../evil.pdf", GOOD_PDF)}))
    saved = db.saved_documents(run_id)
    assert list(saved) == ["bank_proof"]
    assert saved["bank_proof"].parent == db.upload_dir(run_id)


@pytest.mark.parametrize("bad_id", ["../etc", "VS-0001/../..", "..", "", "VS-x"])
def test_upload_dir_rejects_ids_that_are_not_run_ids(bad_id):
    import store
    with pytest.raises(ValueError):
        store.upload_dir(bad_id)


# --- secrets never reach the audit trail ------------------------------------

def test_error_details_redact_anything_key_shaped():
    import pipeline
    exc = RuntimeError("auth failed for sk-ant-api03-AbC123secretKEYvalue-xyz")
    safe = pipeline._safe_error(exc)
    assert "sk-ant-api03-AbC123secretKEYvalue-xyz" not in safe
    assert "REDACTED" in safe


def test_persisted_stage_failure_contains_no_key(db):
    import pipeline

    def exploding(path, doc_type):
        raise RuntimeError("401 from provider, key sk-ant-api03-LEAKEDKEY0000")

    run_id = db.create_run("Leaky Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=exploding, draft_fn=fake_drafter())

    blob = json.dumps([dict(e) for e in db.get_events(run_id)])
    assert "sk-ant-api03-LEAKEDKEY0000" not in blob
    assert "REDACTED" in blob


APP_MODULES = ("app.py", "pipeline.py", "rules.py", "store.py",
               "extract.py", "matching.py")


def test_no_api_key_appears_anywhere_in_application_source():
    """Scans shipped code. Test fixtures deliberately contain fake key strings."""
    import pathlib
    import re
    pattern = re.compile(r"sk-ant-[A-Za-z0-9]")
    targets = [pathlib.Path(m) for m in APP_MODULES]
    targets += list(pathlib.Path("templates").glob("*.html"))
    targets += list(pathlib.Path("samples").glob("*.py"))
    targets += list(pathlib.Path("samples").glob("*.json"))
    for path in targets:
        assert not pattern.search(path.read_text(encoding="utf-8")), path


def test_env_example_holds_no_actual_key():
    import pathlib
    text = pathlib.Path(".env.example").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=" in text
    assert text.strip().endswith("ANTHROPIC_API_KEY=")   # blank value


def test_missing_api_key_is_a_clear_error_not_a_stack_trace(monkeypatch):
    import extract
    monkeypatch.setattr(extract, "_client", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        extract.client()
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert ".env" in str(exc.value)


def test_gitignore_covers_secrets_and_runtime_state():
    import pathlib
    text = pathlib.Path(".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "vendor.db", "uploads/", "__pycache__/", ".pytest_cache/"):
        assert entry in text, entry
    assert "!.env.example" in text          # the template stays committed


# --- one failed stage must not corrupt persisted state ----------------------

def test_a_failed_stage_leaves_earlier_state_intact(db):
    """Findings written before the failure survive; nothing is half-written."""
    import pipeline

    def exploding(path, doc_type):
        raise RuntimeError("extraction died")

    run_id = db.create_run("Half Ltd", base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    assert pipeline.run(run_id, today=TODAY, extract_fn=exploding,
                        draft_fn=fake_drafter()) == "ERROR"

    run = db.get_run(run_id)
    assert run["status"] == "ERROR"
    assert run["submission"]["legal_entity_name"] == "Sundaram Industrial Supplies LLP"
    assert run["extracted"] is None                 # never half-written
    assert run["followup_draft"] is None
    assert [f["rule_id"] for f in db.get_findings(run_id)] == ["R01"]
    assert run["duration_ms"] is not None


def test_error_run_still_emits_the_terminal_marker(db):
    import pipeline
    run_id = db.create_run("Boom Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY,
                 extract_fn=lambda p, t: (_ for _ in ()).throw(RuntimeError("x")),
                 draft_fn=fake_drafter())
    finished = [e for e in db.get_events(run_id) if e["event_type"] == "run_finished"]
    assert len(finished) == 1
    assert json.loads(finished[0]["detail_json"])["status"] == "ERROR"


def test_communication_failure_never_overwrites_the_decision(db):
    import pipeline
    run_id = db.create_run("Draft Fail Ltd", base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)

    def exploding_draft(*a, **k):
        raise RuntimeError("drafting died")

    status = pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                          draft_fn=exploding_draft)
    assert status == "PENDING"
    run = db.get_run(run_id)
    assert run["status"] == "PENDING" and run["status"] != "ERROR"
    assert run["followup_draft"] is None
    assert [f["rule_id"] for f in db.get_findings(run_id)] == ["R01"]
    assert any(e["event_type"] == "decision" for e in db.get_events(run_id))


def test_a_run_after_a_failure_works_normally(db):
    """A crashed run must not poison the next one."""
    import pipeline
    bad = db.create_run("Boom Ltd", base_submission())
    attach(db, bad, *FAKE_DOCS)
    pipeline.run(bad, today=TODAY, draft_fn=fake_drafter(),
                 extract_fn=lambda p, t: (_ for _ in ()).throw(RuntimeError("x")))

    good = db.create_run("Fine Ltd", base_submission())
    attach(db, good, *FAKE_DOCS)
    assert pipeline.run(good, today=TODAY, extract_fn=fake_extractor(),
                        draft_fn=fake_drafter()) == "APPROVED"
    assert db.get_run(bad)["status"] == "ERROR"     # unchanged


# --- reset ------------------------------------------------------------------

def test_reset_clears_runs_findings_events_and_uploads(db):
    import pipeline
    run_id = db.create_run("Doomed Ltd", base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 draft_fn=fake_drafter())
    assert db.list_runs() and db.get_findings(run_id) and db.get_events(run_id)
    assert db.upload_dir(run_id).exists()

    db.reset_all()
    assert db.list_runs() == []
    assert db.get_findings(run_id) == []
    assert db.get_events(run_id) == []
    assert not db.UPLOAD_DIR.exists()
    assert db.create_run("Fresh", {}) == "VS-0001"   # ids restart


def test_reset_is_safe_when_nothing_exists(db):
    db.reset_all()
    db.reset_all()
    assert db.list_runs() == []


# --- invalid submission data ------------------------------------------------

def test_completely_empty_submission_is_pending_not_a_crash(db):
    run_id, status = execute(db, {})
    assert status == "PENDING"
    rule_ids = {f["rule_id"] for f in db.get_findings(run_id)}
    assert rule_ids == {"R01"}
    assert len(db.get_findings(run_id)) == len(rules.ALWAYS_REQUIRED)


@pytest.mark.parametrize("junk", [
    {"legal_entity_name": None}, {"legal_entity_name": 12345},
    {"gstin": "  "}, {"pan": "\t\n"}, {"entity_type": "Nonsense Ltd Type"},
    {"country_of_incorporation": "ZZ"}, {"tax_id_type": "VAT"},
])
def test_junk_field_values_never_raise(db, junk):
    """Bad data becomes findings, never a 500."""
    run_id, status = execute(db, base_submission(**junk))
    assert status in ("APPROVED", "PENDING", "REJECTED")
    assert db.get_run(run_id)["status"] == status


def test_absurdly_long_values_are_handled(db):
    run_id, status = execute(db, base_submission(legal_entity_name="A" * 20000))
    assert status in ("APPROVED", "PENDING", "REJECTED")


def test_unicode_and_quotes_survive_persistence(db):
    name = "Sündaram “Industrial” Supplies LLP <script>alert(1)</script>"
    run_id, _ = execute(db, base_submission(legal_entity_name=name))
    assert db.get_run(run_id)["submission"]["legal_entity_name"] == name


# --- HTTP surface -----------------------------------------------------------

@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient
    import app
    return TestClient(app.app)


def seeded_pending(db):
    import pipeline
    run_id = db.create_run("Sundaram Industrial Supplies LLP",
                           base_submission(contact_phone=""))
    attach(db, run_id, *FAKE_DOCS)
    pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                 draft_fn=fake_drafter())
    return run_id


def test_unknown_run_is_404_on_every_run_route(client):
    for path in ("/run/VS-9999", "/run/VS-9999/export", "/run/VS-9999/stages"):
        assert client.get(path).status_code == 404
    assert client.post("/run/VS-9999/send").status_code == 404


def test_unknown_sample_is_404(client):
    assert client.get("/samples/nope").status_code == 404


def test_browser_gets_an_html_error_page(client):
    r = client.get("/run/VS-9999", headers={"Accept": "text/html"})
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]
    assert "Back to the dashboard" in r.text
    assert "Traceback" not in r.text


def test_api_client_still_gets_json_errors(client):
    r = client.get("/run/VS-9999", headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert r.json()["detail"]


def test_send_without_a_draft_is_400(client, db):
    run_id, _ = execute(db, base_submission())          # APPROVED, no draft
    r = client.post(f"/run/{run_id}/send")
    assert r.status_code == 400 and "no follow-up draft" in r.json()["detail"]


def test_duplicate_send_is_409(client, db):
    run_id = seeded_pending(db)
    assert client.post(f"/run/{run_id}/send", json={"actor": "priya"}).status_code == 200
    r = client.post(f"/run/{run_id}/send", json={"actor": "priya"})
    assert r.status_code == 409 and "already sent" in r.json()["detail"]


def test_send_is_the_only_thing_that_sets_sent_at(client, db):
    run_id = seeded_pending(db)
    assert db.get_run(run_id)["followup_draft"] is not None
    assert db.get_run(run_id)["followup_sent_at"] is None
    client.post(f"/run/{run_id}/send", json={"actor": "priya"})
    assert db.get_run(run_id)["followup_sent_at"] is not None


def test_submit_rejects_a_non_object_json_body(client):
    assert client.post("/submit", json=["not", "an", "object"]).status_code == 400


def test_dashboard_filter_ignores_an_unknown_status(client, db):
    execute(db, base_submission())
    assert client.get("/dashboard?status=NONSENSE").status_code == 200


def test_dashboard_and_submit_render_when_empty(client):
    assert "No runs yet" in client.get("/dashboard").text
    assert client.get("/").status_code == 200


def test_pages_never_render_a_traceback(client, db):
    run_id = seeded_pending(db)
    for path in ("/", "/dashboard", f"/run/{run_id}", f"/run/{run_id}/stages"):
        text = client.get(path).text
        assert "Traceback" not in text and "sqlite3" not in text


# --- export completeness ----------------------------------------------------

def test_export_is_json_serialisable_and_complete(client, db):
    run_id = seeded_pending(db)
    r = client.get(f"/run/{run_id}/export")
    assert r.status_code == 200
    ex = r.json()
    assert set(ex) == {"run", "submission", "extracted", "findings",
                       "communication", "events", "exported_at"}
    assert len(ex["events"]) == len(db.get_events(run_id))
    assert len(ex["findings"]) == len(db.get_findings(run_id))
    assert json.dumps(ex)                                   # round-trips


def test_export_records_every_ai_call_with_usage(db):
    import app
    run_id = seeded_pending(db)
    ex = app.export_run(run_id)
    ai = [e for e in ex["events"] if e["event_type"] == "ai_call"]
    assert ai
    for e in ai:
        assert e["detail"]["model"]
        assert e["detail"]["usage"]["input_tokens"] > 0
        assert e["detail"]["raw_response"]


# --- audit trail integrity --------------------------------------------------

def test_events_are_ordered_and_attributed(db):
    run_id = seeded_pending(db)
    events = db.get_events(run_id)
    assert [e["id"] for e in events] == sorted(e["id"] for e in events)
    assert all(e["ts"] and e["actor"] and e["stage"] for e in events)
    assert all(e["actor"] == "system" for e in events)


def test_the_audit_trail_is_append_only_in_practice(db):
    """Nothing in the codebase updates or deletes an event except reset."""
    import pathlib
    src = pathlib.Path("store.py").read_text(encoding="utf-8")
    assert "UPDATE events" not in src
    deletes = [ln for ln in src.splitlines() if "DELETE FROM events" in ln]
    assert len(deletes) == 1                        # only reset_all


def test_every_stage_emits_start_and_end(db):
    run_id = seeded_pending(db)
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
    for module in ("extract.py", "matching.py"):
        src = pathlib.Path(module).read_text(encoding="utf-8")
        assert "decide(" not in src
        assert "import rules" not in src or module == "matching.py"


def test_matching_imports_only_pure_helpers_from_rules():
    """It may borrow normalisation; it may never call the decision function."""
    import pathlib
    src = pathlib.Path("matching.py").read_text(encoding="utf-8")
    assert "from rules import NameVerdict, normalize_name" in src
    assert "decide(" not in src and "rules.decide" not in src
