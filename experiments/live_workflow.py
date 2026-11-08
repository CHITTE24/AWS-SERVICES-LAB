"""The Live Workflow Monitor: starts a workflow, runs the FastAPI stages, and reports live status.

Real workflow ("aws" mode), done by FastAPI:
    1. created  - the workflow record is saved (idempotent: the same workflow_id is never created twice)
    2. upload   - PutObject with SSE-KMS. Completed means only "S3 ACKNOWLEDGED the request".
    3. verify   - a separate HeadObject request INDEPENDENTLY confirms the object: same size, same ETag,
                  and the workflow-id label. (S3 is strongly consistent, so no waiting is needed.)
    4. kms      - from HeadObject's metadata: S3 reports aws:kms with the configured key.
                  Nothing is downloaded or decrypted, and no checksum is requested.
    5. event / 6. lambda - written by the Lambda function (Phase 3). Until then they wait for their
                  deadline, or are Skipped when WORKFLOW_LAMBDA_NAME is empty.

Simulated workflow: the file is checked but not stored; nothing is sent to AWS (experiments/workflow_simulator.py).

Safety: pages only ever show short explanations. Raw exceptions stay in logs/app.log.
"""

import logging  # Records technical details in logs/app.log.
import re  # Checks the workflow ID format.
import uuid  # Creates random workflow IDs.

from botocore.exceptions import ClientError  # The error type AWS services raise.

from core.aws_errors import (ACCESS_DENIED, CONFIGURATION_ERROR, NOT_FOUND, TIMED_OUT, UNAVAILABLE, WRONG_REGION,
                             classify_error)
from core.config import get_settings  # Reads the bucket, KMS key, deadlines, and limits.
from experiments import architecture_evidence  # Records real results as Architecture Map evidence.
from experiments import workflow_simulator  # The offline simulation.
from experiments import workflow_states as rules  # States, stages, and time helpers.
from integrations import s3  # The existing S3 upload, plus HeadObject.
from repositories import workflow_repository  # Saves workflows and stages.

logger = logging.getLogger(__name__)

# 32 hexadecimal characters, the same format as submission IDs.
WORKFLOW_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
# Every workflow object is stored under this prefix, so ONLY these uploads trigger the workflow Lambda.
# (The lab form uploads to "submissions/", which never triggers it.)
S3_PREFIX = "workflows/"
# The FastAPI stages run during the upload request. If the app stopped half-way, they time out after this.
FASTAPI_STAGE_DEADLINE_SECONDS = 60

# Short, safe explanations for each error status. The AWS error CODE is added, never the AWS message.
SAFE_EXPLANATIONS = {
    ACCESS_DENIED: "The app's IAM role is missing a permission for this step.",
    NOT_FOUND: "AWS says the bucket, object, or table does not exist.",
    WRONG_REGION: "The resource is in a different region than AWS_REGION.",
    TIMED_OUT: "AWS did not answer in time.",
    UNAVAILABLE: "AWS could not be reached, or is busy.",
    CONFIGURATION_ERROR: "A setting is wrong, or the credentials are invalid.",
}


class WorkflowError(Exception):
    """A problem that stops a workflow from starting. Its message is always safe to show."""


def new_workflow_id():
    return uuid.uuid4().hex


def log_summary(error):
    """What goes into logs/app.log: the error type and AWS error code only, never AWS's message text."""
    if isinstance(error, ClientError):
        return f"{type(error).__name__} ({error.response.get('Error', {}).get('Code', 'Unknown')})"
    return type(error).__name__


def safe_error_detail(error):
    """A short, safe text for the page, for example "Access Denied (AccessDenied): ...". Never the raw error."""
    status = classify_error(error)
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "Unknown")  # A short identifier such as "AccessDenied".
    else:
        code = type(error).__name__
    return f"{status} ({code}). {SAFE_EXPLANATIONS.get(status, 'Unexpected error.')} Details are in logs/app.log."


def short_key(key_arn):
    # Shows only the end of a key ARN, for example "...ab12". Enough to compare, without the full ARN.
    return f"...{key_arn[-4:]}" if key_arn else "none"


def real_workflow_problem():
    """Returns why a REAL workflow cannot start (a missing setting), or None if it can."""
    settings = get_settings()
    if not settings["s3_bucket_name"] or not settings["s3_kms_key_arn"]:
        return ("Real workflows need S3_BUCKET_NAME and S3_KMS_KEY_ARN in .env. "
                "Until then, start a Simulated workflow (nothing is sent to AWS).")
    return None


def limit_reached(owner, now):
    """Cost control: at most WORKFLOW_MAX_PER_HOUR real workflows per user per hour."""
    since = rules.to_text(rules.add_seconds(now, -3600))
    return workflow_repository.count_real_workflows_since(owner, since) >= get_settings()["workflow_max_per_hour"]


# ---------------------------------------------------------------- creating workflows

def build_meta(workflow_id, owner, mode, scenario, document, s3_key, now):
    settings = get_settings()
    return {"workflow_id": workflow_id, "owner": owner, "mode": mode, "scenario": scenario,
            "file_name": document["name"], "file_size": document["size"], "s3_key": s3_key,
            "created_at": rules.to_text(now),
            "expires_at": rules.to_text(rules.add_seconds(now, settings["workflow_retention_days"] * 86400)),
            "store": workflow_repository.choose_store(mode)}


def build_stages(mode, now):
    """The starting state of all six stages."""
    settings = get_settings()
    now_text = rules.to_text(now)

    def stage(stage_id, state, detail, source, deadline_seconds=None, attempt=0):
        deadline = rules.to_text(rules.add_seconds(now, deadline_seconds)) if deadline_seconds else None
        finished = now_text if state in rules.FINAL_STATES else None
        return {"stage_id": stage_id, "position": [s[0] for s in rules.STAGES].index(stage_id) + 1,
                "name": rules.STAGE_NAMES[stage_id], "state": state, "detail": detail, "source": source,
                "attempt": attempt, "started_at": finished, "updated_at": now_text, "finished_at": finished,
                "deadline_at": deadline}

    store_label = "DynamoDB" if workflow_repository.choose_store(mode) == "dynamodb" else "local SQLite"
    stages = [stage("created", rules.COMPLETED, f"Workflow record saved ({store_label}).", rules.SOURCE_FASTAPI,
                    attempt=1)]
    if mode == "simulated":
        not_sent = "Simulated: the file was checked locally and NOT sent to AWS."
        for stage_id in ("upload", "verify", "kms"):
            stages.append(stage(stage_id, rules.SIMULATED, not_sent, rules.SOURCE_SIMULATOR))
        stages.append(stage("event", rules.PENDING, "Waiting for the simulated S3 event.", rules.SOURCE_SIMULATOR,
                            workflow_simulator.SIMULATED_EVENT_DEADLINE_SECONDS))
        stages.append(stage("lambda", rules.PENDING, "Waiting for the simulated Lambda function.",
                            rules.SOURCE_SIMULATOR, workflow_simulator.SIMULATED_PROCESSING_DEADLINE_SECONDS))
        return stages

    for stage_id in ("upload", "verify", "kms"):
        stages.append(stage(stage_id, rules.PENDING, "Waiting.", rules.SOURCE_FASTAPI, FASTAPI_STAGE_DEADLINE_SECONDS))
    if settings["workflow_lambda_name"]:
        stages.append(stage("event", rules.PENDING, "Waiting for the S3 ObjectCreated event to reach Lambda.",
                            rules.SOURCE_LAMBDA, settings["workflow_event_deadline_seconds"]))
        stages.append(stage("lambda", rules.PENDING, "Waiting for the Lambda function's result.", rules.SOURCE_LAMBDA,
                            settings["workflow_processing_deadline_seconds"]))
    else:
        skipped = "WORKFLOW_LAMBDA_NAME is not set, so no S3-triggered Lambda processing is expected."
        stages.append(stage("event", rules.SKIPPED, skipped, rules.SOURCE_FASTAPI))
        stages.append(stage("lambda", rules.SKIPPED, skipped, rules.SOURCE_FASTAPI))
    return stages


def create(workflow_id, owner, mode, scenario, document, now):
    """Saves a new workflow. Returns (workflow, created). created=False means it already existed."""
    s3_key = None
    if mode == "aws":
        s3_key = f"{S3_PREFIX}{now:%Y/%m/%d}/{workflow_id}/{document['name']}"
    meta = build_meta(workflow_id, owner, mode, scenario, document, s3_key, now)
    try:
        created = workflow_repository.create_workflow(meta, build_stages(mode, now))
    except Exception as error:
        logger.warning("Could not create workflow %s: %s", workflow_id, log_summary(error))
        raise WorkflowError("The workflow record could not be saved, so nothing was uploaded. "
                            + safe_error_detail(error)) from None
    return workflow_repository.get_workflow(workflow_id), created


def start_simulated(workflow_id, owner, document, scenario):
    """Starts an OFFLINE simulated workflow. Nothing is sent to AWS. Returns the workflow ID."""
    if scenario not in workflow_simulator.SCENARIOS:
        raise WorkflowError("Unknown simulation scenario.")
    create(workflow_id, owner, "simulated", scenario, document, rules.utc_now())
    return workflow_id


def start_real(workflow_id, owner, document):
    """Starts a REAL workflow: creates it, then runs the upload, verify, and KMS stages. Returns the ID."""
    problem = real_workflow_problem()
    if problem:
        raise WorkflowError(problem)
    workflow, created = create(workflow_id, owner, "aws", None, document, rules.utc_now())
    if created:  # A repeated request for an existing workflow never uploads again.
        # The exact resources this workflow uses, saved NOW for the Architecture Map (never re-read from .env).
        context = architecture_evidence.capture_workflow_context(workflow)
        if workflow["store"] == "dynamodb":
            architecture_evidence.record_app_operation(context, "dynamodb:TransactWriteItems", "dynamodb",
                                                       (context or {}).get("table_name"), "workflow.created",
                                                       "This app created the workflow record in this table.")
        run_fastapi_stages(workflow, document, context)
    return workflow_id


def run_fastapi_stages(workflow, document, context=None):
    """Stages 2-4. Each stage is Completed only when AWS's own answer proves it.

    context: the identity snapshot from architecture_evidence.capture_workflow_context(). Each operation's
    real result (success, AWS error, or verification mismatch) is also recorded as Architecture Map evidence.
    """
    settings = get_settings()
    bucket, key, kms_key_arn = settings["s3_bucket_name"], workflow["s3_key"], settings["s3_kms_key_arn"]

    def update(stage_id, state, detail):
        workflow_repository.set_stage(workflow, stage_id, state, detail, rules.SOURCE_FASTAPI, attempt=1)

    def evidence(operation_name, target_type, target_identifier, check_id, detail, error=None, mismatch=False):
        architecture_evidence.record_app_operation(context, operation_name, target_type, target_identifier, check_id,
                                                   detail, error=error, mismatch=mismatch)

    def skip_later(stage_ids, reason):
        for stage_id in stage_ids:
            update(stage_id, rules.SKIPPED, reason)

    # ---- 2. Upload: S3's acknowledgement only.
    update("upload", rules.RUNNING, "Uploading to S3 with SSE-KMS.")
    try:
        put = s3.upload_document(bucket, key, document["content"], document["content_type"], kms_key_arn,
                                 metadata={"workflow-id": workflow["workflow_id"]})
    except Exception as error:
        logger.warning("Workflow %s: upload failed: %s", workflow["workflow_id"], log_summary(error))
        update("upload", rules.FAILED, safe_error_detail(error))
        evidence("s3:PutObject", "s3", bucket, "workflow.upload", safe_error_detail(error), error=error)
        skip_later(("verify", "kms", "event", "lambda"), "Skipped: the upload failed, so there is no object.")
        return
    etag_end = put["etag"].strip('"')[-6:]  # The last characters of the ETag, enough to recognise it.
    evidence("s3:PutObject", "s3", bucket, "workflow.upload", "S3 accepted the upload (PutObject).")
    update("upload", rules.COMPLETED, f"S3 acknowledged the PutObject request (ETag ...{etag_end}). "
                                      "This is only an acknowledgement; the next stage checks the object independently.")

    # ---- 3. Independent verification with a separate HeadObject request.
    update("verify", rules.RUNNING, "Checking the object with HeadObject.")
    try:
        head = s3.head_object(bucket, key)
    except Exception as error:
        logger.warning("Workflow %s: HeadObject failed: %s", workflow["workflow_id"], log_summary(error))
        update("verify", rules.FAILED, safe_error_detail(error))
        evidence("s3:HeadObject", "s3", bucket, "workflow.verify", safe_error_detail(error), error=error)
        update("kms", rules.SKIPPED, "Skipped: the object could not be read, so its encryption is unknown.")
        return
    problems = []
    if head["size"] != document["size"]:
        problems.append(f"S3 reports {head['size']} bytes, but {document['size']} were uploaded")
    if head["etag"] != put["etag"]:
        problems.append("the ETag differs from the upload's ETag (a different object)")
    if head["metadata"].get("workflow-id") != workflow["workflow_id"]:
        problems.append("the workflow-id label does not match")
    if problems:
        update("verify", rules.FAILED, "HeadObject found the object, but " + "; ".join(problems) + ".")
        # The HeadObject CALL worked; the object did not match. That is a mismatch, not an AWS failure.
        evidence("s3:HeadObject", "s3", bucket, "workflow.verify",
                 "HeadObject worked, but the object did not match the upload.", mismatch=True)
    else:
        evidence("s3:HeadObject", "s3", bucket, "workflow.verify", "HeadObject confirmed the uploaded object.")
        update("verify", rules.COMPLETED, f"HeadObject found the object: {head['size']:,} bytes, the same ETag, "
                                          "and the matching workflow-id label.")

    # ---- 4. SSE-KMS, from HeadObject's metadata (nothing is decrypted).
    key_id = (context or {}).get("kms_key_id")
    if head["encryption"] == "aws:kms" and head["kms_key_id"] == kms_key_arn:
        evidence("kms:GenerateDataKey", "kms", key_id, "workflow.kms", "S3 reported SSE-KMS with this key.")
        update("kms", rules.COMPLETED, f"S3 reports aws:kms encryption with the configured key ({short_key(kms_key_arn)}).")
    else:
        evidence("kms:GenerateDataKey", "kms", key_id, "workflow.kms",
                 "S3 did not report SSE-KMS with this key (verification mismatch).", mismatch=True)
        update("kms", rules.FAILED, f"S3 reports encryption '{head['encryption'] or 'none'}' with key "
                                    f"{short_key(head['kms_key_id'])}, not the configured key ({short_key(kms_key_arn)}).")


# ---------------------------------------------------------------- live status

def timeout_detail(workflow, stage):
    if workflow["mode"] == "simulated":
        return "Simulated: no result arrived before the deadline."
    if stage["stage_id"] in ("upload", "verify", "kms"):
        return "The app stopped before finishing this step (for example a restart during the upload)."
    return ("No result before the deadline (WORKFLOW_EVENT_DEADLINE_SECONDS / WORKFLOW_PROCESSING_DEADLINE_SECONDS). "
            "This is NOT proof of failure: if the result arrives later, this stage still updates. "
            "Check the S3 event notification and the Lambda logs.")


def refresh(workflow_id, now=None):
    """Brings a workflow up to date (simulation steps, deadlines) and returns it, or None if unknown."""
    now = now or rules.utc_now()
    workflow = workflow_repository.get_workflow(workflow_id)
    if workflow is None:
        return None
    changed = False
    if workflow["mode"] == "simulated":
        changed = workflow_simulator.advance(workflow, now)
        workflow = workflow_repository.get_workflow(workflow_id) if changed else workflow
    source = rules.SOURCE_SIMULATOR if workflow["mode"] == "simulated" else rules.SOURCE_FASTAPI
    for stage in rules.overdue_stages(workflow["stages"], now):
        # Conditional: if a real result arrived a moment ago, this write is refused and the result wins.
        if workflow_repository.set_stage(workflow, stage["stage_id"], rules.TIMED_OUT,
                                         timeout_detail(workflow, stage), source, now=now):
            changed = True
    workflow = workflow_repository.get_workflow(workflow_id) if changed else workflow
    if workflow["mode"] == "aws":
        architecture_evidence.record_workflow(workflow)  # What the Lambda function reported (never raises).
    return workflow


def describe(workflow, now=None):
    """The JSON-friendly view used by the page and the status endpoint. Contains no secrets or raw errors."""
    now = now or rules.utc_now()
    overall = rules.overall_state(workflow["stages"])
    poll = rules.should_poll(overall)
    stages = []
    for stage in workflow["stages"]:
        stages.append({
            "id": stage["stage_id"], "name": stage["name"], "state": stage["state"], "detail": stage["detail"],
            "source": stage["source"], "attempt": stage.get("attempt", 0),
            # True when the state did NOT come from a real AWS event or call.
            "simulated": stage["source"] == rules.SOURCE_SIMULATOR or stage["state"] == rules.SIMULATED,
            "started_at": stage.get("started_at"), "finished_at": stage.get("finished_at"),
            "duration_seconds": rules.duration_seconds(stage),
        })
    return {
        "workflow_id": workflow["workflow_id"], "mode": workflow["mode"], "simulated": workflow["mode"] == "simulated",
        "scenario": workflow.get("scenario"), "file_name": workflow["file_name"], "file_size": workflow["file_size"],
        "created_at": workflow["created_at"], "overall": overall, "stages": stages,
        "poll": poll, "next_poll_ms": rules.next_poll_milliseconds(workflow["created_at"], now) if poll else None,
        # The page stops asking after this many seconds per page view (cost control). Reloading resumes.
        "poll_limit_seconds": get_settings()["workflow_poll_limit_seconds"],
        "checked_at": rules.to_text(now),
    }
