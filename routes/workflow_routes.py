"""The Live Workflow Monitor pages. Separate from "Check AWS environment", which stays unchanged.

    GET  /workflows                    the page: start a workflow, and the workflow history
    POST /workflows                    starts a workflow, then redirects to its live page
    GET  /workflows/{id}               one workflow's live page (works without JavaScript too)
    GET  /workflows/{id}/status        JSON for the page's automatic updates (static/workflow.js)

Every page is private to its owner and is never cached by the browser (Cache-Control: no-store).
"""

import logging  # Records errors in logs/app.log.

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse  # JSON answers and redirects.
from starlette.concurrency import run_in_threadpool  # Runs slow AWS calls without freezing the web server.

from auth.access import require_login  # Blocks the page unless someone is logged in.
from core.form_checks import MAX_DOCUMENT_BYTES, check_document  # The existing file checks (type, size, content).
from core.templates import render  # Renders HTML pages.
from experiments import live_workflow  # Starts workflows and reports their status.
from experiments import workflow_states as rules  # States and time helpers.
from experiments.workflow_simulator import SCENARIOS  # The simulation choices.
from repositories import workflow_repository  # Reads workflows.
from repositories.history_repository import save_run  # Every workflow also appears on the History page.

router = APIRouter()
logger = logging.getLogger(__name__)


def no_store(response):
    # The browser must never show a saved copy: the states would be out of date.
    response.headers["Cache-Control"] = "no-store"
    return response


def find_own_workflow(workflow_id, username):
    """Returns the workflow if the ID is valid AND belongs to this user, otherwise None (shown as 404)."""
    if not live_workflow.WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
        return None
    workflow = workflow_repository.get_workflow(workflow_id)
    if workflow is None or workflow["owner"] != username:
        return None  # Another user's workflow looks exactly like a missing one.
    return workflow


def show_list(request, username, error=None, status_code=200):
    # The main page: the start form and the user's 10 newest workflows, with their current state.
    workflows = []
    for meta in workflow_repository.list_workflows(username, limit=10):
        try:
            workflows.append(live_workflow.describe(live_workflow.refresh(meta["workflow_id"])))
        except Exception as problem:
            # For example DynamoDB is unreachable: the row is still listed, with an unknown state.
            logger.warning("Could not read workflow %s: %s", meta["workflow_id"], live_workflow.log_summary(problem))
            workflows.append(dict(meta, overall="Unknown", simulated=meta["mode"] == "simulated"))
    context = {"workflows": workflows, "error": error, "new_workflow_id": live_workflow.new_workflow_id(),
               "scenarios": SCENARIOS, "real_problem": live_workflow.real_workflow_problem(),
               "max_megabytes": MAX_DOCUMENT_BYTES // (1024 * 1024)}
    return no_store(render(request, "workflows.html", context, status_code=status_code))


def save_history(workflow_id, username):
    """Adds a snapshot to the History page, so every workflow (also failed or incomplete) stays visible."""
    view = live_workflow.describe(workflow_repository.get_workflow(workflow_id))
    if view["simulated"]:
        status = "simulated"
    elif view["overall"] == rules.FAILED:
        status = "error"
    elif view["overall"] == rules.COMPLETED:
        status = "success"
    else:
        status = "pending"  # Still waiting for later stages.
    snapshot = {"overall_when_saved": view["overall"], "live_status": f"/workflows/{workflow_id}",
                "note": "Snapshot when the FastAPI stages finished. The Live Workflow Monitor shows the current state.",
                "stages": [{"stage": stage["name"], "state": stage["state"], "detail": stage["detail"]}
                           for stage in view["stages"]]}
    save_run("workflow", f"Live workflow {workflow_id[:8]} ({view['file_name']})",
             {"workflow_id": workflow_id, "mode": view["mode"]}, status, snapshot, username)


@router.get("/workflows")
def workflows_page(request: Request, username: str = Depends(require_login)):
    return show_list(request, username)


@router.post("/workflows")
async def start_workflow(request: Request, username: str = Depends(require_login)):
    """Validates the upload, starts the workflow, then redirects to its live page (Post/Redirect/Get)."""
    # Rejects very large requests BEFORE reading them (the exact 5 MB check is in check_document).
    if int(request.headers.get("content-length") or 0) > MAX_DOCUMENT_BYTES + 100_000:
        return show_list(request, username, "The file is larger than 5 MB.", 413)

    form = await request.form(max_files=1, max_fields=5)
    workflow_id = str(form.get("workflow_id", ""))
    if not live_workflow.WORKFLOW_ID_PATTERN.fullmatch(workflow_id):
        workflow_id = live_workflow.new_workflow_id()
    mode = str(form.get("mode", ""))
    scenario = str(form.get("scenario", "success"))
    if mode not in ("aws", "simulated"):
        return show_list(request, username, "Choose Real AWS or Simulated.", 400)

    # Double click or Back + Submit: the same workflow_id already exists, so just show it again.
    existing = workflow_repository.get_workflow(workflow_id)
    if existing is not None:
        if existing["owner"] == username:
            return RedirectResponse(f"/workflows/{workflow_id}", status_code=303)
        workflow_id = live_workflow.new_workflow_id()

    upload = form.get("document")
    if upload is None or not getattr(upload, "filename", ""):
        return show_list(request, username, "Choose a file to upload.", 400)
    content = await upload.read(MAX_DOCUMENT_BYTES + 1)  # At most 5 MB + 1 byte is ever read.
    try:
        document = check_document(upload.filename, content)
    except ValueError as problem:
        return show_list(request, username, str(problem), 400)

    if mode == "aws":
        problem = live_workflow.real_workflow_problem()
        if problem:
            return show_list(request, username, problem, 400)
        if live_workflow.limit_reached(username, rules.utc_now()):
            return show_list(request, username, "Cost limit reached: WORKFLOW_MAX_PER_HOUR real workflows per hour. "
                                                "Simulated workflows are still available.", 429)

    try:
        if mode == "simulated":
            await run_in_threadpool(live_workflow.start_simulated, workflow_id, username, document, scenario)
        else:
            await run_in_threadpool(live_workflow.start_real, workflow_id, username, document)
    except live_workflow.WorkflowError as problem:
        # Nothing could be recorded. The attempt still appears on the History page.
        save_run("workflow", f"Live workflow {workflow_id[:8]} ({document['name']})",
                 {"workflow_id": workflow_id, "mode": mode}, "error", {"error": str(problem)}, username)
        return show_list(request, username, str(problem), 503)

    save_history(workflow_id, username)
    return RedirectResponse(f"/workflows/{workflow_id}", status_code=303)


@router.get("/workflows/{workflow_id}")
def workflow_page(request: Request, workflow_id: str, username: str = Depends(require_login)):
    if find_own_workflow(workflow_id, username) is None:
        return no_store(render(request, "error.html", {"message": "Unknown workflow."}, status_code=404))
    view = live_workflow.describe(live_workflow.refresh(workflow_id))
    return no_store(render(request, "workflow_detail.html", {"view": view}))


@router.get("/workflows/{workflow_id}/status")
def workflow_status(workflow_id: str, username: str = Depends(require_login)):
    """The live status as JSON. Read by static/workflow.js every few seconds while the workflow runs."""
    if find_own_workflow(workflow_id, username) is None:
        return no_store(JSONResponse({"error": "Unknown workflow."}, status_code=404))
    try:
        view = live_workflow.describe(live_workflow.refresh(workflow_id))
    except Exception as problem:
        logger.warning("Status check failed for workflow %s: %s", workflow_id, live_workflow.log_summary(problem))
        return no_store(JSONResponse({"error": live_workflow.safe_error_detail(problem)}, status_code=503))
    return no_store(JSONResponse(view))
