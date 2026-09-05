"""All SQL for the Vendor Onboarding Decision Engine.

Schema is defined in docs/02-data-model.md: exactly three tables.
No business logic lives here — see docs/07-architecture.md.
"""

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "vendor.db"
UPLOAD_DIR = BASE_DIR / "uploads"

RUN_ID_RE = re.compile(r"^VS-\d{4,}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id           TEXT PRIMARY KEY,
  created_at       TEXT NOT NULL,
  vendor_name      TEXT,
  submission_json  TEXT NOT NULL,
  extracted_json   TEXT,
  status           TEXT NOT NULL,
  followup_draft   TEXT,
  followup_sent_at TEXT,
  duration_ms      INTEGER
);

CREATE TABLE IF NOT EXISTS findings (
  id       INTEGER PRIMARY KEY,
  run_id   TEXT NOT NULL REFERENCES runs(run_id),
  rule_id  TEXT NOT NULL,
  severity TEXT NOT NULL,
  stage    TEXT NOT NULL,
  message  TEXT NOT NULL,
  expected TEXT,
  actual   TEXT,
  tag      TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id          INTEGER PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(run_id),
  ts          TEXT NOT NULL,
  stage       TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  actor       TEXT NOT NULL,
  detail_json TEXT,
  duration_ms INTEGER
);

-- An onboarding case exists BEFORE any run does: the employee creates it, the
-- vendor fills it in later. `run_id` is therefore NULL until the vendor submits,
-- at which point the existing PS-2 pipeline takes over unchanged.
CREATE TABLE IF NOT EXISTS onboarding_cases (
  id            TEXT PRIMARY KEY,      -- 'CASE-0001'
  run_id        TEXT REFERENCES runs(run_id),
  vendor_name   TEXT NOT NULL,
  contact_name  TEXT,
  contact_email TEXT,
  token_hash    TEXT NOT NULL UNIQUE,  -- sha256 of the link token; never the token
  status        TEXT NOT NULL,         -- AWAITING_VENDOR | PROCESSING
  created_at    TEXT NOT NULL,
  submitted_at  TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


# --- runs -------------------------------------------------------------------

def _next_run_id(conn) -> str:
    row = conn.execute("SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    n = int(row["run_id"].split("-")[1]) + 1 if row else 1
    return f"VS-{n:04d}"


def create_run(vendor_name: str, submission: dict) -> str:
    """Insert a RUNNING run holding the immutable input snapshot. Returns its id."""
    with _conn() as conn:
        run_id = _next_run_id(conn)
        conn.execute(
            "INSERT INTO runs (run_id, created_at, vendor_name, submission_json, status)"
            " VALUES (?, ?, ?, ?, 'RUNNING')",
            (run_id, _now(), vendor_name, json.dumps(submission)),
        )
        return run_id


def get_run(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["submission"] = json.loads(run["submission_json"])
    run["extracted"] = json.loads(run["extracted_json"]) if run["extracted_json"] else None
    return run


def list_runs(status: str | None = None) -> list[dict]:
    """Newest first. Each row carries finding_count for the dashboard."""
    sql = (
        "SELECT r.*, (SELECT COUNT(*) FROM findings f WHERE f.run_id = r.run_id)"
        " AS finding_count FROM runs r"
    )
    params: tuple = ()
    if status:
        sql += " WHERE r.status = ?"
        params = (status,)
    sql += " ORDER BY r.run_id DESC"
    with _conn() as conn:
        return [dict(row) for row in conn.execute(sql, params)]


def set_status(run_id: str, status: str, duration_ms: int | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, duration_ms = ? WHERE run_id = ?",
            (status, duration_ms, run_id),
        )


def set_extracted(run_id: str, extracted: dict) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET extracted_json = ? WHERE run_id = ?",
            (json.dumps(extracted), run_id),
        )


def set_draft(run_id: str, draft: str) -> None:
    """A draft is not a sent message. `followup_sent_at` stays NULL until a human
    clicks send — that column is the entire human gate."""
    with _conn() as conn:
        conn.execute("UPDATE runs SET followup_draft = ? WHERE run_id = ?", (draft, run_id))


def mark_followup_sent(run_id: str, body: str) -> str:
    """Record that a human sent the follow-up. Returns the timestamp."""
    ts = _now()
    with _conn() as conn:
        conn.execute(
            "UPDATE runs SET followup_draft = ?, followup_sent_at = ? WHERE run_id = ?",
            (body, ts, run_id))
    return ts


# --- findings ---------------------------------------------------------------

def add_findings(run_id: str, findings) -> None:
    """Accepts Finding dataclasses (Part 2 onward) or plain dicts."""
    rows = [asdict(f) if is_dataclass(f) else dict(f) for f in findings]
    if not rows:
        return
    with _conn() as conn:
        conn.executemany(
            "INSERT INTO findings (run_id, rule_id, severity, stage, message, expected, actual, tag)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
                    r["rule_id"],
                    r["severity"],
                    r["stage"],
                    r["message"],
                    r.get("expected"),
                    r.get("actual"),
                    r.get("tag"),
                )
                for r in rows
            ],
        )


def get_findings(run_id: str) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM findings WHERE run_id = ? ORDER BY id", (run_id,))]


# --- events (append-only) ---------------------------------------------------

def get_events(run_id: str) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM events WHERE run_id = ? ORDER BY id", (run_id,))]


def add_event(
    run_id: str,
    stage: str,
    event_type: str,
    actor: str = "system",
    detail: dict | None = None,
    duration_ms: int | None = None,
) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO events (run_id, ts, stage, event_type, actor, detail_json, duration_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                _now(),
                stage,
                event_type,
                actor,
                json.dumps(detail) if detail is not None else None,
                duration_ms,
            ),
        )


# --- uploaded documents -----------------------------------------------------

def upload_dir(run_id: str) -> Path:
    """Where this run's documents live.

    The directory *existing* is the signal that the submission carried documents
    as a channel — a multipart submit creates it even with zero files attached,
    so R02 can report all three as missing. A JSON-only submit never creates it,
    and extraction is skipped entirely.
    """
    if not RUN_ID_RE.match(run_id):
        # Never let a caller-supplied id become a filesystem path.
        raise ValueError(f"invalid run id: {run_id!r}")
    return UPLOAD_DIR / run_id


def saved_documents(run_id: str) -> dict[str, Path]:
    """{doc_type: path} for whatever was actually uploaded."""
    d = upload_dir(run_id)
    return {p.stem: p for p in sorted(d.iterdir())} if d.exists() else {}


# --- onboarding cases -------------------------------------------------------

AWAITING_VENDOR = "AWAITING_VENDOR"
PROCESSING = "PROCESSING"


def new_token() -> str:
    """A vendor link token. Cryptographically secure and unguessable."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Only the hash is ever stored, so a database leak yields no working links."""
    return hashlib.sha256(token.encode()).hexdigest()


def _next_case_id(conn) -> str:
    row = conn.execute(
        "SELECT id FROM onboarding_cases ORDER BY id DESC LIMIT 1").fetchone()
    n = int(row["id"].split("-")[1]) + 1 if row else 1
    return f"CASE-{n:04d}"


def create_case(vendor_name: str, contact_name: str, contact_email: str,
                token: str) -> str:
    """Create a case awaiting the vendor. Stores the token's hash, never the token."""
    with _conn() as conn:
        case_id = _next_case_id(conn)
        conn.execute(
            "INSERT INTO onboarding_cases (id, vendor_name, contact_name,"
            " contact_email, token_hash, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, vendor_name, contact_name, contact_email,
             hash_token(token), AWAITING_VENDOR, _now()),
        )
        return case_id


def get_case_by_token(token: str) -> dict | None:
    """The vendor's only authorisation path. Never look a case up by id for them."""
    if not token:
        return None
    with _conn() as conn:
        row = conn.execute("SELECT * FROM onboarding_cases WHERE token_hash = ?",
                           (hash_token(token),)).fetchone()
    return dict(row) if row else None


def get_case(case_id: str) -> dict | None:
    """Employee-side lookup by id. Not reachable from a vendor request."""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM onboarding_cases WHERE id = ?",
                           (case_id,)).fetchone()
    return dict(row) if row else None


def attach_run_to_case(case_id: str, run_id: str) -> None:
    """Vendor has submitted: bind the case to its run and move it to PROCESSING."""
    with _conn() as conn:
        conn.execute(
            "UPDATE onboarding_cases SET run_id = ?, status = ?, submitted_at = ?"
            " WHERE id = ?",
            (run_id, PROCESSING, _now(), case_id))


def list_cases() -> list[dict]:
    """Newest first, joined to the run so the dashboard needs one query.

    `run_status` / `finding_count` / `last_activity` are NULL while the case is
    still awaiting the vendor — there is no run yet.
    """
    sql = """
      SELECT c.*,
             r.status      AS run_status,
             r.duration_ms AS run_duration_ms,
             (SELECT COUNT(*) FROM findings f WHERE f.run_id = c.run_id)
               AS finding_count,
             (SELECT e.stage FROM events e WHERE e.run_id = c.run_id
               ORDER BY e.id DESC LIMIT 1) AS current_stage,
             (SELECT e.ts FROM events e WHERE e.run_id = c.run_id
               ORDER BY e.id DESC LIMIT 1) AS last_activity
      FROM onboarding_cases c
      LEFT JOIN runs r ON r.run_id = c.run_id
      ORDER BY c.id DESC
    """
    with _conn() as conn:
        return [dict(row) for row in conn.execute(sql)]


# --- demo reset -------------------------------------------------------------

def reset_all() -> None:
    """Wipe every run, finding, event and upload. Used between demo takes."""
    with _conn() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM findings")
        conn.execute("DELETE FROM onboarding_cases")
        conn.execute("DELETE FROM runs")
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
