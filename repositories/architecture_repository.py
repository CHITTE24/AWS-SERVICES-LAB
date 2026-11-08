"""Stores Architecture Map diagrams, the verification EVIDENCE history, and workflow identity snapshots.

Everything lives in its OWN SQLite file (ARCHITECTURE_DATABASE_PATH, default data/architecture.db), so the
existing data/lab.db (run history, submissions, workflows) is never changed.

Schema version 2 (see docs/ARCHITECTURE_MAP.md):
  - A NEW file is created directly at version 2.
  - An EXISTING file at another version is never changed automatically: the app stops with a clear message,
    and you upgrade it explicitly with scripts/migrate_architecture_db.py (which makes a backup first).

Evidence is an APPEND-ONLY audit trail: this file has no function that changes or deletes evidence.
"""

import sqlite3  # Python's built-in SQLite library.

from core.config import get_settings  # Reads ARCHITECTURE_DATABASE_PATH.
from experiments import workflow_states as clock  # utc_now() and to_text() (tests can move the clock).

SCHEMA_VERSION = 2

SCHEMA = """
    CREATE TABLE IF NOT EXISTS schema_info (
        version     INTEGER NOT NULL            -- One row: the schema version of this file.
    );
    CREATE TABLE IF NOT EXISTS diagrams (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        owner       TEXT NOT NULL,              -- Only the owner can see or change a diagram.
        name        TEXT NOT NULL,
        version     INTEGER NOT NULL,           -- +1 on every save; a stale save is refused.
        body        TEXT NOT NULL,              -- The diagram as JSON (boxes, arrows, positions, scope).
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS evidence (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_key TEXT NOT NULL,              -- "s3:bucket" (resource), "a|operation|b" (operation),
                                                -- or "unattributed|..." when the identity was not certain.
        subject_kind TEXT NOT NULL,             -- "resource", "operation", or "unattributed".
        check_id    TEXT NOT NULL,              -- Which check produced it, for example "workflow.upload".
        outcome     TEXT NOT NULL,              -- "pass", "fail", or "partial".
        status      TEXT NOT NULL,              -- For example "Success", "Access Denied", "Verification Mismatch".
        detail      TEXT NOT NULL,              -- A short, safe explanation (never a raw error or a secret).
        source      TEXT NOT NULL,              -- "automatic", "manual", or "simulated".
        observed_at TEXT NOT NULL,              -- When it was observed (UTC).
        recorded_at TEXT NOT NULL,              -- When it was written here (UTC).
        recorded_by TEXT NOT NULL,              -- A username (manual records and Verify Now), or "system".
        reference   TEXT,                       -- Environment check run, workflow ID, or submission ID.
        notes       TEXT,                       -- Manual records only: your evidence notes.
        account     TEXT,                       -- The AWS account the operation used (NULL = unknown).
        region      TEXT                        -- The AWS region the operation used (NULL = unknown).
    );
    CREATE INDEX IF NOT EXISTS evidence_by_subject ON evidence (subject_key, id);
    CREATE TABLE IF NOT EXISTS workflow_context (
        workflow_id TEXT PRIMARY KEY,           -- One row per REAL Live Workflow.
        app_key     TEXT,                       -- The app's identity when it uploaded (NULL = unknown).
        bucket      TEXT,                       -- The resources the upload really used ...
        kms_key_id  TEXT,
        table_name  TEXT,                       -- (NULL when the workflow is stored in SQLite)
        lambda_name TEXT,                       -- ... and the function that was expected to process it.
        account     TEXT,
        region      TEXT,
        created_at  TEXT NOT NULL,
        synced_final INTEGER NOT NULL DEFAULT 0 -- 1 when its Lambda stages are final and recorded.
    );
"""


class VersionConflict(Exception):
    """The diagram was saved elsewhere (for example in another tab) since it was loaded."""


class SchemaMismatch(RuntimeError):
    """The architecture database file has an older (or unknown) layout. It is never changed automatically."""


def connect():
    path = get_settings()["architecture_database_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def file_version(connection):
    """The schema version of an open file: 0 = empty file, 1 = the first layout, 2 = current, -1 = unknown."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables:
        return 0
    if "schema_info" in tables:
        row = connection.execute("SELECT version FROM schema_info").fetchone()
        return row[0] if row else -1
    if "evidence" in tables and "diagrams" in tables:
        return 1
    return -1


def create_tables():
    """Creates a NEW file at the current version. An existing file at another version is left untouched."""
    connection = connect()
    try:
        version = file_version(connection)
        if version not in (0, SCHEMA_VERSION):
            path = get_settings()["architecture_database_path"]
            raise SchemaMismatch(
                f"{path} has schema version {version}, but this app needs version {SCHEMA_VERSION}. It was NOT "
                "changed. Upgrade it explicitly (a backup is made first): "
                "python scripts/migrate_architecture_db.py --apply   (see docs/ARCHITECTURE_MAP.md)")
        connection.executescript(SCHEMA)
        if version == 0:
            connection.execute("INSERT INTO schema_info (version) VALUES (?)", (SCHEMA_VERSION,))
        connection.commit()
    finally:
        connection.close()


# ---------------------------------------------------------------- evidence (append-only)

def add_evidence(subject_key, subject_kind, check_id, outcome, status, detail, source="automatic",
                 observed_at=None, recorded_by="system", reference=None, notes=None, account=None, region=None):
    """Appends one evidence record. Returns True if it was added.

    An automatic record that repeats the SAME result for the SAME reference (for example the same workflow
    seen again by a status check) is not added twice. A CHANGED result is always added, so the history shows
    every step: for example Retrying, then Success.
    """
    now = clock.to_text(clock.utc_now())
    connection = connect()
    try:
        with connection:
            if reference and source != "manual":
                duplicate = connection.execute(
                    "SELECT 1 FROM evidence WHERE subject_key = ? AND check_id = ? AND reference = ? "
                    "AND outcome = ? AND status = ? AND source = ?",
                    (subject_key, check_id, reference, outcome, status, source)).fetchone()
                if duplicate:
                    return False
            connection.execute(
                "INSERT INTO evidence (subject_key, subject_kind, check_id, outcome, status, detail, source, "
                "observed_at, recorded_at, recorded_by, reference, notes, account, region) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (subject_key, subject_kind, check_id, outcome, status, detail, source, observed_at or now, now,
                 recorded_by, reference, notes, account, region))
        return True
    finally:
        connection.close()


def evidence_for(subject_keys, viewer, limit_per_subject=10, account=None):
    """The newest evidence for each subject key, newest first: {key: [records]}. One query per 500 keys.

    Manual records are private to the person who recorded them; automatic records are shared by everyone
    who may log in (they describe the app's own AWS environment, like "Check AWS environment" does).
    account: if given, records from a DIFFERENT known account are left out (scoping).
    """
    keys = [key for key in dict.fromkeys(subject_keys) if key]
    result = {key: [] for key in keys}
    connection = connect()
    try:
        for start in range(0, len(keys), 500):  # SQLite allows a limited number of "?" per query.
            chunk = keys[start:start + 500]
            rows = connection.execute(
                f"""SELECT * FROM (
                        SELECT *, ROW_NUMBER() OVER (PARTITION BY subject_key ORDER BY id DESC) AS position
                        FROM evidence
                        WHERE subject_key IN ({', '.join('?' for _ in chunk)})
                          AND (source != 'manual' OR recorded_by = ?)
                          AND (? IS NULL OR account IS NULL OR account = ?)
                    ) WHERE position <= ? ORDER BY subject_key, id DESC""",
                (*chunk, viewer, account, account, limit_per_subject)).fetchall()
            for row in rows:
                item = dict(row)
                item.pop("position")
                result[item["subject_key"]].append(item)
    finally:
        connection.close()
    return result


def recent_unattributed(since_text, limit=5):
    """The newest evidence that could not be tied to an exact resource (shown separately, never on a box)."""
    connection = connect()
    try:
        total = connection.execute("SELECT COUNT(*) FROM evidence WHERE subject_kind = 'unattributed' "
                                   "AND observed_at >= ?", (since_text,)).fetchone()[0]
        rows = connection.execute("SELECT check_id, status, detail, observed_at, reference FROM evidence "
                                  "WHERE subject_kind = 'unattributed' AND observed_at >= ? ORDER BY id DESC LIMIT ?",
                                  (since_text, limit)).fetchall()
        return total, [dict(row) for row in rows]
    finally:
        connection.close()


def count_recent(recorded_by, check_prefix, since_text):
    """How many records were caused recently: by one user, or by everyone when recorded_by is None."""
    connection = connect()
    try:
        row = connection.execute("SELECT COUNT(*) AS total FROM evidence WHERE (? IS NULL OR recorded_by = ?) "
                                 "AND check_id LIKE ? AND recorded_at >= ?",
                                 (recorded_by, recorded_by, check_prefix + "%", since_text)).fetchone()
        return row["total"]
    finally:
        connection.close()


# ---------------------------------------------------------------- workflow identity snapshots

def save_workflow_context(context):
    """Stores the exact resources a workflow used, at the moment it used them. Never overwritten."""
    connection = connect()
    try:
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO workflow_context (workflow_id, app_key, bucket, kms_key_id, table_name, "
                "lambda_name, account, region, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (context["workflow_id"], context["app_key"], context["bucket"], context["kms_key_id"],
                 context["table_name"], context["lambda_name"], context["account"], context["region"],
                 context["created_at"]))
    finally:
        connection.close()


def get_workflow_context(workflow_id):
    connection = connect()
    try:
        row = connection.execute("SELECT * FROM workflow_context WHERE workflow_id = ?", (workflow_id,)).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def workflows_to_sync(since_text, limit=20):
    """Recent real workflows whose Lambda results are not final yet (their evidence may still arrive)."""
    connection = connect()
    try:
        rows = connection.execute("SELECT workflow_id FROM workflow_context WHERE synced_final = 0 AND created_at >= ? "
                                  "ORDER BY created_at DESC LIMIT ?", (since_text, limit)).fetchall()
        return [row["workflow_id"] for row in rows]
    finally:
        connection.close()


def mark_workflow_synced(workflow_id):
    connection = connect()
    try:
        with connection:
            connection.execute("UPDATE workflow_context SET synced_final = 1 WHERE workflow_id = ?", (workflow_id,))
    finally:
        connection.close()


# ---------------------------------------------------------------- diagrams (owner-only)

def list_diagrams(owner):
    connection = connect()
    try:
        rows = connection.execute("SELECT id, name, version, updated_at FROM diagrams WHERE owner = ? "
                                  "ORDER BY updated_at DESC", (owner,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def create_diagram(owner, name, body):
    now = clock.to_text(clock.utc_now())
    connection = connect()
    try:
        with connection:
            cursor = connection.execute("INSERT INTO diagrams (owner, name, version, body, created_at, updated_at) "
                                        "VALUES (?, ?, 1, ?, ?, ?)", (owner, name, body, now, now))
        return cursor.lastrowid
    finally:
        connection.close()


def get_diagram(diagram_id, owner):
    """Returns the diagram, or None if it does not exist OR belongs to someone else."""
    connection = connect()
    try:
        row = connection.execute("SELECT * FROM diagrams WHERE id = ? AND owner = ?", (diagram_id, owner)).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def update_diagram(diagram_id, owner, name, body, expected_version):
    """Saves a new version. Raises VersionConflict if someone saved since expected_version. Returns the new version."""
    now = clock.to_text(clock.utc_now())
    connection = connect()
    try:
        with connection:
            # The WHERE clause checks owner and version in the same statement, so two saves cannot both win.
            cursor = connection.execute(
                "UPDATE diagrams SET name = ?, body = ?, version = version + 1, updated_at = ? "
                "WHERE id = ? AND owner = ? AND version = ?", (name, body, now, diagram_id, owner, expected_version))
        if cursor.rowcount != 1:
            raise VersionConflict("The diagram was changed elsewhere. Reload it before saving.")
        return expected_version + 1
    finally:
        connection.close()


def delete_diagram(diagram_id, owner):
    """Deletes a DESIGN only. Evidence is never deleted. Returns True if a diagram was deleted."""
    connection = connect()
    try:
        with connection:
            cursor = connection.execute("DELETE FROM diagrams WHERE id = ? AND owner = ?", (diagram_id, owner))
        return cursor.rowcount == 1
    finally:
        connection.close()
