"""History pages: the list of past runs and the details of one run."""

from fastapi import APIRouter, Depends, Request

from auth.access import require_login  # Blocks the page unless someone is logged in.
from core.templates import render, render_not_found  # Renders HTML pages.
from repositories.history_repository import get_run, list_runs  # Reads saved runs.

router = APIRouter()


@router.get("/history")
def history_page(request: Request, username: str = Depends(require_login)):
    # Shows the newest 100 runs.
    return render(request, "history.html", {"runs": list_runs(limit=100)})


@router.get("/history/{run_id}")
def history_detail(request: Request, run_id: int, username: str = Depends(require_login)):
    # run_id: int makes FastAPI reject non-numeric ids such as /history/abc automatically.
    run = get_run(run_id)
    if run is None:
        return render_not_found(request, f"No run with id {run_id}")
    return render(request, "history_detail.html", {"run": run})
