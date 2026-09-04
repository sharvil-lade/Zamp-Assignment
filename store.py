"""All SQL for the Vendor Onboarding Decision Engine.

Schema is defined in docs/02-data-model.md: exactly three tables.
No business logic lives here — see docs/07-architecture.md.
"""

import json
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "vendor.db"
UPLOAD_DIR = BASE_DIR / "uploads"

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
    with _conn() as conn:
        conn.execute("UPDATE runs SET followup_draft = ? WHERE run_id = ?", (draft, run_id))


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


# --- demo reset -------------------------------------------------------------

def reset_all() -> None:
    """Wipe every run, finding, event and upload. Used between demo takes."""
    with _conn() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM findings")
        conn.execute("DELETE FROM runs")
    if UPLOAD_DIR.exists():
        shutil.rmtree(UPLOAD_DIR)
