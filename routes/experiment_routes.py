"""Experiment list, experiment input forms, and running experiments."""

import logging  # Records errors in logs/app.log.

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool  # Runs slow AWS calls without freezing the web server.

from auth.access import require_login  # Blocks the page unless someone is logged in.
from core.aws_errors import friendly_error_message  # Readable error text.
from core.inputs import clean_inputs  # Validates form values.
from core.templates import render, render_not_found  # Renders HTML pages.
from experiments.registry import EXPERIMENTS, get_experiment  # The list of experiments.
from repositories.history_repository import save_run  # Run history.

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/experiments")
def experiments_page(request: Request, username: str = Depends(require_login)):
    # Lists all experiments from experiments/registry.py.
    return render(request, "experiments.html", {"experiments": EXPERIMENTS})


@router.get("/experiments/{experiment_id}")
def experiment_form(request: Request, experiment_id: str, username: str = Depends(require_login)):
    # Shows the input form for one experiment.
    experiment = get_experiment(experiment_id)
    if experiment is None:
        return render_not_found(request, f"Unknown experiment: {experiment_id}")
    return render(request, "experiment_form.html", {"experiment": experiment, "error_message": None})


@router.post("/experiments/{experiment_id}")
async def run_experiment(request: Request, experiment_id: str, username: str = Depends(require_login)):
    """Validates the inputs, runs the experiment, saves it to history, and shows the result."""
    experiment = get_experiment(experiment_id)
    if experiment is None:
        return render_not_found(request, f"Unknown experiment: {experiment_id}")

    form_data = await request.form()
    try:
        inputs = clean_inputs(experiment["inputs"], form_data)
    except ValueError as error:
        # Invalid input: show the form again with the problem explained. Nothing is sent to AWS.
        context = {"experiment": experiment, "error_message": str(error)}
        return render(request, "experiment_form.html", context, status_code=400)

    try:
        # Calls the experiment's run() function with the inputs as named arguments.
        result = await run_in_threadpool(experiment["run"], **inputs)
        # "warning" means the experiment finished but some steps failed (for example, missing permissions).
        status = "warning" if result["errors"] else "success"
    except Exception as error:
        # Experiments catch AWS errors per step, so reaching this point means an unexpected bug.
        logger.exception("Experiment %s crashed", experiment_id)
        result = {"summary": {}, "tables": [], "notes": [], "errors": [friendly_error_message(error)]}
        status = "error"

    run_id = save_run("experiment", experiment_id, inputs, status, result, username)
    context = {"experiment": experiment, "inputs": inputs, "result": result, "status": status, "run_id": run_id}
    return render(request, "experiment_result.html", context)
