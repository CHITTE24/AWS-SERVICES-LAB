"""Regression tests: an empty AWS_PROFILE must not break boto3.

Bug: the line "AWS_PROFILE=" in .env made boto3 look for a profile named "" and fail with
"The config profile () could not be found". These tests never call AWS: creating a boto3
Session only reads local settings and files.
"""

import os  # Reads environment variables.

import boto3  # The AWS SDK. Creating a Session does not contact AWS.

from core.config import clean_aws_profile  # The function under test.


def test_empty_profile_is_removed(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "")
    clean_aws_profile()
    assert "AWS_PROFILE" not in os.environ
    boto3.Session()  # Before the fix this raised ProfileNotFound.


def test_whitespace_only_profile_is_removed(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "   ")
    clean_aws_profile()
    assert "AWS_PROFILE" not in os.environ
    boto3.Session()


def test_missing_profile_stays_missing(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    clean_aws_profile()
    assert "AWS_PROFILE" not in os.environ
    boto3.Session()


def test_named_profile_is_kept(monkeypatch, tmp_path):
    # A small fake AWS config file that contains the profile, so boto3 can find it without your real ~/.aws.
    config_file = tmp_path / "config"
    config_file.write_text("[profile lab-dev]\nregion = us-east-1\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("AWS_PROFILE", " lab-dev ")  # Stray spaces around a real name are removed.

    clean_aws_profile()

    assert os.environ["AWS_PROFILE"] == "lab-dev"
    assert boto3.Session().profile_name == "lab-dev"
