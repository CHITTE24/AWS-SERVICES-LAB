"""Tests for password hashing, the demo login, logout, and protected pages."""

import base64  # Decodes the session cookie to look inside it.

from auth.passwords import hash_password, verify_password
from core.config import get_settings
from tests.conftest import TEST_PASSWORD


def test_hash_and_verify_password():
    stored = hash_password("correct horse", iterations=1000)
    # The hash must never contain the original password.
    assert "correct horse" not in stored
    assert verify_password("correct horse", stored) is True
    assert verify_password("wrong", stored) is False


def test_verify_rejects_malformed_hash():
    assert verify_password("anything", "not-a-real-hash") is False
    assert verify_password("anything", "pbkdf2_sha256:abc:salt:hash") is False


def test_protected_page_redirects_to_login(client):
    # follow_redirects=False lets us inspect the redirect itself.
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Log in" in response.text
    assert "Demonstration login" in response.text


def test_any_non_empty_credentials_log_in(client):
    # Any username and password work, and the browser is sent to the main page.
    response = client.post("/login", data={"username": "anyone", "password": "anything"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    # The session cookie was created, so the main page is now visible.
    page = client.get("/")
    assert page.status_code == 200
    assert "Welcome to AWS Service Lab" in page.text
    assert "anyone" in page.text


def test_empty_username_is_rejected(client):
    response = client.post("/login", data={"username": "  ", "password": "secret"})
    assert response.status_code == 400
    assert "Please enter both a username and a password" in response.text


def test_empty_password_is_rejected(client):
    response = client.post("/login", data={"username": "anyone", "password": ""})
    assert response.status_code == 400
    # The main page stays locked.
    assert client.get("/", follow_redirects=False).status_code == 303


def test_missing_fields_are_rejected(client):
    # A request with no form fields at all gets the same friendly message, not a crash.
    response = client.post("/login", data={})
    assert response.status_code == 400


def test_password_is_never_logged_or_shown(client):
    failed = client.post("/login", data={"username": "", "password": TEST_PASSWORD})  # Fails: empty username.
    assert TEST_PASSWORD not in failed.text
    response = client.post("/login", data={"username": "logcheck", "password": TEST_PASSWORD})
    assert TEST_PASSWORD not in response.text

    # The session cookie is signed but NOT encrypted, so anyone could read it. Decode it and check.
    # Its format is "<base64 data>.<timestamp>.<signature>".
    cookie_data = base64.b64decode(client.cookies["session"].split(".")[0] + "==")
    assert b"logcheck" in cookie_data  # Proves the decoding worked.
    assert TEST_PASSWORD.encode() not in cookie_data

    with open(get_settings()["log_file"], encoding="utf-8") as log_file:
        assert TEST_PASSWORD not in log_file.read()


def test_login_then_logout(logged_in_client):
    # After login the main page is visible.
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert "Welcome to AWS Service Lab" in response.text
    assert "Log out" in response.text

    # After logout the main page redirects to the login page again.
    logged_in_client.post("/logout")
    response = logged_in_client.get("/", follow_redirects=False)
    assert response.status_code == 303
