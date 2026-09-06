"""Vendor Onboarding Decision Engine — HTTP layer.

Wiring only. Every route lives in `api/`; every rule lives in `rules.py`; every
query lives in `store.py`. This module starts the process, decides what a
failure looks like, and serves the React bundle.

See docs/07-architecture.md.
"""

import logging
import re as _re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

import routes as api
import config
from data import store
from auth import NotAuthenticated

BASE_DIR = Path(__file__).parent
DIST = config.PROJECT_ROOT / "frontend" / "dist"


# Why this is not just `raise`: on a serverless platform a startup exception is
# an opaque 500 with the reason buried in a log nobody is looking at. Recording
# it and answering every request with it costs fifteen lines and turns "the
# function crashed" into "APP_PASSWORD is not set".
STARTUP_ERROR: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global STARTUP_ERROR
    log = logging.getLogger("uvicorn.error")

    problems = config.verify()
    if problems:
        STARTUP_ERROR = "; ".join(problems)
        log.error("config: refusing to serve — %s", STARTUP_ERROR)
        yield
        return

    log.info("config: %s", config.summary())
    for warning in config.warnings():
        log.warning("config: %s", warning)
    if not DIST.is_dir():
        log.warning("config: frontend/dist is missing — run `npm run build` in "
                    "frontend/ to serve the UI from this process")
    try:
        store.init_db()
    except Exception as exc:
        STARTUP_ERROR = _db_help(exc)
        log.error("config: %s", STARTUP_ERROR)
    yield


def _db_help(exc: Exception) -> str:
    """A startup failure should name the fix, not dump a driver traceback."""
    if config.database_backend() != "postgres":
        return f"Could not open the local database: {exc}"

    import psycopg
    host = psycopg.conninfo.conninfo_to_dict(config.DATABASE_URL).get("host", "?")
    hint = ("Supabase's direct host (db.<ref>.supabase.co) is IPv6-only. Most "
            "networks and Vercel cannot reach it — use the Session pooler URI "
            "instead: postgresql://postgres.<ref>:<password>@aws-0-<region>"
            ".pooler.supabase.com:6543/postgres"
            if host.startswith("db.") and "supabase" in host else
            "Check the host, port and that the project is running (a paused "
            "Supabase project accepts no connections).")
    return (f"Could not connect to Postgres at {host}: "
            f"{type(exc).__name__}. {hint} "
            "If the password contains @ : / or ?, percent-encode it (@ is %40). "
            "To run locally without Supabase, leave DATABASE_URL blank.")


# The vendor token travels in the URL path, which means the access log would
# record a working credential on every request. Redact it at the log boundary.
_TOKEN_IN_PATH = _re.compile(r"(/vendor/onboard/)[A-Za-z0-9_\-]{16,}")


def _redact(text: str) -> str:
    """Keep the route, drop the credential. A function replacement rather than
    a template string — backreference escaping is not worth the ambiguity."""
    return _TOKEN_IN_PATH.sub(lambda m: m.group(1) + "<redacted>", text)


class _RedactVendorToken(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # `args` is a tuple for %-style formatting but a *mapping* for
        # %(name)s-style. Rebuilding a mapping as a tuple silently destroys the
        # record — iterating a dict yields its keys — so handle both shapes.
        if isinstance(record.args, tuple):
            record.args = tuple(_redact(a) if isinstance(a, str) else a
                                for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact(v) if isinstance(v, str) else v
                           for k, v in record.args.items()}
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactVendorToken())


app = FastAPI(title="Vendor Onboarding Decision Engine", lifespan=lifespan)

app.include_router(api.router)


@app.middleware("http")
async def refuse_while_misconfigured(request: Request, call_next):
    """Answer every request with the reason, rather than failing silently."""
    if STARTUP_ERROR:
        return JSONResponse({"detail": STARTUP_ERROR}, status_code=503)
    return await call_next(request)

# The React dev server runs on a different origin. The credential is a Bearer
# header rather than a cookie, so `allow_credentials` stays off — there is
# nothing for the browser to attach automatically, which is the point.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# FAILURES
# ============================================================================
#
# Everything under /api answers in JSON. There is no server-rendered error page
# to fall back to, and no redirect: React owns navigation, so an unauthenticated
# API call is a 401 and the client decides to show the login screen.

@app.exception_handler(NotAuthenticated)
async def not_authenticated(request: Request, exc: NotAuthenticated):
    return JSONResponse({"detail": "authentication required"}, status_code=401)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# ============================================================================
# THE REACT APP
# ============================================================================

if (DIST / "assets").is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.api_route("/api/{path:path}", include_in_schema=False,
               methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def unknown_endpoint(path: str):
    """Anything under /api that no router claimed.

    Registered after the API router, so real routes still win, and before the
    SPA route below — otherwise an unknown endpoint would either be answered
    with index.html or, for a non-GET method, with a misleading 405.
    """
    raise StarletteHTTPException(404, "No such endpoint.")


@app.get("/{path:path}", include_in_schema=False)
def react_app(path: str = ""):
    """Serve the file if it exists, otherwise index.html.

    Client-side routing means /dashboard is not a file on disk; the bundle
    resolves it once loaded. The resolved path is checked against the build
    directory, so a crafted `..` cannot read outside it.
    """
    if not DIST.is_dir():
        raise StarletteHTTPException(
            503, "The frontend has not been built. Run `npm run build` in frontend/.")
    if path:
        candidate = (DIST / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(DIST.resolve()):
            return FileResponse(candidate)
    return FileResponse(DIST / "index.html")
