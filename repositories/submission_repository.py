"""Saves form submissions in the lab_submissions table.

DATABASE_BACKEND in .env chooses where:
  - "sqlite"   (default): the local file data/lab.db. For local development. These are NOT RDS records.
  - "postgres": Amazon RDS for PostgreSQL. In aws mode the password always comes from Secrets Manager.

The login password is never stored here. Only the login USERNAME is saved.
Create the table on RDS once with:   python scripts/init_database.py
"""

import json  # The service activity list is stored as JSON text.
import sqlite3  # Python's built-in SQLite library.

from core.config import get_settings  # Reads the backend choice and connection settings.
from core.secret_store import get_secret_value  # Reads the RDS password from Secrets Manager.

# The table definition. It works on both SQLite and PostgreSQL.
# mobile_number is TEXT (VARCHAR), not a number, so a leading zero such as "01234" is kept.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lab_submissions (
    id                VARCHAR(32)   PRIMARY KEY,  -- Random submission ID (also stops duplicate submissions).
    submitted_at      TIMESTAMPTZ   NOT NULL,     -- When the form was submitted (with time zone, UTC).
    login_username    VARCHAR(100)  NOT NULL,     -- Who was logged in. The password is NEVER stored.
    username          VARCHAR(50)   NOT NULL,
    first_name        VARCHAR(50)   NOT NULL,
    last_name         VARCHAR(50)   NOT NULL,
    mobile_number     VARCHAR(5)    NOT NULL,
    email             VARCHAR(254)  NOT NULL,
    document_name     VARCHAR(120),               -- Empty (NULL) when no document was uploaded.
    s3_bucket         VARCHAR(63),                -- Bucket and key, never a public URL.
    s3_object_key     VARCHAR(1024),
    upload_status     VARCHAR(20)   NOT NULL,     -- Success / Failed / Skipped / Simulated.
    processing_status VARCHAR(20)   NOT NULL,     -- Lambda result.
    email_status      VARCHAR(20)   NOT NULL,     -- Email result reported by Lambda.
    activity_json     TEXT          NOT NULL      -- The AWS Service Activity rows, as JSON text.
)
"""

# The columns written by insert_submission(), in order.
COLUMNS = ["id", "submitted_at", "login_username", "username", "first_name", "last_name", "mobile_number",
           "email", "document_name", "s3_bucket", "s3_object_key", "upload_status", "processing_status",
           "email_status", "activity_json"]


def using_rds():
    # True when submissions go to Amazon RDS (PostgreSQL) instead of the local SQLite file.
    return get_settings()["database_backend"] == "postgres"


def backend_label():
    # A clear label for the page, so local records are never mistaken for RDS records.
    if using_rds():
        return "Amazon RDS (PostgreSQL)"
    return "Local SQLite file (NOT Amazon RDS)"


def connect():
    """Opens a database connection for the configured backend."""
    settings = get_settings()
    if settings["database_backend"] == "postgres":
        return connect_to_rds(settings)
    if settings["database_backend"] != "sqlite":
        raise ValueError("DATABASE_BACKEND must be sqlite or postgres.")
    settings["database_path"].parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings["database_path"])
    connection.row_factory = sqlite3.Row  # Lets us read columns by name.
    return connection


def connect_to_rds(settings):
    # Imported here, so local SQLite mode keeps working even if psycopg is not installed.
    import psycopg  # The PostgreSQL driver.
    from psycopg.rows import dict_row  # Returns each row as a dictionary.

    for name in ("db_host", "db_name", "db_user"):
        if not settings[name]:
            raise ValueError(f"{name.upper()} is not set in .env.")

    # In aws mode the password ALWAYS comes from Secrets Manager. If that fails, the connection fails:
    # the app never falls back to another password.
    if settings["app_mode"] != "local" or not settings["db_password"]:
        password = get_secret_value("password")
    else:
        password = settings["db_password"]  # Local development only, for example a PostgreSQL on your PC.

    options = {
        "host": settings["db_host"], "port": settings["db_port"], "dbname": settings["db_name"],
        "user": settings["db_user"], "password": password,
        "sslmode": settings["db_sslmode"],  # "verify-full" = encrypted AND the server certificate is checked.
        "connect_timeout": 5,  # Seconds. Gives up quickly if RDS is unreachable (for example a security group).
        "row_factory": dict_row,
    }
    if settings["db_sslrootcert"]:
        options["sslrootcert"] = settings["db_sslrootcert"]  # Path to the downloaded RDS certificate bundle.
    return psycopg.connect(**options)


def run_sql(statement, values=(), fetch=False):
    """Runs one SQL statement inside a transaction, then closes the connection.

    The statement uses "?" placeholders. They are changed to "%s" for PostgreSQL.
    Placeholders let the database insert the values safely, which prevents SQL injection.
    If anything fails, rollback() undoes the change, so the database never holds half a record.
    """
    if using_rds():
        statement = statement.replace("?", "%s")
    connection = connect()
    try:
        cursor = connection.cursor()
        cursor.execute(statement, values)
        rows = [dict(row) for row in cursor.fetchall()] if fetch else None
        connection.commit()  # Saves the change permanently.
        return rows
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def create_submissions_table():
    """Creates lab_submissions if it does not exist yet. Safe to run many times."""
    run_sql(CREATE_TABLE_SQL)


def insert_submission(record):
    """Saves a new submission. record is a dictionary containing every name in COLUMNS (activity as a list)."""
    values = []
    for column in COLUMNS:
        if column == "activity_json":
            values.append(json.dumps(record["activity"]))
        elif column == "submitted_at" and not using_rds():
            values.append(record["submitted_at"].isoformat())  # SQLite stores dates as text.
        else:
            values.append(record[column])
    placeholders = ", ".join("?" for _ in COLUMNS)
    run_sql(f"INSERT INTO lab_submissions ({', '.join(COLUMNS)}) VALUES ({placeholders})", values)


def update_submission_results(submission_id, processing_status, email_status, activity):
    """Stores the Lambda and email results after they arrive."""
    run_sql("UPDATE lab_submissions SET processing_status = ?, email_status = ?, activity_json = ? WHERE id = ?",
            (processing_status, email_status, json.dumps(activity), submission_id))


def get_submission(submission_id, login_username):
    """Returns one submission as a dictionary, or None. Users can only see their own submissions."""
    rows = run_sql("SELECT * FROM lab_submissions WHERE id = ? AND login_username = ?",
                   (submission_id, login_username), fetch=True)
    if not rows:
        return None
    submission = rows[0]
    submission["activity"] = json.loads(submission.pop("activity_json"))
    return submission


def describe_database_error(error):
    """A safe message for the page. Driver errors can include the RDS host name, so they stay in the log."""
    if isinstance(error, ValueError):  # Our own messages, such as "DB_HOST is not set", or a SecretError.
        return str(error)
    return (f"The database could not save the record ({type(error).__name__}). "
            "The technical details are in logs/app.log.")


def check_sqlite():
    """Opens the local SQLite file and runs SELECT 1. Used by "Check AWS environment".

    The SQLite file always holds the run history, and also the submissions when DATABASE_BACKEND=sqlite.
    """
    path = get_settings()["database_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("SELECT 1")
    finally:
        connection.close()
    return path


def classify_database_error(error):
    """Turns a database connection error into (status, safe message) for "Check AWS environment".

    PostgreSQL error texts can include the RDS host name, so only a short explanation is shown.
    The full error is in logs/app.log.
    """
    # Imported here to avoid a circular import (core.aws_errors does not import this file).
    from core.aws_errors import (ACCESS_DENIED, CONFIGURATION_ERROR, NOT_FOUND, TIMED_OUT, UNAVAILABLE,
                                 classify_error)

    if isinstance(error, ImportError):
        return CONFIGURATION_ERROR, "The PostgreSQL driver (psycopg) is not installed: pip install -r requirements.txt."
    if isinstance(error, ValueError):  # Our own messages ("DB_HOST is not set") or a SecretError.
        return classify_error(error), str(error)

    text = str(error).lower()
    # Each entry: (words in the driver's error text, status, what to show the user).
    known_problems = [
        (("timeout",), TIMED_OUT,
         "RDS did not answer within 5 seconds. Check that the RDS security group allows port 5432 "
         "from the EC2 instance's security group, and that both are in the same VPC."),
        (("password authentication failed", "pg_hba.conf"), ACCESS_DENIED,
         "RDS refused the login. Check DB_USER and the password stored in Secrets Manager."),
        (("could not translate host name", "name or service not known", "nodename nor servname"),
         CONFIGURATION_ERROR, "DB_HOST could not be found in DNS. Copy the endpoint from the RDS console."),
        (("does not exist",), NOT_FOUND, "The database named in DB_NAME (or the user in DB_USER) does not exist."),
        (("ssl", "certificate"), CONFIGURATION_ERROR,
         "The TLS connection failed. Check DB_SSLMODE and that DB_SSLROOTCERT points to the RDS certificate bundle."),
        (("connection refused",), UNAVAILABLE, "RDS refused the connection. Check that the instance is running "
                                               "and DB_PORT is correct."),
    ]
    for words, status, message in known_problems:
        if any(word in text for word in words):
            return status, message
    return UNAVAILABLE, (f"The database connection failed ({type(error).__name__}). "
                         "The technical details are in logs/app.log.")


def check_connection():
    """Connects to the database and reports whether the connection is encrypted with TLS.

    Returns a short description, for example "Connected to Amazon RDS (PostgreSQL). TLS in use: yes".
    """
    connection = connect()
    try:
        connection.cursor().execute("SELECT 1")  # The simplest possible query.
        if not using_rds():
            return "Connected to the local SQLite file (TLS does not apply to a local file)."
        # ssl_in_use is reported by the PostgreSQL client library itself, so it is real proof.
        tls = "yes" if connection.pgconn.ssl_in_use else "NO (check DB_SSLMODE)"
        return f"Connected to Amazon RDS (PostgreSQL). TLS in use: {tls}."
    finally:
        connection.close()
