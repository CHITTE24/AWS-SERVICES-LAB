"""The Architecture Map: your own diagrams, coloured ONLY by recorded verification evidence.

    GET    /architecture                                 the page (Design or Presentation mode)
    GET    /api/architecture/catalog                     box types and arrow operations
    GET    /api/architecture/diagrams                    your diagrams
    POST   /api/architecture/diagrams                    create (blank, or the optional starter drawing)
    GET    /api/architecture/diagrams/{id}               load
    PUT    /api/architecture/diagrams/{id}               save (refused if it changed elsewhere)
    DELETE /api/architecture/diagrams/{id}               delete the DRAWING (evidence is always kept)
    GET    /api/architecture/diagrams/{id}/evidence      colours, timestamps, history (no AWS calls)
    POST   /api/architecture/diagrams/{id}/manual        add a MANUAL verification record
    POST   /api/architecture/diagrams/{id}/verify        Verify Now: one READ-ONLY check of one box

Every endpoint needs a login, and a diagram is visible only to its owner (others get 404).
Requests that change something must be JSON AND come from this app's own pages (same-origin check).
Nothing here creates, connects, changes, or deletes anything in AWS.
"""

import json
import logging
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool  # Verify Now calls AWS: do not block the server.

from auth.access import require_login
from catalog import architecture_catalog as catalog
from core.config import PROJECT_ROOT
from core.templates import render
from experiments import architecture_diagram, architecture_evidence, architecture_verify, live_workflow
from experiments import workflow_states as clock
from repositories import architecture_repository as store

router = APIRouter()
logger = logging.getLogger(__name__)
ICON_FOLDER = PROJECT_ROOT / "static" / "icons" / "aws"
# Recent workflows are brought up to date at most this often (seconds), when the map reads its evidence.
SYNC_INTERVAL_SECONDS = 60
_last_sync = {"at": None}


def answer(data, status_code=200):
    response = JSONResponse(data, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"  # Private, always fresh.
    return response


def problem(message, status_code=400):
    return answer({"error": message}, status_code)


def from_other_site(request):
    """True if a browser sent this request from ANOTHER website (Origin / Referer of a different host).

    Browsers add Origin to every POST, PUT, and DELETE made by a page. The host part must match the address
    this app was reached at (127.0.0.1, localhost, or your load balancer's name all work). Requests without
    either header come from tools such as curl and still need the login cookie and a JSON body.
    """
    sender = request.headers.get("origin") or request.headers.get("referer")
    if not sender or sender == "null":
        return sender == "null"  # "null" is what sandboxed or file:// pages send: refuse it.
    return urlparse(sender).netloc.lower() != request.headers.get("host", "").lower()


async def read_json(request):
    """Reads a JSON request body safely. Returns (data, None) or (None, error response)."""
    if from_other_site(request):
        return None, problem("This request did not come from the Architecture Map page.", 403)
    # Only real JSON requests: a plain HTML form on another website cannot send this content type.
    if not request.headers.get("content-type", "").startswith("application/json"):
        return None, problem("Send the request as application/json.", 415)
    raw = await request.body()
    if len(raw) > catalog.MAX_DIAGRAM_BYTES + 10_000:
        return None, problem("The request is too large.", 413)
    try:
        return json.loads(raw), None
    except (ValueError, UnicodeDecodeError):
        return None, problem("The request is not valid JSON.")


def own_diagram(diagram_id, username):
    """The stored diagram with its parsed body, or None if it is missing or someone else's."""
    row = store.get_diagram(diagram_id, username)
    if row is None:
        return None
    row["body"] = json.loads(row["body"])
    return row


def find_element(body, element_id):
    """Returns ("node", node) or ("edge", edge, source_node, target_node), or None."""
    nodes = {node["id"]: node for node in body["nodes"]}
    if element_id in nodes:
        return ("node", nodes[element_id])
    for edge in body["edges"]:
        if edge["id"] == element_id:
            return ("edge", edge, nodes[edge["source"]], nodes[edge["target"]])
    return None


# ---------------------------------------------------------------- the page

@router.get("/architecture")
def architecture_page(request: Request, username: str = Depends(require_login)):
    mode = "present" if request.query_params.get("mode") == "present" else "design"
    response = render(request, "architecture.html", {"mode": mode})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/architecture/catalog")
def catalog_data(username: str = Depends(require_login)):
    types = {}
    for type_id, spec in catalog.RESOURCE_TYPES.items():
        icon = ICON_FOLDER / spec["icon"]
        types[type_id] = {"label": spec["label"], "identifier": spec["identifier"],
                          "container": bool(spec.get("container")), "verify_now": type_id in architecture_verify.CHECKS,
                          # Official AWS icons are used only if you copied them there (they are not in Git).
                          "icon": f"/static/icons/aws/{spec['icon']}" if icon.is_file() else None}
    operations = {op_id: {key: (list(value) if isinstance(value, tuple) else value) for key, value in spec.items()}
                  for op_id, spec in catalog.OPERATIONS.items()}
    return answer({"types": types, "operations": operations,
                   "limits": {"nodes": catalog.MAX_NODES, "edges": catalog.MAX_EDGES, "label": catalog.MAX_LABEL,
                              "notes": catalog.MAX_NOTES}})


# ---------------------------------------------------------------- diagrams

@router.get("/api/architecture/diagrams")
def list_diagrams(username: str = Depends(require_login)):
    return answer({"diagrams": store.list_diagrams(username)})


@router.post("/api/architecture/diagrams")
async def create_diagram(request: Request, username: str = Depends(require_login)):
    data, error = await read_json(request)
    if error:
        return error
    try:
        name = architecture_diagram.clean_text(data.get("name"), catalog.MAX_NAME, "Name") or "Untitled architecture"
        if data.get("template") == "starter":
            body = await run_in_threadpool(architecture_diagram.starter_diagram)  # May ask instance metadata once.
        else:
            body = architecture_diagram.validate_diagram(data.get("body") or {"nodes": [], "edges": []})
            if not body["scope"]["account"] and not body["scope"]["region"]:
                # A new diagram is scoped to the account and region the app uses now (you can change it).
                body["scope"] = await run_in_threadpool(architecture_diagram.current_scope)
    except (ValueError, AttributeError) as invalid:
        return problem(str(invalid) or "Invalid request.")
    diagram_id = store.create_diagram(username, name, json.dumps(body))
    return answer({"id": diagram_id, "version": 1}, 201)


@router.get("/api/architecture/diagrams/{diagram_id}")
def get_diagram(diagram_id: int, username: str = Depends(require_login)):
    diagram = own_diagram(diagram_id, username)
    if diagram is None:
        return problem("Unknown diagram.", 404)
    return answer({"id": diagram["id"], "name": diagram["name"], "version": diagram["version"],
                   "updated_at": diagram["updated_at"], "body": diagram["body"]})


@router.put("/api/architecture/diagrams/{diagram_id}")
async def save_diagram(diagram_id: int, request: Request, username: str = Depends(require_login)):
    if store.get_diagram(diagram_id, username) is None:
        return problem("Unknown diagram.", 404)
    data, error = await read_json(request)
    if error:
        return error
    try:
        name = architecture_diagram.clean_text(data.get("name"), catalog.MAX_NAME, "Name") or "Untitled architecture"
        body = architecture_diagram.validate_diagram(data.get("body"))
        version = int(data.get("version"))
    except (ValueError, TypeError, AttributeError) as invalid:
        return problem(str(invalid) or "Invalid request.")
    try:
        new_version = store.update_diagram(diagram_id, username, name, json.dumps(body), version)
    except store.VersionConflict as conflict:
        return problem(str(conflict), 409)
    return answer({"id": diagram_id, "version": new_version})


@router.delete("/api/architecture/diagrams/{diagram_id}")
def delete_diagram(diagram_id: int, request: Request, username: str = Depends(require_login)):
    if from_other_site(request):
        return problem("This request did not come from the Architecture Map page.", 403)
    if not store.delete_diagram(diagram_id, username):
        return problem("Unknown diagram.", 404)
    return answer({"deleted": diagram_id, "note": "Only the drawing was deleted. Evidence history is kept."})


# ---------------------------------------------------------------- evidence

def sync_recent_workflows():
    """Records the Lambda results of recent REAL workflows, even if nobody opened their pages.

    Runs at most once per SYNC_INTERVAL_SECONDS, for at most 20 workflows from the last 24 hours whose Lambda
    stages are not final yet. Each one is refreshed exactly as its own page would (a few DynamoDB reads).
    """
    now = time.monotonic()
    if _last_sync["at"] is not None and now - _last_sync["at"] < SYNC_INTERVAL_SECONDS:
        return
    _last_sync["at"] = now
    since = clock.to_text(clock.add_seconds(clock.utc_now(), -24 * 3600))
    for workflow_id in store.workflows_to_sync(since):
        try:
            workflow = live_workflow.refresh(workflow_id)  # Records what the Lambda function reported.
            if workflow and architecture_evidence.lambda_stages_final(workflow):
                store.mark_workflow_synced(workflow_id)
        except Exception as error:
            logger.warning("Workflow %s could not be synced (%s).", workflow_id, type(error).__name__)


@router.get("/api/architecture/diagrams/{diagram_id}/evidence")
def diagram_evidence(diagram_id: int, username: str = Depends(require_login)):
    diagram = own_diagram(diagram_id, username)
    if diagram is None:
        return problem("Unknown diagram.", 404)
    sync_recent_workflows()
    return answer(architecture_evidence.evaluate_diagram(diagram["body"], username))


@router.post("/api/architecture/diagrams/{diagram_id}/manual")
async def add_manual_record(diagram_id: int, request: Request, username: str = Depends(require_login)):
    """A MANUAL verification: your attested result, time, and evidence notes. Never counted as automatic."""
    diagram = own_diagram(diagram_id, username)
    if diagram is None:
        return problem("Unknown diagram.", 404)
    data, error = await read_json(request)
    if error:
        return error
    element = find_element(diagram["body"], str(data.get("element_id", "")))
    if element is None:
        return problem("Unknown box or arrow. Save the diagram first.", 404)
    if element[0] == "node":
        node = element[1]
        subject, kind = catalog.resource_key(node["type"], node["identifier"]), "resource"
    else:
        edge, source, target = element[1], element[2], element[3]
        subject = catalog.operation_key(catalog.resource_key(source["type"], source["identifier"]), edge["operation"],
                                        catalog.resource_key(target["type"], target["identifier"]))
        kind = "operation"
    if not subject:
        return problem("Give the box (or both boxes of the arrow) an identifier first.")
    outcome = data.get("outcome")
    if outcome not in ("pass", "fail", "partial"):
        return problem("The result must be pass, fail, or partial.")
    notes = data.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > catalog.MAX_NOTES or \
            any(ord(character) < 32 and character not in "\n\t" for character in notes):
        return problem(f"Notes must be text of at most {catalog.MAX_NOTES} characters.")
    if not notes.strip():
        return problem("Describe your evidence in the notes (what you checked, where, and what you saw).")
    observed_at = clock.utc_now()
    if data.get("observed_at"):
        try:
            observed_at = clock.from_text(str(data["observed_at"]))
            if observed_at.tzinfo is None:
                raise ValueError
        except ValueError:
            return problem("The time must be an ISO date and time with a time zone, for example 2026-11-08T10:30:00Z.")
        if (observed_at - clock.utc_now()).total_seconds() > 300:
            return problem("The time of a verification cannot be in the future.")
    status = {"pass": "Verified", "fail": "Failed", "partial": "Partial"}[outcome]
    scope = diagram["body"].get("scope") or {}
    # A manual record belongs to the diagram's account and region (MANUAL: never counted as automatic).
    store.add_evidence(subject, kind, "manual", outcome, status, f"Manually attested by {username}.", source="manual",
                       observed_at=clock.to_text(observed_at), recorded_by=username,
                       reference=f"diagram:{diagram_id}", notes=notes.strip(),
                       account=scope.get("account") or None, region=scope.get("region") or None)
    return answer({"recorded": True}, 201)


@router.post("/api/architecture/diagrams/{diagram_id}/verify")
async def verify_now(diagram_id: int, request: Request, username: str = Depends(require_login)):
    """Verify Now: ONE read-only metadata check of ONE box. Never writes, sends, invokes, or reads secret values."""
    diagram = own_diagram(diagram_id, username)
    if diagram is None:
        return problem("Unknown diagram.", 404)
    data, error = await read_json(request)
    if error:
        return error
    element = find_element(diagram["body"], str(data.get("element_id", "")))
    if element is None or element[0] != "node":
        return problem("Verify Now works on boxes only. Arrows (operations) need real evidence from the app; "
                       "a read-only check cannot prove an operation.")
    node = element[1]
    if not architecture_verify.supported(node):
        return problem("Verify Now is not available for this box (no read-only check exists, or it has no identifier).")
    if architecture_verify.limit_reached(username):
        return problem("Too many Verify Now checks. Wait a few minutes.", 429)
    result = await run_in_threadpool(architecture_verify.verify_now, node, username)
    return answer(result)

