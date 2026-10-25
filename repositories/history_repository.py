"""Stores every operation and experiment run in SQLite (a database contained in a single file).

The database file location comes from DATABASE_PATH in .env (default: data/lab.db).
You can open the file with any SQLite viewer, for example the "SQLite Viewer" extension for VS Code.
"""

import json  # Converts Python dictionaries to text for storage, and back again.
import sqlite3  # Python's built-in SQLite database library.
from datetime import datetime, timezone  # Records when each run happened.

from core.config import get_settings  # Reads the database file location.


def connect():
    """Opens a connection to the database file and creates its folder if needed."""
    database_path = get_settings()["database_path"]
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    # Lets us read columns by name, like row["status"], instead of by position.
    connection.row_factory = sqlite3.Row
    return connection


def create_tables():
    """Creates the runs table if it does not exist yet. Safe to call every time the app starts."""
    connection = connect()
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- Unique number for each run.
                kind TEXT NOT NULL,                     -- "operation" or "experiment".
                name TEXT NOT NULL,                     -- For example "s3 / list_buckets".
                inputs_json TEXT NOT NULL,              -- The form values used, as JSON text.
                status TEXT NOT NULL,                   -- "success", "warning", or "error".
                result_json TEXT NOT NULL,              -- The result or error, as JSON text.
                username TEXT,                          -- Who ran it.
                created_at TEXT NOT NULL                -- When it ran (UTC, ISO format).
            )
        """)
        connection.commit()  # Saves the change permanently.
    finally:
        connection.close()  # Always closes the file, even if an error happened.


def save_run(kind, name, inputs, status, result, username):
    """Saves one run and returns its new id number.

    inputs and result can be any dictionaries or lists. default=str converts dates and other
    unusual values into text so json.dumps never fails.
    """
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    connection = connect()
    try:
        # The ? placeholders let SQLite insert the values safely. This prevents SQL injection.
        cursor = connection.execute(
            "INSERT INTO runs (kind, name, inputs_json, status, result_json, username, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (kind, name, json.dumps(inputs, default=str), status,
             json.dumps(result, default=str), username, created_at),
        )
        connection.commit()
        return cursor.lastrowid  # The id SQLite gave the new row.
    finally:
        connection.close()


def list_runs(limit=50):
    """Returns the newest runs first, as a list of dictionaries (without the large result data)."""
    connection = connect()
    try:
        rows = connection.execute(
            "SELECT id, kind, name, status, username, created_at FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        # Converts each sqlite3.Row into a normal dictionary.
        return [dict(row) for row in rows]
    finally:
        connection.close()


def get_run(run_id):
    """Returns one run, with its inputs and result converted back into Python data, or None if not found."""
    connection = connect()
    try:
        row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        connection.close()

    if row is None:
        return None
    run = dict(row)
    # json.loads turns the stored text back into dictionaries and lists.
    run["inputs"] = json.loads(run.pop("inputs_json"))
    run["result"] = json.loads(run.pop("result_json"))
    return run
