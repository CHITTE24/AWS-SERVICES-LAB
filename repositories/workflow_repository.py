"""Stores Live Workflow Monitor workflows and their stages.

Two places can hold a workflow's stages (its "store"):
  - "sqlite":   the local file data/lab.db. Used for simulated workflows, local development, and tests.
  - "dynamodb": the DynamoDB table aws-lab-workflows (repositories/workflow_dynamodb.py). Needed for
                real workflows once the Lambda function reports its own stages (Phase 3).
The store is chosen when a workflow is created and saved with it, so later reads always use the right one.

Every workflow is ALSO listed in the local SQLite table "workflows" (the index). That keeps failed,
incomplete, and expired workflows visible in the app's history, even after DynamoDB deletes old items.

Stage changes are CONDITIONAL: the database itself refuses a change that the rules in
experiments/workflow_states.py do not allow. This makes duplicate events, retries, and two writers at the
same time safe: the first valid write wins, and the others are ignored (set_stage returns False).
"""

import sqlite3  # Python's built-in SQLite library.

from core.config import get_settings  # Reads WORKFLOW_BACKEND.
from experiments import workflow_states as rules  # States, stages, and allowed changes.
from repositories import workflow_dynamodb  # The DynamoDB store.
from repositories.history_repository import connect  # Opens data/lab.db (the same file as the run history).

STORE_SQLITE = "sqlite"
STORE_DYNAMODB = "dynamodb"

# The columns of the index table, in order.
META_COLUMNS = ["workflow_id", "owner", "mode", "scenario", "file_name", "file_size", "s3_key", "created_at",
                "expires_at", "store"]
STAGE_COLUMNS = ["workflow_id", "stage_id", "position", "name", "state", "detail", "source", "attempt",
                 "started_at", "updated_at", "finished_at", "deadline_at"]


def create_tables():
    """Creates the two SQLite tables if they do not exist yet. Safe to run every time the app starts."""
    connection = connect()
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,   -- 32 hexadecimal characters, random (uuid4).
                owner       TEXT NOT NULL,      -- The logged-in username. Only the owner can see it.
                mode        TEXT NOT NULL,      -- "aws" (real AWS calls) or "simulated" (nothing sent to AWS).
                scenario    TEXT,               -- Which simulation was chosen (simulated workflows only).
                file_name   TEXT,               -- The safe file name. The file itself is never stored here.
                file_size   INTEGER,
                s3_key      TEXT,               -- Where the object was uploaded (real workflows only).
                created_at  TEXT NOT NULL,      -- UTC time, ISO format.
                expires_at  TEXT,               -- When DynamoDB deletes the stages (the index row stays).
                store       TEXT NOT NULL       -- Where the stages are kept: "sqlite" or "dynamodb".
            )""")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS workflow_stages (
                workflow_id TEXT NOT NULL,
                stage_id    TEXT NOT NULL,      -- For example "upload" or "kms".
                position    INTEGER NOT NULL,   -- Display order.
                name        TEXT NOT NULL,
                state       TEXT NOT NULL,      -- Pending, Running, Retrying, Completed, Failed, ...
                detail      TEXT,               -- A short, safe explanation. Never a raw error.
                source      TEXT,               -- Who wrote the state: fastapi, lambda, or simulator.
                attempt     INTEGER NOT NULL DEFAULT 0,
                started_at  TEXT,
                updated_at  TEXT,
                finished_at TEXT,
                deadline_at TEXT,               -- After this time a waiting stage becomes Timed Out.
                PRIMARY KEY (workflow_id, stage_id)
            )""")
        connection.commit()
    finally:
        connection.close()


def choose_store(mode):
    """Simulated workflows always stay local (offline). Real ones use WORKFLOW_BACKEND."""
    if mode == "simulated":
        return STORE_SQLITE
    backend = get_settings()["workflow_backend"]
    if backend not in (STORE_SQLITE, STORE_DYNAMODB):
        raise ValueError("WORKFLOW_BACKEND must be sqlite or dynamodb.")
    return backend


def create_workflow(meta, stages):
    """Saves a new workflow and its stages. Returns False if this workflow_id already exists.

    Returning False (instead of failing) makes creation idempotent: a double click or a resubmitted
    form with the same workflow_id never creates a second workflow.
    """
    if meta["store"] == STORE_DYNAMODB:
        # DynamoDB first: if it fails, nothing is listed locally for a workflow that does not exist.
        if not workflow_dynamodb.create_workflow(meta, stages):
            return False
        insert_index(meta)
        return True

    connection = connect()
    try:
        with connection:  # One transaction: either everything is saved, or nothing.
            insert_meta(connection, meta)
            for stage in stages:
                values = [stage.get(column) for column in STAGE_COLUMNS[1:]]
                connection.execute(f"INSERT INTO workflow_stages ({', '.join(STAGE_COLUMNS)}) "
                                   f"VALUES ({', '.join('?' for _ in STAGE_COLUMNS)})", [meta["workflow_id"]] + values)
        return True
    except sqlite3.IntegrityError:
        return False  # The workflow_id already exists.
    finally:
        connection.close()


def insert_meta(connection, meta):
    connection.execute(f"INSERT INTO workflows ({', '.join(META_COLUMNS)}) VALUES ({', '.join('?' for _ in META_COLUMNS)})",
                       [meta.get(column) for column in META_COLUMNS])


def insert_index(meta):
    # The local index row for a DynamoDB workflow. "OR IGNORE": a repeated call changes nothing.
    connection = connect()
    try:
        with connection:
            connection.execute(f"INSERT OR IGNORE INTO workflows ({', '.join(META_COLUMNS)}) "
                               f"VALUES ({', '.join('?' for _ in META_COLUMNS)})",
                               [meta.get(column) for column in META_COLUMNS])
    finally:
        connection.close()


def set_stage(workflow, stage_id, new_state, detail, source, attempt=None, deadline_at=None, now=None):
    """Changes one stage, ONLY if the rules allow it. Returns True if it changed, False if it was ignored.

    workflow: the dictionary from get_workflow() (only "workflow_id" and "store" are used).
    attempt: the writer's attempt number. A write from an older attempt than the stored one is ignored.
    deadline_at: a new deadline for a stage that is still waiting (text), or None to keep the old one.
    """
    now_text = rules.to_text(now or rules.utc_now())
    if workflow["store"] == STORE_DYNAMODB:
        return workflow_dynamodb.set_stage(workflow["workflow_id"], stage_id, new_state, detail, source,
                                           attempt, deadline_at, now_text)

    previous_states = rules.ALLOWED_PREVIOUS.get(new_state, ())
    if not previous_states:
        return False
    is_start = new_state in (rules.RUNNING, rules.RETRYING)
    finished_at = now_text if new_state in rules.FINAL_STATES else None
    placeholders = ", ".join("?" for _ in previous_states)
    connection = connect()
    try:
        with connection:
            # The WHERE clause is the safety check: SQLite changes the row only if its CURRENT state may be
            # replaced, and the writer's attempt is not older than the stored one. One statement = atomic.
            cursor = connection.execute(f"""
                UPDATE workflow_stages
                SET state = ?, detail = ?, source = ?, updated_at = ?,
                    attempt = COALESCE(?, attempt),
                    deadline_at = COALESCE(?, deadline_at),
                    started_at = CASE WHEN started_at IS NULL AND ? THEN ? ELSE started_at END,
                    finished_at = ?
                WHERE workflow_id = ? AND stage_id = ? AND state IN ({placeholders})
                  AND (? IS NULL OR attempt <= ?)""",
                [new_state, detail, source, now_text, attempt, deadline_at, is_start, now_text, finished_at,
                 workflow["workflow_id"], stage_id, *previous_states, attempt, attempt])
        return cursor.rowcount == 1
    finally:
        connection.close()


def get_workflow(workflow_id):
    """Returns the workflow (index fields + "stages", in display order), or None if it is unknown.

    "stages" is empty if DynamoDB already deleted the record after the retention period.
    """
    connection = connect()
    try:
        row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if row is None:
            return None
        workflow = dict(row)
        if workflow["store"] == STORE_SQLITE:
            rows = connection.execute("SELECT * FROM workflow_stages WHERE workflow_id = ? ORDER BY position",
                                      (workflow_id,)).fetchall()
            workflow["stages"] = [dict(stage) for stage in rows]
    finally:
        connection.close()
    if workflow["store"] == STORE_DYNAMODB:
        workflow["stages"] = workflow_dynamodb.get_stages(workflow_id)
    return workflow


def list_workflows(owner, limit=10):
    """The owner's newest workflows (index fields only), including failed and incomplete ones."""
    connection = connect()
    try:
        rows = connection.execute("SELECT * FROM workflows WHERE owner = ? ORDER BY created_at DESC LIMIT ?",
                                  (owner, limit)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def count_real_workflows_since(owner, since_text):
    """How many REAL (not simulated) workflows the owner started since a time. Used as a cost limit."""
    connection = connect()
    try:
        row = connection.execute("SELECT COUNT(*) AS total FROM workflows WHERE owner = ? AND mode = 'aws' "
                                 "AND created_at >= ?", (owner, since_text)).fetchone()
        return row["total"]
    finally:
        connection.close()
