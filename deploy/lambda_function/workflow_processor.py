"""AWS Lambda function "aws-lab-workflow-processor": processes S3 ObjectCreated events for the Live Workflow Monitor.

You deploy this file yourself (Phase 5 documents how). It is NOT run by the web app.
    Runtime: Python 3.12 or newer      Handler: workflow_processor.lambda_handler
    Memory: 128 MB    Timeout: 15 s    NOT in a VPC (it only calls the S3 and DynamoDB APIs)
    Trigger: S3 event notification "s3:ObjectCreated:Put", prefix "workflows/" only
Environment variables (names and ARNs only, never secrets):
    WORKFLOW_TABLE_NAME   the DynamoDB table (default aws-lab-workflows)
    EXPECTED_KMS_KEY_ARN  the key the object must be encrypted with (the same value as the app's S3_KMS_KEY_ARN)
    WORKFLOW_BUCKET_NAME  optional: events from any other bucket are ignored
    MAX_ATTEMPTS          optional, default 3: how many processing attempts one workflow may use
    LEASE_SECONDS         optional, default 30: how long an attempt "owns" the work (see "Crashes" below)
Credentials: the function's IAM execution role, found automatically by boto3. No keys anywhere.

---- How attempts are counted (durable, in DynamoDB) ----
AWS Lambda retries a failed ASYNCHRONOUS invocation as a NEW invocation, and S3 may deliver the same event
more than once. So nothing here relies on the Lambda request ID; it is only written to the logs and the
stage for diagnostics. Instead, the workflow's own "lambda" stage item in DynamoDB holds:
    attempt      how many attempts have been CLAIMED so far (1, 2, 3, ...)
    lease_until  until when (seconds since 1970) the current attempt owns the work
An invocation starts an attempt with ONE conditional UpdateItem (a "claim"). DynamoDB applies it only if
the stage is Pending or Retrying, or Running / Timed Out WITHOUT a live lease, and attempts remain. The same
update adds 1 to the counter. So two invocations at the same time can never both claim, and the counter
can never skip, repeat, or reset.

Duplicates: a finished stage means "already done": nothing happens. A live lease means "someone else is
working on it": this invocation stops quietly.
Crashes: if an attempt dies (for example the 15-second Lambda timeout), its lease expires and the next
invocation takes over. The lease is always longer than the function's remaining run time, so a slow but
living attempt is never taken over.
Identity: every stage this function writes also carries "processed_by_arn" (this function's own ARN, which
contains its account and region) and "event_bucket", plus "outcome_kind" (success, mismatch, configuration,
retrying, timeout, or aws_error). The web app uses these for exact evidence instead of guessing from settings.

Results: Failed and Retrying are accepted only from the attempt that holds the current claim, so an older
attempt can never overwrite a newer one. Completed (proven success) is accepted from any attempt that
still finds the stage unfinished, and it is final: nothing can overwrite it.

---- How MAX_ATTEMPTS relates to Lambda's own settings ----
Lambda retries a function ERROR in an asynchronous invocation up to "Maximum retry attempts" times (0, 1,
or 2; default 2), so a normal event gets 1 + that many invocations. Set MAX_ATTEMPTS to the same total
(default 3). The counter in DynamoDB is what decides, not that assumption:
  - More invocations than expected (Lambda also retries throttled invocations, and S3 may deliver duplicates):
    after MAX_ATTEMPTS the stage is Failed ("all attempts used"), and later invocations change nothing.
  - Fewer invocations (for example the event is older than "Maximum age of event" and Lambda discards it):
    the stage stays Retrying until the web app's deadline marks it Timed Out, which is NOT shown as failure.
  - A temporary error on the last allowed attempt is written as Failed and the function returns normally,
    so Lambda does not retry pointlessly. Permanent errors are Failed at once, without using more attempts.
"""

import logging  # print() and logging output go to CloudWatch Logs (/aws/lambda/<function name>).
import os  # Reads the environment variables.
import re  # Checks the object key format.
import time  # The lease uses seconds since 1970.
from datetime import datetime, timezone  # Timestamps (UTC).
from urllib.parse import unquote_plus  # S3 event keys are URL-encoded ("my+file.txt" means "my file.txt").

import boto3  # Already installed in the Lambda Python runtime.
from botocore.config import Config  # Short timeouts.
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, EndpointConnectionError, \
    ReadTimeoutError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---- These MUST stay identical to experiments/workflow_states.py (tests/test_workflow_processor.py checks it).
PENDING, RUNNING, RETRYING = "Pending", "Running", "Retrying"
COMPLETED, FAILED, TIMED_OUT = "Completed", "Failed", "Timed Out"
SIMULATED, SKIPPED = "Simulated", "Skipped"
FINAL_STATES = (COMPLETED, FAILED, SIMULATED, SKIPPED)
ALLOWED_PREVIOUS = {
    RUNNING: (PENDING, RETRYING, TIMED_OUT),
    RETRYING: (PENDING, RUNNING, RETRYING, TIMED_OUT),
    COMPLETED: (PENDING, RUNNING, RETRYING, TIMED_OUT),
    FAILED: (PENDING, RUNNING, RETRYING, TIMED_OUT),
    TIMED_OUT: (PENDING, RUNNING, RETRYING),
    SIMULATED: (PENDING, RUNNING, RETRYING),
    SKIPPED: (PENDING,),
}
# The DynamoDB sort keys of the two stages this function writes (same as repositories/workflow_dynamodb.py).
EVENT_STAGE = "STAGE#05#event"
LAMBDA_STAGE = "STAGE#06#lambda"
SOURCE = "lambda"

# A workflow object key: workflows/YYYY/MM/DD/<32 hexadecimal characters>/<file name without "/">
KEY_PATTERN = re.compile(r"workflows/\d{4}/\d{2}/\d{2}/(?P<workflow_id>[0-9a-f]{32})/(?P<file_name>[^/]{1,255})")
MAX_KEY_LENGTH = 1024  # The longest key S3 allows.

# Short timeouts and one quick retry inside each call. Bigger problems use Lambda's own retries instead.
CLIENT_CONFIG = Config(retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=3, read_timeout=5)

# AWS error codes that are TEMPORARY: another attempt may succeed.
TEMPORARY_CODES = {
    "SlowDown", "Throttling", "ThrottlingException", "RequestLimitExceeded", "TooManyRequestsException",
    "ProvisionedThroughputExceededException", "RequestTimeout", "RequestTimeoutException",
    "InternalError", "InternalServerError", "ServiceUnavailable", "TransactionConflictException",
}

# Short, safe explanations for permanent S3 errors. Only the error CODE is ever logged, never AWS's message.
PERMANENT_HELP = {
    "403": "Access Denied: the Lambda role cannot read this object's metadata (s3:GetObject on workflows/*).",
    "AccessDenied": "Access Denied: the Lambda role cannot read this object's metadata (s3:GetObject on workflows/*).",
    "404": "Not Found: the object no longer exists.",
    "NoSuchKey": "Not Found: the object no longer exists.",
}


class TemporaryProcessingError(Exception):
    """Raised at the end of an invocation so that Lambda retries the event automatically."""


# ---------------------------------------------------------------- small helpers

def settings():
    # Read on every call, so tests (and a changed configuration) take effect immediately.
    return {
        "table_name": os.environ.get("WORKFLOW_TABLE_NAME", "aws-lab-workflows"),
        "expected_kms_key_arn": os.environ.get("EXPECTED_KMS_KEY_ARN", ""),
        "bucket_name": os.environ.get("WORKFLOW_BUCKET_NAME", ""),
        "max_attempts": max(1, int(os.environ.get("MAX_ATTEMPTS", "3") or 3)),
        "lease_seconds": max(1, int(os.environ.get("LEASE_SECONDS", "30") or 30)),
    }


def s3_client():
    return boto3.client("s3", config=CLIENT_CONFIG)


def workflow_table():
    return boto3.resource("dynamodb", config=CLIENT_CONFIG).Table(settings()["table_name"])


def epoch_now():
    # Seconds since 1970. Tests replace this function to move the clock.
    return int(time.time())


def now_text():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def short(text):
    # The last characters of an ID or ARN: enough to recognise it, without showing all of it.
    return f"...{text[-6:]}" if text else "none"


def error_code(error):
    # A short identifier such as "AccessDenied". AWS's message text is never used.
    if isinstance(error, ClientError):
        return error.response.get("Error", {}).get("Code", "Unknown")
    return type(error).__name__


def is_temporary(error):
    """True for errors where another attempt may succeed (timeouts, throttling, AWS-side 5xx errors)."""
    if isinstance(error, (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError)):
        return True
    if isinstance(error, ClientError):
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0
        return error_code(error) in TEMPORARY_CODES or status >= 500
    return isinstance(error, BotoCoreError)  # Other network-level problems.


def lease_length(context):
    # The lease must outlast this invocation, so a slow but living attempt is never taken over.
    remaining = 0
    if hasattr(context, "get_remaining_time_in_millis"):
        remaining = context.get_remaining_time_in_millis() // 1000 + 5
    return max(settings()["lease_seconds"], remaining)


# ---------------------------------------------------------------- reading the event

def parse_record(record):
    """Checks one S3 event record. Returns (details, None) or (None, the reason it is ignored)."""
    if record.get("eventSource") != "aws:s3" or not str(record.get("eventName", "")).startswith("ObjectCreated:"):
        return None, "not an S3 ObjectCreated event"
    s3_part = record.get("s3", {})
    bucket = s3_part.get("bucket", {}).get("name", "")
    raw_key = s3_part.get("object", {}).get("key", "")
    expected_bucket = settings()["bucket_name"]
    if expected_bucket and bucket != expected_bucket:
        return None, "the event is from another bucket"
    key = unquote_plus(raw_key)  # Decode: S3 sends spaces as "+" and special characters as %XX.
    if len(key) > MAX_KEY_LENGTH:
        return None, "the key is too long"
    match = KEY_PATTERN.fullmatch(key)
    if not match:
        return None, "the key is not a workflows/YYYY/MM/DD/<workflow id>/<file> key"
    return {
        "bucket": bucket, "key": key, "workflow_id": match.group("workflow_id"),
        "event_name": record["eventName"], "event_time": record.get("eventTime", ""),
        "event_size": s3_part.get("object", {}).get("size"),
        "event_etag": str(s3_part.get("object", {}).get("eTag", "")).strip('"'),
    }, None


def delivery_seconds(event_time):
    # How long S3 took to deliver the event, or None if the time is missing.
    try:
        created = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())


# ---------------------------------------------------------------- DynamoDB (every write is conditional)

def read_item(table, workflow_id, item):
    response = table.get_item(Key={"workflow_id": workflow_id, "item": item}, ConsistentRead=True)
    return response.get("Item")


def is_refused(error):
    # DynamoDB refused a conditional write: expected for duplicates and races, never an error.
    return isinstance(error, ClientError) and error_code(error) == "ConditionalCheckFailedException"


def complete_event_stage(table, workflow_id, detail, function_arn="", bucket=""):
    """Stage 5 -> Completed. Allowed from Pending or Timed Out (a late event still counts). True if it changed."""
    try:
        table.update_item(
            Key={"workflow_id": workflow_id, "item": EVENT_STAGE},
            UpdateExpression="SET #state = :done, #detail = :detail, #source = :source, #updated = :now, "
                             "#finished = :now, #fn = :fn, #bucket = :bucket, #kind = :kind",
            ConditionExpression="attribute_exists(#wid) AND #state IN (:pending, :timed_out)",
            ExpressionAttributeNames={"#wid": "workflow_id", "#state": "state", "#detail": "detail",
                                      "#source": "source", "#updated": "updated_at", "#finished": "finished_at",
                                      "#fn": "processed_by_arn", "#bucket": "event_bucket", "#kind": "outcome_kind"},
            ExpressionAttributeValues={":done": COMPLETED, ":detail": detail, ":source": SOURCE, ":now": now_text(),
                                       ":pending": PENDING, ":timed_out": TIMED_OUT, ":fn": function_arn,
                                       ":bucket": bucket, ":kind": "success"})
        return True
    except ClientError as error:
        if is_refused(error):
            return False  # Already Completed (a duplicate event), or not expected (Skipped).
        raise


def claim_attempt(table, workflow_id, request_id, lease_seconds, max_attempts, function_arn="", bucket=""):
    """Starts the next attempt with ONE atomic, conditional update. Returns the new attempt number, or None.

    Succeeds only if the stage is waiting (Pending / Retrying), or Running / Timed Out WITHOUT a live lease
    (no attempt, or its attempt crashed), and fewer than max_attempts attempts were claimed so far.
    """
    now = epoch_now()
    try:
        response = table.update_item(
            Key={"workflow_id": workflow_id, "item": LAMBDA_STAGE},
            UpdateExpression="SET #state = :running, #detail = :detail, #source = :source, #updated = :now_text, "
                             "#started = if_not_exists(#started, :now_text), #lease = :lease_until, "
                             "#owner = :request, #attempt = if_not_exists(#attempt, :zero) + :one, "
                             "#fn = :fn, #bucket = :bucket REMOVE #finished, #kind",
            # Timed Out counts like Running here: if a live attempt still holds the lease, wait for it.
            ConditionExpression="attribute_exists(#wid) "
                                "AND (#state IN (:pending, :retrying) "
                                "     OR (#state IN (:running, :timed_out) "
                                "         AND (attribute_not_exists(#lease) OR #lease < :now))) "
                                "AND (attribute_not_exists(#attempt) OR #attempt < :max)",
            ExpressionAttributeNames={"#wid": "workflow_id", "#state": "state", "#detail": "detail",
                                      "#source": "source", "#updated": "updated_at", "#started": "started_at",
                                      "#lease": "lease_until", "#owner": "lease_owner", "#attempt": "attempt",
                                      "#finished": "finished_at", "#fn": "processed_by_arn",
                                      "#bucket": "event_bucket", "#kind": "outcome_kind"},
            ExpressionAttributeValues={":running": RUNNING, ":pending": PENDING, ":retrying": RETRYING,
                                       ":timed_out": TIMED_OUT, ":source": SOURCE, ":now_text": now_text(),
                                       ":detail": "Lambda is verifying the object with HeadObject.",
                                       ":lease_until": now + lease_seconds, ":request": request_id,
                                       ":zero": 0, ":one": 1, ":now": now, ":max": max_attempts,
                                       ":fn": function_arn, ":bucket": bucket},
            ReturnValues="ALL_NEW")
        return int(response["Attributes"]["attempt"])
    except ClientError as error:
        if is_refused(error):
            return None
        raise


def finish_attempt(table, workflow_id, attempt, new_state, detail, kind=None):
    """Writes the result of an attempt. Returns True if it was written.

    Completed: accepted from ANY attempt while the stage is unfinished (proven success; then final).
    Failed / Retrying: accepted only from the attempt that holds the current claim (the newest attempt),
    so an older attempt can never overwrite a newer one.
    """
    names = {"#wid": "workflow_id", "#state": "state", "#detail": "detail", "#source": "source",
             "#updated": "updated_at", "#finished": "finished_at", "#lease": "lease_until"}
    values = {":state": new_state, ":detail": detail, ":source": SOURCE, ":now": now_text(),
              ":running": RUNNING, ":timed_out": TIMED_OUT}
    expression = "SET #state = :state, #detail = :detail, #source = :source, #updated = :now"
    if kind:  # What exactly happened (see the module text), so the web app never guesses from the detail.
        names["#kind"] = "outcome_kind"
        values[":kind"] = kind
        expression += ", #kind = :kind"
    expression += ", #finished = :now REMOVE #lease" if new_state in FINAL_STATES else " REMOVE #finished, #lease"
    if new_state == COMPLETED:
        values.update({":pending": PENDING, ":retrying": RETRYING})
        condition = "attribute_exists(#wid) AND #state IN (:pending, :running, :retrying, :timed_out)"
    else:
        names["#attempt"] = "attempt"
        values[":attempt"] = attempt
        # Running (normal) or Timed Out (the web app's deadline passed meanwhile; a late result still counts).
        condition = "attribute_exists(#wid) AND #state IN (:running, :timed_out) AND #attempt = :attempt"
    try:
        table.update_item(Key={"workflow_id": workflow_id, "item": LAMBDA_STAGE}, UpdateExpression=expression,
                          ConditionExpression=condition, ExpressionAttributeNames=names,
                          ExpressionAttributeValues=values)
        return True
    except ClientError as error:
        if is_refused(error):
            return False  # A newer attempt took over, or the stage is already final.
        raise


def fail_after_last_attempt(table, workflow_id, stage, max_attempts):
    """Every attempt was claimed but the stage is unfinished (the last one stopped). Mark it Failed, safely."""
    try:
        table.update_item(
            Key={"workflow_id": workflow_id, "item": LAMBDA_STAGE},
            UpdateExpression="SET #state = :failed, #detail = :detail, #source = :source, #updated = :now, "
                             "#finished = :now, #kind = :kind REMOVE #lease",
            # Only if nothing changed since we looked (same state, same attempt count).
            ConditionExpression="#state = :seen_state AND #attempt = :seen_attempt",
            ExpressionAttributeNames={"#state": "state", "#detail": "detail", "#source": "source",
                                      "#updated": "updated_at", "#finished": "finished_at", "#lease": "lease_until",
                                      "#attempt": "attempt", "#kind": "outcome_kind"},
            ExpressionAttributeValues={":failed": FAILED, ":source": SOURCE, ":now": now_text(), ":kind": "timeout",
                                       ":seen_state": stage["state"], ":seen_attempt": stage["attempt"],
                                       ":detail": f"All {max_attempts} attempts were used without a result "
                                                  "(the last attempt stopped or timed out). Retries exhausted."})
        return True
    except ClientError as error:
        if is_refused(error):
            return False
        raise


# ---------------------------------------------------------------- verifying the object

def verify_object(details, meta):
    """HeadObject (metadata only, no checksum, no content). Returns (problems, facts). Raises AWS errors."""
    head = s3_client().head_object(Bucket=details["bucket"], Key=details["key"])
    size = head.get("ContentLength", 0)
    expected_key = settings()["expected_kms_key_arn"]
    problems = []
    if size != int(meta.get("file_size", -1)):
        problems.append(f"S3 reports {size} bytes, but the workflow uploaded {int(meta.get('file_size', -1))}")
    if details["event_size"] is not None and int(details["event_size"]) != size:
        problems.append("the event reports a different size than the object has now")
    if details["event_etag"] and head.get("ETag", "").strip('"') != details["event_etag"]:
        problems.append("the object was replaced after the event (different ETag)")
    if head.get("Metadata", {}).get("workflow-id") != details["workflow_id"]:
        problems.append("the workflow-id label does not match")
    if head.get("ServerSideEncryption") != "aws:kms":
        problems.append(f"the object is not SSE-KMS encrypted ('{head.get('ServerSideEncryption') or 'none'}')")
    elif head.get("SSEKMSKeyId") != expected_key:
        problems.append(f"it is encrypted with key {short(head.get('SSEKMSKeyId', ''))}, "
                        f"not the expected key {short(expected_key)}")
    return problems, {"size": size, "kms_key": short(expected_key)}


# ---------------------------------------------------------------- one record

def is_timeout(error):
    # Timeouts are reported as "timeout", other temporary problems (throttling, 5xx) as "aws_error".
    return isinstance(error, (ConnectTimeoutError, ReadTimeoutError)) or "Timeout" in error_code(error)


def process_record(record, request_id, lease_seconds, function_arn=""):
    """Processes one S3 event record. Returns a short result word (also written to the logs)."""
    details, reason = parse_record(record)
    if details is None:
        logger.info("Ignored S3 record: %s", reason)  # The key is not logged: it may contain a file name.
        return "ignored"
    workflow_id = details["workflow_id"]
    table = workflow_table()

    meta = read_item(table, workflow_id, "META")
    if meta is None or meta.get("mode") != "aws":
        logger.info("Ignored event for unknown workflow %s", workflow_id)
        return "ignored"
    if meta.get("s3_key") != details["key"]:
        logger.info("Ignored event for workflow %s: not the workflow's own object", workflow_id)
        return "ignored"

    # ---- Stage 5: the event was delivered. Evidence: this invocation holds a matching ObjectCreated record.
    delay = delivery_seconds(details["event_time"])
    delay_text = f" {delay:.1f} s after the object was created" if delay is not None else ""
    complete_event_stage(table, workflow_id,
                         f"S3 {details['event_name']} event received by Lambda{delay_text} (request {short(request_id)}).",
                         function_arn, details["bucket"])

    # ---- Stage 6: claim the next attempt (atomic). Refused = duplicate, in progress elsewhere, or used up.
    max_attempts = settings()["max_attempts"]
    attempt = claim_attempt(table, workflow_id, request_id, lease_seconds, max_attempts, function_arn,
                            details["bucket"])
    if attempt is None:
        stage = read_item(table, workflow_id, LAMBDA_STAGE) or {}
        stage["attempt"] = int(stage.get("attempt", 0) or 0)
        if stage.get("state") in FINAL_STATES:
            logger.info("Workflow %s: duplicate event ignored (processing already %s)", workflow_id, stage["state"])
            return "duplicate"
        lease_valid = stage.get("state") in (RUNNING, TIMED_OUT) and             int(stage.get("lease_until", 0) or 0) >= epoch_now()
        if lease_valid:
            logger.info("Workflow %s: attempt %s is already running elsewhere", workflow_id, stage["attempt"])
            return "in_progress"
        if stage["attempt"] >= max_attempts and fail_after_last_attempt(table, workflow_id, stage, max_attempts):
            logger.warning("Workflow %s: all %s attempts used without a result", workflow_id, max_attempts)
            return "failed"
        return "stale"  # Another invocation changed the stage a moment ago: nothing to do.
    logger.info("Workflow %s: attempt %s of %s claimed (request %s)", workflow_id, attempt, max_attempts,
                short(request_id))

    if not settings()["expected_kms_key_arn"]:
        finish_attempt(table, workflow_id, attempt, FAILED,
                       "Configuration Error: EXPECTED_KMS_KEY_ARN is not set on the Lambda function, so the "
                       "encryption cannot be verified. Permanent error: not retried.", "configuration")
        return "failed"

    try:
        problems, facts = verify_object(details, meta)
    except Exception as error:
        code = error_code(error)
        logger.warning("Workflow %s: HeadObject failed on attempt %s: %s", workflow_id, attempt, code)
        if not is_temporary(error):
            finish_attempt(table, workflow_id, attempt, FAILED,
                           f"{PERMANENT_HELP.get(code, f'AWS returned {code}.')} Permanent error: not retried, "
                           "because another attempt would fail the same way.", "aws_error")
            return "failed"
        if attempt < max_attempts:
            written = finish_attempt(table, workflow_id, attempt, RETRYING,
                                     f"Temporary error ({code}) on attempt {attempt} of {max_attempts}. Lambda "
                                     "retries automatically; this is not a failure yet.", "retrying")
            return "retrying" if written else "stale"
        finish_attempt(table, workflow_id, attempt, FAILED,
                       f"Temporary error ({code}) on the last attempt ({attempt} of {max_attempts}). "
                       "Retries exhausted.", "timeout" if is_timeout(error) else "aws_error")
        return "failed"

    if problems:
        finish_attempt(table, workflow_id, attempt, FAILED,
                       "Lambda's HeadObject check failed: " + "; ".join(problems) + ". Permanent error: not retried.",
                       "mismatch")
        return "failed"

    finish_attempt(table, workflow_id, attempt, COMPLETED,
                   f"Lambda independently verified the object on attempt {attempt}: {facts['size']:,} bytes, the "
                   f"workflow-id label and the event's ETag match, aws:kms with the expected key ({facts['kms_key']}). "
                   "The content was not read.", "success")
    return "completed"


# ---------------------------------------------------------------- the entry point

def lambda_handler(event, context):
    """Lambda calls this with an S3 event (usually one record). Raises only to ask Lambda to retry."""
    request_id = getattr(context, "aws_request_id", "") or "no-request-id"  # For diagnostics only.
    function_arn = getattr(context, "invoked_function_arn", "") or ""  # This function's identity (account, region).
    lease_seconds = lease_length(context)
    results = []
    for record in event.get("Records", []):
        try:
            result = process_record(record, request_id, lease_seconds, function_arn)
        except Exception as error:
            # Reached for DynamoDB problems (S3 problems are handled inside process_record).
            if is_temporary(error):
                logger.warning("Temporary problem while recording a workflow stage: %s", error_code(error))
                result = "retrying"
            else:
                # For example AccessDenied on the table: another attempt would fail the same way.
                logger.error("Cannot record the workflow stage (%s). Check the role's DynamoDB permissions.",
                             error_code(error))
                result = "cannot_record"
        results.append(result)
    logger.info("Results: %s", results)
    if "retrying" in results:
        # Lambda retries the whole event. Records that already finished are ignored next time.
        raise TemporaryProcessingError(f"Temporary error; Lambda will retry. Results: {results}")
    return {"results": results}
