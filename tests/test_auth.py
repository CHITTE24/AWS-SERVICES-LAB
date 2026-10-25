"""Tests for password hashing, login, logout, and protected pages."""

from auth.passwords import hash_password, verify_password
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


def test_wrong_password_is_rejected(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 401
    assert "Wrong username or password" in response.text


def test_wrong_username_is_rejected(client):
    response = client.post("/login", data={"username": "someone", "password": TEST_PASSWORD})
    assert response.status_code == 401


def test_login_then_logout(logged_in_client):
    # After login the dashboard is visible.
    response = logged_in_client.get("/")
    assert response.status_code == 200
    assert "AWS services" in response.text

    # After logout the dashboard redirects to the login page again.
    logged_in_client.post("/logout")
    response = logged_in_client.get("/", follow_redirects=False)
    assert response.status_code == 303
