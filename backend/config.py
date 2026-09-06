"""Environment-driven configuration. The only module that reads os.environ.

Two backends are selected purely by which variables are present:

    DATABASE_URL set                  -> Postgres (Supabase)      else SQLite
    SUPABASE_URL + service key set    -> Supabase Storage         else local disk

The SQLite / local-disk pair is the development and test configuration. It is
never chosen silently in production: `verify()` refuses to start a production
process that has fallen back to it.
"""

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).parent          # backend/  — the Python package
PROJECT_ROOT = BACKEND_DIR.parent            # repo root — .env, uploads, db, docs
BASE_DIR = BACKEND_DIR                       # retained: existing callers use this

# Load .env before anything reads os.environ. Real environment variables win, so
# a shell export or a Vercel setting always overrides the file.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=False)
except ImportError:      # optional in environments that inject vars directly
    pass

# --- database ---------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# --- storage ----------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "onboarding-documents").strip()

# --- auth -------------------------------------------------------------------
# One shared password guards the employee side. There is no user directory and
# no sign-up: this is a door, not an identity system.
#
# Required, always. There is deliberately no fallback: a constant here would be
# a working credential committed to the repository, and a value generated at
# start-up would differ on every serverless cold start, so nobody could log in
# to a deployment twice. One rule everywhere - set it, or the app refuses.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()

# --- frontend ---------------------------------------------------------------
# Origins allowed to call /api from a browser.
_DEV_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
# The deployed app serves the React bundle from this same process, so nothing
# needs cross-origin access there. The Vite dev server does, and allowing it
# everywhere costs nothing: the credential is an Authorization header the client
# attaches deliberately, `allow_credentials` is off, and a browser never sends
# it on its own.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", _DEV_ORIGINS)
                .split(",") if o.strip()]

def database_backend() -> str:
    return "postgres" if DATABASE_URL else "sqlite"


def service_key_is_publishable() -> bool:
    """A browser-safe key pasted into the service-role slot.

    Publishable keys are subject to row-level security, so they cannot create a
    bucket or write to a private one. Catching it here turns a confusing runtime
    403 into a named configuration problem at startup.
    """
    return SUPABASE_SERVICE_ROLE_KEY.startswith(
        ("sb_publishable_", "sbp_", "anon"))


def storage_backend() -> str:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and not service_key_is_publishable():
        return "supabase"
    return "local"


def warnings() -> list[str]:
    """Non-fatal misconfiguration, logged at startup so it cannot go unnoticed."""
    out = []
    if database_backend() == "sqlite":
        out.append("DATABASE_URL is not set — using a local SQLite file. "
                   "Fine for development; set it before deploying.")
    if storage_backend() == "local":
        out.append("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set — "
                   "documents are being written to ./uploads. Fine for "
                   "development; set them before deploying.")
    return out


def verify() -> list[str]:
    """Configuration that is wrong everywhere, not merely wrong in production.

    There is no environment flag to key off any more: the app runs one way, and
    which database and storage it uses is decided by which credentials are
    present. The only hard stop left is a credential that cannot do its job.
    """
    problems = []
    if not APP_PASSWORD:
        problems.append("APP_PASSWORD is not set — nobody could sign in. "
                        "Set it in .env locally and in the project's "
                        "environment variables when you deploy.")
    if SUPABASE_URL and service_key_is_publishable():
        problems.append(
            "SUPABASE_SERVICE_ROLE_KEY is a publishable key, not the secret key: "
            "it is subject to row-level security and cannot write to a private "
            "bucket. Use the secret key from Project Settings -> API Keys.")
    return problems


def summary() -> dict:
    """Safe to log. Reports which backend is active, never a credential."""
    return {
        "database": database_backend(),
        "storage": storage_backend(),
        "bucket": SUPABASE_BUCKET if storage_backend() == "supabase" else None,
        # Whether a password was configured, never the password itself.
        "auth_configured": bool(APP_PASSWORD),
    }
