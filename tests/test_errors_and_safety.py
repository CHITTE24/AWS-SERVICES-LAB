"""Tests for friendly error messages and the write-operation safety switch."""

import pytest
from botocore.exceptions import ClientError, NoCredentialsError

from core.aws_client import ensure_writes_allowed
from core.aws_errors import friendly_error_message


def test_access_denied_message():
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, "ListBuckets")
    message = friendly_error_message(error)
    assert "Permission denied for ListBuckets" in message


def test_expired_credentials_message():
    error = ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "ListBuckets")
    assert "invalid or expired" in friendly_error_message(error)


def test_no_credentials_message():
    assert "No AWS credentials" in friendly_error_message(NoCredentialsError())


def test_unknown_error_shows_type():
    assert "RuntimeError" in friendly_error_message(RuntimeError("boom"))


def test_writes_blocked_by_default():
    with pytest.raises(PermissionError):
        ensure_writes_allowed()


def test_writes_allowed_when_switch_is_on(monkeypatch):
    # monkeypatch.setenv changes an environment variable for this test only.
    monkeypatch.setenv("ALLOW_WRITE_OPERATIONS", "true")
    ensure_writes_allowed()  # Must not raise.
