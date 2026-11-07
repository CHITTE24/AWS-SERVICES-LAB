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
from repositories.submission_repository import create_submissions_table, using_rds  # The lab_submissions table.
from repositories.workflow_repository import create_tables as create_workflow_tables  # Live Workflow Monitor.
from routes import (auth_routes, dashboard_routes, experiment_routes, history_routes, lab_routes,  # The pages.
                    workflow_routes)

settings = get_settings()
setup_logging(settings["log_level"], settings["log_file"])
logger = logging.getLogger(__name__)

# Creates the history table the first time the app runs. Later runs leave it unchanged.
create_tables()
# The Live Workflow Monitor's local tables (its workflow list, and simulated or SQLite-stored stages).
create_workflow_tables()
# The local SQLite submissions table is created automatically. The RDS table is created once,
# by hand, with "python scripts/init_database.py", so starting the app never needs RDS to be reachable.
if not using_rds():
    create_submissions_table()

# docs_url=None and redoc_url=None turn off FastAPI's automatic API documentation pages, which this app does not need.
app = FastAPI(title=settings["app_title"], docs_url=None, redoc_url=None)

# Without a configured secret, a random one is used. Everyone is then logged out each time the app restarts.
session_secret = settings["session_secret"]
if not session_secret:
    logger.warning("SESSION_SECRET is not set. Using a temporary secret (logins reset on restart).")
    session_secret = secrets.token_hex(32)

# Stores the login in a signed cookie. The cookie is always HttpOnly (JavaScript cannot read it).
# same_site="lax" stops other websites from submitting our forms as you.
# https_only=True adds the "Secure" flag, so the browser sends the cookie only over HTTPS.
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
app.include_router(lab_routes.router)
app.include_router(dashboard_routes.router)
app.include_router(experiment_routes.router)
app.include_router(history_routes.router)
app.include_router(workflow_routes.router)


@app.exception_handler(LoginRequired)
def redirect_to_login(request: Request, error: LoginRequired):
    # When a protected page is opened without a login, send the browser to the login page.
    return RedirectResponse("/login", status_code=303)


@app.get("/health")
def health():
    # A simple check used by you, scripts, and load balancers to confirm the app is running.
    # It deliberately does not call AWS, so it works even without credentials.
    return {"status": "ok"}


logger.warning("DEMO LOGIN is active: any non-empty username and password are accepted. "
               "Do not expose this app publicly or use it for sensitive data.")
if settings["app_mode"] not in ("local", "aws"):
    logger.warning("APP_MODE must be 'local' or 'aws'. '%s' is treated like aws mode.", settings["app_mode"])
if settings["trust_proxy_headers"]:
    # Only safe behind your own load balancer, with uvicorn's --forwarded-allow-ips limited to it.
    logger.warning("TRUST_PROXY_HEADERS=true: X-Forwarded-Proto is accepted when uvicorn trusts the sender. "
                   "Start uvicorn with --proxy-headers --forwarded-allow-ips=<load balancer subnets>.")
if settings["app_mode"] != "local" and not settings["session_https_only"]:
    logger.warning("APP_MODE=aws but SESSION_HTTPS_ONLY=false. Serve the app over HTTPS and set it to true.")
logger.info("AWS Services Lab started. Mode: %s. Default region: %s", settings["app_mode"], settings["aws_region"])
