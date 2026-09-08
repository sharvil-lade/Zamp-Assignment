"""The decision engine as a reviewer meets it.

`test_rules.py` proves each rule is individually correct. This file proves the
things a reviewer actually depends on: that the register of checks is complete
and honest, that a skipped check is never sold as a passed one, that the plain
English on the run page is the engine's own output rather than a caption someone
typed, and that the four demo scenarios explain themselves.
"""

import json

import pytest
from conftest import backend as backend_path

from engine import policy
from engine import rules

TODAY = __import__("datetime").date(2026, 9, 6)
CTX = rules.RuleContext(today=TODAY)

SCENARIOS = ["ec1_happy", "ec2_incomplete", "ec3_bank_mismatch", "ec4_crossfield"]


def scenario(name):
    return json.loads(backend_path("samples", name + ".json").read_text("utf-8"))


# ============================================================================
# The registry is the single source of truth
# ============================================================================

def test_every_registered_rule_runs_in_a_stage():
    """A rule in the registry but not in a stage set would never execute, and a
    rule in a stage set but not the registry would run with no name, no category
    and no stated purpose on the run page."""
    registered = {r.check for r in rules.RULES}
    wired = set(rules.ALL_RULES)
    assert registered == wired
    assert len(rules.RULES) == len(rules.ALL_RULES) == 17


def test_rule_ids_are_unique_and_never_reuse_a_retired_number():
    ids = [r.id for r in rules.RULES]
    assert len(ids) == len(set(ids))
    assert not (set(ids) & rules.RETIRED_RULE_IDS), "R11 must stay retired"


def test_every_rule_states_why_it_exists():
    """`purpose` is what the run page shows a reviewer who asks 'why is this
    checked?'. A rule that cannot answer that should not be in the engine."""
    for rule in rules.RULES:
        assert rule.name and rule.name != rule.id, rule.id
        assert rule.category in rules.CATEGORY_ORDER, rule.id
        assert len(rule.purpose) > 40, f"{rule.id} has no real purpose text"
        assert set(rule.impact) <= {rules.BLOCK, rules.FIX}, rule.id


def test_every_category_is_populated_and_ordered():
    used = {r.category for r in rules.RULES}
    assert used == set(rules.CATEGORY_ORDER)


def test_stage_membership_matches_the_registry():
    for stage in (rules.STAGE_COMPLETENESS, rules.STAGE_FORMAT,
                  rules.STAGE_CONSISTENCY):
        assert tuple(r.check for r in rules.rules_for_stage(stage)) == \
            {rules.STAGE_COMPLETENESS: rules.COMPLETENESS_RULES,
             rules.STAGE_FORMAT: rules.FORMAT_RULES,
             rules.STAGE_CONSISTENCY: rules.CONSISTENCY_RULES}[stage]


# ============================================================================
# A skipped check is not a passed check
# ============================================================================

def base_submission(**over):
    submission = {
        "legal_entity_name": "Sundaram Industrial Supplies LLP", "entity_type": "LLP",
        "country_of_incorporation": "IN",
        "registered_address": "42 Industrial Layout, Bengaluru",
        "registered_address_state": "Karnataka", "contact_name": "Priya Raghavan",
        "contact_email": "priya@example.in", "contact_phone": "+91 80 4123 7788",
        "tax_id_type": "GSTIN", "gstin": "29ABCFS1234K1Z3", "pan": "ABCFS1234K",
        "account_holder_name": "Sundaram Industrial Supplies LLP",
        "account_number": "50200071234567", "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank",
    }
    return {**submission, **over}


def by_id(outcomes):
    return {o.rule.id: o.state for o in outcomes}


def test_a_rule_with_nothing_to_judge_is_skipped_not_passed():
    """The whole point of `applies`. A US vendor has no PAN, so R03 did not pass
    — it never ran, and counting it as a pass would inflate every run page."""
    us = base_submission(country_of_incorporation="US", tax_id_type="EIN",
                         gstin="", pan="", ifsc="")
    states = by_id(rules.evaluate(rules.RULES, us, None, CTX))
    for rule_id in ("R03", "R04", "R05", "R06", "R07", "R08"):
        assert states[rule_id] == rules.SKIPPED, rule_id


def test_a_rule_that_ran_and_found_nothing_is_a_pass():
    states = by_id(rules.evaluate(rules.RULES, base_submission(), None, CTX))
    assert states["R01"] == rules.PASSED
    assert states["R06"] == rules.PASSED          # GSTIN and PAN agree
    assert states["R09"] == rules.SKIPPED         # no bank document to read


def test_document_rules_skip_entirely_when_nothing_was_attached():
    states = by_id(rules.evaluate(rules.RULES, base_submission(), None, CTX))
    for rule_id in ("R13", "R14", "R17", "R09", "R10", "R12", "R15", "R16", "R18"):
        assert states[rule_id] == rules.SKIPPED, rule_id


def test_the_counts_always_add_up():
    """Nothing may fall between passed, failed and skipped — a reviewer reading
    '17 checks' must be able to account for all 17."""
    for submission in (base_submission(),
                       base_submission(country_of_incorporation="US",
                                       tax_id_type="EIN", gstin="", pan="", ifsc=""),
                       base_submission(pan="nonsense")):
        summary = rules.summarize(rules.evaluate(rules.RULES, submission, None, CTX))
        assert summary["passed"] + summary["failed"] + summary["skipped"] == \
            summary["total"] == 17
        assert summary["evaluated"] == summary["passed"] + summary["failed"]


# ============================================================================
# Evidence: `expected` is the evidence, `actual` is what the vendor typed
# ============================================================================

COMPARISON_RULES = [r for r in rules.RULES if r.evidence]


def test_every_comparison_rule_declares_where_its_two_values_came_from():
    assert {r.id for r in COMPARISON_RULES} == {
        "R06", "R07", "R08", "R09", "R10", "R12", "R15", "R16", "R18"}
    for rule in COMPARISON_RULES:
        assert len(rule.evidence) == 2 and all(rule.evidence), rule.id


def test_expected_holds_the_document_and_actual_holds_the_form():
    """One convention across every rule, which is what lets the run page label a
    side-by-side without a special case per rule."""
    extracted = {
        "bank_proof": {"document_type": "cheque", "account_holder_name": "S. Ramesh Kumar",
                       "account_number": "999", "ifsc": "HDFC0009999", "bank_name": "H"},
        "address_proof": {"document_type": "electricity bill", "name": "Someone Else",
                          "address": "x", "state": "Maharashtra"},
    }
    submission = base_submission()

    r09 = rules.r09_bank_holder_name(submission, extracted, CTX)[0]
    assert r09.expected == "S. Ramesh Kumar"                     # the cheque
    assert r09.actual == submission["legal_entity_name"]         # the form

    r10 = rules.r10_bank_details_match(submission, extracted, CTX)[0]
    assert r10.expected == "999" and r10.actual == "50200071234567"

    r18 = rules.r18_address_proof(submission, extracted, CTX)
    assert [f.expected for f in r18] == ["Someone Else", "Maharashtra"]
    assert [f.actual for f in r18] == [submission["legal_entity_name"], "Karnataka"]


# ============================================================================
# One identifier implementation, four separate findings
# ============================================================================

@pytest.mark.parametrize("kind,good,bad", [
    ("PAN", "ABCFS1234K", "ABC1234"),
    ("GSTIN", "29ABCFS1234K1Z3", "29ABCFS1234K1Z"),
    ("IFSC", "HDFC0001234", "HDFC1234"),
    ("CIN", "U74999KA2019PTC123456", "AAB-1234"),
    ("LLPIN", "AAB-1234", "U74999KA2019PTC123456"),
])
def test_one_identifier_validator_serves_every_identifier(kind, good, bad):
    assert rules.validate_identifier_format(kind, good) is None
    assert rules.validate_identifier_format(kind, bad) == rules.IDENTIFIER_FORMATS[kind][1]


def test_shared_validation_still_produces_separate_findings():
    """Unify the implementation, never the business meaning: a reviewer must
    still be told which identifier is wrong."""
    broken = base_submission(pan="nope", gstin="nope", ifsc="nope")
    found = rules.apply(rules.FORMAT_RULES, broken, None, CTX)
    assert sorted(f.rule_id for f in found) == ["R03", "R04", "R05"]
    assert len({f.message for f in found}) == 3


# ============================================================================
# The decision explains itself, without a model
# ============================================================================

def test_explain_answers_for_every_status_without_touching_anything():
    """`explain` is the engine's own words, not a model's. It has to work on a
    run where the AI Employee failed - exactly when a reviewer needs it most.
    (`rules.py` being import-pure is guarded in test_rules.py.)"""
    block = rules.Finding("R09", rules.BLOCK, rules.STAGE_CONSISTENCY, "Contradicts")
    fix = rules.Finding("R02", rules.FIX, rules.STAGE_COMPLETENESS, "Missing")
    # Each status paired with findings `decide()` could actually have seen: it
    # never returns PENDING with nothing to fix, so that pairing is not a case.
    for status, findings in [("APPROVED", []), ("PENDING", [fix]),
                             ("REJECTED", [block]), ("ERROR", []), ("RUNNING", [])]:
        out = policy.explain(status, findings)
        assert set(out) == {"headline", "summary", "reason", "next_action"}, status
        assert out["headline"] and out["summary"], status


def test_approved_reads_as_a_clearance():
    out = policy.explain("APPROVED", [])
    assert out["headline"] == "All required checks passed"
    assert "no further action" in out["next_action"].lower()


def test_rejected_names_the_contradiction_and_refuses_to_proceed():
    finding = rules.Finding("R09", rules.BLOCK, rules.STAGE_CONSISTENCY,
                            "Bank account is held in a different name than the "
                            "vendor entity")
    out = policy.explain("REJECTED", [finding])
    assert out["headline"] == "1 blocking inconsistency"
    assert out["reason"] == policy.BLOCK_REASONS[rules.CAT_BANKING]
    assert "do not proceed" in out["next_action"].lower()
    assert "R09" not in out["summary"], "the headline explanation is not rule jargon"


def test_pending_says_what_to_request():
    fixes = [rules.Finding("R02", rules.FIX, rules.STAGE_COMPLETENESS, "A missing"),
             rules.Finding("R14", rules.FIX, rules.STAGE_FORMAT, "B unreadable")]
    out = policy.explain("PENDING", fixes)
    assert out["headline"] == "2 items need correction"
    assert "correction link" in out["next_action"].lower()
    assert "A missing and B unreadable." == out["summary"]


def test_pending_on_uncertainty_alone_sends_nothing_to_the_vendor():
    """An unsure comparison is our problem, not the vendor's, so the next action
    must not be 'ask the vendor'."""
    uncertain = rules.Finding("R09", rules.FIX, rules.STAGE_CONSISTENCY,
                              "Could not confidently determine", tag="ai_uncertain")
    out = policy.explain("PENDING", [uncertain])
    assert "yourself" in out["next_action"]
    assert "request" not in out["next_action"].lower()


def test_error_is_explained_as_not_a_decision():
    out = policy.explain("ERROR", [])
    assert "not a rejection" in out["summary"].lower()


def test_explain_accepts_findings_as_they_come_back_from_the_database():
    row = {"rule_id": "R09", "severity": rules.BLOCK, "stage": "consistency",
           "message": "Bank account is held in a different name", "expected": "a",
           "actual": "b", "tag": None}
    assert policy.explain("REJECTED", [row])["headline"] == "1 blocking inconsistency"


# ============================================================================
# End to end: the four scenarios explain themselves
# ============================================================================

EXPECTED = {
    "ec1_happy": ("APPROVED", "All required checks passed"),
    "ec2_incomplete": ("PENDING", "3 items need correction"),
    "ec3_bank_mismatch": ("REJECTED", "1 blocking inconsistency"),
    "ec4_crossfield": ("REJECTED", "4 blocking inconsistencies"),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_each_scenario_produces_a_register_and_an_explanation(db, name, monkeypatch):
    from test_rules import fixture_extractor, fake_reviewer
    from engine import pipeline

    spec = scenario(name)
    run_id = db.create_run(spec["submission"]["legal_entity_name"], spec["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())

    status = pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                          review_fn=fake_reviewer())
    assert status == spec["expected_status"]

    events = {e["event_type"]: json.loads(e["detail_json"])
              for e in db.get_events(run_id) if e["detail_json"]}
    register, decision = events["checks_evaluated"], events["decision"]

    # Every rule is accounted for on every run.
    assert len(register["checks"]) == 17
    assert {c["rule_id"] for c in register["checks"]} == {r.id for r in rules.RULES}
    summary = register["summary"]
    assert summary["passed"] + summary["failed"] + summary["skipped"] == 17

    # The counts on the page are the findings in the database, not a caption.
    stored = db.get_findings(run_id)
    assert summary["blocking"] == sum(f["severity"] == rules.BLOCK for f in stored)
    assert summary["corrections"] == sum(f["severity"] == rules.FIX for f in stored)

    expected_status, expected_headline = EXPECTED[name]
    assert status == expected_status
    assert decision["explanation"]["headline"] == expected_headline
    assert decision["explanation"]["next_action"]


def test_a_failing_rule_is_recorded_as_failed_in_the_register(db):
    from test_rules import fixture_extractor, fake_reviewer
    from engine import pipeline

    spec = scenario("ec3_bank_mismatch")
    run_id = db.create_run("Sundaram", spec["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())
    pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                 review_fn=fake_reviewer())

    register = next(json.loads(e["detail_json"]) for e in db.get_events(run_id)
                    if e["event_type"] == "checks_evaluated")
    states = {c["rule_id"]: c["state"] for c in register["checks"]}
    assert states["R09"] == rules.FAILED
    assert states["R10"] == rules.PASSED       # the numbers themselves agree
    r09 = next(c for c in register["checks"] if c["rule_id"] == "R09")
    assert r09["findings"][0]["expected"] == "S. Ramesh Kumar"
    # Name, category and purpose are not written per run — they live in RULES
    # and the view model joins them back by id.
    assert set(r09) == {"rule_id", "state", "findings"}
    assert rules.BY_ID["R09"].category == rules.CAT_BANKING


# ============================================================================
# The API hands React a decision, not raw rows
# ============================================================================

def prepared_run(db):
    from test_rules import fixture_extractor, fake_reviewer
    from engine import pipeline

    spec = scenario("ec3_bank_mismatch")
    run_id = db.create_run("Sundaram Industrial Supplies LLP", spec["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())
    pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                 review_fn=fake_reviewer())
    return run_id


def test_the_run_endpoint_serves_the_decision_and_the_register(client, db):
    run_id = prepared_run(db)
    body = client.get(f"/api/runs/{run_id}").json()

    assert body["decision"]["status"] == "REJECTED"
    assert body["decision"]["headline"] == "1 blocking inconsistency"
    assert body["decision"]["next_action"]
    assert body["checks"]["summary"]["total"] == 17

    categories = [c["name"] for c in body["checks"]["categories"]]
    assert categories == [c for c in rules.CATEGORY_ORDER if c in categories]
    assert "Banking consistency" in categories


def test_findings_reach_react_with_their_rule_and_labelled_evidence(client, db):
    run_id = prepared_run(db)
    finding = next(f for f in client.get(f"/api/runs/{run_id}").json()["findings"]
                   if f["rule_id"] == "R09")

    assert finding["name"] == "Bank account holder is the vendor"
    assert finding["category"] == rules.CAT_BANKING
    assert finding["purpose"]
    # Labelled by source, so the reviewer never guesses which column is evidence.
    assert finding["evidence_rows"] == [
        {"source": "Cancelled cheque or bank letter", "value": "S. Ramesh Kumar",
         "evidence": True},
        {"source": "Vendor submission",
         "value": "Sundaram Industrial Supplies LLP", "evidence": False},
    ]


def test_the_ui_never_has_to_derive_a_verdict_of_its_own(client, db):
    """Everything the page states is computed server-side from persisted state."""
    run_id = prepared_run(db)
    body = client.get(f"/api/runs/{run_id}").json()
    for key in ("headline", "summary", "reason", "next_action"):
        assert body["decision"][key] is not None
    assert all("result" in stage for stage in body["stages"])


def test_an_errored_run_offers_no_check_register(client, db, monkeypatch):
    """A run that never reached the decision has no register. Showing an empty
    one would present checks that never ran."""
    from engine import pipeline
    monkeypatch.setattr(rules, "evaluate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    run_id = db.create_run("Crashy Corp", base_submission())
    assert pipeline.run(run_id, today=TODAY) == "ERROR"

    body = client.get(f"/api/runs/{run_id}").json()
    assert body["checks"] is None
    assert "not a rejection" in body["decision"]["summary"].lower()


# ============================================================================
# What the audit trail keeps
# ============================================================================

def test_the_audit_trail_records_the_call_not_the_answer(db):
    """The values a model read live on the run (`extracted_json`), which is what
    every rule and every reviewer actually uses. Copying the raw response into
    events as well stored the same account numbers twice for no extra answer."""
    from test_rules import fake_reviewer, fixture_extractor
    from engine import pipeline

    spec = scenario("ec1_happy")
    run_id = db.create_run("Sundaram", spec["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())
    pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                 review_fn=fake_reviewer())

    calls = [json.loads(e["detail_json"]) for e in db.get_events(run_id)
             if e["event_type"] == "ai_call"]
    assert calls, "the calls themselves are still recorded"
    for call in calls:
        assert "raw_response" not in call
        # ...but enough to audit one: which model, on what, at what cost.
        assert {"purpose", "model", "input_summary", "usage"} <= set(call)

    # And the answer itself is still there, once, where it is used.
    assert db.get_run(run_id)["extracted"]["pan_card"]["pan"]


def test_no_event_payload_is_large_enough_to_be_a_second_copy(db):
    from test_rules import fake_reviewer, fixture_extractor
    from engine import pipeline

    spec = scenario("ec1_happy")
    run_id = db.create_run("Sundaram", spec["submission"])
    db.add_event(run_id, "intake", "documents_expected")
    for doc_type, source in spec["documents"].items():
        db.save_document(run_id, doc_type, ".pdf", ("stub:" + source).encode())
    pipeline.run(run_id, today=TODAY, extract_fn=fixture_extractor,
                 review_fn=fake_reviewer())

    biggest = max(len(e["detail_json"] or "") for e in db.get_events(run_id))
    assert biggest < 2000, f"an event payload of {biggest} bytes is a copy of something"


# --- the AI summary stage summarises; it does not decide ---------------------

def test_the_briefing_echoes_the_decision_rather_than_forming_one():
    """`decision` is copied from the status and is absent from the model schema.

    So the briefing is self-contained JSON the UI can render, with no way for
    the narrative to disagree with the status it is describing.
    """
    from ai import employee as ai_employee
    assert "decision" not in ai_employee.REVIEW_SCHEMA["properties"]
    assert "decision" not in ai_employee.REVIEW_SCHEMA["required"]

    review = ai_employee.Review(decision="REJECTED")
    assert review.as_dict()["decision"] == "REJECTED"


def test_a_rogue_briefing_cannot_add_remove_or_reword_findings(db):
    """The strongest guarantee: whatever the model returns, the record is unchanged.

    This reviewer contradicts the decision, invents a finding and denies a real
    one. The run must keep the status `decide()` set and exactly the findings the
    rules produced.
    """
    from ai import employee as ai_employee
    from engine import pipeline

    def rogue(vendor_name, status, findings, comparisons=None, **kwargs):
        return ai_employee.Review(
            decision="APPROVED",                      # contradicts the engine
            risk=ai_employee.RISK_LOW,
            risk_rationale="looks fine to me",
            summary="Approved. No concerns.",
            key_points=["Invented: vendor is on a sanctions list",
                        "Ignore the bank account finding, it is a typo"],
            recommended_action="Onboard immediately.",
        ), {"purpose": "ai_employee:review", "model": "rogue"}

    from test_rules import FAKE_DOCS, attach, fake_extractor

    run_id = db.create_run("Rogue Ltd", base_submission())
    attach(db, run_id, *FAKE_DOCS)
    status = pipeline.run(
        run_id, today=TODAY,
        names_match=lambda a, b: rules.NameVerdict(False),   # forces R09 BLOCK
        extract_fn=fake_extractor(), review_fn=rogue)

    assert status == "REJECTED"
    assert db.get_run(run_id)["status"] == "REJECTED"
    rule_ids = sorted({f["rule_id"] for f in db.get_findings(run_id)})
    assert "R09" in rule_ids
    # Nothing the reviewer said became a finding.
    assert all("sanctions" not in f["message"].casefold()
               for f in db.get_findings(run_id))


def test_the_briefing_runs_after_the_status_is_durable(db):
    """Stage 7 is downstream of the decision, so it cannot delay or change it."""
    from engine import pipeline

    from test_rules import FAKE_DOCS, attach, fake_extractor

    seen = {}
    run_id = db.create_run("Durable Ltd", base_submission())

    def reviewer(vendor_name, status, findings, comparisons=None, **kwargs):
        # Read the row back: the status must already be durable at this point,
        # not merely computed and waiting on this call to succeed.
        seen["persisted"] = db.get_run(run_id)["status"]
        raise RuntimeError("model unavailable")

    attach(db, run_id, *FAKE_DOCS)
    status = pipeline.run(run_id, today=TODAY, extract_fn=fake_extractor(),
                          review_fn=reviewer)

    # The briefing blew up; the decision survived it.
    assert status == "APPROVED"
    assert db.get_run(run_id)["status"] == "APPROVED"
    assert seen["persisted"] == "APPROVED"


# --- the rules / policy boundary --------------------------------------------

def test_policy_owns_the_decision_and_rules_owns_the_checks():
    """The three-layer claim, asserted rather than described.

    AI interprets documents -> rules validate evidence -> policy decides.
    `rules.py` may not know that `policy.py` exists, which is what keeps it
    standard-library-only and therefore unable to reach a model.
    """
    import ast

    from engine import policy
    src = backend_path("engine/rules.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "policy" not in imported and "engine" not in imported and "ai" not in imported

    # The policy layer lives in exactly one place.
    for name in ("decide", "explain", "assess_risk", "correction_request",
                 "correction_items", "BLOCK_REASONS"):
        assert hasattr(policy, name), name
        assert not hasattr(rules, name), f"{name} still in rules.py"


def test_decide_maps_severity_to_status_and_nothing_else():
    from engine import policy
    block = rules.Finding("R09", rules.BLOCK, rules.STAGE_CONSISTENCY, "blocking")
    fix = rules.Finding("R01", rules.FIX, rules.STAGE_COMPLETENESS, "fixable")

    assert policy.decide([]) == "APPROVED"
    assert policy.decide([fix]) == "PENDING"
    assert policy.decide([block]) == "REJECTED"
    # Precedence: one blocking finding outweighs any number of fixable ones.
    assert policy.decide([fix, fix, block]) == "REJECTED"


def test_assess_risk_follows_severity_after_the_move():
    from engine import policy
    assert policy.assess_risk([]) == policy.RISK_LOW
    assert policy.assess_risk([rules.Finding("R01", rules.FIX, rules.STAGE_COMPLETENESS, "x")]) == policy.RISK_MEDIUM
    assert policy.assess_risk([rules.Finding("R09", rules.BLOCK, rules.STAGE_CONSISTENCY, "x")]) == policy.RISK_HIGH
    # Dicts read back from the database work the same as live findings.
    assert policy.assess_risk([{"severity": "BLOCK"}]) == policy.RISK_HIGH


def test_the_assistant_still_exposes_assess_risk_from_policy():
    """It is a listed capability of the worker, so the name has to resolve —
    but there is only one implementation, and it is policy's."""
    from ai import employee as ai_employee
    from engine import policy
    assert "assess_risk" in ai_employee.CAPABILITIES
    assert ai_employee.assess_risk is policy.assess_risk
