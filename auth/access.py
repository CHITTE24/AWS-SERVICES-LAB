"""Decides who may use the app.

DEMONSTRATION LOGIN ONLY: any non-empty username and password are accepted.
Anyone who can reach the app can log in with made-up details, so this is NOT real authentication.
Never use it to protect sensitive information, and never expose the app publicly like this.
For real authentication, use something like Amazon Cognito, or the password-hash check in auth/passwords.py.
"""

from fastapi import Request  # The incoming web request, which carries the session cookie.


class LoginRequired(Exception):
    """Raised when a page needs a logged-in user. main.py turns it into a redirect to /login."""


def check_login_form(username, password):
    """Checks the demo login form.

    Returns: None when both fields are filled in, otherwise a message to show the user.
    The password is only checked for being non-empty. It is never stored, logged, or sent anywhere.
    """
    if not username.strip() or not password.strip():
        return "Please enter both a username and a password."
    if len(username.strip()) > 100:
        return "The username must be 100 characters or fewer."
    return None


def require_login(request: Request):
    """FastAPI dependency: add `username: str = Depends(require_login)` to a route to protect it.

    Returns the logged-in username, or raises LoginRequired if nobody is logged in.
    """
    # The session is a signed cookie. Its contents are set in routes/auth_routes.py after a successful login.
    username = request.session.get("username")
    if not username:
        raise LoginRequired()
    return username
