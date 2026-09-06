"""Configurable forms, invite links, and the dynamic vendor portal.

Forms are not versioned: creating one gives a form, editing changes it in place,
and the next onboarding uses it as it stands. What keeps history honest instead
is the *snapshot* each onboarding case stores of the schema it was created with
(`onboarding_cases.form_schema_json`) — so a vendor part-way through is
unaffected by an edit, and a past submission stays readable against the
questions actually asked. Those two halves are the subject of this file.

Shares the fixtures in conftest.py: development backends, a one-person employee
directory, a throwaway database, and signed-in / anonymous clients.
"""

import json

import pytest

from conftest import backend


# --- helpers ----------------------------------------------------------------

def simple_schema(**overrides) -> dict:
    """A small custom form: one canonical field, two that PS-2 knows nothing about."""
    schema = {"sections": [{"title": "About you", "fields": [
        {"id": "legal_entity_name", "label": "Legal entity name", "type": "text",
         "required": True, "canonical": "legal_entity_name"},
        {"id": "trading_since", "label": "Trading since", "type": "date",
         "required": True},
        {"id": "employee_count", "label": "Employees", "type": "number",
         "required": False},
    ]}]}
    schema.update(overrides)
    return schema


def make_form(client, name="International Vendor Onboarding", schema=None) -> str:
    """Create a form and return its id. There is no second id to unpack now."""
    r = client.post("/api/forms/templates", json={
        "name": name, "description": "for the tests",
        "form_schema": schema or simple_schema()})
    assert r.status_code == 201, r.text
    return r.json()["template_id"]


def edit_form(client, form_id, schema):
    """Save a new schema over the form. There is no draft and nothing to publish."""
    r = client.put(f"/api/forms/templates/{form_id}", json={"form_schema": schema})
    assert r.status_code == 200, r.text
    return r


def with_website(required=True) -> dict:
    """`simple_schema` plus one extra question, for before/after edit comparisons."""
    schema = simple_schema()
    schema["sections"][0]["fields"].append(
        {"id": "website", "label": "Website", "type": "text", "required": required})
    return schema


def field_ids(schema: dict) -> list[str]:
    return [f["id"] for s in schema["sections"] for f in s["fields"]]


def token_from(url: str) -> str:
    return url.rsplit("/", 1)[-1]


# --- the standard form is seeded --------------------------------------------

def test_the_standard_ps2_form_is_seeded_and_usable(db):
    from engine import forms
    form = db.standard_form()
    assert form is not None
    assert forms.validate_schema(form["schema"]) == []


def test_the_standard_schema_matches_the_ps2_fields(db):
    """Derived from rules.py, so the default form cannot drift from the engine."""
    from ai import extract
    from engine import forms
    from engine import rules
    schema = db.standard_form()["schema"]
    canonical = {f["canonical"] for f in forms.value_fields(schema)}
    assert canonical == set(rules.SUBMISSION_FIELDS)
    documents = {f["canonical"] for f in forms.document_fields(schema)}
    assert documents == set(extract.DOC_TYPES)


def test_the_field_groups_cover_every_submission_field_exactly_once():
    """The direct submission page renders from this and nothing else. A field
    added to the engine but missing here is a question no vendor is ever asked,
    and a typo'd id is a field that silently renders nothing."""
    from engine import forms
    from engine import rules
    grouped = [fid for _, field_ids in forms.FIELD_GROUPS for fid in field_ids]
    assert sorted(grouped) == sorted(rules.SUBMISSION_FIELDS)
    assert len(grouped) == len(set(grouped))            # none listed twice


def test_the_standard_form_sections_read_as_questions_to_a_vendor(db):
    """The titles are the vendor's headings, not our table names."""
    from engine import forms
    schema = db.standard_form()["schema"]
    assert [s["title"] for s in schema["sections"]] == [
        title for title, _ in forms.FIELD_GROUPS] + ["Documents"]


def test_only_unconditionally_required_fields_are_required_in_the_browser(db):
    """GSTIN, PAN and IFSC depend on country and tax type, and R01/R02 decide
    that from the answers. Marking them required in the browser would stop a US
    vendor submitting at all — the form would block what the engine allows."""
    from engine import forms
    from engine import rules
    schema = db.standard_form()["schema"]
    required = {f["id"] for f in forms.value_fields(schema) if f["required"]}
    assert required == set(rules.ALWAYS_REQUIRED)
    assert {"gstin", "pan", "ifsc"}.isdisjoint(required)


def test_conditionally_required_documents_are_not_required_in_the_browser(db):
    """Same reason: R02 asks for a PAN card and a GST certificate only when the
    submission claims the things they prove."""
    from engine import forms
    from engine import rules
    schema = db.standard_form()["schema"]
    required = {f["id"] for f in forms.document_fields(schema) if f["required"]}
    assert required == set(rules.required_documents({}))
    assert {"pan_card", "gst_certificate"}.isdisjoint(required)


def test_a_us_vendor_can_satisfy_the_standard_form_as_the_browser_enforces_it(db):
    """The point of the two tests above, stated as the outcome they protect."""
    from engine import forms
    schema = db.standard_form()["schema"]
    us = {"legal_entity_name": "Northwind Trading Inc", "entity_type":
          "Foreign Corporation", "country_of_incorporation": "US",
          "registered_address": "500 Pike Street, Seattle WA",
          "registered_address_state": "Washington", "contact_name": "Dana Brooks",
          "contact_email": "dana@northwind.example", "contact_phone": "+1 206 555 0132",
          "tax_id_type": "EIN", "account_holder_name": "Northwind Trading Inc",
          "account_number": "1234567890", "bank_name": "First Republic"}

    # Nothing the browser insists on is left blank, so the vendor can press send
    # with no GSTIN, no PAN and no IFSC...
    unanswered = [f["id"] for f in forms.value_fields(schema)
                  if f["required"] and not us.get(f["id"])]
    assert unanswered == []

    # ...and the engine then agrees, which is the half that would make blocking
    # them in the browser a bug rather than a preference.
    from engine import rules
    from datetime import date
    ctx = rules.RuleContext(today=date(2026, 9, 5))
    canonical, _ = forms.normalize_submission(schema, us)
    extracted = dict.fromkeys(rules.required_documents(canonical), {})
    assert rules.r01_required_fields(canonical, None, ctx) == []
    assert rules.r02_required_documents(canonical, extracted, ctx) == []


def test_the_standard_form_explains_what_it_is_asking_for(db):
    """Help text is why a vendor gets it right first time rather than being
    chased by R15. It has to survive the trip through the database."""
    schema = db.standard_form()["schema"]
    fields = {f["id"]: f for s in schema["sections"] for f in s["fields"]}
    assert "not a trading" in fields["legal_entity_name"]["help"]
    assert "address proof" in fields["registered_address"]["help"]
    assert "cancelled cheque" in fields["bank_proof"]["help"]
    documents = next(s for s in schema["sections"] if s["title"] == "Documents")
    assert "legible" in documents["description"]


def test_seeding_is_idempotent(db):
    before = len(db.list_templates())
    db.seed_standard_template()
    db.init_db()
    assert len(db.list_templates()) == before


def test_seeding_keeps_a_cosmetic_edit_to_the_standard_form(client, db):
    """Seeding means "make sure it can still do its job", not "put it back".
    Reordering, rewording and an added question are all deliberate employee edits
    that still collect everything the engine reads, so start-up leaves them."""
    standard = db.standard_form()
    edited = json.loads(json.dumps(standard["schema"]))
    edited["sections"].reverse()                                  # reordered
    edited["sections"][0]["title"] = "Paperwork"                  # reworded
    edited["sections"][0]["fields"].append(                       # one added
        {"id": "trading_since", "label": "Trading since", "type": "date",
         "required": False})
    edit_form(client, standard["id"], edited)

    db.seed_standard_template()
    db.init_db()

    after = db.standard_form()["schema"]
    assert after["sections"][0]["title"] == "Paperwork"
    assert "trading_since" in field_ids(after)


def test_seeding_rebuilds_a_standard_form_that_has_fallen_behind_the_engine(
        client, db):
    """The one edit that is not survivable: dropping a question the rules read.
    A vendor filling in a form that cannot satisfy the engine is worse than a
    lost edit, so it is rebuilt from rules.py even though a human made it."""
    from engine import rules
    standard = db.standard_form()
    trimmed = {"sections": standard["schema"]["sections"][:1]}
    edit_form(client, standard["id"], trimmed)

    db.seed_standard_template()
    db.init_db()

    after = db.standard_form()["schema"]
    assert len(after["sections"]) > 1
    assert set(rules.SUBMISSION_FIELDS) <= set(field_ids(after))


def test_missing_engine_fields_names_what_a_schema_stopped_asking_for():
    """The check that tells the two edits above apart."""
    from ai import extract
    from engine import forms
    from engine import rules
    assert forms.missing_engine_fields(forms.standard_schema()) == set()

    schema = forms.standard_schema()
    dropped = schema["sections"][0]["fields"].pop(0)
    del schema["sections"][-1]["fields"][0]       # and one document
    assert forms.missing_engine_fields(schema) == {
        dropped["canonical"], extract.DOC_TYPES[0]}

    # An added custom question is not a gap: it asks for *more*, not less.
    schema = forms.standard_schema()
    schema["sections"][0]["fields"].append(
        {"id": "trading_since", "label": "Trading since", "type": "date"})
    assert forms.missing_engine_fields(schema) == set()
    assert forms.missing_engine_fields({"sections": []}) == (
        set(rules.SUBMISSION_FIELDS) | set(extract.DOC_TYPES))


# --- schema validation -------------------------------------------------------

@pytest.mark.parametrize("schema,fragment", [
    ({}, "at least one section"),
    ({"sections": []}, "at least one section"),
    ({"sections": [{"title": "", "fields": []}]}, "needs a title"),
    ({"sections": [{"title": "A", "fields": []}]}, "has no fields"),
    ({"sections": [{"title": "A", "fields": [
        {"id": "Bad Id", "label": "x", "type": "text"}]}]}, "not a valid field id"),
    ({"sections": [{"title": "A", "fields": [
        {"id": "a", "label": "", "type": "text"}]}]}, "needs a label"),
    ({"sections": [{"title": "A", "fields": [
        {"id": "a", "label": "A", "type": "wat"}]}]}, "unknown type"),
    ({"sections": [{"title": "A", "fields": [
        {"id": "a", "label": "A", "type": "select"}]}]}, "needs at least one option"),
    ({"sections": [{"title": "A", "fields": [
        {"id": "a", "label": "A", "type": "text", "canonical": "nope"}]}]},
     "unknown canonical field"),
])
def test_invalid_schemas_are_named_precisely(schema, fragment):
    from engine import forms
    problems = forms.validate_schema(schema)
    assert any(fragment in p for p in problems), problems


def test_duplicate_field_ids_are_rejected():
    from engine import forms
    schema = {"sections": [{"title": "A", "fields": [
        {"id": "a", "label": "A", "type": "text"},
        {"id": "a", "label": "B", "type": "text"}]}]}
    assert any("duplicate field id" in p for p in forms.validate_schema(schema))


def test_an_invalid_schema_cannot_be_saved(client, db):
    """Validation happens on the way in — the last gate before a schema becomes
    the live form — so a form that could not be rendered is never stored, and the
    one already there is left exactly as it was."""
    form_id = make_form(client)
    broken = {"sections": [{"title": "A", "fields": [
        {"id": "a", "label": "A", "type": "select"}]}]}

    r = client.put(f"/api/forms/templates/{form_id}", json={"form_schema": broken})
    assert r.status_code == 400 and "option" in r.json()["detail"]
    assert field_ids(db.get_template(form_id)["schema"]) == field_ids(simple_schema())


def test_a_form_always_needs_a_name(client, db):
    assert client.post("/api/forms/templates", json={"name": "  "}).status_code == 400
    form_id = make_form(client)
    assert client.put(f"/api/forms/templates/{form_id}",
                      json={"name": " "}).status_code == 400


# --- form lifecycle: create, edit in place, copy, delete ---------------------

def test_employee_creates_a_form_and_reads_it_back(client, db):
    form_id = make_form(client)
    body = client.get(f"/api/forms/templates/{form_id}").json()
    detail = body["template"]
    assert detail["id"] == form_id
    assert detail["name"] == "International Vendor Onboarding"
    import auth
    assert detail["created_by"] == auth.ACTOR
    assert detail["field_count"] == 3 and detail["document_count"] == 0
    assert detail["is_standard"] is False
    # No versions anywhere in the payload: a form is just a form now.
    assert "versions" not in body


def test_a_form_is_edited_in_place(client, db):
    """One form, one schema: saving replaces what is there rather than branching."""
    form_id = make_form(client)
    changed = simple_schema()
    changed["sections"][0]["fields"][1]["label"] = "In business since"
    edit_form(client, form_id, changed)

    form = db.get_template(form_id)
    assert form["schema"]["sections"][0]["fields"][1]["label"] == "In business since"
    assert len(db.list_templates()) == 2      # the standard one plus this one


def test_editing_touches_only_the_fields_that_were_sent(client, db):
    """A rename must not blank the description or drop the schema."""
    form_id = make_form(client)
    assert client.put(f"/api/forms/templates/{form_id}",
                      json={"name": "Renamed"}).status_code == 200
    form = db.get_template(form_id)
    assert form["name"] == "Renamed"
    assert form["description"] == "for the tests"
    assert field_ids(form["schema"]) == field_ids(simple_schema())


def test_a_form_can_be_duplicated(client, db):
    """A copy is an independent form: editing it leaves the original alone."""
    form_id = make_form(client)
    r = client.post(f"/api/forms/templates/{form_id}/duplicate",
                    json={"name": "Copy of International"})
    assert r.status_code == 201
    copy_id = r.json()["template_id"]
    assert copy_id != form_id

    copy = client.get(f"/api/forms/templates/{copy_id}").json()["template"]
    assert copy["name"] == "Copy of International"
    assert copy["schema"] == db.get_template(form_id)["schema"]

    edit_form(client, copy_id, {"sections": [{"title": "Only", "fields": [
        {"id": "note", "label": "Note", "type": "text"}]}]})
    assert field_ids(db.get_template(form_id)["schema"]) == field_ids(simple_schema())


def test_an_unused_form_can_be_deleted(client, db):
    form_id = make_form(client)
    assert client.delete(f"/api/forms/templates/{form_id}").status_code == 200
    assert client.get(f"/api/forms/templates/{form_id}").status_code == 404
    assert db.get_template(form_id) is None


def test_the_standard_form_cannot_be_deleted(client, db):
    """It is the default every onboarding falls back to; without it there would be
    nothing to create a case against."""
    standard = db.standard_form()
    r = client.delete(f"/api/forms/templates/{standard['id']}")
    assert r.status_code == 409 and "standard" in r.json()["detail"].lower()
    assert db.standard_form() is not None


def test_a_form_an_onboarding_used_cannot_be_deleted(client, db):
    """A case must always be able to name where its questions came from — the
    snapshot preserves the questions, but not the identity of the form."""
    form_id = make_form(client)
    case_id = client.post("/api/onboardings", json={
        "vendor_name": "Alpha LLP", "form_id": form_id}).json()["case_id"]

    r = client.delete(f"/api/forms/templates/{form_id}")
    assert r.status_code == 409 and "Onboardings" in r.json()["detail"]
    assert db.get_template(form_id) is not None
    assert client.get(f"/api/onboardings/{case_id}").json()["form"]["template_name"] \
        == "International Vendor Onboarding"


def test_unknown_forms_are_404_on_every_verb(client, db):
    assert client.get("/api/forms/templates/FORM-9999").status_code == 404
    assert client.put("/api/forms/templates/FORM-9999",
                      json={"name": "x"}).status_code == 404
    assert client.post("/api/forms/templates/FORM-9999/duplicate",
                       json={"name": "x"}).status_code == 404
    assert client.delete("/api/forms/templates/FORM-9999").status_code == 404


def test_the_form_list_carries_what_the_builder_needs(client, db):
    """The field types and canonical targets come from the server, so the React
    builder never retypes a list that lives in forms.py, rules.py and extract.py."""
    from ai import extract
    from engine import forms
    from engine import rules
    body = client.get("/api/forms/templates").json()
    assert [t["name"] for t in body["templates"]][0] == forms.STANDARD_TEMPLATE_NAME
    assert body["field_types"] == list(forms.FIELD_TYPES)
    assert body["canonical_fields"] == sorted(rules.SUBMISSION_FIELDS)
    assert body["document_types"] == list(extract.DOC_TYPES)


def test_form_endpoints_require_authentication(anon_client, db):
    headers = {"Accept": "application/json"}
    assert anon_client.get("/api/forms/templates", headers=headers).status_code == 401
    assert anon_client.post("/api/forms/templates", headers=headers,
                            json={"name": "x"}).status_code == 401
    assert anon_client.put("/api/forms/templates/FORM-0001", headers=headers,
                           json={"name": "x"}).status_code == 401
    assert anon_client.delete("/api/forms/templates/FORM-0001",
                              headers=headers).status_code == 401


# --- historical isolation: the case's schema snapshot ------------------------
#
# This is what replaces version immutability. Nothing about a form is frozen any
# more, so the guarantee has to come from the copy taken when the case was made.

def test_editing_a_form_does_not_change_what_an_existing_case_asks(client,
                                                                   anon_client, db):
    """The whole point of the snapshot: a vendor part-way through a form must not
    have the questions change underneath them when an employee edits it."""
    form_id = make_form(client)
    created = client.post("/api/onboardings", json={
        "vendor_name": "Alpha LLP", "form_id": form_id}).json()
    token = token_from(created["vendor_url"])

    # The form moves on: one question dropped, a required one added.
    changed = with_website()
    changed["sections"][0]["fields"].remove(
        next(f for f in changed["sections"][0]["fields"]
             if f["id"] == "employee_count"))
    edit_form(client, form_id, changed)

    case = db.get_case(created["case_id"])
    assert case["form_id"] == form_id                   # still points at the form
    assert field_ids(case["form_schema"]) == field_ids(simple_schema())

    # And the vendor is served the snapshot, not the edited form.
    served = anon_client.get(f"/api/vendor/onboard/{token}").json()["schema"]
    assert field_ids(served) == ["legal_entity_name", "trading_since",
                                 "employee_count"]


def test_a_case_created_after_an_edit_gets_the_new_questions(client, anon_client, db):
    """The other half of the rule: an edit is not a draft waiting to be published,
    it is the form. The very next onboarding asks the new questions."""
    form_id = make_form(client)
    edit_form(client, form_id, with_website())

    created = client.post("/api/onboardings", json={
        "vendor_name": "Beta LLP", "form_id": form_id}).json()
    served = anon_client.get(
        f"/api/vendor/onboard/{token_from(created['vendor_url'])}").json()["schema"]
    assert "website" in field_ids(served)
    assert field_ids(db.get_case(created["case_id"])["form_schema"]) == \
        field_ids(with_website())


def test_two_cases_on_one_form_keep_the_snapshots_they_were_created_with(client, db):
    """Two vendors invited either side of an edit each keep their own questions —
    exactly what versioning used to buy, now bought by the snapshot."""
    form_id = make_form(client)
    before = client.post("/api/onboardings", json={
        "vendor_name": "Early Ltd", "form_id": form_id}).json()["case_id"]

    edit_form(client, form_id, with_website())

    after = client.post("/api/onboardings", json={
        "vendor_name": "Late Ltd", "form_id": form_id}).json()["case_id"]

    assert len(db.get_case(before)["form_schema"]["sections"][0]["fields"]) == 3
    assert len(db.get_case(after)["form_schema"]["sections"][0]["fields"]) == 4
    assert db.get_case(before)["form_id"] == db.get_case(after)["form_id"] == form_id


def test_the_case_carries_a_schema_snapshot_not_a_reference(client, db):
    """History stays reproducible on its own: the case holds the schema itself, so
    reading a past submission never depends on the form still saying that."""
    form_id = make_form(client)
    case_id = client.post("/api/onboardings", json={
        "vendor_name": "Snapshot Ltd", "form_id": form_id}).json()["case_id"]
    snapshot = db.get_case(case_id)["form_schema"]
    assert snapshot is not None
    assert snapshot == db.get_template(form_id)["schema"]


def test_an_unknown_form_cannot_be_onboarded_against(client, db):
    """Better a 404 than a silent fall back to the standard form: the employee
    chose a form and needs to know they did not get it."""
    r = client.post("/api/onboardings", json={"vendor_name": "Nope Ltd",
                                              "form_id": "FORM-9999"})
    assert r.status_code == 404


def test_onboarding_defaults_to_the_standard_form(client, db):
    r = client.post("/api/onboardings", json={"vendor_name": "Default Ltd"})
    assert r.status_code == 201
    assert r.json()["form"]["template_name"] == "Standard Vendor Onboarding"
    assert r.json()["form"]["template_id"] == db.standard_form()["id"]


# --- the dynamic vendor form -------------------------------------------------

def test_the_vendor_receives_the_schema_its_case_was_created_with(client,
                                                                  anon_client, db):
    form_id = make_form(client)
    created = client.post("/api/onboardings", json={
        "vendor_name": "Alpha LLP", "form_id": form_id}).json()

    form = anon_client.get(
        f"/api/vendor/onboard/{token_from(created['vendor_url'])}").json()
    assert form["state"] == "open"
    assert form["vendor_name"] == "Alpha LLP"
    assert field_ids(form["schema"]) == ["legal_entity_name", "trading_since",
                                         "employee_count"]
    assert form["max_upload_mb"] == 10


def test_two_cases_on_different_forms_get_different_questions(client, anon_client, db):
    custom = make_form(client)
    standard = db.standard_form()

    a = client.post("/api/onboardings", json={"vendor_name": "Custom Ltd",
                                              "form_id": custom}).json()
    b = client.post("/api/onboardings", json={"vendor_name": "Standard Ltd",
                                              "form_id": standard["id"]}).json()

    form_a = anon_client.get(f"/api/vendor/onboard/{token_from(a['vendor_url'])}").json()
    form_b = anon_client.get(f"/api/vendor/onboard/{token_from(b['vendor_url'])}").json()
    assert len(form_a["schema"]["sections"]) == 1
    assert len(form_b["schema"]["sections"]) == 5          # the PS-2 form
    assert form_a["schema"] != form_b["schema"]


def test_the_vendor_cannot_choose_a_form(client, anon_client, db):
    """The schema is derived from the case, never from anything the vendor sends."""
    custom = make_form(client)
    standard = db.standard_form()
    created = client.post("/api/onboardings", json={
        "vendor_name": "Fixed Ltd", "form_id": custom}).json()
    token = token_from(created["vendor_url"])

    form = anon_client.get(
        f"/api/vendor/onboard/{token}?form_id={standard['id']}").json()
    assert field_ids(form["schema"]) == ["legal_entity_name", "trading_since",
                                         "employee_count"]


def test_an_unknown_token_is_404(anon_client, db):
    assert anon_client.get("/api/vendor/onboard/nope").status_code == 404


def test_a_case_id_is_not_a_vendor_credential(client, anon_client, db):
    case_id = client.post("/api/onboardings",
                          json={"vendor_name": "Alpha LLP"}).json()["case_id"]
    assert anon_client.get(f"/api/vendor/onboard/{case_id}").status_code == 404


def test_the_vendor_api_exposes_nothing_internal(client, anon_client, db):
    created = client.post("/api/onboardings",
                          json={"vendor_name": "Alpha LLP"}).json()
    body = anon_client.get(
        f"/api/vendor/onboard/{token_from(created['vendor_url'])}").text
    for leak in ("findings", "risk", "ai_summary", "decision", "audit",
                 "created_by", "operator"):
        assert leak not in body, leak


# --- invite links ------------------------------------------------------------

def test_opening_corrections_keeps_the_same_url(client, anon_client, db,
                                                monkeypatch):
    """One link per case, for its whole life. A vendor who kept the first email
    can use it again; nothing is minted and nothing is invalidated."""
    from engine import pipeline
    r = client.post("/api/onboardings", json={"vendor_name": "Same Link Ltd"})
    case_id, url = r.json()["case_id"], r.json()["vendor_url"]
    token = url.rsplit("/", 1)[1]

    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    anon_client.post(f"/api/vendor/onboard/{token}",
                     data={"legal_entity_name": "Same Link Ltd"})
    # Submitted: the same URL now refuses a second submission.
    assert anon_client.post(f"/api/vendor/onboard/{token}",
                            data={}).status_code == 409

    run_id = db.get_case(case_id)["run_id"]
    db.set_status(run_id, "PENDING")
    reopened = client.post(f"/api/onboardings/{case_id}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["vendor_url"] == url          # same URL, not a new one
    assert anon_client.get(f"/api/vendor/onboard/{token}").json()["state"] == "open"


def test_the_link_is_shown_whenever_it_is_needed(client, db):
    """Stored on purpose, so a link is never lost by closing a tab. The hash
    stays as the lookup key."""
    created = client.post("/api/onboardings",
                          json={"vendor_name": "Alpha LLP"}).json()
    token = token_from(created["vendor_url"])
    case = db.get_case(created["case_id"])
    assert case["token"] == token
    assert case["token_hash"] == db.hash_token(token)

    # And it is the same link every time it is asked for.
    again = client.get(f"/api/onboardings/{created['case_id']}").json()
    assert again["link"]["url"] == created["vendor_url"]


def test_the_case_detail_returns_the_link_and_whether_it_is_open(client, db):
    created = client.post("/api/onboardings",
                          json={"vendor_name": "Alpha LLP"}).json()
    detail = client.get(f"/api/onboardings/{created['case_id']}").json()
    assert detail["link"]["url"] == created["vendor_url"]
    # `open` is the gate: a fresh case is accepting its first submission.
    assert detail["link"]["open"] is True


def test_a_submitted_case_cannot_be_reopened_until_it_is_pending(
        client, anon_client, db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "APPROVED")
    created = client.post("/api/onboardings",
                          json={"vendor_name": "Alpha LLP"}).json()
    anon_client.post(f"/api/vendor/onboard/{token_from(created['vendor_url'])}",
                     data={"legal_entity_name": "Alpha LLP"})
    r = client.post(f"/api/onboardings/{created['case_id']}/reopen")
    assert r.status_code == 409


def test_reopening_requires_authentication(anon_client, client, db):
    created = client.post("/api/onboardings",
                          json={"vendor_name": "Alpha LLP"}).json()
    r = anon_client.post(f"/api/onboardings/{created['case_id']}/reopen",
                         headers={"Accept": "application/json"})
    assert r.status_code == 401


def test_the_raw_token_is_never_logged():
    """The redaction filter still covers the vendor route."""
    import app
    line = app._redact("GET /api/vendor/onboard/AbCdEf0123456789_-QwErTyUiOpAs")
    assert "AbCdEf0123456789" not in line
    assert "/api/vendor/onboard/<redacted>" in line


# --- normalisation: canonical vs custom --------------------------------------

def test_canonical_fields_reach_the_rules_unchanged(db):
    from engine import forms
    from engine import rules
    schema = db.standard_form()["schema"]
    canonical, custom = forms.normalize_submission(schema, {
        "legal_entity_name": "  Acme LLP  ", "gstin": "29ABCFS1234K1Z3"})
    assert set(canonical) == set(rules.SUBMISSION_FIELDS)
    assert canonical["legal_entity_name"] == "Acme LLP"     # trimmed
    assert custom == {}                                     # the PS-2 form has none


def test_custom_fields_are_kept_separate(db):
    from engine import forms
    canonical, custom = forms.normalize_submission(simple_schema(), {
        "legal_entity_name": "Alpha LLP", "trading_since": "2019-04-11",
        "employee_count": "42"})
    assert canonical["legal_entity_name"] == "Alpha LLP"
    assert custom == {"trading_since": "2019-04-11", "employee_count": "42"}
    assert "trading_since" not in canonical


@pytest.mark.parametrize("value,fragment", [
    ("not-a-date", "valid date"),
    ("", "is missing"),
])
def test_custom_fields_get_generic_validation(value, fragment):
    from engine import forms
    found = forms.custom_field_findings(
        simple_schema(), {"trading_since": value}, present_documents=[])
    assert any(fragment in f.message for f in found), [f.message for f in found]


def test_custom_validation_never_invents_a_business_rule():
    """Only completeness and type sanity — severities stay FIX, never BLOCK."""
    from engine import forms
    from engine import rules
    found = forms.custom_field_findings(
        simple_schema(), {"trading_since": "nope", "employee_count": "many"},
        present_documents=[])
    assert found
    assert all(f.severity == rules.FIX for f in found)
    assert {f.rule_id for f in found} <= {"R01", "R02", "R03"}


def test_a_custom_document_is_presence_checked_only():
    from engine import forms
    schema = {"sections": [{"title": "Docs", "fields": [
        {"id": "trade_licence", "label": "Trade licence", "type": "document",
         "required": True}]}]}
    assert forms.custom_field_findings(schema, {}, present_documents=[])
    assert forms.custom_field_findings(schema, {},
                                       present_documents=["trade_licence"]) == []


# --- the pipeline still behaves ---------------------------------------------

def test_the_standard_form_produces_unchanged_ps2_behaviour(client, anon_client,
                                                            db, monkeypatch):
    """A case on the standard form hits exactly the rules it always did."""
    from engine import pipeline
    from test_rules import (FAKE_DOCS, base_submission,
                            fake_extractor, fake_reviewer, TODAY)

    created = client.post("/api/onboardings",
                          json={"vendor_name": "Sundaram Industrial Supplies LLP"}).json()
    token = token_from(created["vendor_url"])
    real_run = pipeline.run
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    anon_client.post(f"/api/vendor/onboard/{token}",
                     data=base_submission(pan="ABCFS1234Z",
                                          entity_type="Proprietorship",
                                          registered_address_state="Maharashtra"))

    run_id = db.get_case(created["case_id"])["run_id"]
    for doc in FAKE_DOCS:
        db.save_document(run_id, doc, ".pdf", b"%PDF stub")

    status = real_run(run_id, today=TODAY, extract_fn=fake_extractor(),
                          review_fn=fake_reviewer())
    assert status == "REJECTED"                                    # EC-4
    # The cross-field rules plus the three document rules that now corroborate
    # them: the card and the certificate both carry the PAN that was not typed,
    # and the utility bill is still for the state that was not declared.
    assert sorted({f["rule_id"] for f in db.get_findings(run_id)}) == \
        ["R06", "R07", "R08", "R15", "R16", "R18"]


def test_custom_answers_survive_submission_and_reach_the_run(client, anon_client,
                                                             db, monkeypatch):
    from engine import pipeline
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: "PENDING")
    form_id = make_form(client)
    created = client.post("/api/onboardings", json={
        "vendor_name": "Alpha LLP", "form_id": form_id}).json()

    anon_client.post(f"/api/vendor/onboard/{token_from(created['vendor_url'])}",
                     data={"legal_entity_name": "Alpha LLP",
                           "trading_since": "2019-04-11", "employee_count": "42"})

    run = db.get_run(db.get_case(created["case_id"])["run_id"])
    assert run["submission"]["legal_entity_name"] == "Alpha LLP"
    assert run["submission"]["_custom"] == {"trading_since": "2019-04-11",
                                            "employee_count": "42"}


def test_a_submission_is_judged_against_the_snapshot_not_the_edited_form(
        client, anon_client, db, monkeypatch):
    """The audit half of the snapshot: a vendor is never marked incomplete for a
    question that was added to the form after they were invited."""
    from engine import pipeline
    from test_rules import fake_reviewer, TODAY

    real_run = pipeline.run
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    form_id = make_form(client)
    created = client.post("/api/onboardings", json={
        "vendor_name": "Alpha LLP", "form_id": form_id}).json()
    edit_form(client, form_id, with_website())          # after the invite went out

    anon_client.post(f"/api/vendor/onboard/{token_from(created['vendor_url'])}",
                     data={"legal_entity_name": "Alpha LLP",
                           "trading_since": "2019-04-11"})
    run_id = db.get_case(created["case_id"])["run_id"]
    real_run(run_id, today=TODAY, review_fn=fake_reviewer())

    messages = [f["message"] for f in db.get_findings(run_id)]
    assert not any("Website" in m for m in messages), messages


def test_custom_fields_reach_the_ai_employee(db):
    from ai import employee as ai_employee
    seen = {}

    def capture(vendor_name, status, findings, comparisons=None, **kwargs):
        seen.update(kwargs)
        return ai_employee.Review(risk="Low"), {"purpose": "x", "model": "m",
                                                "input_summary": "i",
                                                "raw_response": "{}",
                                                "usage": {"input_tokens": 1,
                                                          "output_tokens": 1}}

    ai_employee.run_review("VS-0001", "Alpha LLP", "PENDING", [],
                           custom_answers={"trading_since": "2019-04-11"},
                           schema=simple_schema(), review_fn=capture)
    assert seen["custom_answers"] == {"trading_since": "2019-04-11"}
    assert seen["schema"]["sections"][0]["title"] == "About you"


def test_custom_answers_are_labelled_for_the_model():
    from ai import employee as ai_employee
    rendered = ai_employee._render_custom({"trading_since": "2019-04-11"},
                                          simple_schema())
    assert "Trading since: 2019-04-11" in rendered


def test_the_ai_employee_is_told_custom_answers_carry_no_rule():
    from ai import employee as ai_employee
    assert "no rule for" in ai_employee.REVIEW_PROMPT
    assert "never treat it as a failed check" in ai_employee.REVIEW_PROMPT


def test_a_custom_required_field_makes_the_run_pending(client, anon_client, db,
                                                       monkeypatch):
    """Deterministic engine still decides — a missing custom field is a FIX."""
    from engine import pipeline
    from test_rules import fake_reviewer, TODAY

    real_run = pipeline.run
    monkeypatch.setattr(pipeline, "run", lambda *a, **k: None)
    form_id = make_form(client)
    created = client.post("/api/onboardings", json={
        "vendor_name": "Alpha LLP", "form_id": form_id}).json()
    anon_client.post(f"/api/vendor/onboard/{token_from(created['vendor_url'])}",
                     data={"legal_entity_name": "Alpha LLP", "trading_since": ""})

    run_id = db.get_case(created["case_id"])["run_id"]
    status = real_run(run_id, today=TODAY,
                          review_fn=fake_reviewer())
    assert status == "PENDING"
    messages = [f["message"] for f in db.get_findings(run_id)]
    assert any("Trading since" in m for m in messages)


# --- API contract stability --------------------------------------------------

def test_session_endpoint_reports_whether_this_browser_is_signed_in(
        client, anon_client, db):
    assert anon_client.get("/api/session").json() == {"authenticated": False}
    assert client.get("/api/session").json() == {"authenticated": True}


def test_dashboard_api_returns_the_same_metrics_and_columns(client, db):
    from test_rules import base_submission, execute
    execute(db, base_submission())
    body = client.get("/api/dashboard").json()
    assert set(body["stats"]) == {"total", "approved", "pending", "rejected", "error"}
    assert set(body) == {"stats", "cases", "runs", "statuses", "status_labels",
                         "active"}
    row = body["runs"][0]
    # `case_id` and `submission_no` were added so run history can say which
    # journey each execution belongs to. The metrics above are unchanged.
    assert set(row) == {"run_id", "case_id", "submission_no", "is_latest",
                        "vendor_name", "status", "status_label",
                        "finding_count", "created_at", "duration_ms"}
    # A run created directly in the store belongs to no case.
    assert row["case_id"] is None and row["submission_no"] is None


def test_dashboard_case_rows_keep_created_and_last_activity(client, db):
    client.post("/api/onboardings", json={"vendor_name": "Awaiting Ltd"})
    case = client.get("/api/dashboard").json()["cases"][0]
    assert set(case) >= {"case_id", "vendor_name", "status",                          "finding_count", "created_at", "last_activity_at",
                         "last_activity_label"}
    assert "+00:00" not in case["created_at"]           # formatted server-side
    assert case["last_activity_at"] == case["created_at"]   # no run yet


def test_no_sql_in_the_api_layer():
    """Persistence stays behind store.py."""
    import re
    keywords = re.compile(r"\b(SELECT .* FROM|INSERT INTO|UPDATE \w+ SET|DELETE FROM)\b")
    for path in backend("routes").glob("*.py"):
        assert not keywords.search(path.read_text(encoding="utf-8")), path.name


def test_the_api_never_imports_a_database_driver():
    import ast
    for path in backend("routes").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            assert "psycopg" not in names and "sqlite3" not in names, path.name


def test_a_form_cannot_be_created_with_a_schema_that_cannot_be_rendered(client):
    """Validated on create as well as on edit.

    There is no publish step any longer, so an unrenderable form created here
    could be used for an onboarding immediately — this is the gate that used to
    sit at publish time.
    """
    r = client.post("/api/forms/templates", json={
        "name": "Broken", "description": "",
        "form_schema": {"sections": [{"title": "A", "fields": [
            {"id": "a", "label": "A", "type": "select"}]}]}})
    assert r.status_code == 400
    assert "option" in r.json()["detail"]
    assert not [f for f in client.get("/api/forms/templates").json()["templates"]
                if f["name"] == "Broken"]


# --- migrating a database that predates the change --------------------------

def test_a_database_with_the_old_versioned_shape_is_migrated(db, monkeypatch):
    """Every form keeps its questions, and no insert trips over a dropped column.

    Tests always build a fresh database, so nothing else here would ever see the
    old shape — and the columns that were removed were NOT NULL, which makes
    leaving them a startup failure rather than a cosmetic wart.
    """
    schema = {"sections": [{"title": "Legacy", "fields": [
        {"id": "trading_since", "label": "Trading since", "type": "date"}]}]}

    # Rebuild the pre-change shape by hand: schema in a versions table, a
    # NOT NULL status on the form, and a case pointing at a version id.
    with db._conn() as conn:
        conn.execute("DROP TABLE IF EXISTS form_templates")
        conn.execute("""CREATE TABLE form_templates (
                          id          TEXT PRIMARY KEY,
                          name        TEXT NOT NULL,
                          description TEXT,
                          status      TEXT NOT NULL,
                          created_by  TEXT,
                          created_at  TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE form_template_versions (
                          id TEXT PRIMARY KEY, template_id TEXT, version INTEGER,
                          schema_json TEXT, status TEXT)""")
        conn.execute("ALTER TABLE onboarding_cases ADD COLUMN form_version_id TEXT")
        conn.execute("INSERT INTO form_templates (id, name, description, status,"
                     " created_at) VALUES ('TPL-0001', 'Legacy form', '',"
                     " 'PUBLISHED', '2026-09-01T00:00:00+00:00')")
        conn.execute("INSERT INTO form_template_versions VALUES"
                     " ('TPLV-0001', 'TPL-0001', 1, ?, 'PUBLISHED')",
                     (json.dumps({"sections": [{"title": "old", "fields": []}]}),))
        conn.execute("INSERT INTO form_template_versions VALUES"
                     " ('TPLV-0002', 'TPL-0001', 2, ?, 'PUBLISHED')",
                     (json.dumps(schema),))

    db.init_db()

    with db._conn() as conn:
        assert "status" not in db._existing_columns(conn, "form_templates")
        assert "form_version_id" not in db._existing_columns(conn, "onboarding_cases")

    migrated = db.get_template("TPL-0001")
    assert migrated["schema"] == schema, "the newest version's questions carry over"
    assert db.standard_form() is not None, "the standard form is reseeded"

    # The thing that actually broke: an insert against the migrated table.
    assert db.create_template("After the migration", "", "e@x.test",
                              {"sections": []})
