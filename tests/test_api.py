"""The REST layer, and how the React bundle is served alongside it.

Everything under /api answers in JSON; everything else is the single-page app.
The two must not blur into each other, which is most of what is checked here.
"""

import json

import pytest

from conftest import repo


# --- serving the built React bundle -----------------------------------------

DIST = repo("frontend", "dist")

needs_build = pytest.mark.skipif(not DIST.is_dir(),
                                 reason="the frontend has not been built")


@needs_build
def test_the_bundle_is_served_at_the_root(anon_client):
    r = anon_client.get("/")
    assert r.status_code == 200
    assert '<div id="root">' in r.text


@needs_build
def test_client_side_routes_fall_back_to_the_bundle(anon_client):
    """/dashboard is not a file on disk — React resolves it once loaded."""
    for path in ("/dashboard", "/run/VS-1", "/forms/FT-1", "/vendor/onboard/abc"):
        r = anon_client.get(path)
        assert r.status_code == 200, path
        assert '<div id="root">' in r.text


@needs_build
def test_a_crafted_path_cannot_read_outside_the_build_directory(anon_client):
    """Traversal falls back to index.html rather than serving a repo file."""
    for path in ("/../backend/config.py", "/..%2f..%2fbackend%2fconfig.py"):
        r = anon_client.get(path)
        assert "SUPABASE_SERVICE_ROLE_KEY" not in r.text, path


@needs_build
def test_serving_the_bundle_never_grants_access(anon_client):
    """The bundle is public; everything it then asks for is not."""
    assert anon_client.get("/dashboard").status_code == 200
    assert anon_client.get("/api/dashboard").status_code == 401


# --- the API and the app never blur together --------------------------------

def test_an_unknown_api_path_is_a_json_404_not_the_single_page_app(anon_client):
    """A typo in a fetch must fail loudly rather than parse as HTML."""
    for method in ("get", "post", "put", "delete"):
        r = getattr(anon_client, method)("/api/nope")
        assert r.status_code == 404, method
        assert r.headers["content-type"].startswith("application/json"), method
        assert r.json()["detail"] == "No such endpoint."


def test_every_api_route_returns_json_not_html(client, db):
    """A JSON client must never be handed a login page or an error template."""
    run_id = db.create_run("Acme", {"legal_entity_name": "Acme"})

    for path in ("/api/session", "/api/dashboard", "/api/forms/templates",
                 f"/api/runs/{run_id}"):
        r = client.get(path)
        assert r.headers["content-type"].startswith("application/json"), path
        json.loads(r.text)


def test_an_unauthenticated_api_call_is_a_401_and_never_a_redirect(anon_client):
    """React owns navigation. The server states the fact; the client decides."""
    r = anon_client.get("/api/dashboard", follow_redirects=False)
    assert r.status_code == 401
    assert "location" not in {k.lower() for k in r.headers}
    assert r.json()["detail"] == "authentication required"


def test_errors_carry_a_readable_detail_rather_than_a_traceback(client):
    r = client.get("/api/runs/VS-9999")
    assert r.status_code == 404
    body = r.json()
    assert body["detail"] == "That run does not exist."
    assert "Traceback" not in r.text and "File \"" not in r.text


# --- what the page costs -----------------------------------------------------

def _count_connections(monkeypatch):
    """Every open is a TLS handshake to another region — ~350ms each against a
    real pooler, which is why the run page took three seconds before this."""
    from data import store
    opened = []
    real = store._open
    monkeypatch.setattr(store, "_open", lambda: (opened.append(1), real())[1])
    return opened


def test_a_request_opens_one_database_connection(client, db, monkeypatch):
    opened = _count_connections(monkeypatch)
    run_id, _ = _seed_run(db)
    opened.clear()
    assert client.get(f"/api/runs/{run_id}").status_code == 200
    assert len(opened) == 1, f"{len(opened)} connections for one page"


def test_the_dashboard_opens_one_connection_too(client, db, monkeypatch):
    opened = _count_connections(monkeypatch)
    opened.clear()
    assert client.get("/api/dashboard").status_code == 200
    assert len(opened) == 1


def test_a_static_request_opens_none(anon_client, db, monkeypatch):
    """A favicon has no business reaching for the database."""
    opened = _count_connections(monkeypatch)
    opened.clear()
    anon_client.get("/favicon.ico")
    assert opened == []


def test_each_operation_still_commits_on_its_own(db):
    """Sharing the socket must not widen the transaction: a stage that crashes
    has to leave the events before it on disk."""
    from data import store
    with store.connection():
        run_id = db.create_run("Durable Ltd", {"legal_entity_name": "Durable Ltd"})
        db.add_event(run_id, "intake", "stage_started")
        try:
            with store._conn() as conn:
                conn.execute("SELECT * FROM nonexistent_table")
        except Exception:
            pass
        # The connection survives the failure and the earlier write is intact.
        assert len(db.get_events(run_id)) == 1
        db.add_event(run_id, "intake", "stage_completed")
        assert len(db.get_events(run_id)) == 2


def _seed_run(db):
    from test_rules import base_submission, execute
    return execute(db, base_submission())


# --- the vendor's own submission --------------------------------------------

def test_the_run_reports_what_the_vendor_submitted_including_blanks(client, db):
    """The reviewer sees the input, not only what the rules made of it."""
    run_id = db.create_run("Acme", {"legal_entity_name": "Acme Pvt Ltd",
                                    "pan": "", "_custom": {"note": "hi"}})
    db.save_document(run_id, "pan_card", ".pdf", b"%PDF-1.4 pan")

    body = client.get(f"/api/runs/{run_id}").json()["submitted"]
    fields = {f["field"]: f["value"] for f in body["fields"]}
    assert fields["legal_entity_name"] == "Acme Pvt Ltd"
    assert fields["pan"] is None                       # blank, not missing
    assert "_custom" not in fields
    assert [d["key"] for d in body["documents"]] == ["pan_card"]


def test_an_attached_document_can_be_opened_by_a_signed_in_reviewer(client,
                                                                    anon_client,
                                                                    db):
    run_id = db.create_run("Acme", {"legal_entity_name": "Acme"})
    db.save_document(run_id, "pan_card", ".pdf", b"%PDF-1.4 pan")

    r = client.get(f"/api/runs/{run_id}/documents/pan_card")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 pan"
    assert "inline" in r.headers["content-disposition"]

    assert client.get(f"/api/runs/{run_id}/documents/gst_certificate"
                      ).status_code == 404
    assert client.get("/api/runs/VS-9999/documents/pan_card").status_code == 404
    # The document is behind the same door as the run it belongs to.
    assert anon_client.get(f"/api/runs/{run_id}/documents/pan_card"
                           ).status_code == 401
