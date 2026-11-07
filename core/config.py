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


def clean_aws_profile():
    """Makes an empty AWS_PROFILE behave as if it were not set at all.

    boto3 reads AWS_PROFILE by itself. The line "AWS_PROFILE=" in .env sets it to an empty
    text "", and boto3 then looks for a profile literally named "" and fails with
    "The config profile () could not be found". Removing the empty value lets boto3 use its
    normal search instead: environment variables, the default profile, or the EC2 IAM role.
    A real profile name (for example "lab-dev") is kept, with any stray spaces removed.
    """
    profile = os.environ.get("AWS_PROFILE")
    if profile is None:
        return  # Not set at all: nothing to do.
    if profile.strip() == "":
        del os.environ["AWS_PROFILE"]  # Empty or only spaces: treat it as unset.
    else:
        os.environ["AWS_PROFILE"] = profile.strip()  # A named profile: keep it, minus spaces.


# Runs once when this file is first imported, right after .env has been loaded.
clean_aws_profile()


def read_bool(name, default):
    # Reads a true/false setting. Accepts "true", "1", or "yes" (any capitalisation) as true.
    value = os.getenv(name, default)
    return value.strip().lower() in ("true", "1", "yes")


def read_int(name, default):
    """Reads a whole-number setting, such as DB_PORT=5432.

    An empty value (for example the line "DB_PORT=" in .env) uses the default instead of crashing.
    A value that is not a number stops the app with a message that names the setting to fix.
    """
    value = os.getenv(name, "").strip()
    if value == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be a whole number (for example {default}), but it is '{value}'. "
                         f"Fix it in .env.") from None


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
        # True ONLY behind a load balancer that you control (core/request_security.py explains why).
        # False: an X-Forwarded-Proto header is never believed, because anyone can send one.
        "trust_proxy_headers": read_bool("TRUST_PROXY_HEADERS", "false"),
        # Safety switch: False blocks any operation marked as "write".
        "allow_write_operations": read_bool("ALLOW_WRITE_OPERATIONS", "false"),
        # Location of the SQLite database file (run history, and form submissions in local mode).
        "database_path": resolve_path(os.getenv("DATABASE_PATH", "data/lab.db")),
        # Where form submissions are saved: "sqlite" (local file) or "postgres" (Amazon RDS for PostgreSQL).
        "database_backend": os.getenv("DATABASE_BACKEND", "sqlite").strip().lower(),
        # RDS connection settings. These are NOT secret. The password comes from Secrets Manager.
        "db_host": os.getenv("DB_HOST", ""),
        "db_port": read_int("DB_PORT", 5432),
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
        "secret_cache_seconds": read_int("SECRET_CACHE_SECONDS", 900),
        # Private S3 bucket for uploaded documents, and the KMS key that encrypts them (SSE-KMS).
        "s3_bucket_name": os.getenv("S3_BUCKET_NAME", ""),
        "s3_kms_key_arn": os.getenv("S3_KMS_KEY_ARN", ""),
        # Lambda function that processes each submission and sends the confirmation email with SES.
        "lambda_function_name": os.getenv("LAMBDA_FUNCTION_NAME", ""),
        # Optional messaging. Leave empty to skip.
        "sns_topic_arn": os.getenv("SNS_TOPIC_ARN", ""),
        "sqs_queue_url": os.getenv("SQS_QUEUE_URL", ""),
        # ---- Live Workflow Monitor (experiments/live_workflow.py) ----
        # Where real workflows keep their stages: "sqlite" (local) or "dynamodb" (needed once Lambda reports).
        "workflow_backend": os.getenv("WORKFLOW_BACKEND", "sqlite").strip().lower(),
        "workflow_table_name": os.getenv("WORKFLOW_TABLE_NAME", "aws-lab-workflows").strip(),
        # The Lambda function triggered by S3 events. Empty: no Lambda stages are expected.
        "workflow_lambda_name": os.getenv("WORKFLOW_LAMBDA_NAME", "").strip(),
        # Deadlines. When one passes, the stage shows "Timed Out", which is NOT proof of failure:
        # a late result still updates the stage.
        "workflow_event_deadline_seconds": read_int("WORKFLOW_EVENT_DEADLINE_SECONDS", 120),
        "workflow_processing_deadline_seconds": read_int("WORKFLOW_PROCESSING_DEADLINE_SECONDS", 300),
        # Cost controls: the browser stops polling after this many seconds, and at most this many REAL
        # workflows per user per hour (simulated ones are free and not counted).
        "workflow_poll_limit_seconds": read_int("WORKFLOW_POLL_LIMIT_SECONDS", 300),
        "workflow_max_per_hour": read_int("WORKFLOW_MAX_PER_HOUR", 30),
        # DynamoDB deletes workflow stages after this many days (Time To Live). The local list keeps them.
        "workflow_retention_days": read_int("WORKFLOW_RETENTION_DAYS", 7),
        # Logging detail level and file location.
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "log_file": PROJECT_ROOT / "logs" / "app.log",
    }
