"""Tests for the Security Configuration table on the main page.

Each row's explanation must match its "In .env" status: a "Not Configured" row must not claim the setting
"is set". The "Connection" column must stay "Not Checked" until a real check has run: a setting that
merely exists is never shown as Connected.
"""

from types import SimpleNamespace  # A quick stand-in for the browser request (url.scheme and headers).

from routes.lab_routes import security_configuration  # Builds the table rows.

# The three rows checked here, and the .env setting behind each one.
ROWS = {
    "S3 bucket": ("S3_BUCKET_NAME", "my-lab-bucket"),
    "KMS key reference": ("S3_KMS_KEY_ARN", "arn:aws:kms:us-east-1:123456789012:key/abcd"),
    "Secrets Manager reference": ("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/db"),
}


def build_rows(checks=None):
    # Turns the list of (name, in .env, connection, explanation) into {name: (in .env, connection, explanation)}.
    fake_request = SimpleNamespace(url=SimpleNamespace(scheme="http"), headers={})
    return {name: (setting, connection, text)
            for name, setting, connection, text in security_configuration(fake_request, checks)}


def test_descriptions_when_not_configured(monkeypatch):
    for setting, _ in ROWS.values():
        monkeypatch.delenv(setting, raising=False)
    rows = build_rows()
    for row_name, (setting, _) in ROWS.items():
        in_env, connection, text = rows[row_name]
        assert in_env == "Not Configured"
        assert connection == "Not Checked"
        assert f"{setting} is empty" in text
        assert "is set" not in text


def test_descriptions_when_configured(monkeypatch):
    for setting, value in ROWS.values():
        monkeypatch.setenv(setting, value)
    rows = build_rows()
    for row_name, (setting, value) in ROWS.items():
        in_env, connection, text = rows[row_name]
        assert in_env == "Configured"
        assert connection == "Not Checked"  # Present in .env is NOT the same as connected.
        assert f"{setting} is set" in text
        assert value not in text  # The table never shows the actual value.


def test_connection_column_comes_from_real_checks(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "my-lab-bucket")
    checks = [{"service": "Amazon S3", "action": "HeadBucket (exists + access)", "status": "Connected", "detail": ""},
              {"service": "AWS Secrets Manager", "action": "Describe secret (metadata)", "status": "Connected",
               "detail": ""},
              {"service": "AWS Secrets Manager", "action": "Retrieve value (permission)", "status": "Access Denied",
               "detail": ""}]
    rows = build_rows(checks)
    assert rows["S3 bucket"][1] == "Connected"
    # The secret row reflects the VALUE permission, not the metadata row.
    assert rows["Secrets Manager reference"][1] == "Access Denied"
    assert rows["KMS key reference"][1] == "Not Checked"


def test_main_page_shows_not_configured_text(logged_in_client):
    # The real page, as a logged-in browser sees it, with nothing configured (the test default).
    page = logged_in_client.get("/").text
    assert "S3_BUCKET_NAME is empty" in page
    assert "S3_BUCKET_NAME is set" not in page
    assert "Not Checked" in page
