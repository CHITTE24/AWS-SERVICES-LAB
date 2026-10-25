"""Creates the lab_submissions table in the configured database. Safe to run more than once.

Run it from the project folder, with the virtual environment active:
    python scripts/init_database.py

With DATABASE_BACKEND=postgres it connects to Amazon RDS, using DB_HOST, DB_NAME, DB_USER, and the
password from Secrets Manager (AWS_SECRETS_MANAGER_SECRET_NAME). Run it on the EC2 instance,
because RDS is private and only reachable from inside the VPC.
"""

import sys  # sys.path lets this script import the project's own packages.
from pathlib import Path  # Builds the project folder path.

# Makes "core" and "repositories" importable when this file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from repositories.submission_repository import (  # noqa: E402  (must come after the sys.path line)
    backend_label, check_connection, create_submissions_table, describe_database_error)

if __name__ == "__main__":
    print(f"Database: {backend_label()}")
    try:
        print(check_connection())
        create_submissions_table()
    except Exception as error:
        # This runs in YOUR terminal, so the driver's details are shown. They never include the password.
        print(f"FAILED: {describe_database_error(error)}\nDetails: {error}")
        sys.exit(1)
    print("OK: the lab_submissions table exists.")
