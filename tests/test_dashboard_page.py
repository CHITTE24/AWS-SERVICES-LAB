"""Tests for the main page around the "Check AWS environment" button (P2/P3 changes).

- The page is never cached by the browser (Cache-Control: no-store).
- The results show when they were checked ("Last checked").
- The Security Configuration panel separates "In .env" (Configured) from "Connection" (Connected).
- The secret value never appears in the page, the run history, or the log file, even at LOG_LEVEL=DEBUG.
"""

import logging  # Restores the logging setup after the DEBUG-logging test.
import re  # Reads statuses out of the HTML page.
import urllib.error  # Simulates "not running on EC2".

import boto3  # Creates fake resources inside moto.
import pytest

from core.config import get_settings
from core.logging_setup import QUIET_LIBRARIES, setup_logging
from integrations import ec2

SECRET_TEXT = "never-show-this-secret-789"


@pytest.fixture(autouse=True)
def not_on_ec2(monkeypatch):
    # Avoids the 1-second EC2 metadata timeout on a normal PC.
    def no_metadata():
        raise urllib.error.URLError("metadata service unreachable")
    monkeypatch.setattr(ec2, "get_current_instance", no_metadata)


def panel_row(page, name):
    """Returns (In .env, Connection) for one row of the Security Configuration panel."""
    badge = r"\s*(?:<span class=\"badge [^\"]+\">([^<]+)</span>|<span class=\"muted\">(-)</span>)\s*"
    match = re.search(rf"<td>{re.escape(name)}</td>\s*<td>{badge}</td>\s*<td>{badge}</td>", page)
    assert match, f"Row {name} not found"
    values = [value for value in match.groups() if value]
    return values[0], values[1]


# ---------------------------------------------------------------- caching and timestamp

def test_pages_are_never_cached(logged_in_client, fake_aws):
    assert logged_in_client.get("/").headers["cache-control"] == "no-store"
    assert logged_in_client.post("/environment-check").headers["cache-control"] == "no-store"


def test_last_checked_time_is_shown_only_after_a_check(logged_in_client, fake_aws):
    assert "Not checked yet on this page." in logged_in_client.get("/").text
    assert "Last checked:" not in logged_in_client.get("/").text
    page = logged_in_client.post("/environment-check").text
    match = re.search(r"Last checked:</strong> (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC, region us-east-1, took [\d.]+ s",
                      page)
    assert match, "The 'Last checked' line is missing or has the wrong format"


# ---------------------------------------------------------------- Configured vs Connected

def test_panel_separates_configured_from_connected(logged_in_client, fake_aws, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-bucket")

    # 1. Only the setting exists: "Configured", but the connection is not proven.
    page = logged_in_client.get("/").text
    assert panel_row(page, "S3 bucket") == ("Configured", "Not Checked")

    # 2. A check runs, but the bucket does not exist.
    page = logged_in_client.post("/environment-check").text
    assert panel_row(page, "S3 bucket") == ("Configured", "Not Found")

    # 3. The bucket exists: now, and only now, it is Connected.
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="lab-bucket")
    page = logged_in_client.post("/environment-check").text
    assert panel_row(page, "S3 bucket") == ("Configured", "Connected")

    # 4. A normal page load does not reuse the earlier result.
    assert panel_row(logged_in_client.get("/").text, "S3 bucket") == ("Configured", "Not Checked")


def test_panel_https_row_uses_trusted_information(logged_in_client):
    assert panel_row(logged_in_client.get("/").text, "HTTPS / TLS for this page") == ("-", "Not Configured")
    # A forged header is not believed.
    page = logged_in_client.get("/", headers={"X-Forwarded-Proto": "https"}).text
    assert panel_row(page, "HTTPS / TLS for this page") == ("-", "Configuration Error")
    logged_in_client.base_url = "https://testserver"
    assert panel_row(logged_in_client.get("/").text, "HTTPS / TLS for this page") == ("-", "Connected")


# ---------------------------------------------------------------- secrets never leak

@pytest.fixture
def debug_log_file(tmp_path):
    """Turns on LOG_LEVEL=DEBUG logging into a temporary file, and restores the normal setup afterwards."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    saved_library_levels = {name: logging.getLogger(name).level for name in QUIET_LIBRARIES}
    log_file = tmp_path / "debug.log"
    setup_logging("DEBUG", log_file)
    yield log_file
    for handler in root.handlers:
        handler.close()  # Closes the file, so Windows can delete the temporary folder.
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, level in saved_library_levels.items():
        logging.getLogger(name).setLevel(level)


def test_aws_libraries_never_log_below_info(debug_log_file):
    for name in QUIET_LIBRARIES:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.INFO


@pytest.mark.parametrize("secret_string", [
    '{"password": "%s"}' % SECRET_TEXT,  # A normal JSON secret.
    SECRET_TEXT,  # Not JSON: the error path must not leak it either.
])
def test_secret_never_appears_in_page_history_or_log(logged_in_client, fake_aws, monkeypatch, debug_log_file,
                                                     secret_string):
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/app")
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(Name="lab/app", SecretString=secret_string)

    page = logged_in_client.post("/environment-check").text

    assert "AWS Secrets Manager" in page  # The check really ran.
    assert SECRET_TEXT not in page
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert SECRET_TEXT not in debug_log_file.read_text(encoding="utf-8")
    # The run history (SQLite file) stores the check rows: the value must not be there either.
    assert SECRET_TEXT.encode() not in get_settings()["database_path"].read_bytes()
