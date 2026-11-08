"""EVIDENCE for the Architecture Map: what was actually observed, and what it lets us conclude.

Two strictly separate things:
  1. Evidence records (repositories/architecture_repository.py): facts with a time, a source, the AWS account
     and region, and the EXACT identity they are about, for example
        resource  "s3:lab-docs"                                -> HeadBucket answered (the bucket is reachable)
        operation "ec2:i-0abc|s3:PutObject|s3:lab-docs"         -> S3 accepted an upload from this instance
  2. Conclusions (conclude / evaluate_diagram below): the colour of a box or arrow.
     - A BOX uses only evidence about that exact resource; an ARROW only evidence about that exact operation.
       Resource checks are shown next to an arrow as "context", never as proof.
     - A drawn arrow without evidence is grey. Simulated evidence is never green. Old evidence is "stale".
     - Manual records keep their attested colour but are always marked MANUAL.

Identities are captured WHEN the operation happens (never re-derived later from the current .env):
  - Check AWS environment and the lab form record evidence in the same request that did the work.
  - A real Live Workflow saves its exact resources (workflow_context) at upload time; the Lambda function
    writes its own ARN (account, region, name) and the event's bucket onto the stages it writes.
If an identity cannot be established reliably (unknown instance, unknown account, missing Lambda ARN), the
record is stored as UNATTRIBUTED: kept in the history, counted on the page, but never placed on any box.

Operation results are classified precisely:
  success                -> pass     "Success"
  verification mismatch  -> partial  "Verification Mismatch" (the API call worked, the object did not match)
  configuration problem  -> partial  "Configuration Error"
  timeout                -> partial  "Timed Out"
  AWS refused / failed   -> fail     (for example "Access Denied", "Not Found")
A configuration error that stopped the call from being made records nothing about that call.

The recorders never call AWS themselves (apart from one cached, read-only STS call to learn the account),
and a problem here never breaks the feature that produced the evidence.
"""

import logging
import re
from datetime import timedelta

from catalog import architecture_catalog as catalog
from core import instance_identity
from core.aws_errors import CONFIGURATION_ERROR, TIMED_OUT, classify_error
from core.config import get_settings
from experiments import workflow_states as clock
from repositories import architecture_repository as store

logger = logging.getLogger(__name__)

# Status words used across the app -> evidence outcome. Words not listed (Not Configured, Skipped,
# Simulated, Pending, ...) mean "nothing was checked" and produce no evidence at all.
PASS_WORDS = {"Connected", "Completed", "Success", "Verified", "Sent"}
FAIL_WORDS = {"Failed", "Access Denied", "Not Found", "Wrong Region", "Unavailable", "Retries Exhausted"}
PARTIAL_WORDS = {"Timed Out", "No Data", "Retrying", "Configuration Error", "Verification Mismatch"}

COLOURS = {"pass": "green", "fail": "red", "partial": "yellow"}
LABELS = {"green": "Verified", "red": "Failed", "yellow": "Partial", "gray": "Not verified"}

SUCCESS = ("pass", "Success")
MISMATCH = ("partial", "Verification Mismatch")
# Kinds written by the Lambda function on its stage (outcome_kind) -> (outcome, status).
LAMBDA_KINDS = {"success": SUCCESS, "mismatch": MISMATCH, "retrying": ("partial", "Retrying"),
                "timeout": ("partial", "Timed Out"), "aws_error": ("fail", "Failed")}

# Names that are not tied to one region (S3 bucket names, IAM roles), so region scoping does not apply.
GLOBAL_TYPES = {"s3", "iam", "external"}
UNKNOWN = object()  # "This identity could not be established": the record becomes unattributed.


def outcome_of(status):
    if status in PASS_WORDS:
        return "pass"
    if status in FAIL_WORDS:
        return "fail"
    if status in PARTIAL_WORDS:
        return "partial"
    return None


def outcome_for_error(error):
    """(outcome, status) for an exception from an AWS call: timeouts and configuration problems are partial."""
    status = classify_error(error)
    if status in (TIMED_OUT, CONFIGURATION_ERROR):
        return "partial", status
    return "fail", status


def resource(resource_type, identifier):
    if identifier is UNKNOWN:
        return UNKNOWN
    return catalog.resource_key(resource_type, identifier)


def operation(source_key, name, target_key):
    if source_key is UNKNOWN or target_key is UNKNOWN:
        return UNKNOWN
    return catalog.operation_key(source_key, name, target_key)


def app_key():
    return instance_identity.app_resource_key() or UNKNOWN


def lambda_identity(function_arn):
    """"arn:aws:lambda:REGION:ACCOUNT:function:NAME[:alias]" -> (name, account, region), or None."""
    parts = (function_arn or "").split(":")
    if len(parts) >= 7 and parts[0] == "arn" and parts[2] == "lambda" and parts[5] == "function":
        return parts[6], parts[4], parts[3]
    return None


def secret_name(value):
    # "arn:aws:secretsmanager:...:secret:lab/db-AbC123" -> "lab/db" (AWS adds 6 random characters).
    if value.startswith("arn:"):
        value = value.split(":secret:", 1)[-1]
        value = re.sub(r"-[A-Za-z0-9]{6}$", "", value)
    return value


def record(subject, kind, check_id, status, detail, reference, scope, outcome=None, recorded_by="system"):
    """Adds one automatic evidence record.

    subject None: the resource is not configured, so nothing was checked and nothing is recorded.
    subject UNKNOWN, or the account unknown: the record is stored as UNATTRIBUTED (never on a box).
    """
    outcome = outcome or outcome_of(status)
    if subject is None or outcome is None:
        return
    account, region = scope.get("account"), scope.get("region")
    if subject is UNKNOWN or not account:
        store.add_evidence(f"unattributed|{check_id}|{reference}", "unattributed", check_id, outcome, status,
                           detail, reference=reference, account=account, region=region, recorded_by=recorded_by)
        return
    store.add_evidence(subject, kind, check_id, outcome, status, detail, reference=reference, account=account,
                       region=region, recorded_by=recorded_by)


def safely(function):
    """Recording evidence must never break the feature that produced it: errors are only logged."""
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as error:
            logger.warning("Architecture evidence was not recorded (%s).", type(error).__name__)
            return None
    return wrapper


# ---------------------------------------------------------------- Check AWS environment

@safely
def record_environment_check(rows, run_id):
    """Evidence from one click on "Check AWS environment", recorded in the same request (same .env)."""
    settings = get_settings()
    scope = instance_identity.scope()
    app = app_key()
    reference = f"environment-check:{run_id}"
    names = {
        "s3": settings["s3_bucket_name"], "kms": catalog.kms_key_id(settings["s3_kms_key_arn"]),
        "lambda": catalog.name_from_arn_or_url(settings["lambda_function_name"]),
        "sns": catalog.name_from_arn_or_url(settings["sns_topic_arn"]),
        "sqs": catalog.name_from_arn_or_url(settings["sqs_queue_url"]),
        "secretsmanager": secret_name(settings["secret_name"]), "rds": settings["db_host"], "ses": settings["aws_region"],
    }

    def note(subject, kind, check_id, row, outcome=None, detail=None):
        status = row["status"]
        # Failures keep only the status word: the full text may contain AWS's own error message.
        text = detail or (row["detail"] if outcome_of(status) != "fail" else f"{status}. See Check AWS environment.")
        record(subject, kind, check_id, status, text, reference, scope, outcome=outcome)

    for row in rows:
        service, action, status = row["service"], row["action"], row["status"]
        if service == "AWS IAM / STS" and status == "Connected":
            match = re.search(r"assumed-role/([^/\s]+)/([^\s.,]+)", row["detail"])
            if match:
                role, session = match.group(1), match.group(2)
                note(resource("iam", role), "resource", "environment.sts", row,
                     detail="This app is using temporary credentials of this role.")
                # An EC2 instance role's session name IS the instance ID. Anything else is not this instance.
                if app is not UNKNOWN and app == f"ec2:{session}":
                    note(operation(resource("iam", role), "sts:AssumeRole", app), "operation", "environment.sts", row,
                         detail="The app on this instance uses this role's credentials (session = instance ID).")
        elif service == "Amazon EC2":
            if app is UNKNOWN or app.startswith("ec2:"):
                # "Region match" is a settings mismatch, not a broken instance: partial, never red.
                outcome = "partial" if action.startswith("Region match") and outcome_of(status) == "fail" else None
                note(app, "resource", f"environment.ec2.{action.split()[0].lower()}", row, outcome=outcome)
        elif service == "AWS Secrets Manager":
            key = resource("secretsmanager", names["secretsmanager"])
            if action.startswith("Retrieve value"):
                note(operation(app, "secretsmanager:GetSecretValue", key), "operation", "environment.secret_value", row)
            else:
                note(key, "resource", "environment.secret_metadata", row)
        elif service == "Amazon RDS (PostgreSQL)":
            note(operation(app, "rds:SqlQuery", resource("rds", names["rds"])), "operation", "environment.rds_query", row)
        elif service == "Amazon CloudWatch":
            if action.startswith("Metrics API"):
                note(operation(app, "cloudwatch:GetMetricStatistics", resource("cloudwatch", "metrics")), "operation",
                     "environment.metrics_api", row)
            elif action.startswith("Logs delivered"):
                group = f"/aws/lambda/{names['lambda']}"
                # A missing log group means "nothing delivered yet", not a broken connection.
                outcome = "partial" if status == "Not Found" else None
                note(operation(resource("lambda", names["lambda"]), "logs:PutLogEvents", resource("cloudwatch", group)),
                     "operation", "environment.logs_delivered", row, outcome=outcome)
        else:
            simple = {"Amazon S3": ("s3", "environment.head_bucket"), "AWS KMS": ("kms", "environment.describe_key"),
                      "AWS Lambda": ("lambda", "environment.lambda_configuration"),
                      "Amazon SES": ("ses", "environment.ses_account"), "Amazon SNS": ("sns", "environment.sns_topic"),
                      "Amazon SQS": ("sqs", "environment.sqs_queue")}.get(service)
            if simple:
                note(resource(simple[0], names[simple[0]]), "resource", simple[1], row)


# ---------------------------------------------------------------- Live Workflow Monitor

@safely
def capture_workflow_context(workflow):
    """Saves the exact resources a REAL workflow uses, at upload time. Returns the snapshot (or None)."""
    settings = get_settings()
    context = {
        "workflow_id": workflow["workflow_id"], "app_key": instance_identity.app_resource_key(),
        "bucket": settings["s3_bucket_name"], "kms_key_id": catalog.kms_key_id(settings["s3_kms_key_arn"]),
        "table_name": settings["workflow_table_name"] if workflow.get("store") == "dynamodb" else None,
        "lambda_name": catalog.name_from_arn_or_url(settings["workflow_lambda_name"]) or None,
        "account": instance_identity.aws_account(), "region": settings["aws_region"],
        "created_at": workflow["created_at"],
    }
    store.save_workflow_context(context)
    return context


@safely
def record_app_operation(context, operation_name, target_type, target_identifier, check_id, detail,
                         error=None, mismatch=False):
    """Evidence for ONE operation the app just performed in a workflow, classified from the real result."""
    if not context:
        return
    outcome, status = outcome_for_error(error) if error else (MISMATCH if mismatch else SUCCESS)
    source = context["app_key"] or UNKNOWN
    subject = operation(source, operation_name, resource(target_type, target_identifier))
    record(subject, "operation", check_id, status, detail, f"workflow:{context['workflow_id']}",
           {"account": context["account"], "region": context["region"]}, outcome=outcome)


def lambda_stages_final(workflow):
    """True when the event and Lambda stages can no longer change (so no more evidence will come)."""
    states = {stage["stage_id"]: stage["state"] for stage in workflow.get("stages", [])}
    return all(states.get(stage_id) in clock.FINAL_STATES for stage_id in ("event", "lambda"))


@safely
def record_workflow(workflow):
    """Evidence from what the Lambda function reported on a REAL workflow (called by status checks and syncs).

    Identities come from the stage itself (the Lambda's own ARN and the event's bucket) and from the
    snapshot saved at upload time, never from the current .env. Without them, the record is unattributed.
    """
    if not workflow or workflow.get("mode") != "aws":
        return
    context = store.get_workflow_context(workflow["workflow_id"]) or {}
    reference = f"workflow:{workflow['workflow_id']}"
    stages = {stage["stage_id"]: stage for stage in workflow["stages"]}
    snapshot_scope = {"account": context.get("account"), "region": context.get("region")}
    expected_function = resource("lambda", context["lambda_name"]) if context.get("lambda_name") else UNKNOWN
    snapshot_bucket = resource("s3", context["bucket"]) if context.get("bucket") else UNKNOWN

    def reported_by_lambda(stage):
        identity = lambda_identity(stage.get("processed_by_arn"))
        if not identity or not stage.get("event_bucket"):
            return UNKNOWN, UNKNOWN, snapshot_scope
        name, account, region = identity
        return resource("lambda", name), resource("s3", stage["event_bucket"]), {"account": account, "region": region}

    event = stages.get("event", {})
    if event.get("source") == "lambda" and event.get("state") == "Completed":
        function, bucket, scope = reported_by_lambda(event)
        record(operation(bucket, "s3:ObjectCreated", function), "operation", "workflow.event", "Success",
               "This function received the S3 ObjectCreated event for the workflow's object.", reference, scope)
    elif event.get("state") == "Timed Out":
        record(operation(snapshot_bucket, "s3:ObjectCreated", expected_function), "operation", "workflow.event",
               "Timed Out", "No S3 event reached the expected function before the deadline (not proof of failure).",
               reference, snapshot_scope)

    processing = stages.get("lambda", {})
    if processing.get("source") == "lambda":
        function, bucket, scope = reported_by_lambda(processing)
        table = resource("dynamodb", context["table_name"]) if context.get("table_name") else UNKNOWN
        # The Lambda function wrote this stage into the workflow's table: that write worked.
        record(operation(function, "dynamodb:UpdateItem", table), "operation", "workflow.lambda_write", "Success",
               "This Lambda function wrote a workflow stage to this table.", reference, scope)
        result = LAMBDA_KINDS.get(processing.get("outcome_kind"))
        if result:  # "configuration" (the call was never made) and "Running" record nothing about HeadObject.
            record(operation(function, "s3:HeadObject", bucket), "operation", "workflow.lambda_verify", result[1],
                   processing.get("detail", ""), reference, scope, outcome=result[0])
    elif processing.get("state") == "Timed Out":
        record(operation(expected_function, "s3:HeadObject", snapshot_bucket), "operation", "workflow.lambda_verify",
               "Timed Out", "No result from the expected function before the deadline (not proof of failure).",
               reference, snapshot_scope)


# ---------------------------------------------------------------- lab form

def lab_failure(detail):
    """The lab form only says "Failed": tell timeouts and configuration problems apart from AWS refusals."""
    text = (detail or "").lower()
    if "did not answer in time" in text:
        return "partial", "Timed Out"
    if "wrong format" in text or "is not set" in text or "invalid or expired" in text:
        return "partial", "Configuration Error"
    return "fail", "Failed"


@safely
def record_submission(result):
    """Evidence from one lab-form submission, recorded in the same request (same .env). Statuses only."""
    settings = get_settings()
    scope = instance_identity.scope()
    app = app_key()
    reference = f"submission:{result['id']}"
    submission_function = resource("lambda", catalog.name_from_arn_or_url(settings["lambda_function_name"]))
    targets = {
        ("Amazon S3", "Upload document"): (app, "s3:PutObject", resource("s3", settings["s3_bucket_name"]),
                                           "Lab form: S3 confirmed the document upload."),
        ("AWS KMS", "Verify SSE-KMS encryption"): (
            app, "kms:GenerateDataKey", resource("kms", catalog.kms_key_id(settings["s3_kms_key_arn"])),
            "Lab form: S3 reported SSE-KMS with this key."),
        ("Amazon RDS (PostgreSQL)", "Save submission"): (app, "rds:SqlQuery", resource("rds", settings["db_host"]),
                                                         "Lab form: the submission was saved in this database."),
        ("AWS Lambda", "Process submission"): (app, "lambda:InvokeFunction", submission_function,
                                               "Lab form: this function was invoked and returned a result."),
        ("Amazon SES", "Send confirmation email"): (submission_function, "ses:SendEmail",
                                                    resource("ses", settings["aws_region"]),
                                                    "Lab form: SES accepted the email (inbox delivery is not proven)."),
        ("Amazon SNS", "Publish notification"): (app, "sns:Publish",
                                                 resource("sns", catalog.name_from_arn_or_url(settings["sns_topic_arn"])),
                                                 "Lab form: SNS accepted the message."),
        ("Amazon SQS", "Queue message"): (app, "sqs:SendMessage",
                                          resource("sqs", catalog.name_from_arn_or_url(settings["sqs_queue_url"])),
                                          "Lab form: SQS stored the message."),
    }
    for row in result.get("activity", []):
        target = targets.get((row["service"], row["action"]))
        if not target:
            continue
        source, name, destination, detail = target
        outcome, status = None, row["status"]
        if row["status"] == "Failed":
            outcome, status = lab_failure(row.get("detail"))
            detail = f"Lab form: {status}."
        elif row["status"] == "Verified" and name == "kms:GenerateDataKey":
            status = "Success"
        record(operation(source, name, destination), "operation", "submission", status, detail, reference, scope,
               outcome=outcome)


# ---------------------------------------------------------------- conclusions

def public_record(item):
    """The fields the page may show. The recorder's name is shown only for manual records."""
    return {"check": item["check_id"], "outcome": item["outcome"], "status": item["status"], "detail": item["detail"],
            "source": item["source"], "observed_at": item["observed_at"], "reference": item["reference"],
            "notes": item["notes"], "account": item.get("account"), "region": item.get("region"),
            "recorded_by": item["recorded_by"] if item["source"] == "manual" else None}


def conclude(records, now, max_age_hours):
    """Turns the evidence history of ONE exact subject into a colour, a label, and the reason."""
    automatic = [item for item in records if item["source"] == "automatic"]
    manual = [item for item in records if item["source"] == "manual"]
    simulated = [item for item in records if item["source"] == "simulated"]
    # Real (automatic) evidence first, then a person's attestation, then a clearly labelled simulation.
    chosen = (automatic or manual or simulated or [None])[0]
    summary = {"history": [public_record(item) for item in records], "note": ""}
    if chosen is None:
        return dict(summary, colour="gray", label=LABELS["gray"], basis="none", stale=False, latest=None)
    if chosen["source"] == "simulated":
        return dict(summary, colour="gray", label="SIMULATED", basis="simulated", stale=False,
                    latest=public_record(chosen))
    age_hours = (now - clock.from_text(chosen["observed_at"])).total_seconds() / 3600
    stale = age_hours > max_age_hours
    colour = "yellow" if stale else COLOURS[chosen["outcome"]]
    label = f"Stale (last: {LABELS[COLOURS[chosen['outcome']]]})" if stale else LABELS[colour]
    if automatic and manual and manual[0]["id"] > automatic[0]["id"] and manual[0]["outcome"] != automatic[0]["outcome"]:
        summary["note"] = "A newer MANUAL record disagrees; the automatic evidence decides the colour."
    return dict(summary, colour=colour, label=label, basis=chosen["source"], stale=stale, latest=public_record(chosen))


def regional(key):
    """True if the subject depends on a region (anything except S3 buckets, IAM roles, and external boxes)."""
    types = {part.split(":", 1)[0] for part in (key.split("|")[0], key.split("|")[-1])}
    return not types <= GLOBAL_TYPES


def evaluate_diagram(body, viewer, now=None):
    """Colours every box and arrow of a stored diagram, from evidence only. Makes no AWS calls."""
    now = now or clock.utc_now()
    max_age = get_settings()["evidence_max_age_hours"]
    scope = body.get("scope") or {}
    account, region = scope.get("account") or None, scope.get("region") or None
    nodes = {node["id"]: node for node in body["nodes"]}
    node_keys = {node_id: catalog.resource_key(node["type"], node["identifier"]) for node_id, node in nodes.items()}
    edge_keys = {edge["id"]: catalog.operation_key(node_keys[edge["source"]], edge["operation"], node_keys[edge["target"]])
                 for edge in body["edges"]}
    history = store.evidence_for(list(node_keys.values()) + list(edge_keys.values()), viewer, account=account)
    if region:  # Records from another region never count for a regional resource.
        history = {key: [item for item in items if not (item.get("region") and item["region"] != region and regional(key))]
                   for key, items in history.items()}

    node_results = {}
    for node_id, key in node_keys.items():
        result = conclude(history.get(key, []), now, max_age) if key else \
            dict(conclude([], now, max_age), note="Give this box an identifier to match evidence.")
        node_results[node_id] = dict(result, key=key)

    edge_results = {}
    for edge in body["edges"]:
        key = edge_keys[edge["id"]]
        spec = catalog.OPERATIONS[edge["operation"]]
        result = conclude(history.get(key, []), now, max_age)
        if not key:
            result["note"] = "Both boxes need an identifier before evidence can match this arrow."
        elif not spec["automatic"] and result["basis"] == "none":
            result["note"] = "Design only: the app has no automatic check for this operation. A manual record is possible."
        # The two boxes' own checks, shown as CONTEXT ONLY: they do not prove this operation.
        context = [{"box": edge[end], "label": node_results[edge[end]]["label"],
                    "colour": node_results[edge[end]]["colour"], "basis": node_results[edge[end]]["basis"]}
                   for end in ("source", "target")]
        edge_results[edge["id"]] = dict(result, key=key, context=context, operation=edge["operation"],
                                        proves=spec["proves"], not_proves=spec["not_proves"], iam=spec["iam"],
                                        how=spec["how"], automatic=spec["automatic"])

    since = clock.to_text(now - timedelta(hours=max_age))
    unattributed_total, unattributed_recent = store.recent_unattributed(since)
    return {"nodes": node_results, "edges": edge_results, "max_age_hours": max_age, "generated_at": clock.to_text(now),
            "scope": {"account": account or "", "region": region or ""},
            "scope_warning": "" if account else ("No AWS account is set for this diagram, so evidence from any account "
                                                 "can match. Set the account and region in the diagram settings."),
            "unattributed": {"total": unattributed_total, "recent": unattributed_recent}}
