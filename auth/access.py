"""Decides who may use the dashboard."""

import hmac  # compare_digest compares text without leaking timing information.

from fastapi import Request  # The incoming web request, which carries the session cookie.

from auth.passwords import verify_password  # Checks a typed password against the stored hash.
from core.config import get_settings  # Reads ADMIN_USERNAME and ADMIN_PASSWORD_HASH.


class LoginRequired(Exception):
    """Raised when a page needs a logged-in user. main.py turns it into a redirect to /login."""


def check_credentials(username, password):
    """Returns True only if both the username and the password are correct."""
    settings = get_settings()
    stored_hash = settings["admin_password_hash"]

    # If no password hash is configured, nobody can log in. This is safer than allowing everyone.
    if not stored_hash.startswith("pbkdf2_sha256:"):
        return False

    # Checks the username with a timing-safe comparison.
    username_ok = hmac.compare_digest(username.encode("utf-8"), settings["admin_username"].encode("utf-8"))
    # Always checks the password too, so a wrong username takes the same time as a wrong password.
    password_ok = verify_password(password, stored_hash)
    return username_ok and password_ok


def require_login(request: Request):
    """FastAPI dependency: add `username: str = Depends(require_login)` to a route to protect it.

    Returns the logged-in username, or raises LoginRequired if nobody is logged in.
    """
    # The session is a signed cookie. Its contents are set in routes/auth_routes.py after a successful login.
    username = request.session.get("username")
    if not username:
        raise LoginRequired()
    return username
