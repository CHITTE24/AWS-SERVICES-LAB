"""The main page ("Welcome to AWS Service Lab"): the form, its results, and the AWS environment check."""

import logging  # Records errors in logs/app.log.
import re  # Checks the format of submission IDs.
import time  # Measures how long the environment check took.
import uuid  # Creates random submission IDs.
from datetime import datetime, timezone  # The "Last checked" time.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse  # Sends the browser to another URL.
from starlette.concurrency import run_in_threadpool  # Runs slow AWS calls without freezing the web server.

from auth.access import require_login  # Blocks the page unless someone is logged in.
from core import request_security  # Decides HTTPS from trusted request information only.
from core.aws_errors import NOT_CONFIGURED, PROBLEM_STATUSES  # Status words.
from core.config import get_settings  # Reads the configuration for the Security Configuration panel.
from core.form_checks import MAX_DOCUMENT_BYTES, check_document, validate_submission  # Form checks.
from core.templates import render  # Renders HTML pages.
from experiments import architecture_evidence  # Records results as Architecture Map evidence.
from experiments import environment_check, submission_workflow  # The two workflows.
from repositories import submission_repository  # Reads saved submissions.
from repositories.history_repository import save_run  # Saves environment checks to the run history.

router = APIRouter()
logger = logging.getLogger(__name__)

# A submission ID is 32 hexadecimal characters (a UUID without dashes).
SUBMISSION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


# Shown in the "Connection" column until "Check AWS environment" has been clicked on this page.
NOT_CHECKED = "Not Checked"
# Shown in the "In .env" column for rows that are not a setting (for example HTTPS, which depends on the request).
NOT_A_SETTING = "-"


def connection_from_checks(checks, service, action=""):
    """Finds the status a real check reported for a service, or "Not Checked" if there is none."""
    for row in checks or []:
        if row["service"] == service and row["action"].startswith(action):
            return row["status"]
    return NOT_CHECKED


def security_configuration(request, checks=None):
    """Builds the Security Configuration rows: (name, in .env, connection, explanation). No secret values.

    "In .env" (Configured / Not Configured) only says whether a setting is PRESENT. It is never proof.
    "Connection" is filled in only by a real request: the HTTPS rows by this page request itself,
    the AWS rows by the "Check AWS environment" button (checks = its rows, or None if not clicked).
    """
    settings = get_settings()

    def in_env(value):
        return "Configured" if value else NOT_CONFIGURED

    info = request_security.describe_request(request)
    https = request_security.https_status(info)
    cookie = request_security.cookie_status(info)
    rows = [
        ("HTTPS / TLS for this page", NOT_A_SETTING, https[0], https[1]),
        ("Secure session cookie", in_env(settings["session_https_only"]), cookie[0], cookie[1]),
        # Each AWS row has two explanations: the first when the setting is filled in, the second when it is empty.
        ("S3 bucket", in_env(settings["s3_bucket_name"]), connection_from_checks(checks, "Amazon S3"),
         "S3_BUCKET_NAME is set. Connection = HeadBucket proved the bucket exists and is accessible."
         if settings["s3_bucket_name"] else
         "S3_BUCKET_NAME is empty, so documents are not uploaded to S3. Set it after creating the bucket."),
        ("KMS key reference", in_env(settings["s3_kms_key_arn"]), connection_from_checks(checks, "AWS KMS"),
         "S3_KMS_KEY_ARN is set. Connection = DescribeKey read the key; encrypting with it is proven per upload."
         if settings["s3_kms_key_arn"] else
         "S3_KMS_KEY_ARN is empty, so S3 uploads are refused (they must be SSE-KMS encrypted). "
         "Set it to the key ARN after creating the KMS key."),
        ("SSE-KMS on uploads", NOT_A_SETTING, NOT_CHECKED,
         "Verified separately for each upload: see the AWS KMS row in AWS Service Activity."),
        ("Secrets Manager reference", in_env(settings["secret_name"]),
         connection_from_checks(checks, "AWS Secrets Manager", "Retrieve value"),
         "AWS_SECRETS_MANAGER_SECRET_NAME is set. Connection = a fresh read succeeded. Values are never shown."
         if settings["secret_name"] else
         "AWS_SECRETS_MANAGER_SECRET_NAME is empty, so no database password is read from Secrets Manager. "
         "Set it to the secret's name or ARN (never the value) after creating the secret."),
    ]
    if submission_repository.using_rds():
        tls_on = settings["db_sslmode"] in ("require", "verify-ca", "verify-full")
        rows.append(("RDS TLS", in_env(tls_on), connection_from_checks(checks, "Amazon RDS (PostgreSQL)"),
                     f"DB_SSLMODE={settings['db_sslmode']}. Connection = a real connection with TLS in use."))
    else:
        rows.append(("RDS TLS", NOT_CONFIGURED, NOT_CONFIGURED,
                     "Not applicable: submissions are saved to the local SQLite file."))
    return rows


def show_page(request, values=None, errors=None, result=None, checks=None, status_code=200, submission_id=None,
              checked_at=None):
    """Renders the main page. Every visit gets a fresh submission ID unless one is passed in (after an error).

    checked_at: text such as "2026-11-08 09:30:12 UTC ...", shown above the environment check results.
    """
    settings = get_settings()
    context = {
        "values": values or {}, "errors": errors or {}, "result": result, "checks": checks,
        "submission_id": submission_id or uuid.uuid4().hex,
        "app_mode": settings["app_mode"], "database_label": submission_repository.backend_label(),
        "security_rows": security_configuration(request, checks), "checked_at": checked_at,
        "max_megabytes": MAX_DOCUMENT_BYTES // (1024 * 1024),
    }
    response = render(request, "lab.html", context, status_code=status_code)
    # "no-store": the browser must never show a saved copy of this page (for example with the Back button),
    # because its connection statuses would be out of date.
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/")
def lab_page(request: Request, submission_id: str = "", username: str = Depends(require_login)):
    # The main page. After a submission, the browser is sent here with ?submission_id=... to show the result.
    result = None
    errors = {}
    if SUBMISSION_ID_PATTERN.fullmatch(submission_id):
        try:
            result = submission_repository.get_submission(submission_id, username)
        except Exception as error:
            logger.warning("Could not read submission %s: %s", submission_id, error)
            errors["page"] = submission_repository.describe_database_error(error)
        if result:
            result["database_status"] = "Success"  # The record was read back from the database, so it was saved.
    return show_page(request, errors=errors, result=result)


@router.post("/submit")
async def submit(request: Request, username: str = Depends(require_login)):
    """Validates the form, runs the workflow, then redirects to the result (so a refresh cannot resubmit)."""
    # Rejects very large requests BEFORE reading them. The exact 5 MB file check happens below.
    if int(request.headers.get("content-length") or 0) > MAX_DOCUMENT_BYTES + 100_000:
        return show_page(request, errors={"document": "The document is larger than 5 MB. Please upload a smaller file."},
                         status_code=413)

    # max_files and max_fields limit what a single request can contain.
    form = await request.form(max_files=1, max_fields=10)
    values, errors = validate_submission(form)

    # The hidden submission ID ties a click to one record. An unexpected value is replaced by a new one.
    submission_id = str(form.get("submission_id", ""))
    if not SUBMISSION_ID_PATTERN.fullmatch(submission_id):
        submission_id = uuid.uuid4().hex

    # The optional document. An empty file box still sends a part with an empty file name.
    document = None
    upload = form.get("document")
    if upload is not None and getattr(upload, "filename", ""):
        # Reads at most 5 MB + 1 byte: enough to know whether the file is too large, without reading more.
        content = await upload.read(MAX_DOCUMENT_BYTES + 1)
        try:
            document = check_document(upload.filename, content)
        except ValueError as error:
            errors["document"] = str(error)

    if errors:
        # Shows the form again with a message beside each problem. Nothing was sent to AWS.
        return show_page(request, values=values, errors=errors, status_code=400, submission_id=submission_id)

    # Duplicate protection: if this submission ID was already saved (double click, Back + Submit), show it instead.
    try:
        if submission_repository.get_submission(submission_id, username):
            return RedirectResponse(f"/?submission_id={submission_id}", status_code=303)
    except Exception as error:
        logger.warning("Duplicate check failed: %s", error)  # The workflow below reports the database problem.

    result = await run_in_threadpool(submission_workflow.process_submission, submission_id, username, values, document)
    architecture_evidence.record_submission(result)  # Statuses only (no personal data). Never raises.
    if result["saved"]:
        # Post/Redirect/Get: refreshing the result page only reloads it, it does not submit again.
        return RedirectResponse(f"/?submission_id={submission_id}", status_code=303)
    # The database failed, so there is no stored copy to redirect to. Show the result directly.
    return show_page(request, result=result)


@router.post("/environment-check")
async def run_environment_check(request: Request, username: str = Depends(require_login)):
    # Runs the read-only AWS environment checks and saves them to the run history page.
    # Every click runs every check again: nothing is reused from an earlier click.
    started = time.monotonic()
    checked_at = datetime.now(timezone.utc)
    # How THIS request arrived, for the HTTPS check (see core/request_security.py).
    request_info = request_security.describe_request(request)
    checks = await run_in_threadpool(environment_check.run, request_info)
    seconds = time.monotonic() - started
    status = "error" if any(row["status"] in PROBLEM_STATUSES for row in checks) else "success"
    run_id = save_run("experiment", "AWS environment check", {}, status, checks, username)
    architecture_evidence.record_environment_check(checks, run_id)  # Never raises; see the module.
    checked_text = (f"{checked_at:%Y-%m-%d %H:%M:%S} UTC, region {get_settings()['aws_region']}, "
                    f"took {seconds:.1f} s")
    return show_page(request, checks=checks, checked_at=checked_text)
