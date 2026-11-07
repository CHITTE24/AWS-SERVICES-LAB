"""The main page ("Welcome to AWS Service Lab"): the form, its results, and the AWS environment check."""

import logging  # Records errors in logs/app.log.
import re  # Checks the format of submission IDs.
import uuid  # Creates random submission IDs.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse  # Sends the browser to another URL.
from starlette.concurrency import run_in_threadpool  # Runs slow AWS calls without freezing the web server.

from auth.access import require_login  # Blocks the page unless someone is logged in.
from core.config import get_settings  # Reads the configuration for the Security Configuration panel.
from core.form_checks import MAX_DOCUMENT_BYTES, check_document, validate_submission  # Form checks.
from core.templates import render  # Renders HTML pages.
from experiments import environment_check, submission_workflow  # The two workflows.
from repositories import submission_repository  # Reads saved submissions.
from repositories.history_repository import save_run  # Saves environment checks to the run history.

router = APIRouter()
logger = logging.getLogger(__name__)

# A submission ID is 32 hexadecimal characters (a UUID without dashes).
SUBMISSION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def security_configuration(request):
    """Builds the Security Configuration rows: (setting, status, explanation). No secret values are shown.

    "Configured" only means a setting is present. "Verified" means it was proven by a real request.
    """
    settings = get_settings()
    https = request.url.scheme == "https"
    rows = [
        ("HTTPS / TLS for this page", "Verified" if https else "Not Configured",
         "This page arrived over HTTPS." if https else
         "This page arrived over plain HTTP. That is fine on your own computer (localhost), "
         "but a deployed app must be reached through HTTPS (see README: AWS Security Setup)."),
        ("Secure session cookie", "Configured" if settings["session_https_only"] else "Not Configured",
         "SESSION_HTTPS_ONLY=true: the login cookie is only sent over HTTPS (it is always HttpOnly and SameSite=Lax)."
         if settings["session_https_only"] else "SESSION_HTTPS_ONLY=false. Set it to true when the app is served over HTTPS."),
        # Each row below has two explanations: the first when the setting is filled in, the second when it is empty.
        ("S3 bucket", "Configured" if settings["s3_bucket_name"] else "Not Configured",
         "S3_BUCKET_NAME is set. Access is proven only by a successful upload."
         if settings["s3_bucket_name"] else
         "S3_BUCKET_NAME is empty, so documents are not uploaded to S3. Set it after creating the bucket."),
        ("KMS key reference", "Configured" if settings["s3_kms_key_arn"] else "Not Configured",
         "S3_KMS_KEY_ARN is set. This alone does NOT prove that objects are encrypted with it."
         if settings["s3_kms_key_arn"] else
         "S3_KMS_KEY_ARN is empty, so S3 uploads are refused (they must be SSE-KMS encrypted). "
         "Set it to the key ARN after creating the KMS key."),
        ("SSE-KMS on uploads", "Unknown",
         "Verified separately for each upload: see the AWS KMS row in AWS Service Activity."),
        ("Secrets Manager reference", "Configured" if settings["secret_name"] else "Not Configured",
         "AWS_SECRETS_MANAGER_SECRET_NAME is set. Use 'Check AWS environment' to verify it can be read."
         if settings["secret_name"] else
         "AWS_SECRETS_MANAGER_SECRET_NAME is empty, so no database password is read from Secrets Manager. "
         "Set it to the secret's name or ARN (never the value) after creating the secret."),
    ]
    if submission_repository.using_rds():
        tls_on = settings["db_sslmode"] in ("require", "verify-ca", "verify-full")
        rows.append(("RDS TLS", "Configured" if tls_on else "Not Configured",
                     f"DB_SSLMODE={settings['db_sslmode']}. 'Check AWS environment' shows whether TLS is really in use."))
    else:
        rows.append(("RDS TLS", "Not Configured", "Not applicable: submissions are saved to the local SQLite file."))
    return rows


def show_page(request, values=None, errors=None, result=None, checks=None, status_code=200, submission_id=None):
    # Renders the main page. Every visit gets a fresh submission ID unless one is passed in (after an error).
    settings = get_settings()
    context = {
        "values": values or {}, "errors": errors or {}, "result": result, "checks": checks,
        "submission_id": submission_id or uuid.uuid4().hex,
        "app_mode": settings["app_mode"], "database_label": submission_repository.backend_label(),
        "security_rows": security_configuration(request),
        "max_megabytes": MAX_DOCUMENT_BYTES // (1024 * 1024),
    }
    return render(request, "lab.html", context, status_code=status_code)


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
    if result["saved"]:
        # Post/Redirect/Get: refreshing the result page only reloads it, it does not submit again.
        return RedirectResponse(f"/?submission_id={submission_id}", status_code=303)
    # The database failed, so there is no stored copy to redirect to. Show the result directly.
    return show_page(request, result=result)


@router.post("/environment-check")
async def run_environment_check(request: Request, username: str = Depends(require_login)):
    # Runs the read-only AWS environment checks and saves them to the run history page.
    checks = await run_in_threadpool(environment_check.run)
    status = "error" if any(row["status"] == "Failed" for row in checks) else "success"
    save_run("experiment", "AWS environment check", {}, status, checks, username)
    return show_page(request, checks=checks)
