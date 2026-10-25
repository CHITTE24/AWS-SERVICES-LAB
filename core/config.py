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
        # Login details for the single dashboard user.
        "admin_username": os.getenv("ADMIN_USERNAME", "admin"),
        "admin_password_hash": os.getenv("ADMIN_PASSWORD_HASH", ""),
        # Secret used to sign the session cookie. Empty means main.py creates a temporary one.
        "session_secret": os.getenv("SESSION_SECRET", ""),
        "session_https_only": read_bool("SESSION_HTTPS_ONLY", "false"),
        # Safety switch: False blocks any operation marked as "write".
        "allow_write_operations": read_bool("ALLOW_WRITE_OPERATIONS", "false"),
        # Location of the SQLite database file.
        "database_path": resolve_path(os.getenv("DATABASE_PATH", "data/lab.db")),
        # Logging detail level and file location.
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "log_file": PROJECT_ROOT / "logs" / "app.log",
    }
