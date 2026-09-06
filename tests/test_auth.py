"""Password-only access to the employee side.

One shared password, one signed session token, no user directory. What has to
hold: the right password gets in, anything else does not, a token cannot be
forged or outlived, every employee route is behind it, and the vendor portal is
completely unaffected by any of it.
"""

import time

import pytest
from conftest import TEST_PASSWORD, backend

import auth
import config

EMPLOYEE_ROUTES = ["/api/dashboard", "/api/forms/templates"]


# ============================================================================
# The password
# ============================================================================

def test_the_configured_password_is_accepted_and_nothing_else_is(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "gozamp")
    assert auth.check_password("gozamp") is True
    for wrong in ("", "GOZAMP", "gozamp ", " gozamp", "gozam", "gozampp", None):
        assert auth.check_password(wrong) is False, wrong


def test_there_is_no_password_in_the_source():
    """A constant here would be a working credential in every clone and fork."""
    assert not hasattr(config, "DEFAULT_PASSWORD")


def test_the_password_is_required_with_no_fallback_of_any_kind():
    """One rule everywhere. A constant here would be a credential committed to
    the repo; a value generated at start-up would differ on every serverless
    cold start, so nobody could sign in to a deployment twice."""
    for gone in ("DEFAULT_PASSWORD", "resolve_password", "PASSWORD_WAS_GENERATED",
                 "APP_ENV", "PIPELINE_MODE", "is_production"):
        assert not hasattr(config, gone), f"{gone} still exists"


def test_no_password_is_a_refusal_to_start(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    assert any("APP_PASSWORD" in problem for problem in config.verify())


def test_a_configured_password_is_used_exactly_as_given(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "hunter2")
    assert auth.check_password("hunter2") is True
    assert config.verify() == []
    assert config.summary()["auth_configured"] is True


def test_the_password_never_appears_in_a_response_or_the_config_summary(
        anon_client, monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)
    assert TEST_PASSWORD not in str(config.summary())
    assert config.summary()["auth_configured"] is True
    body = anon_client.post("/api/login", json={"password": TEST_PASSWORD}).text
    assert TEST_PASSWORD not in body


def test_a_refusal_says_nothing_useful(anon_client):
    r = anon_client.post("/api/login", json={"password": "nope"})
    assert r.status_code == 401
    assert "not recognised" in r.text
    assert TEST_PASSWORD not in r.text


# ============================================================================
# Session tokens
# ============================================================================

def test_a_fresh_token_verifies(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)
    assert auth.valid_token(auth.issue_token()) is True


def test_a_token_carries_an_expiry_and_a_signature_and_nothing_else(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)
    expiry, _, signature = auth.issue_token().partition(".")
    assert expiry.isdigit()
    assert len(signature) == 64                     # HMAC-SHA256, hex
    assert TEST_PASSWORD not in auth.issue_token()


@pytest.mark.parametrize("bad", [
    "", "garbage", "a.b", "9999999999", "9999999999.", ".deadbeef",
    "9999999999.deadbeef", "notanumber.deadbeef",
])
def test_a_forged_or_malformed_token_is_rejected(bad, monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)
    assert auth.valid_token(bad) is False


def test_an_expired_token_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)
    stale = auth.issue_token(now=time.time() - auth.SESSION_TTL_SECONDS - 60)
    assert auth.valid_token(stale) is False


def test_the_expiry_cannot_be_extended_without_the_password(monkeypatch):
    """The signature covers the expiry, so pushing it out invalidates the token."""
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)
    expiry, _, signature = auth.issue_token().partition(".")
    assert auth.valid_token(f"{int(expiry) + 100_000}.{signature}") is False


def test_changing_the_password_invalidates_every_existing_session(monkeypatch):
    """There is no session store to clear, so this is the revocation mechanism."""
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)
    token = auth.issue_token()
    assert auth.valid_token(token) is True
    monkeypatch.setattr(config, "APP_PASSWORD", "a different password")
    assert auth.valid_token(token) is False


# ============================================================================
# How the token travels
# ============================================================================

def test_only_the_authorization_header_is_read(monkeypatch):
    """Never a query parameter: those land in access logs, browser history and
    referrer headers, which is exactly where a credential must not be."""
    from starlette.requests import Request

    def request(headers):
        return Request({"type": "http", "headers": [
            (k.lower().encode(), v.encode()) for k, v in headers.items()]})

    assert auth.token_from(request({"Authorization": "Bearer abc"})) == "abc"
    assert auth.token_from(request({"Authorization": "bearer abc"})) == "abc"
    assert auth.token_from(request({"Authorization": "Basic abc"})) is None
    assert auth.token_from(request({"Authorization": "Bearer "})) is None
    assert auth.token_from(request({})) is None


def test_the_token_is_never_accepted_from_a_query_string(anon_client, token):
    assert anon_client.get(f"/api/dashboard?token={token}").status_code == 401


# ============================================================================
# The gate on every employee route
# ============================================================================

@pytest.mark.parametrize("path", EMPLOYEE_ROUTES)
def test_every_employee_route_refuses_an_anonymous_caller(anon_client, path):
    r = anon_client.get(path)
    assert r.status_code == 401
    assert r.json() == {"detail": "authentication required"}


@pytest.mark.parametrize("path", EMPLOYEE_ROUTES)
def test_every_employee_route_opens_with_the_password(client, path):
    assert client.get(path).status_code == 200


def test_writing_endpoints_are_behind_the_password_too(anon_client, db):
    assert anon_client.post("/api/onboardings",
                            json={"vendor_name": "Sneaky Ltd"}).status_code == 401
    assert anon_client.post("/api/forms/templates",
                            json={"name": "Sneaky"}).status_code == 401
    assert anon_client.post("/api/onboardings/CASE-0001/reopen"
                            ).status_code == 401


def test_a_refusal_is_a_401_and_never_a_redirect(anon_client):
    """React owns navigation, so an unauthenticated call is answered, not moved."""
    r = anon_client.get("/api/dashboard", follow_redirects=False)
    assert r.status_code == 401
    assert "location" not in {k.lower() for k in r.headers}


def test_signing_in_then_out_is_purely_client_side(anon_client, db):
    r = anon_client.post("/api/login", json={"password": TEST_PASSWORD})
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert anon_client.get("/api/session", headers=headers).json() == {
        "authenticated": True}
    # Nothing is stored server-side, so there is deliberately no logout route.
    assert anon_client.post("/api/logout").status_code == 404
    assert anon_client.get("/api/session").json() == {"authenticated": False}


def test_the_session_endpoint_is_the_only_thing_an_anonymous_caller_may_ask(
        anon_client):
    assert anon_client.get("/api/session").status_code == 200


# ============================================================================
# The vendor portal is untouched
# ============================================================================

def make_case(db, vendor="Sundaram Industrial Supplies LLP"):
    token = db.new_token()
    return db.create_case(vendor, "Priya", "priya@example.in", token), token


def test_a_vendor_needs_no_password_and_gets_no_employee_access(anon_client, db):
    _, vendor_token = make_case(db)
    assert anon_client.get(f"/api/vendor/onboard/{vendor_token}").status_code == 200
    # Their link token is not a session token for anything else.
    assert anon_client.get("/api/dashboard", headers={
        "Authorization": f"Bearer {vendor_token}"}).status_code == 401


def test_a_session_token_is_not_a_vendor_link(anon_client, token):
    assert anon_client.get(f"/api/vendor/onboard/{token}").status_code == 404


# ============================================================================
# Nothing of the old identity system is left
# ============================================================================

def test_no_user_directory_roles_or_jwt_remain():
    """The whole point of the change: one door, not an identity system."""
    source = backend("auth.py").read_text(encoding="utf-8")
    # Identifiers, not prose: the module docstring legitimately says a vendor
    # "authenticates" with their own link token.
    for gone in ("import jwt", "pbkdf2", "config.EMPLOYEES", "def hash_password",
                 "def verify_password", "def authenticate", "employee_id",
                 "SECRET_KEY"):
        assert gone not in source, f"{gone} is still in auth.py"


def test_no_module_still_imports_jwt():
    for name in ("auth.py", "config.py", "app.py"):
        assert "import jwt" not in backend(name).read_text(encoding="utf-8"), name


def test_the_api_layer_asks_only_for_a_session():
    """No route may reach for an identity that no longer exists."""
    import pathlib
    for path in pathlib.Path(backend("routes")).glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "require_employee" not in source, path.name
        assert "Employee = Depends" not in source, path.name
