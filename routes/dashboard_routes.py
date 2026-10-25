"""Dashboard, service pages, and running a single service operation."""

import logging  # Records errors in logs/app.log.

from fastapi import APIRouter, Depends, Request  # Depends runs require_login before the route.
from starlette.concurrency import run_in_threadpool  # Runs slow AWS calls without freezing the web server.

from auth.access import require_login  # Blocks the page unless someone is logged in.
from catalog.services import get_operation, get_service, services_by_category  # The service catalog.
from core.aws_client import ensure_writes_allowed  # The cost-safety switch.
from core.aws_errors import friendly_error_message  # Readable error text.
from core.inputs import clean_inputs  # Validates form values.
from core.templates import render, render_not_found  # Renders HTML pages.
from repositories.history_repository import list_runs, save_run  # Run history.

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/services")
def dashboard(request: Request, username: str = Depends(require_login)):
    # The services dashboard: all services grouped by category, plus the 5 most recent runs.
    # (The home page "/" is now the lab form in routes/lab_routes.py.)
    context = {"groups": services_by_category(), "recent_runs": list_runs(limit=5)}
    return render(request, "dashboard.html", context)


@router.get("/services/{service_id}")
def service_page(request: Request, service_id: str, username: str = Depends(require_login)):
    # {service_id} in the URL, for example "s3", arrives here as the service_id parameter.
    service = get_service(service_id)
    if service is None:
        return render_not_found(request, f"Unknown service: {service_id}")
    return render(request, "service.html", {"service": service})


@router.post("/services/{service_id}/{operation_id}")
async def run_operation(request: Request, service_id: str, operation_id: str,
                        username: str = Depends(require_login)):
    """Runs one operation, such as S3 "list_buckets", saves it to history, and shows the result."""
    service = get_service(service_id)
    operation = None
    if service is not None:
        operation = get_operation(service, operation_id)
    if operation is None:
        return render_not_found(request, f"Unknown operation: {service_id} / {operation_id}")

    # Reads the submitted form. "await" waits for the browser's data to arrive.
    form_data = await request.form()
    inputs = {}
    result = None
    error_message = None
    try:
        inputs = clean_inputs(operation["inputs"], form_data)
        # Write operations must pass the safety switch first. Every operation in v1 is a read.
        if operation["kind"] == "write":
            ensure_writes_allowed()
        # Calls the integration function (for example s3.list_buckets) in a background thread.
        result = await run_in_threadpool(operation["function"], **inputs)
        status = "success"
    except Exception as error:
        logger.warning("Operation %s/%s failed: %s", service_id, operation_id, error)
        status = "error"
        error_message = friendly_error_message(error)

    # Saves the run whether it succeeded or failed, so the history page shows everything.
    saved_result = result if error_message is None else {"error": error_message}
    run_id = save_run("operation", f"{service_id} / {operation_id}", inputs, status, saved_result, username)

    context = {"service": service, "operation": operation, "inputs": inputs, "result": result,
               "error_message": error_message, "run_id": run_id}
    return render(request, "operation_result.html", context)
