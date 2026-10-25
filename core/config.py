"""Reads application settings from environment variables and the .env file."""

import os  # Gives access to environment variables.
from pathlib import Path  # Builds file paths that work on both Windows and Linux.

from dotenv import load_dotenv  # Copies values from the .env file into environment variables.

# The project folder (the folder that contains main.py). "parent.parent" goes up from core/config.py.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Tests set LAB_SKIP_DOTENV so your personal .env file never affects automated tests.
if not os.getenv("LAB_SKIP_DOTENV"):
    # Reads PROJECT_ROOT/.env. Existing environment variables are NOT overwritten.
    load_dotenv(PROJECT_ROOT / ".env")


def read_bool(name, default):
    # Reads a true/false setting. Accepts "true", "1", or "yes" (any capitalisation) as true.
    value = os.getenv(name, default)
    return value.strip().lower() in ("true", "1", "yes")


def resolve_path(path_text):
    # Turns a relative path such as "data/lab.db" into a full path inside the project folder.
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def get_settings():
    """Returns all settings as a plain dictionary.

    The settings are read each time this function is called, so tests can change
    environment variables and see the new values immediately.
    """
    return {
        # Text shown in the page header and browser tab.
        "app_title": os.getenv("APP_TITLE", "AWS Services Lab"),
        # Default AWS region for API calls.
        "aws_region": os.getenv("AWS_REGION", "us-east-1"),
        # "local" = AWS services that are not configured are SIMULATED (clearly labelled).
        # "aws"   = every service must be configured; a missing setting is reported as Failed.
        "app_mode": os.getenv("APP_MODE", "local").strip().lower(),
        # Secret used to sign the session cookie. Empty means main.py creates a temporary one.
        "session_secret": os.getenv("SESSION_SECRET", ""),
        # True adds the "Secure" flag to the cookie, so browsers only send it over HTTPS.
        "session_https_only": read_bool("SESSION_HTTPS_ONLY", "false"),
        # Safety switch: False blocks any operation marked as "write".
        "allow_write_operations": read_bool("ALLOW_WRITE_OPERATIONS", "false"),
        # Location of the SQLite database file (run history, and form submissions in local mode).
        "database_path": resolve_path(os.getenv("DATABASE_PATH", "data/lab.db")),
        # Where form submissions are saved: "sqlite" (local file) or "postgres" (Amazon RDS for PostgreSQL).
        "database_backend": os.getenv("DATABASE_BACKEND", "sqlite").strip().lower(),
        # RDS connection settings. These are NOT secret. The password comes from Secrets Manager.
        "db_host": os.getenv("DB_HOST", ""),
        "db_port": int(os.getenv("DB_PORT", "5432")),
        "db_name": os.getenv("DB_NAME", ""),
        "db_user": os.getenv("DB_USER", ""),
        # Only used in local mode (for example a PostgreSQL on your own computer). Ignored in aws mode.
        "db_password": os.getenv("DB_PASSWORD", ""),
        # TLS for the database connection. "verify-full" checks the RDS certificate and host name.
        "db_sslmode": os.getenv("DB_SSLMODE", "verify-full"),
        "db_sslrootcert": os.getenv("DB_SSLROOTCERT", ""),
        # TODO (AWS SETUP): After creating the AWS Secrets Manager secret,
        # set AWS_SECRETS_MANAGER_SECRET_NAME to its name or ARN.
        # Never put the actual secret value in this file. (core/secret_store.py reads it.)
        "secret_name": os.getenv("AWS_SECRETS_MANAGER_SECRET_NAME", ""),
        # How long a retrieved secret is kept in memory before it is fetched again (seconds).
        "secret_cache_seconds": int(os.getenv("SECRET_CACHE_SECONDS", "900")),
        # Private S3 bucket for uploaded documents, and the KMS key that encrypts them (SSE-KMS).
        "s3_bucket_name": os.getenv("S3_BUCKET_NAME", ""),
        "s3_kms_key_arn": os.getenv("S3_KMS_KEY_ARN", ""),
        # Lambda function that processes each submission and sends the confirmation email with SES.
        "lambda_function_name": os.getenv("LAMBDA_FUNCTION_NAME", ""),
        # Optional messaging. Leave empty to skip.
        "sns_topic_arn": os.getenv("SNS_TOPIC_ARN", ""),
        "sqs_queue_url": os.getenv("SQS_QUEUE_URL", ""),
        # Logging detail level and file location.
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "log_file": PROJECT_ROOT / "logs" / "app.log",
    }
