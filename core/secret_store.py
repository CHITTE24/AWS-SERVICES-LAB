"""Reads sensitive settings (such as the RDS database password) from AWS Secrets Manager.

WHICH SECRET?  The secret's NAME or ARN goes in .env as AWS_SECRETS_MANAGER_SECRET_NAME.
The secret's VALUE stays in AWS. It is never written to this project, the logs, or the browser.

Expected secret format (a JSON "key/value" secret, the same format RDS creates for you):
    {"username": "labapp", "password": "<the database password>"}
Extra keys are allowed and simply ignored.

Caching: the secret is kept in memory for SECRET_CACHE_SECONDS (default 900 = 15 minutes),
then fetched again. So after you rotate the password in Secrets Manager, the app picks up
the new one within 15 minutes, or immediately after a restart.
"""

import json  # The secret is stored as JSON text.
import time  # Used to know when the cached copy is too old.

from core.aws_client import get_client  # Creates the Boto3 client (uses the IAM role on EC2).
from core.config import get_settings  # Reads AWS_SECRETS_MANAGER_SECRET_NAME.

# The in-memory cache: {"values": {...}, "fetched_at": <seconds>, "name": <secret name>}.
_cache = {}


class SecretError(ValueError):
    """Raised when a secret cannot be read. The message never contains the secret value.

    It is a kind of ValueError, so core/aws_errors.py shows its message to the user as it is.
    """


def get_secret_values():
    """Returns the secret as a dictionary, for example {"username": ..., "password": ...}.

    Raises SecretError if no secret is configured, AWS refuses the request, or the JSON is invalid.
    """
    settings = get_settings()
    # TODO (AWS SETUP): After creating the AWS Secrets Manager secret,
    # set AWS_SECRETS_MANAGER_SECRET_NAME to its name or ARN.
    # Never put the actual secret value in this file.
    secret_name = settings["secret_name"]
    if not secret_name:
        raise SecretError("AWS_SECRETS_MANAGER_SECRET_NAME is not set.")

    # Uses the cached copy if it is for the same secret and still fresh.
    age = time.time() - _cache.get("fetched_at", 0)
    if _cache.get("name") == secret_name and age < settings["secret_cache_seconds"]:
        return _cache["values"]

    try:
        response = get_client("secretsmanager").get_secret_value(SecretId=secret_name)
        values = json.loads(response["SecretString"])
    except json.JSONDecodeError:
        # Do NOT include the text in the message: it could be the secret itself.
        # "from None" also hides the original error, whose details could include part of the text.
        raise SecretError("The secret is not valid JSON. Store it as key/value pairs.") from None
    except Exception as error:
        # Only the error TYPE and AWS error code are kept. AWS never puts secret values in error messages.
        code = getattr(error, "response", {}).get("Error", {}).get("Code", type(error).__name__)
        raise SecretError(f"Could not read the secret from Secrets Manager ({code}).")

    if not isinstance(values, dict):
        raise SecretError("The secret must be a JSON object such as {\"password\": \"...\"}.")

    _cache.update({"values": values, "fetched_at": time.time(), "name": secret_name})
    return values


def get_secret_value(key):
    """Returns one value from the secret, for example get_secret_value("password")."""
    values = get_secret_values()
    if not values.get(key):
        raise SecretError(f"The secret has no \"{key}\" key.")
    return values[key]


def clear_cache():
    # Forgets the cached secret. Tests use this; you could also call it after rotating a secret.
    _cache.clear()
