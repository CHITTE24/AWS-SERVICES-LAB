"""Tests for the Lambda function in deploy/lambda_function/ (Amazon SES is faked by moto, so no email is sent)."""

import boto3
import pytest

from deploy.lambda_function.submission_processor import lambda_handler

EVENT = {"submission_id": "abc123", "submitted_at": "2026-10-25T10:00:00+00:00", "username": "student_1",
         "first_name": "Mary", "email": "mary@example.com", "document_uploaded": True, "document_name": "a.pdf"}


def test_email_sent(fake_aws, monkeypatch):
    boto3.client("ses", region_name="us-east-1").verify_email_identity(EmailAddress="lab@example.com")
    monkeypatch.setenv("SES_SENDER_EMAIL", "lab@example.com")
    result = lambda_handler(EVENT, None)
    assert result["email_status"] == "Sent"
    assert "not confirmed" in result["email_detail"]


def test_unverified_sender_fails(fake_aws, monkeypatch):
    # The sender was never verified in SES, so SES rejects the email.
    monkeypatch.setenv("SES_SENDER_EMAIL", "unverified@example.com")
    result = lambda_handler(EVENT, None)
    assert result["email_status"] == "Failed"
    assert "SES rejected the email" in result["email_detail"]


def test_no_sender_configured_skips_email(monkeypatch):
    monkeypatch.delenv("SES_SENDER_EMAIL", raising=False)
    assert lambda_handler(EVENT, None)["email_status"] == "Skipped"


def test_event_without_email_is_an_error():
    with pytest.raises(ValueError):
        lambda_handler({"submission_id": "abc123"}, None)
