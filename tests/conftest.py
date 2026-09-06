"""Test bootstrap.

The backend is a plain package directory rather than an installed distribution,
so tests put it on the path here instead of every module doing it. Path helpers
live here too: no test should hardcode where the repository sits.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

sys.path.insert(0, str(BACKEND))


def repo(*parts: str) -> Path:
    """A path relative to the repository root."""
    return ROOT.joinpath(*parts)


def backend(*parts: str) -> Path:
    """A path relative to the backend package.

    Accepts either form: `backend("engine", "rules.py")` or the slash-separated
    `backend("engine/rules.py")`, so a test can name a module the way an import
    statement would.
    """
    return BACKEND.joinpath(*[bit for part in parts for bit in part.split("/")])


# --- shared fixtures --------------------------------------------------------

# The employee side is guarded by one shared password. Tests set their own so
# they never depend on whatever APP_PASSWORD the developer has in .env.
TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite database and upload root per test.

    Explicitly the development backend: no DATABASE_URL and no Supabase keys, so
    config selects sqlite + local disk. Still no network, still no mocking.
    """
    import config
    from data import storage
    from data import store
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(storage, "_LOCAL_ROOT", tmp_path / "uploads")
    store.init_db()
    return store


@pytest.fixture(autouse=True)
def development_backends(monkeypatch):
    """Force the development backends for every test.

    Without this the suite inherits whatever .env the developer happens to have,
    so results would differ between machines — and a test could accidentally talk
    to a real Supabase project.
    """
    import config
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_SERVICE_ROLE_KEY", "")
    monkeypatch.setattr(config, "APP_PASSWORD", TEST_PASSWORD)


@pytest.fixture
def anon_client(db):
    """Not signed in: carries no Authorization header at all.

    A distinct TestClient from `client` — sharing one would mean a test that
    asks for both is silently authenticated on both.
    """
    from fastapi.testclient import TestClient
    import app
    return TestClient(app.app)


@pytest.fixture
def token(db):
    """A real session token, obtained the way a client gets one."""
    from fastapi.testclient import TestClient
    import app
    r = TestClient(app.app).post("/api/login", json={"password": TEST_PASSWORD})
    assert r.status_code == 200, f"login fixture failed: {r.text}"
    return r.json()["access_token"]


@pytest.fixture
def client(db, token):
    """Signed in with the shared password, presenting a bearer token."""
    from fastapi.testclient import TestClient
    import app
    return TestClient(app.app, headers={"Authorization": f"Bearer {token}"})


