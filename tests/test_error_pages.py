"""Regression tests for the error pages (BROWSER_AUDIT_AND_REPAIR_REPORT.md, findings F1 and F2).

F1: an unexpected error (for example a database that cannot be written) used to give a bare
    "Internal Server Error" text, and the details never reached logs/app.log.
F2: an unknown address showed raw JSON ({"detail":"Not Found"}) in the browser.
"""

import logging  # caplog reads the log messages the app writes.
import sqlite3  # The error type SQLite raises, for example for a read-only database file.

from fastapi.testclient import TestClient  # A fake browser.

from main import app
from routes import history_routes

BROWSER = {"accept": "text/html,application/xhtml+xml"}  # What a real browser sends.


def broken_list_runs(limit=100):
    # Simulates the database problem seen on servers: the data/ folder is owned by root.
    raise sqlite3.OperationalError("attempt to write a readonly database")


def test_unexpected_error_shows_friendly_page_and_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(history_routes, "list_runs", broken_list_runs)
    # raise_server_exceptions=False makes the fake browser behave like a real one: it receives the 500 page.
    browser = TestClient(app, raise_server_exceptions=False)
    browser.post("/login", data={"username": "student", "password": "pw"})

    with caplog.at_level(logging.ERROR):
        response = browser.get("/history", headers=BROWSER)

    assert response.status_code == 500
    assert "text/html" in response.headers["content-type"]
    assert "Server error" in response.text and "Reference:" in response.text
    # The technical details must NOT be shown in the browser.
    assert "readonly database" not in response.text
    assert "Traceback" not in response.text
    # ...but they MUST be in the log, with the same reference code, so the cause can be found.
    reference = response.text.split("Reference: ")[1][:8]
    logged = [r for r in caplog.records if reference in r.getMessage()]
    assert logged and logged[0].exc_info is not None
    assert "GET /history" in logged[0].getMessage()


def test_unexpected_error_in_json_request_returns_json(monkeypatch):
    monkeypatch.setattr(history_routes, "list_runs", broken_list_runs)
    browser = TestClient(app, raise_server_exceptions=False)
    browser.post("/login", data={"username": "student", "password": "pw"})
    response = browser.get("/history", headers={"accept": "application/json"})
    assert response.status_code == 500
    assert "Reference:" in response.json()["error"]


def test_unknown_page_shows_html_not_found_page(client):
    response = client.get("/no-such-page", headers=BROWSER)
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Not found" in response.text and "/no-such-page" in response.text


def test_unknown_address_for_scripts_still_returns_json(client):
    # Scripts and API calls keep FastAPI's normal JSON answer.
    assert client.get("/no-such-page").json() == {"detail": "Not Found"}
    assert client.get("/api/no-such-thing", headers=BROWSER).json() == {"detail": "Not Found"}


def test_other_http_errors_unchanged(client):
    # GET /logout is not allowed (logout must be a POST). It must stay a 405, not become a 404 page.
    assert client.get("/logout", headers=BROWSER).status_code == 405


def test_login_redirect_still_works(client):
    # The new catch-all handler must not swallow the "please log in" redirect.
    response = client.get("/services", headers=BROWSER, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_pages_declare_an_icon(logged_in_client):
    # Without it, every page made the browser request /favicon.ico and log a 404 in the console.
    assert '<link rel="icon" href="data:,">' in logged_in_client.get("/login").text
