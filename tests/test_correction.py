"""The correction cycle: PENDING -> vendor fixes it -> a second decision.

The loop the brief describes only closes if a vendor has somewhere to send the
corrections. What has to hold: the ask is generated from *this* run's findings,
the vendor comes back to the same case rather than a new one, the previous
decision and audit trail survive untouched, and nothing is ever sent without a
person clicking.
"""

import json

import pytest
from conftest import backend as backend_path

from engine import policy
from engine import rules

TODAY = __import__("datetime").date(2026, 9, 6)


def scenario(name):
    return json.loads(backend_path("samples", name + ".json").read_text("utf-8"))


def run_scenario(db, name):
    from test_rules import fake_reviewer, fixture_extractor
    from engine import pipeline

    spec = scenario(name)
    run_id = db.create_run(spec["submission"]["legal_entity_name"], spec["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())
    status = pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                          review_fn=fake_reviewer())
    return run_id, status


# ============================================================================
# What the vendor is asked to do (deterministic, per finding)
# ============================================================================

def finding(rule_id, message, **kw):
    return rules.Finding(rule_id, kw.pop("severity", rules.FIX),
                         "consistency", message, **kw)


@pytest.mark.parametrize("f,expected", [
    (finding("R01", "Required field 'registered_address' is missing"),
     "Please provide the registered address."),
    (finding("R02", "Required document 'Certificate of incorporation or "
                    "business registration' was not attached"),
     "Please upload your certificate of incorporation or business registration."),
    (finding("R13", "The file attached as 'Cancelled cheque or bank letter' does "
                    "not look like that kind of document",
             expected="Cancelled cheque or bank letter", actual="salary slip"),
     "Please upload the correct cancelled cheque or bank letter — the file "
     "attached appears to be salary slip instead."),
    (finding("R03", "PAN format is invalid", expected="AAAAA9999A", actual="ABC1"),
     "Please check the PAN — 'ABC1' is not valid (it should look like AAAAA9999A)."),
])
def test_each_kind_of_problem_gets_its_own_instruction(f, expected):
    """No generic "your submission is incomplete" anywhere: the ask names the
    thing to do and the thing to do it to."""
    assert policy.correction_request(f) == expected


def test_an_unreadable_document_asks_for_a_replacement_not_a_correction():
    f = finding("R14", "Nothing could be read from the PAN card — it may be "
                       "blurred, cropped, or a scan of the wrong page")
    text = policy.correction_request(f)
    assert text.startswith("Please re-upload a clearer copy")
    assert "PAN card" in text


def test_a_mismatch_names_both_sides_so_the_vendor_can_see_which_is_wrong():
    f = finding("R18", "The address proof is for a different state than the "
                       "registered address", expected="Karnataka",
                actual="Maharashtra")
    text = policy.correction_request(f)
    assert "Karnataka" in text and "Maharashtra" in text
    assert "address proof" in text


def test_no_instruction_ever_leaks_a_rule_id():
    for rule in rules.RULES:
        f = finding(rule.id, "Something was wrong", expected="a", actual="b")
        assert rule.id not in policy.correction_request(f)


def test_only_fixable_findings_become_asks():
    """A blocking finding is not a correction request, and an uncertain one is
    for a human - never for the vendor."""
    items = policy.correction_items([
        finding("R09", "Blocking", severity=rules.BLOCK),
        finding("R09", "Unsure", tag="ai_uncertain"),
        finding("R01", "Required field 'contact_phone' is missing"),
    ])
    assert [i["rule_id"] for i in items] == ["R01"]


def test_the_ask_list_is_this_run_s_findings_and_nothing_else(db):
    run_id, status = run_scenario(db, "ec2_incomplete")
    assert status == "PENDING"
    items = policy.correction_items(db.get_findings(run_id))
    assert len(items) == len([f for f in db.get_findings(run_id)
                              if f["severity"] == rules.FIX])
    assert any("contact phone number" in i["text"] for i in items)
    assert any("certificate of incorporation" in i["text"].lower() for i in items)


# ============================================================================
# PENDING reopens the case; REJECTED and APPROVED do not
# ============================================================================

def make_case_run(db, name):
    """A run that belongs to a real onboarding case, the way a vendor makes one."""
    from test_rules import fake_reviewer, fixture_extractor
    from engine import pipeline

    spec = scenario(name)
    token = db.new_token()
    case_id = db.create_case("Sundaram Industrial Supplies LLP", "Priya",
                             "priya@example.in", token)
    run_id = db.create_run("Sundaram Industrial Supplies LLP", spec["submission"])
    db.attach_run_to_case(case_id, run_id)
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())
    status = pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                          review_fn=fake_reviewer())
    return case_id, run_id, status


def test_pending_records_what_to_ask_but_does_not_reopen_the_form(db):
    """A decision landing overnight must not silently make a case submittable
    again. The engine records the ask; a reviewer opens the form."""
    case_id, run_id, status = make_case_run(db, "ec2_incomplete")
    assert status == "PENDING"
    assert db.get_case(case_id)["status"] == db.PROCESSING
    assert "correction_requested" in [e["event_type"] for e in db.get_events(run_id)]


def test_an_unsure_comparison_lands_pending_and_stays_correctable(client, db):
    """The ambiguity path, end to end, on an otherwise complete submission.

    Nothing is missing and nothing contradicts: the only thing wrong is that the
    comparator could not tell whether the bank letter names the vendor. The model
    does not get to answer that with a status, so it becomes a FIX, `decide()`
    turns that into PENDING, and the case remains reopenable for a correction.

    The uncertain finding is deliberately kept off the *vendor's* ask list — our
    uncertainty is not their homework — so the reviewer sees it and decides.
    """
    from test_rules import fake_reviewer, fixture_extractor
    from engine import pipeline

    spec = scenario("ec1_happy")
    token = db.new_token()
    case_id = db.create_case("Sundaram Industrial Supplies LLP", "Priya",
                             "priya@example.in", token)
    run_id = db.create_run("Sundaram Industrial Supplies LLP", spec["submission"])
    db.attach_run_to_case(case_id, run_id)
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())

    def unsure(a, b):
        return rules.NameVerdict(None, score=0.85, reason="model confidence 0.55")

    status = pipeline.run(run_id, today=TODAY, names_match=unsure,
                          extract_fn=fixture_extractor, review_fn=fake_reviewer())

    assert status == "PENDING"                       # not APPROVED, not REJECTED
    findings = db.get_findings(run_id)
    uncertain = [f for f in findings if f.get("tag") == "ai_uncertain"]
    assert uncertain, "an unsure comparison must leave a finding behind"
    assert {f["severity"] for f in uncertain} == {rules.FIX}
    assert not [f for f in findings if f["severity"] == rules.BLOCK]

    # A human decides this one, so it is not itemised to the vendor...
    assert policy.correction_items(findings) == []
    # ...but the case is still PENDING, so the reviewer can reopen it for one.
    assert client.post(f"/api/onboardings/{case_id}/reopen").status_code == 200
    assert db.get_case(case_id)["status"] == db.AWAITING_VENDOR


def test_a_reviewer_opens_the_form_on_the_cases_existing_url(client, db):
    """No new case, no new link — the gate moves and nothing else."""
    case_id, _, _ = make_case_run(db, "ec2_incomplete")
    before = db.get_case(case_id)["token"]

    r = client.post(f"/api/onboardings/{case_id}/reopen")
    assert r.status_code == 200 and r.json()["open"] is True
    assert r.json()["vendor_url"].endswith(before)      # same token, same URL

    case = db.get_case(case_id)
    assert case["status"] == db.AWAITING_VENDOR and case["token"] == before


def test_a_form_that_is_already_open_cannot_be_opened_again(client, db):
    case_id, _, _ = make_case_run(db, "ec2_incomplete")
    client.post(f"/api/onboardings/{case_id}/reopen")
    assert client.post(f"/api/onboardings/{case_id}/reopen").status_code == 409


@pytest.mark.parametrize("name", ["ec1_happy", "ec3_bank_mismatch"])
def test_a_decided_case_is_not_reopened(db, name):
    """Approved needs nothing and rejected must not invite a resubmission."""
    case_id, _, status = make_case_run(db, name)
    assert status in ("APPROVED", "REJECTED")
    assert db.get_case(case_id)["status"] == db.PROCESSING


# ============================================================================
# The round trip
# ============================================================================

def test_a_correction_creates_a_second_run_on_the_same_case(client, anon_client,
                                                            db, monkeypatch):
    from engine import pipeline
    case_id, first_run, _ = make_case_run(db, "ec2_incomplete")
    # Only the resubmission is stubbed: the first run has to really execute,
    # because reaching PENDING is what reopens the case.
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)

    link = client.post(f"/api/onboardings/{case_id}/reopen").json()
    assert link["open"] is True
    token = link["vendor_url"].rsplit("/", 1)[1]

    form = anon_client.get(f"/api/vendor/onboard/{token}").json()
    assert form["state"] == "open" and form["correcting"] is True

    anon_client.post(f"/api/vendor/onboard/{token}",
                     data={"legal_entity_name": "Sundaram Industrial Supplies LLP",
                           "contact_phone": "+91 80 4123 7788"})

    runs = db.runs_for_case(case_id)
    assert len(runs) == 2, "a correction must not start a new case"
    assert runs[0]["run_id"] == first_run
    assert db.get_case(case_id)["run_id"] == runs[1]["run_id"]


def test_the_previous_decision_and_audit_trail_survive(client, anon_client, db,
                                                       monkeypatch):
    from engine import pipeline
    case_id, first_run, _ = make_case_run(db, "ec2_incomplete")
    # Only the resubmission is stubbed: the first run has to really execute,
    # because reaching PENDING is what reopens the case.
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    before = {"status": db.get_run(first_run)["status"],
              "findings": len(db.get_findings(first_run)),
              "events": len(db.get_events(first_run))}

    token = client.post(f"/api/onboardings/{case_id}/reopen").json()["vendor_url"].rsplit("/", 1)[1]
    anon_client.post(f"/api/vendor/onboard/{token}", data={"contact_phone": "x"})

    after = {"status": db.get_run(first_run)["status"],
             "findings": len(db.get_findings(first_run)),
             "events": len(db.get_events(first_run))}
    assert before == after


def test_the_correction_form_is_prefilled_with_what_they_sent(client, anon_client,
                                                              db, monkeypatch):
    """A correction changes what was wrong. Retyping fifteen fields is how a
    *new* transcription error gets introduced."""
    from engine import pipeline
    case_id, _, _ = make_case_run(db, "ec2_incomplete")
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    token = client.post(f"/api/onboardings/{case_id}/reopen").json()["vendor_url"].rsplit("/", 1)[1]

    prefill = anon_client.get(f"/api/vendor/onboard/{token}").json()["prefill"]
    assert prefill["gstin"] == "29ABCFS1234K1Z3"
    assert prefill["account_number"] == "50200071234567"
    # The blank that made it Pending stays blank - it is what they must fill in.
    assert not prefill.get("contact_phone")


def test_a_first_submission_is_not_prefilled_with_a_previous_one(client,
                                                                 anon_client, db):
    r = client.post("/api/onboardings", json={"vendor_name": "Fresh Ltd"})
    token = r.json()["vendor_url"].rsplit("/", 1)[1]
    body = anon_client.get(f"/api/vendor/onboard/{token}").json()
    assert body["correcting"] is False
    assert set(body["prefill"]) == {"legal_entity_name", "contact_name",
                                    "contact_email"}


def test_documents_already_uploaded_survive_into_the_correction(client,
                                                                anon_client, db,
                                                                monkeypatch):
    """The vendor replaces what was wrong, not everything they ever sent."""
    from engine import pipeline
    case_id, first_run, _ = make_case_run(db, "ec2_incomplete")
    # Only the resubmission is stubbed: the first run has to really execute,
    # because reaching PENDING is what reopens the case.
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    before = set(db.saved_documents(first_run))
    assert before

    token = client.post(f"/api/onboardings/{case_id}/reopen").json()["vendor_url"].rsplit("/", 1)[1]
    anon_client.post(f"/api/vendor/onboard/{token}", data={"contact_phone": "x"})

    second = db.runs_for_case(case_id)[1]["run_id"]
    assert set(db.saved_documents(second)) == before


def test_a_link_cannot_be_issued_for_a_decided_case(client, db):
    case_id, _, _ = make_case_run(db, "ec3_bank_mismatch")
    r = client.post(f"/api/onboardings/{case_id}/reopen")
    assert r.status_code == 409


# ============================================================================
# What the run page is handed
# ============================================================================

def test_the_run_page_is_told_what_to_ask_and_that_the_form_can_be_opened(
        client, db):
    _, run_id, _ = make_case_run(db, "ec2_incomplete")
    body = client.get(f"/api/runs/{run_id}").json()

    correction = body["correction"]
    # Not open yet, but a reviewer may open it — and the URL is already known,
    # because it is the case's own and never changes.
    assert correction["awaiting_correction"] is False
    assert correction["can_reopen"] is True
    assert correction["vendor_url"].startswith("http")
    assert correction["status_label"] == "Corrections not requested yet"
    assert correction["items"], "the page must be told what to ask for"
    assert all("R0" not in i["text"] and "R1" not in i["text"]
               for i in correction["items"])


@pytest.mark.parametrize("name", ["ec3_bank_mismatch", "ec4_crossfield"])
def test_a_rejected_run_is_never_offered_a_correction_flow(client, db, name):
    """EC-4 is the case that matters: it is REJECTED but still carries two FIX
    findings, and those must not become an offer to correct."""
    _, run_id, _ = make_case_run(db, name)
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["correction"]["awaiting_correction"] is False
    assert body["correction"]["can_reopen"] is False
    assert body["correction"]["items"] == []
    assert body["correction"]["status_label"] is None


def test_the_rounds_are_visible_once_there_is_more_than_one(client, anon_client,
                                                            db, monkeypatch):
    from engine import pipeline
    case_id, first_run, _ = make_case_run(db, "ec2_incomplete")
    # Only the resubmission is stubbed: the first run has to really execute,
    # because reaching PENDING is what reopens the case.
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    assert client.get(f"/api/runs/{first_run}").json()["rounds"] == []

    token = client.post(f"/api/onboardings/{case_id}/reopen").json()["vendor_url"].rsplit("/", 1)[1]
    anon_client.post(f"/api/vendor/onboard/{token}", data={"contact_phone": "x"})

    rounds = client.get(f"/api/runs/{first_run}").json()["rounds"]
    assert [r["round"] for r in rounds] == [1, 2]
    assert [r["current"] for r in rounds] == [True, False]


def test_the_vendor_still_learns_nothing_about_the_decision(client, anon_client,
                                                            db):
    """Reopening a case must not turn the portal into a status page."""
    case_id, _, _ = make_case_run(db, "ec2_incomplete")
    token = client.post(f"/api/onboardings/{case_id}/reopen").json()["vendor_url"].rsplit("/", 1)[1]
    body = anon_client.get(f"/api/vendor/onboard/{token}").text
    for leak in ("PENDING", "R01", "R02", "finding", "Finding", "correction_requested"):
        assert leak not in body, leak
