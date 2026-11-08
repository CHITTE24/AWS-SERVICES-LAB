"""Starts the AWS Services Lab web application.

Run it locally (with the virtual environment activated):   python -m uvicorn main:app --reload
Then open:   http://127.0.0.1:8000
"""

import logging  # Writes startup messages to the log.
import secrets  # Creates a temporary session secret if none is configured.

from fastapi import FastAPI, Request  # The web framework.
from fastapi.exception_handlers import http_exception_handler  # FastAPI's normal answer for 404, 405, ...
from fastapi.responses import JSONResponse, RedirectResponse  # JSON answers; sends the browser to another URL.
from fastapi.staticfiles import StaticFiles  # Serves CSS and JavaScript files.
from starlette.exceptions import HTTPException as StarletteHTTPException  # Raised for unknown URLs (404).
from starlette.middleware.sessions import SessionMiddleware  # Signed login cookies.

from auth.access import LoginRequired  # Raised by pages that need a logged-in user.
from core.config import PROJECT_ROOT, get_settings  # Settings from .env.
from core.logging_setup import setup_logging  # Log configuration.
from core.templates import render_not_found, templates  # The "Not found" page, and the template engine.
from repositories.history_repository import create_tables  # Prepares the SQLite database.
from repositories.submission_repository import create_submissions_table, using_rds  # The lab_submissions table.
from repositories.workflow_repository import create_tables as create_workflow_tables  # Live Workflow Monitor.
from repositories.architecture_repository import create_tables as create_architecture_tables  # Architecture Map.
from routes import (architecture_routes, auth_routes, dashboard_routes, experiment_routes, history_routes,  # Pages.
                    lab_routes, workflow_routes)

settings = get_settings()
setup_logging(settings["log_level"], settings["log_file"])
logger = logging.getLogger(__name__)

# Creates the history table the first time the app runs. Later runs leave it unchanged.
create_tables()
# The Live Workflow Monitor's local tables (its workflow list, and simulated or SQLite-stored stages).
create_workflow_tables()
# The Architecture Map uses its OWN file (data/architecture.db), so data/lab.db is never changed.
create_architecture_tables()
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
app.include_router(architecture_routes.router)


@app.exception_handler(LoginRequired)
def redirect_to_login(request: Request, error: LoginRequired):
    # When a protected page is opened without a login, send the browser to the login page.
    return RedirectResponse("/login", status_code=303)


def wants_html(request):
    """True when a person's browser asked for a page (not a script asking for JSON data)."""
    # The JSON APIs (/api/... and the Live Monitor's /status) keep their JSON answers.
    if request.url.path.startswith("/api/") or request.url.path.endswith("/status"):
        return False
    return "text/html" in request.headers.get("accept", "")


@app.exception_handler(StarletteHTTPException)
async def friendly_http_error(request: Request, error: StarletteHTTPException):
    """Shows the app's own "Not found" page for an unknown address, instead of raw JSON text.

    Only 404s opened in a browser are changed. Every other case (API calls, 405, ...) keeps FastAPI's
    normal answer, so nothing that scripts rely on changes.
    """
    if error.status_code == 404 and wants_html(request):
        return render_not_found(request, f"There is no page at {request.url.path}.")
    return await http_exception_handler(request, error)


@app.exception_handler(Exception)
def unexpected_error(request: Request, error: Exception):
    """Last safety net: any error that no other code handled (it would otherwise be an HTTP 500).

    1. Writes the full error (the "traceback") to logs/app.log, with a short reference code.
       Without this, the details only reached the terminal / journalctl, never logs/app.log.
    2. Shows the user a friendly page with the same reference code. The technical details are
       NEVER shown in the browser, because they can reveal file paths or settings.
    To find the cause on EC2:  grep <reference> logs/app.log   (or: journalctl -u aws-services-lab)
    """
    reference = secrets.token_hex(4)  # For example "3f9a1c2b": connects the page to the log line.
    logger.exception("Unexpected error %s on %s %s", reference, request.method, request.url.path)
    message = (f"Something went wrong on the server. Reference: {reference}. "
               "The details were written to logs/app.log.")
    if not wants_html(request):
        return JSONResponse({"error": message}, status_code=500)
    # This handler runs OUTSIDE the session layer, so it cannot use render() (which reads the session).
    context = {"app_title": settings["app_title"], "username": None, "heading": "Server error", "message": message}
    return templates.TemplateResponse(request, "error.html", context, status_code=500)


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
