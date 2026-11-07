"""Tests for the Security Configuration table on the main page.

Each row's explanation must match its status: a "Not Configured" row must not claim the setting "is set".
"""

from types import SimpleNamespace  # A quick stand-in for the browser request (only url.scheme is used).

from routes.lab_routes import security_configuration  # Builds the table rows.

# The three rows checked here, and the .env setting behind each one.
ROWS = {
    "S3 bucket": ("S3_BUCKET_NAME", "my-lab-bucket"),
    "KMS key reference": ("S3_KMS_KEY_ARN", "arn:aws:kms:us-east-1:123456789012:key/abcd"),
    "Secrets Manager reference": ("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/db"),
}


def build_rows():
    # Turns the list of (name, status, explanation) into {name: (status, explanation)}.
    fake_request = SimpleNamespace(url=SimpleNamespace(scheme="http"))
    return {name: (status, text) for name, status, text in security_configuration(fake_request)}


def test_descriptions_when_not_configured(monkeypatch):
    for setting, _ in ROWS.values():
        monkeypatch.delenv(setting, raising=False)
    rows = build_rows()
    for row_name, (setting, _) in ROWS.items():
        status, text = rows[row_name]
        assert status == "Not Configured"
        assert f"{setting} is empty" in text
        assert "is set" not in text


def test_descriptions_when_configured(monkeypatch):
    for setting, value in ROWS.values():
        monkeypatch.setenv(setting, value)
    rows = build_rows()
    for row_name, (setting, value) in ROWS.items():
        status, text = rows[row_name]
        assert status == "Configured"
        assert f"{setting} is set" in text
        assert value not in text  # The table never shows the actual value.


def test_main_page_shows_not_configured_text(logged_in_client):
    # The real page, as a logged-in browser sees it, with nothing configured (the test default).
    page = logged_in_client.get("/").text
    assert "S3_BUCKET_NAME is empty" in page
    assert "S3_BUCKET_NAME is set" not in page
