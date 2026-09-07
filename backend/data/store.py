"""All database access for the Vendor Onboarding Decision Engine.

Two backends behind one API (docs/02-data-model.md):

    DATABASE_URL set -> Supabase Postgres (psycopg)   production
    otherwise        -> SQLite                        development and tests

Nothing outside this module knows which is active. SQL is written once with `?`
placeholders and translated for Postgres; only the DDL differs, and only where
the dialects genuinely differ (autoincrementing integer keys).

Documents live in storage.py, not on this module's disk — but the public
functions still hand out `Path` objects so the pipeline is unchanged.
"""

import contextvars
import hashlib
import json
import re
import secrets
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import config
from data import storage

BASE_DIR = config.PROJECT_ROOT
DB_PATH = BASE_DIR / "vendor.db"          # SQLite backend only

RUN_ID_RE = re.compile(r"^VS-\d{4,}$")
CASE_ID_RE = re.compile(r"^CASE-\d{4,}$")

AWAITING_VENDOR = "AWAITING_VENDOR"
PROCESSING = "PROCESSING"

_SERIAL = {"sqlite": "INTEGER PRIMARY KEY",
           "postgres": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"}

TABLES = ("runs", "findings", "events", "onboarding_cases", "form_templates")


def _schema() -> list[str]:
    serial = _SERIAL[config.database_backend()]
    return [
        """
        CREATE TABLE IF NOT EXISTS runs (
          run_id           TEXT PRIMARY KEY,
          case_id          TEXT,
          created_at       TEXT NOT NULL,
          vendor_name      TEXT,
          submission_json  TEXT NOT NULL,
          extracted_json   TEXT,
          status           TEXT NOT NULL,
          duration_ms      INTEGER
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS findings (
          id       {serial},
          run_id   TEXT NOT NULL REFERENCES runs(run_id),
          rule_id  TEXT NOT NULL,
          severity TEXT NOT NULL,
          stage    TEXT NOT NULL,
          message  TEXT NOT NULL,
          expected TEXT,
          actual   TEXT,
          tag      TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS events (
          id          {serial},
          run_id      TEXT NOT NULL REFERENCES runs(run_id),
          ts          TEXT NOT NULL,
          stage       TEXT NOT NULL,
          event_type  TEXT NOT NULL,
          actor       TEXT NOT NULL,
          detail_json TEXT,
          duration_ms INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS onboarding_cases (
          id                  TEXT PRIMARY KEY,
          run_id              TEXT REFERENCES runs(run_id),
          vendor_name         TEXT NOT NULL,
          contact_name        TEXT,
          contact_email       TEXT,
          token               TEXT,
          token_hash          TEXT NOT NULL UNIQUE,
          status              TEXT NOT NULL,
          created_at          TEXT NOT NULL,
          submitted_at        TEXT,
          created_by_employee TEXT,
          form_id             TEXT,
          form_schema_json    TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS form_templates (
          id          TEXT PRIMARY KEY,
          name        TEXT NOT NULL,
          description TEXT,
          schema_json TEXT NOT NULL,
          created_by  TEXT,
          created_at  TEXT NOT NULL,
          updated_at  TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS findings_run_idx ON findings(run_id)",
        "CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id)",
        "CREATE INDEX IF NOT EXISTS cases_run_idx ON onboarding_cases(run_id)",
    "CREATE INDEX IF NOT EXISTS runs_case_idx ON runs(case_id)",
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _q(sql: str) -> str:
    """One SQL string, two placeholder styles."""
    return sql.replace("?", "%s") if config.database_backend() == "postgres" else sql


# A connection held for the life of one request. Opening one costs ~350ms
# against a pooler in another region, and the run page needs six queries — so
# the handshake, not the SQL, was the page load. Reusing the socket does not
# widen the transaction: every `_conn()` block still commits on its own, which
# is what keeps a crashed pipeline stage from rolling back the events before it.
_bound: contextvars.ContextVar = contextvars.ContextVar("bound_conn", default=None)


@contextmanager
def connection():
    """Bind one connection for this request. Nothing else changes."""
    conn = _open()
    token = _bound.set(conn)
    try:
        yield
    finally:
        _bound.reset(token)
        try:
            conn.commit()
        finally:
            conn.close()


@contextmanager
def _conn():
    bound = _bound.get()
    if bound is not None:
        try:
            yield bound
        except Exception:
            # Leave the shared connection usable for the rest of the request.
            bound.rollback()
            raise
        else:
            bound.commit()
        return

    conn = _open()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _open():
    if config.database_backend() == "postgres":
        import psycopg
        from psycopg.rows import dict_row
        # prepare_threshold=None disables psycopg's automatic statement
        # preparation. Supabase's transaction pooler hands out a different
        # backend per transaction, so a connection can be given one that already
        # holds "_pg3_0" from an earlier client — which raises
        # DuplicatePreparedStatement intermittently, turning a correct decision
        # into an ERROR. Nothing here is hot enough for prepared statements to
        # be worth that.
        return psycopg.connect(config.DATABASE_URL, row_factory=dict_row,
                               prepare_threshold=None)
    import sqlite3
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` will not
# add them to a table that already exists, so bring them in explicitly. Both
# backends accept `ADD COLUMN IF NOT EXISTS` only on Postgres, hence the probe.
_ADDED_COLUMNS = (
    # The link is one per case and stable for its whole life, so it has to be
    # displayable more than once. Hashing it protected nothing: it opens a form
    # prefilled from `runs.submission_json`, which sits in plaintext two tables
    # away — the same breach that leaks a token already has the bank details.
    # The link is one per case and stable for its whole life, so it has to be
    # displayable more than once. Hashing it protected nothing: it opens a form
    # prefilled from `runs.submission_json`, which sits in plaintext two tables
    # away — the same breach that leaks a token already has the bank details.
    # The hash stays as the lookup key.
    ("onboarding_cases", "token", "TEXT"),
    # A case used to have exactly one run. Corrections make that one-to-many, and
    # the back-link has to live on the run: `onboarding_cases.run_id` now means
    # "the latest run", which is not the same question as "which case is this
    # run part of".
    ("runs", "case_id", "TEXT"),
    ("onboarding_cases", "form_id", "TEXT"),
    ("onboarding_cases", "form_schema_json", "TEXT"),
    ("form_templates", "schema_json", "TEXT"),
    ("form_templates", "updated_at", "TEXT"),
)

# Form versioning was removed: a form is now edited in place. `status` was
# DRAFT/PUBLISHED, which no longer exists, and it was NOT NULL — so leaving it
# would make every insert fail on a database created before the change.
# `form_version_id` held a version id, which no longer identifies anything. It
# is not migrated into `form_id`: the two are different kinds of id. A case that
# predates the change keeps its `form_schema_json` snapshot, which is the part
# that actually matters for reading its submission back.
#
# The follow-up email was removed too: the correction link is now the only
# vendor-facing mechanism, so a stored draft and a sent timestamp describe a
# feature that no longer exists.
_DROPPED_COLUMNS = (("form_templates", "status"),
                    ("onboarding_cases", "form_version_id"),
                    ("runs", "followup_draft"),
                    ("runs", "followup_sent_at"))
_DROPPED_TABLES = ("form_template_versions",)


def _existing_columns(conn, table: str) -> set[str]:
    if config.database_backend() == "postgres":
        rows = conn.execute(_q("SELECT column_name FROM information_schema.columns"
                               " WHERE table_name = ?"), (table,)).fetchall()
        return {r["column_name"] for r in rows}
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def init_db() -> None:
    with _conn() as conn:
        if config.database_backend() == "sqlite":
            conn.execute("PRAGMA journal_mode=WAL")
        for statement in _schema():
            conn.execute(statement)
        for table, column, kind in _ADDED_COLUMNS:
            if column not in _existing_columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        _drop_form_versioning(conn)
    seed_standard_template()


def _table_exists(conn, table: str) -> bool:
    if config.database_backend() == "postgres":
        row = conn.execute(_q("SELECT 1 FROM information_schema.tables"
                              " WHERE table_schema = 'public' AND table_name = ?"),
                           (table,)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                           " AND name = ?", (table,)).fetchone()
    return row is not None


def _drop_form_versioning(conn) -> None:
    """Migrate a database that predates the removal of form versions.

    Each form's schema used to live in its newest version row rather than on the
    form itself, so carry that across before dropping the table — otherwise
    every existing form would come back with no questions on it.
    """
    if _table_exists(conn, "form_template_versions"):
        rows = conn.execute(
            "SELECT template_id, schema_json FROM form_template_versions v"
            " WHERE version = (SELECT MAX(version) FROM form_template_versions"
            "                   WHERE template_id = v.template_id)").fetchall()
        for row in rows:
            conn.execute(_q("UPDATE form_templates SET schema_json = ?"
                            " WHERE id = ? AND schema_json IS NULL"),
                         (row["schema_json"], row["template_id"]))

    for table, column in _DROPPED_COLUMNS:
        if column in _existing_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    for table in _DROPPED_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")

    # A form with no schema at all cannot be rendered or reasoned about. That
    # only happens for a row whose versions were already gone, so there is
    # nothing to recover — drop it and let seeding restore the standard form.
    conn.execute("DELETE FROM form_templates WHERE schema_json IS NULL")


# --- runs -------------------------------------------------------------------

def _next_run_id(conn) -> str:
    row = conn.execute("SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    n = int(row["run_id"].split("-")[1]) + 1 if row else 1
    return f"VS-{n:04d}"


def create_run(vendor_name: str, submission: dict) -> str:
    """Insert a RUNNING run holding the immutable input snapshot. Returns its id."""
    with _conn() as conn:
        run_id = _next_run_id(conn)
        conn.execute(_q(
            "INSERT INTO runs (run_id, created_at, vendor_name, submission_json, status)"
            " VALUES (?, ?, ?, ?, 'RUNNING')"),
            (run_id, _now(), vendor_name, json.dumps(submission)))
        return run_id


def get_run(run_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(_q("SELECT * FROM runs WHERE run_id = ?"),
                           (run_id,)).fetchone()
    if row is None:
        return None
    run = dict(row)
    run["submission"] = json.loads(run["submission_json"])
    run["extracted"] = json.loads(run["extracted_json"]) if run["extracted_json"] else None
    return run


def list_runs(status: str | None = None) -> list[dict]:
    """Newest first, with the case each run belongs to.

    `submission_no` is the run's position within its own case: a case is the
    vendor's journey, a run is one execution inside it, and the dashboard has to
    show both without duplicating the case.
    """
    sql = ("SELECT r.*,"
           " (SELECT COUNT(*) FROM findings f WHERE f.run_id = r.run_id)"
           "   AS finding_count,"
           " (SELECT COUNT(*) FROM runs e"
           "   WHERE e.case_id = r.case_id AND e.run_id <= r.run_id)"
           "   AS submission_no,"
           " (SELECT COUNT(*) FROM runs e WHERE e.case_id = r.case_id)"
           "   AS submission_total"
           " FROM runs r")
    params: tuple = ()
    if status:
        sql += " WHERE r.status = ?"
        params = (status,)
    sql += " ORDER BY r.run_id DESC"
    with _conn() as conn:
        return [dict(row) for row in conn.execute(_q(sql), params)]


def set_status(run_id: str, status: str, duration_ms: int | None = None) -> None:
    with _conn() as conn:
        conn.execute(_q("UPDATE runs SET status = ?, duration_ms = ? WHERE run_id = ?"),
                     (status, duration_ms, run_id))


def set_extracted(run_id: str, extracted: dict) -> None:
    with _conn() as conn:
        conn.execute(_q("UPDATE runs SET extracted_json = ? WHERE run_id = ?"),
                     (json.dumps(extracted), run_id))


# --- findings ---------------------------------------------------------------

def add_findings(run_id: str, findings) -> None:
    """Accepts Finding dataclasses or plain dicts."""
    rows = [asdict(f) if is_dataclass(f) else dict(f) for f in findings]
    if not rows:
        return
    # executemany is a *cursor* method in psycopg 3; sqlite3 only offers it on
    # the connection as a convenience. Go through a cursor, which both provide.
    with _conn() as conn:
        cur = conn.cursor()          # not `with`: sqlite3 cursors are not
        try:                         # context managers, psycopg's are
            cur.executemany(_q(
                "INSERT INTO findings (run_id, rule_id, severity, stage, message,"
                " expected, actual, tag) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"),
                [(run_id, r["rule_id"], r["severity"], r["stage"], r["message"],
                  r.get("expected"), r.get("actual"), r.get("tag")) for r in rows])
        finally:
            cur.close()


def get_findings(run_id: str) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(_q(
            "SELECT * FROM findings WHERE run_id = ? ORDER BY id"), (run_id,))]


# --- events (append-only) ---------------------------------------------------

def get_events(run_id: str) -> list[dict]:
    with _conn() as conn:
        return [dict(r) for r in conn.execute(_q(
            "SELECT * FROM events WHERE run_id = ? ORDER BY id"), (run_id,))]


def add_event(run_id: str, stage: str, event_type: str, actor: str = "system",
              detail: dict | None = None, duration_ms: int | None = None) -> None:
    with _conn() as conn:
        conn.execute(_q(
            "INSERT INTO events (run_id, ts, stage, event_type, actor, detail_json,"
            " duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)"),
            (run_id, _now(), stage, event_type, actor,
             json.dumps(detail) if detail is not None else None, duration_ms))


def has_event(run_id: str, event_type: str) -> bool:
    with _conn() as conn:
        row = conn.execute(_q("SELECT 1 AS present FROM events WHERE run_id = ?"
                              " AND event_type = ? LIMIT 1"),
                           (run_id, event_type)).fetchone()
    return row is not None


# --- onboarding cases -------------------------------------------------------

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
                token: str, created_by: str | None = None,
                form_id: str | None = None,
                form_schema: dict | None = None) -> str:
    """Create a case awaiting the vendor.

    Keeps the token and its hash — the hash is the lookup key, the token is what
    the employee copies. Also keeps a *snapshot* of the form schema, so the case
    renders and validates against exactly the questions it was created with,
    even after the form itself is edited.
    """
    with _conn() as conn:
        case_id = _next_case_id(conn)
        conn.execute(_q(
            "INSERT INTO onboarding_cases (id, vendor_name, contact_name,"
            " contact_email, token, token_hash, status, created_at,"
            " created_by_employee, form_id, form_schema_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
            (case_id, vendor_name, contact_name, contact_email,
             token, hash_token(token), AWAITING_VENDOR, _now(), created_by,
             form_id, json.dumps(form_schema) if form_schema else None))
        return case_id


def rotate_case_token(case_id: str, token: str) -> None:
    """Replace the invite link. The previous token stops working immediately."""
    with _conn() as conn:
        conn.execute(_q("UPDATE onboarding_cases SET token = ?, token_hash = ?"
                        " WHERE id = ?"), (token, hash_token(token), case_id))


def _case(row) -> dict | None:
    """Row -> case, with the schema snapshot decoded."""
    if row is None:
        return None
    case = dict(row)
    case["form_schema"] = (json.loads(case["form_schema_json"])
                           if case.get("form_schema_json") else None)
    return case


def get_case_by_token(token: str) -> dict | None:
    """The vendor's only authorisation path. Never look a case up by id for them."""
    if not token:
        return None
    with _conn() as conn:
        row = conn.execute(_q("SELECT * FROM onboarding_cases WHERE token_hash = ?"),
                           (hash_token(token),)).fetchone()
    return _case(row)


def get_case(case_id: str) -> dict | None:
    """Employee-side lookup by id. Not reachable from a vendor request."""
    with _conn() as conn:
        row = conn.execute(_q("SELECT * FROM onboarding_cases WHERE id = ?"),
                           (case_id,)).fetchone()
    return _case(row)


def get_case_for_run(run_id: str) -> dict | None:
    """The case this run belongs to, by membership rather than by "is latest"."""
    with _conn() as conn:
        row = conn.execute(_q(
            "SELECT c.* FROM onboarding_cases c JOIN runs r ON r.case_id = c.id"
            " WHERE r.run_id = ?"), (run_id,)).fetchone()
        if row is None:
            # A run created before `runs.case_id` existed.
            row = conn.execute(_q("SELECT * FROM onboarding_cases WHERE run_id = ?"),
                               (run_id,)).fetchone()
    return _case(row)


def attach_run_to_case(case_id: str, run_id: str) -> None:
    """Vendor has submitted: bind the case to its run and move it to PROCESSING.

    Both directions are written. `onboarding_cases.run_id` is the *latest* run,
    which is what the dashboard shows; `runs.case_id` is the durable membership,
    which is what survives a correction cycle creating a second run.
    """
    with _conn() as conn:
        conn.execute(_q("UPDATE onboarding_cases SET run_id = ?, status = ?,"
                        " submitted_at = ? WHERE id = ?"),
                     (run_id, PROCESSING, _now(), case_id))
        conn.execute(_q("UPDATE runs SET case_id = ? WHERE run_id = ?"),
                     (case_id, run_id))


def reopen_case_for_correction(case_id: str) -> None:
    """Let the vendor submit again against the same case.

    Same case, same URL, same form. Only the gate moves: the link the vendor
    already has starts accepting a submission again, and the next one creates a
    new run rather than replacing the old one.
    """
    with _conn() as conn:
        conn.execute(_q("UPDATE onboarding_cases SET status = ? WHERE id = ?"),
                     (AWAITING_VENDOR, case_id))


def runs_for_case(case_id: str) -> list[dict]:
    """Every submission round for this case, oldest first.

    Carries the finding count so a reviewer can read the shape of the cycle -
    three issues, then one, then none - without opening each run.
    """
    with _conn() as conn:
        return [dict(r) for r in conn.execute(_q(
            "SELECT r.run_id, r.status, r.created_at, r.duration_ms,"
            " (SELECT COUNT(*) FROM findings f WHERE f.run_id = r.run_id)"
            "   AS finding_count"
            " FROM runs r WHERE r.case_id = ? ORDER BY r.run_id"), (case_id,))]


def list_cases() -> list[dict]:
    """Newest first, joined to the run so the dashboard needs one query.

    `created_at` is the case's own creation time. `last_activity_at` is the most
    recent *meaningful* moment for the case, computed here rather than in the
    template: the latest of the case being created, the vendor submitting, and
    the last audit event on its run. A case still awaiting a vendor has no run
    and no events, so it falls back to its creation time instead of showing a
    dash — that is real information, not missing data.
    """
    sql = """
      SELECT c.*,
             r.status      AS run_status,
             r.duration_ms AS run_duration_ms,
             (SELECT COUNT(*) FROM findings f WHERE f.run_id = c.run_id)
               AS finding_count,
             (SELECT e.stage FROM events e WHERE e.run_id = c.run_id
               ORDER BY e.id DESC LIMIT 1) AS current_stage,
             (SELECT e.event_type FROM events e WHERE e.run_id = c.run_id
               ORDER BY e.id DESC LIMIT 1) AS last_event_type,
             (SELECT e.ts FROM events e WHERE e.run_id = c.run_id
               ORDER BY e.id DESC LIMIT 1) AS last_event_at
      FROM onboarding_cases c
      LEFT JOIN runs r ON r.run_id = c.run_id
      ORDER BY c.id DESC
    """
    with _conn() as conn:
        rows = [dict(row) for row in conn.execute(sql)]

    for row in rows:
        row["last_activity_at"] = max(
            t for t in (row["created_at"], row.get("submitted_at"),
                        row.get("last_event_at")) if t)
        row["last_activity"] = row["last_activity_at"]      # kept for callers
    return rows


# --- forms ------------------------------------------------------------------
#
# A form is a form. There are no versions: editing one changes it in place, and
# the next onboarding uses whatever it says now. What *is* kept is a snapshot of
# the schema on each case (`onboarding_cases.form_schema_json`), so a submission
# stays readable against the questions that were actually asked, even after the
# form has moved on. That is an audit record, not a version.

def _next_id(conn, table: str, prefix: str) -> str:
    """The next id for this prefix.

    Scoped to the prefix on purpose: a migrated database can hold ids from an
    older naming scheme alongside new ones, and counting from whichever sorts
    highest overall hands out an id that already exists.
    """
    row = conn.execute(
        _q(f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY id DESC LIMIT 1"),
        (f"{prefix}-%",)).fetchone()
    n = int(row["id"].split("-")[1]) + 1 if row else 1
    return f"{prefix}-{n:04d}"


def _form(row) -> dict | None:
    if row is None:
        return None
    form = dict(row)
    form["schema"] = json.loads(form["schema_json"]) if form.get("schema_json") else {}
    return form


def create_template(name: str, description: str, created_by: str | None = None,
                    schema: dict | None = None) -> str:
    """Create a form. Returns its id."""
    with _conn() as conn:
        form_id = _next_id(conn, "form_templates", "FORM")
        now = _now()
        conn.execute(_q(
            "INSERT INTO form_templates (id, name, description, schema_json,"
            " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"),
            (form_id, name, description, json.dumps(schema or {"sections": []}),
             created_by, now, now))
    return form_id


def get_template(form_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(_q("SELECT * FROM form_templates WHERE id = ?"),
                           (form_id,)).fetchone()
    return _form(row)


def list_templates() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM form_templates ORDER BY id").fetchall()
    return [_form(r) for r in rows]


def update_template(form_id: str, name: str | None = None,
                    description: str | None = None,
                    schema: dict | None = None, touch: bool = True) -> bool:
    """Edit in place. Only the fields given are touched.

    `touch=False` writes without claiming a human did it, which is how seeding
    refreshes the generated standard form. Bumping `updated_at` there would make
    the form look edited and stop it tracking the generator ever again.
    """
    sets, values = [], []
    if name is not None:
        sets.append("name = ?")
        values.append(name)
    if description is not None:
        sets.append("description = ?")
        values.append(description)
    if schema is not None:
        sets.append("schema_json = ?")
        values.append(json.dumps(schema))
    if not sets:
        return get_template(form_id) is not None

    if touch:
        sets.append("updated_at = ?")
        values.append(_now())
    values.append(form_id)
    with _conn() as conn:
        if conn.execute(_q("SELECT id FROM form_templates WHERE id = ?"),
                        (form_id,)).fetchone() is None:
            return False
        conn.execute(_q(f"UPDATE form_templates SET {', '.join(sets)} WHERE id = ?"),
                     tuple(values))
    return True


def delete_template(form_id: str) -> bool:
    """Refused while any case still points at this form — an onboarding must
    always be able to name the form it was created from."""
    with _conn() as conn:
        used = conn.execute(_q("SELECT COUNT(*) AS n FROM onboarding_cases"
                               " WHERE form_id = ?"), (form_id,)).fetchone()["n"]
        if used:
            return False
        row = conn.execute(_q("SELECT id FROM form_templates WHERE id = ?"),
                           (form_id,)).fetchone()
        if row is None:
            return False
        conn.execute(_q("DELETE FROM form_templates WHERE id = ?"), (form_id,))
    return True


def duplicate_template(form_id: str, name: str,
                       created_by: str | None = None) -> str | None:
    source = get_template(form_id)
    if source is None:
        return None
    return create_template(name, source["description"], created_by, source["schema"])


def seed_standard_template() -> str | None:
    """Ensure the built-in form exists and is usable. Runs on every start-up.

    An existing one is left alone: reordered, reworded, an extra question added
    — all deliberate, all kept. It is rebuilt from the engine in two cases, both
    meaning the form can no longer do its job:

      * it no longer validates, because the engine dropped something it asks for
      * it has fallen behind, and no longer asks for something the rules read

    Deliberately not a third case for "nobody has edited it yet". Timestamps
    here have second resolution, so an edit made in the same second as creation
    is indistinguishable from no edit — and the failure mode is silently
    discarding someone's work. Improvements to the generated wording reach an
    existing install by deleting the form and letting it be reseeded.

    A vendor filling in a form that cannot satisfy the engine is worse than a
    lost edit. Anyone wanting a genuinely different form should duplicate this
    one, which is what the copy button is for.
    """
    from engine import forms
    existing = standard_form()
    if existing is not None:
        stale = (forms.validate_schema(existing["schema"])
                 or forms.missing_engine_fields(existing["schema"]))
        if stale:
            update_template(existing["id"], description=forms.STANDARD_DESCRIPTION,
                            schema=forms.standard_schema(), touch=False)
        return existing["id"]
    return create_template(forms.STANDARD_TEMPLATE_NAME, forms.STANDARD_DESCRIPTION,
                           "system", forms.standard_schema())


def standard_form() -> dict | None:
    """The built-in PS-2 form, used when an onboarding names no other."""
    from engine import forms
    with _conn() as conn:
        row = conn.execute(_q("SELECT * FROM form_templates WHERE name = ?"),
                           (forms.STANDARD_TEMPLATE_NAME,)).fetchone()
    return _form(row)


# --- documents --------------------------------------------------------------

def storage_prefix(run_id: str) -> str:
    """`onboarding/CASE-0001` when the run belongs to a case, else `runs/VS-0001`.

    Derived from the database, never from anything the vendor sends.
    """
    if not RUN_ID_RE.match(run_id or ""):
        raise ValueError(f"invalid run id: {run_id!r}")
    case = get_case_for_run(run_id)
    if case and CASE_ID_RE.match(case["id"]):
        return f"onboarding/{case['id']}"
    return f"runs/{run_id}"


def _candidate_prefixes(run_id: str) -> list[str]:
    """Where this run's documents might live, most specific first.

    A run gains its case *after* it is created, so anything stored before the
    binding sits under the run prefix and anything after sits under the case
    prefix. Reading both makes the write order irrelevant — otherwise attaching a
    case silently orphans documents already uploaded.
    """
    prefixes = [storage_prefix(run_id)]
    fallback = f"runs/{run_id}"
    if fallback not in prefixes:
        prefixes.append(fallback)
    return prefixes


def save_document(run_id: str, doc_type: str, ext: str, data: bytes) -> str:
    return storage.put(storage_prefix(run_id), doc_type, ext, data)


def saved_documents(run_id: str) -> dict[str, Path]:
    """{doc_type: local Path}. Remote objects are materialised on demand, so the
    extraction stage still receives a Path and needs no knowledge of storage."""
    keys: dict[str, str] = {}
    for prefix in reversed(_candidate_prefixes(run_id)):   # specific wins
        keys.update(storage.list_documents(prefix))
    return {doc_type: storage.fetch(key) for doc_type, key in keys.items()}


def has_document_channel(run_id: str) -> bool:
    """Did this submission carry documents *as a channel*?

    Recorded as an intake event rather than inferred from a directory, because
    object storage has no empty folders. A JSON-only API submission never emits
    it, so R02 correctly stays silent for that path.
    """
    return has_event(run_id, "documents_expected")
