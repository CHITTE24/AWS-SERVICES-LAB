"""Tests for AWS Secrets Manager access (core/secret_store.py) and how the RDS connection uses it."""

import boto3
import psycopg  # The PostgreSQL driver. One test replaces its connect() function.
import pytest

from core.secret_store import SecretError, get_secret_value, get_secret_values
from repositories import submission_repository

# A made-up value used only to prove it never appears in messages.
FAKE_PASSWORD = "never-show-this-123"


def create_secret(text):
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(Name="lab/app", SecretString=text)


def test_missing_secret_name_is_explained():
    with pytest.raises(SecretError, match="AWS_SECRETS_MANAGER_SECRET_NAME is not set"):
        get_secret_values()


def test_secret_is_retrieved_and_cached(fake_aws, monkeypatch):
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/app")
    create_secret('{"username": "labapp", "password": "%s"}' % FAKE_PASSWORD)
    assert get_secret_value("password") == FAKE_PASSWORD

    # Delete the secret in (fake) AWS. The cached copy is still used, so AWS is not called again.
    boto3.client("secretsmanager", region_name="us-east-1").delete_secret(SecretId="lab/app",
                                                                         ForceDeleteWithoutRecovery=True)
    assert get_secret_value("username") == "labapp"


def test_secret_not_found(fake_aws, monkeypatch):
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/does-not-exist")
    with pytest.raises(SecretError, match="ResourceNotFoundException"):
        get_secret_values()


def test_invalid_json_does_not_leak_the_value(fake_aws, monkeypatch):
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/app")
    create_secret(FAKE_PASSWORD)  # Plain text instead of JSON.
    with pytest.raises(SecretError) as error_info:
        get_secret_values()
    assert FAKE_PASSWORD not in str(error_info.value)


def test_missing_key_in_secret(fake_aws, monkeypatch):
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/app")
    create_secret('{"username": "labapp"}')
    with pytest.raises(SecretError, match='no "password" key'):
        get_secret_value("password")


def test_rds_connection_in_aws_mode_uses_secrets_manager(fake_aws, monkeypatch):
    monkeypatch.setenv("APP_MODE", "aws")
    monkeypatch.setenv("DATABASE_BACKEND", "postgres")
    monkeypatch.setenv("DB_HOST", "lab-db.abc123.us-east-1.rds.amazonaws.com")
    monkeypatch.setenv("DB_NAME", "labdb")
    monkeypatch.setenv("DB_USER", "labapp")
    monkeypatch.setenv("DB_PASSWORD", "ignored-in-aws-mode")
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/app")
    create_secret('{"password": "%s"}' % FAKE_PASSWORD)

    # Replaces psycopg.connect with a function that records the connection settings instead of connecting.
    captured = {}
    monkeypatch.setattr(psycopg, "connect", lambda **options: captured.update(options) or "connection")

    assert submission_repository.connect() == "connection"
    assert captured["password"] == FAKE_PASSWORD  # From Secrets Manager, not DB_PASSWORD.
    assert captured["sslmode"] == "verify-full"  # TLS with certificate checking by default.


def test_rds_connection_fails_safely_without_secret(monkeypatch):
    monkeypatch.setenv("APP_MODE", "aws")
    monkeypatch.setenv("DATABASE_BACKEND", "postgres")
    monkeypatch.setenv("DB_HOST", "lab-db.example")
    monkeypatch.setenv("DB_NAME", "labdb")
    monkeypatch.setenv("DB_USER", "labapp")
    monkeypatch.setenv("DB_PASSWORD", "must-not-be-used")
    # No secret configured: the connection must fail, NOT fall back to DB_PASSWORD.
    with pytest.raises(SecretError):
        submission_repository.connect()
