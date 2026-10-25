"""Starts the AWS Services Lab web application.

Run it locally (with the virtual environment activated):   python -m uvicorn main:app --reload
Then open:   http://127.0.0.1:8000
"""

import logging  # Writes startup messages to the log.
import secrets  # Creates a temporary session secret if none is configured.

from fastapi import FastAPI, Request  # The web framework.
from fastapi.responses import RedirectResponse  # Sends the browser to another URL.
from fastapi.staticfiles import StaticFiles  # Serves CSS and JavaScript files.
from starlette.middleware.sessions import SessionMiddleware  # Signed login cookies.

from auth.access import LoginRequired  # Raised by pages that need a logged-in user.
from core.config import PROJECT_ROOT, get_settings  # Settings from .env.
from core.logging_setup import setup_logging  # Log configuration.
from repositories.history_repository import create_tables  # Prepares the SQLite database.
from routes import auth_routes, dashboard_routes, experiment_routes, history_routes  # The pages.

settings = get_settings()
setup_logging(settings["log_level"], settings["log_file"])
logger = logging.getLogger(__name__)

# Creates the history table the first time the app runs. Later runs leave it unchanged.
create_tables()

# docs_url=None and redoc_url=None turn off FastAPI's automatic API documentation pages, which this app does not need.
app = FastAPI(title=settings["app_title"], docs_url=None, redoc_url=None)

# Without a configured secret, a random one is used. Everyone is then logged out each time the app restarts.
session_secret = settings["session_secret"]
if not session_secret:
    logger.warning("SESSION_SECRET is not set. Using a temporary secret (logins reset on restart).")
    session_secret = secrets.token_hex(32)

# Stores the login in a signed cookie. same_site="lax" stops other websites from submitting our forms as you.
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    max_age=8 * 60 * 60,  # Log out automatically after 8 hours (in seconds).
    same_site="lax",
    https_only=settings["session_https_only"],
)

# Makes files in static/ available at URLs like /static/style.css.
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")

# Connects each group of pages to the app.
app.include_router(auth_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(experiment_routes.router)
app.include_router(history_routes.router)


@app.exception_handler(LoginRequired)
def redirect_to_login(request: Request, error: LoginRequired):
    # When a protected page is opened without a login, send the browser to the login page.
    return RedirectResponse("/login", status_code=303)


@app.get("/health")
def health():
    # A simple check used by you, scripts, and load balancers to confirm the app is running.
    # It deliberately does not call AWS, so it works even without credentials.
    return {"status": "ok"}


if not settings["admin_password_hash"]:
    logger.warning("ADMIN_PASSWORD_HASH is not set, so nobody can log in. See docs/LOCAL_SETUP.md.")
logger.info("AWS Services Lab started. Default region: %s", settings["aws_region"])
