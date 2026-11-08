"""EXPLICIT upgrade of the Architecture Map database (data/architecture.db) from schema version 1 to 2.

The app never changes this file by itself. Run this only if the app refused to start with
"schema version 1, but this app needs version 2".

    python scripts/migrate_architecture_db.py            # dry run: shows what would change, changes nothing
    python scripts/migrate_architecture_db.py --apply    # makes a backup copy first, then upgrades

What it does (additive only: no row is changed or deleted):
    - adds the columns "account" and "region" to the evidence table (existing records keep NULL = unknown)
    - creates the tables "workflow_context" and "schema_info"
Rollback: stop the app and put the backup file back (its path is printed), for example
    copy data\\architecture.db.v1-backup-<time> data\\architecture.db
"""

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # Lets this script import the project.

from core.config import get_settings  # noqa: E402
from repositories import architecture_repository as store  # noqa: E402


def migrate(path, apply):
    if not path.is_file():
        print(f"No file at {path}: nothing to migrate (the app creates a new version-2 file itself).")
        return 0
    connection = sqlite3.connect(path)
    try:
        version = store.file_version(connection)
    finally:
        connection.close()
    if version == store.SCHEMA_VERSION:
        print(f"{path} is already at version {store.SCHEMA_VERSION}. Nothing to do.")
        return 0
    if version != 1:
        print(f"{path} has an unknown layout (version {version}). Not changed. Move it away to start fresh.")
        return 1
    steps = ["ALTER TABLE evidence ADD COLUMN account TEXT", "ALTER TABLE evidence ADD COLUMN region TEXT"]
    print(f"{path}: version 1 -> {store.SCHEMA_VERSION}. Planned changes (additive only):")
    for step in steps + ["CREATE TABLE workflow_context (...)", "CREATE TABLE schema_info (...)"]:
        print("   ", step)
    if not apply:
        print("Dry run: nothing was changed. Run again with --apply to upgrade (a backup is made first).")
        return 0
    backup = path.with_name(f"{path.name}.v1-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    shutil.copy2(path, backup)
    print(f"Backup: {backup}")
    connection = sqlite3.connect(path)
    try:
        # ONE transaction: either every step is applied, or (on any error) nothing is.
        connection.executescript("BEGIN;\n" + ";\n".join(steps) + ";\n" + store.SCHEMA +
                                 f"\nINSERT INTO schema_info (version) VALUES ({store.SCHEMA_VERSION});\nCOMMIT;")
    except sqlite3.Error as error:
        connection.rollback()
        print(f"Migration failed and was rolled back ({error}). The file is unchanged; the backup is also kept.")
        return 1
    finally:
        connection.close()
    print(f"Done. To roll back: stop the app and copy {backup.name} back to {path.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(migrate(get_settings()["architecture_database_path"], "--apply" in sys.argv))
