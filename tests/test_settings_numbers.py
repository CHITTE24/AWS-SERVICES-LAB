"""Regression tests: number settings such as DB_PORT must never stop the app from starting by surprise.

Bug: the line "DB_PORT=" (empty) in .env crashed the whole app at start-up with
"invalid literal for int()", even in local mode where DB_PORT is not used.
"""

import pytest  # The test framework (pytest.raises checks that an error happens).

from core.config import get_settings  # The function under test.


def test_empty_numbers_use_defaults(monkeypatch):
    monkeypatch.setenv("DB_PORT", "")
    monkeypatch.setenv("SECRET_CACHE_SECONDS", "  ")
    settings = get_settings()
    assert settings["db_port"] == 5432
    assert settings["secret_cache_seconds"] == 900


def test_missing_numbers_use_defaults(monkeypatch):
    monkeypatch.delenv("DB_PORT", raising=False)
    monkeypatch.delenv("SECRET_CACHE_SECONDS", raising=False)
    assert get_settings()["db_port"] == 5432


def test_valid_numbers_are_used(monkeypatch):
    monkeypatch.setenv("DB_PORT", "6543")
    monkeypatch.setenv("SECRET_CACHE_SECONDS", "60")
    settings = get_settings()
    assert settings["db_port"] == 6543
    assert settings["secret_cache_seconds"] == 60


def test_invalid_number_names_the_setting(monkeypatch):
    monkeypatch.setenv("DB_PORT", "abc")
    with pytest.raises(ValueError, match="DB_PORT must be a whole number"):
        get_settings()
