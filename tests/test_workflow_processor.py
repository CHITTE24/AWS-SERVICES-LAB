"""Tests for the workflow Lambda function (deploy/lambda_function/workflow_processor.py).

Each test creates a REAL workflow through the app's own code (experiments/live_workflow.start_real) against
moto's fake S3, KMS, and DynamoDB, then feeds the Lambda function a realistic S3 event. So these tests also
prove that the app and the Lambda function agree on keys, labels, and the table layout.
Errors that moto cannot produce (AccessDenied, throttling) come from botocore's Stubber.

Retries are ALWAYS invoked with DIFFERENT request IDs here: a Lambda retry is a new invocation, so the
attempt count must come from DynamoDB, never from the request ID.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import quote_plus

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from core.form_checks import check_document
from deploy.lambda_function import workflow_processor as processor
from experiments import live_workflow
from experiments import workflow_states as rules
from repositories import workflow_dynamodb, workflow_repository

BUCKET = "lab-workflow-bucket"
CONTENT = b"hello workflow"  # 14 bytes.
REGION = "us-east-1"


@pytest.fixture
def aws(workflow_table, monkeypatch):
    """Fake bucket, KMS key, and table; the app and the Lambda function configured like production."""
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
    key_arn = boto3.client("kms", region_name=REGION).create_key()["KeyMetadata"]["Arn"]
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)  # The app's settings ...
    monkeypatch.setenv("S3_KMS_KEY_ARN", key_arn)
    monkeypatch.setenv("WORKFLOW_LAMBDA_NAME", "aws-lab-workflow-processor")
    monkeypatch.setenv("EXPECTED_KMS_KEY_ARN", key_arn)  # ... and the Lambda function's environment.
    monkeypatch.setenv("WORKFLOW_BUCKET_NAME", BUCKET)
    monkeypatch.delenv("MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("LEASE_SECONDS", raising=False)
    return key_arn


@pytest.fixture
def lambda_clock(monkeypatch):
    """The Lambda function's clock (seconds since 1970), which decides when a lease has expired."""
    clock = {"now": 1_800_000_000}
    monkeypatch.setattr(processor, "epoch_now", lambda: clock["now"])
    return clock


def new_workflow():
    """Starts a real workflow the way the web page does. Returns (workflow_id, s3_key)."""
    workflow_id = uuid.uuid4().hex
    live_workflow.start_real(workflow_id, f"user-{workflow_id[:6]}", check_document("notes.txt", CONTENT))
    workflow = workflow_repository.get_workflow(workflow_id)
    assert {s["stage_id"]: s["state"] for s in workflow["stages"]}["verify"] == "Completed"
    return workflow_id, workflow["s3_key"]


def s3_event(key, event_name="ObjectCreated:Put", bucket=BUCKET, size=None, etag=None):
    """A realistic S3 notification, shaped exactly like the ones AWS sends (the key is URL-encoded)."""
    if size is None or etag is None:
        head = boto3.client("s3", region_name=REGION).head_object(Bucket=bucket, Key=key)
        size = head["ContentLength"] if size is None else size
        etag = head["ETag"].strip('"') if etag is None else etag
    return {"Records": [{
        "eventVersion": "2.1", "eventSource": "aws:s3", "awsRegion": REGION, "eventName": event_name,
        "eventTime": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
        "s3": {"s3SchemaVersion": "1.0", "bucket": {"name": bucket, "arn": f"arn:aws:s3:::{bucket}"},
               "object": {"key": quote_plus(key, safe="/"), "size": size, "eTag": etag,
                          "sequencer": "0055AED6DCD90281E5"}},
    }]}


def invoke(event, request_id=None):
    # Every invocation gets its own request ID unless a test chooses one, exactly like real Lambda.
    # Like real Lambda: a fresh request ID per invocation, and the function's own ARN (account, region, name).
    return processor.lambda_handler(event, SimpleNamespace(aws_request_id=request_id or uuid.uuid4().hex,
                                                           invoked_function_arn="arn:aws:lambda:us-east-1:123456789012:function:aws-lab-workflow-processor"))


def stages(workflow_id):
    return {s["stage_id"]: s for s in workflow_repository.get_workflow(workflow_id)["stages"]}


def raw_lambda_stage(workflow_id):
    return processor.read_item(processor.workflow_table(), workflow_id, processor.LAMBDA_STAGE)


def stub_head_object_errors(monkeypatch, code, status, count=1):
    """Makes HeadObject fail with the given AWS error (moto cannot produce these)."""
    client = boto3.client("s3", region_name=REGION, config=Config(retries={"max_attempts": 1}))
    stubber = Stubber(client)
    for _ in range(count):
        stubber.add_client_error("head_object", service_error_code=code, http_status_code=status)
    stubber.activate()
    monkeypatch.setattr(processor, "s3_client", lambda: client)


def restore_s3(monkeypatch):
    # AWS "recovers": the Lambda function uses the normal (moto) S3 client again.
    monkeypatch.setattr(processor, "s3_client", lambda: boto3.client("s3", config=processor.CLIENT_CONFIG))


# ---------------------------------------------------------------- success and duplicates

def test_successful_event_completes_both_stages(aws):
    workflow_id, key = new_workflow()
    assert stages(workflow_id)["event"]["state"] == "Pending"  # Waiting for evidence.
    assert invoke(s3_event(key), "request-abc123") == {"results": ["completed"]}
    result = stages(workflow_id)
    assert result["event"]["state"] == "Completed" and result["event"]["source"] == "lambda"
    assert "ObjectCreated:Put event received by Lambda" in result["event"]["detail"]
    assert result["lambda"]["state"] == "Completed" and result["lambda"]["attempt"] == 1
    assert "The content was not read." in result["lambda"]["detail"]
    assert aws not in result["lambda"]["detail"]  # Only the end of the key ARN is shown.
    assert "lease_until" not in raw_lambda_stage(workflow_id)  # The lease is released when finished.
    assert raw_lambda_stage(workflow_id)["lease_owner"] == "request-abc123"  # Diagnostics only.
    view = live_workflow.describe(live_workflow.refresh(workflow_id))
    assert view["overall"] == "Completed" and len(view["stages"]) == 6


def test_duplicate_notification_is_ignored_without_another_aws_call(aws, monkeypatch):
    workflow_id, key = new_workflow()
    invoke(s3_event(key))
    calls = []
    real_client = processor.s3_client
    monkeypatch.setattr(processor, "s3_client", lambda: calls.append(1) or real_client())
    assert invoke(s3_event(key)) == {"results": ["duplicate"]}  # S3 delivered it twice.
    assert calls == [] and stages(workflow_id)["lambda"]["attempt"] == 1  # No attempt used, no S3 call.


def test_only_one_invocation_can_hold_the_claim(aws, lambda_clock):
    workflow_id, _ = new_workflow()
    table = processor.workflow_table()
    assert processor.claim_attempt(table, workflow_id, "request-A", 30, 3) == 1
    # A concurrent invocation is refused while the lease is alive, so the counter cannot be corrupted.
    assert processor.claim_attempt(table, workflow_id, "request-B", 30, 3) is None
    lambda_clock["now"] += 29
    assert processor.claim_attempt(table, workflow_id, "request-C", 30, 3) is None
    assert int(raw_lambda_stage(workflow_id)["attempt"]) == 1


def test_concurrent_duplicate_while_processing(aws, lambda_clock):
    workflow_id, key = new_workflow()
    processor.claim_attempt(processor.workflow_table(), workflow_id, "request-A", 30, 3)
    assert invoke(s3_event(key), "request-B") == {"results": ["in_progress"]}
    assert stages(workflow_id)["lambda"]["state"] == "Running" and stages(workflow_id)["lambda"]["attempt"] == 1


def test_encoded_key_is_decoded():
    details, reason = processor.parse_record(s3_event(
        "workflows/2026/11/08/" + "a" * 32 + "/my file(1).txt", size=1, etag="x")["Records"][0])
    assert reason is None
    assert details["key"].endswith("/my file(1).txt") and details["workflow_id"] == "a" * 32


# ---------------------------------------------------------------- records that must be ignored

@pytest.mark.parametrize("key, event_name", [
    ("submissions/2026/11/08/" + "a" * 32 + "/notes.txt", "ObjectCreated:Put"),  # The lab form's prefix.
    ("workflows/2026/11/08/" + "A" * 32 + "/notes.txt", "ObjectCreated:Put"),  # Not lowercase hex.
    ("workflows/2026/11/08/" + "a" * 31 + "/notes.txt", "ObjectCreated:Put"),  # ID too short.
    ("workflows/2026/11/08/" + "a" * 32 + "/sub/notes.txt", "ObjectCreated:Put"),  # Extra folder.
    ("workflows/2026/11/08/" + "a" * 32 + "/", "ObjectCreated:Put"),  # No file name.
    ("workflows/../" + "a" * 32 + "/notes.txt", "ObjectCreated:Put"),  # Path tricks.
    ("workflows/2026/11/08/" + "a" * 32 + "/" + "n" * 1100, "ObjectCreated:Put"),  # Too long.
    ("workflows/2026/11/08/" + "a" * 32 + "/notes.txt", "ObjectRemoved:Delete"),  # Not ObjectCreated.
])
def test_malformed_or_unrelated_records_are_ignored(key, event_name):
    details, reason = processor.parse_record(s3_event(key, event_name=event_name, size=1, etag="x")["Records"][0])
    assert details is None and reason


def test_other_bucket_and_unknown_workflow_are_ignored(aws):
    workflow_id, key = new_workflow()
    assert invoke(s3_event(key, bucket="someone-elses-bucket", size=14, etag="x")) == {"results": ["ignored"]}
    unknown = key.replace(workflow_id, uuid.uuid4().hex)
    assert invoke(s3_event(unknown, size=14, etag="x")) == {"results": ["ignored"]}
    other_object = key.replace("notes.txt", "other.txt")  # Valid format, but not this workflow's object.
    assert invoke(s3_event(other_object, size=14, etag="x")) == {"results": ["ignored"]}
    assert stages(workflow_id)["event"]["state"] == "Pending"  # Nothing was written.


# ---------------------------------------------------------------- verification failures (permanent)

def test_missing_object(aws):
    workflow_id, key = new_workflow()
    event = s3_event(key)
    boto3.client("s3", region_name=REGION).delete_object(Bucket=BUCKET, Key=key)
    assert invoke(event) == {"results": ["failed"]}
    result = stages(workflow_id)
    assert result["event"]["state"] == "Completed"  # The event itself really arrived.
    assert result["lambda"]["state"] == "Failed" and "Not Found" in result["lambda"]["detail"]


def test_wrong_metadata(aws):
    workflow_id, key = new_workflow()
    boto3.client("s3", region_name=REGION).put_object(Bucket=BUCKET, Key=key, Body=CONTENT,
                                                       ServerSideEncryption="aws:kms", SSEKMSKeyId=aws,
                                                       Metadata={"workflow-id": "f" * 32})
    assert invoke(s3_event(key)) == {"results": ["failed"]}
    assert "workflow-id label does not match" in stages(workflow_id)["lambda"]["detail"]


def test_replaced_object_is_detected_by_etag(aws):
    workflow_id, key = new_workflow()
    event = s3_event(key)  # The event describes the ORIGINAL object.
    boto3.client("s3", region_name=REGION).put_object(Bucket=BUCKET, Key=key, Body=b"replaced content",
                                                       ServerSideEncryption="aws:kms", SSEKMSKeyId=aws,
                                                       Metadata={"workflow-id": workflow_id})
    assert invoke(event) == {"results": ["failed"]}
    detail = stages(workflow_id)["lambda"]["detail"]
    assert "replaced after the event" in detail and "16 bytes" in detail


def test_wrong_kms_key(aws, monkeypatch):
    workflow_id, key = new_workflow()
    other_key = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-0000000wxyz9"
    monkeypatch.setenv("EXPECTED_KMS_KEY_ARN", other_key)
    assert invoke(s3_event(key)) == {"results": ["failed"]}
    detail = stages(workflow_id)["lambda"]["detail"]
    assert "...0wxyz9" in detail and other_key not in detail and aws not in detail


def test_missing_kms_setting_is_a_configuration_error(aws, monkeypatch):
    workflow_id, key = new_workflow()
    monkeypatch.delenv("EXPECTED_KMS_KEY_ARN")
    assert invoke(s3_event(key)) == {"results": ["failed"]}
    assert "EXPECTED_KMS_KEY_ARN is not set" in stages(workflow_id)["lambda"]["detail"]


def test_iam_denial_is_permanent_and_not_retried(aws, monkeypatch):
    workflow_id, key = new_workflow()
    event = s3_event(key)
    stub_head_object_errors(monkeypatch, "403", 403)
    assert invoke(event) == {"results": ["failed"]}  # No exception: retrying would not help.
    lambda_stage = stages(workflow_id)["lambda"]
    assert lambda_stage["state"] == "Failed" and lambda_stage["attempt"] == 1
    assert lambda_stage["detail"].startswith("Access Denied") and "Permanent error" in lambda_stage["detail"]


# ---------------------------------------------------------------- temporary errors and retries

def test_retry_with_a_new_request_id_continues_the_count(aws, monkeypatch):
    workflow_id, key = new_workflow()
    event = s3_event(key)
    stub_head_object_errors(monkeypatch, "SlowDown", 503)
    with pytest.raises(processor.TemporaryProcessingError):  # Raising makes Lambda retry automatically.
        invoke(event, "request-first")
    lambda_stage = stages(workflow_id)["lambda"]
    assert lambda_stage["state"] == "Retrying" and "not a failure yet" in lambda_stage["detail"]
    restore_s3(monkeypatch)
    assert invoke(event, "request-second") == {"results": ["completed"]}  # A NEW request ID.
    assert (stages(workflow_id)["lambda"]["state"], stages(workflow_id)["lambda"]["attempt"]) == ("Completed", 2)


def test_failed_only_when_retries_are_used_up(aws, monkeypatch):
    workflow_id, key = new_workflow()
    event = s3_event(key)
    stub_head_object_errors(monkeypatch, "ThrottlingException", 503, count=3)
    for attempt in (1, 2):
        with pytest.raises(processor.TemporaryProcessingError):
            invoke(event)  # Each retry has a different request ID.
        assert (stages(workflow_id)["lambda"]["state"], stages(workflow_id)["lambda"]["attempt"]) == \
            ("Retrying", attempt)  # NOT Failed while attempts remain.
    assert invoke(event) == {"results": ["failed"]}  # The 3rd and last attempt: no exception, no more retries.
    lambda_stage = stages(workflow_id)["lambda"]
    assert lambda_stage["state"] == "Failed" and lambda_stage["attempt"] == 3
    assert "Retries exhausted" in lambda_stage["detail"]
    assert invoke(event) == {"results": ["duplicate"]}  # A late extra invocation changes nothing.


@pytest.mark.parametrize("max_attempts", [1, 2, 5])
def test_retry_limit_is_configurable(aws, monkeypatch, max_attempts):
    monkeypatch.setenv("MAX_ATTEMPTS", str(max_attempts))
    workflow_id, key = new_workflow()
    event = s3_event(key)
    stub_head_object_errors(monkeypatch, "SlowDown", 503, count=max_attempts)
    for _ in range(max_attempts - 1):
        with pytest.raises(processor.TemporaryProcessingError):
            invoke(event)
    assert invoke(event) == {"results": ["failed"]}
    assert stages(workflow_id)["lambda"]["attempt"] == max_attempts


def test_crashed_attempt_is_taken_over_after_its_lease(aws, lambda_clock):
    # Attempt 1 claimed the work and then died (for example the 15-second Lambda timeout).
    workflow_id, key = new_workflow()
    processor.claim_attempt(processor.workflow_table(), workflow_id, "request-crashed", 30, 3)
    assert invoke(s3_event(key)) == {"results": ["in_progress"]}  # Too early: the lease is still alive.
    lambda_clock["now"] += 31  # The lease expired. Lambda's retry (a new request ID) arrives.
    assert invoke(s3_event(key)) == {"results": ["completed"]}
    assert stages(workflow_id)["lambda"]["attempt"] == 2


def test_last_attempt_crashed_means_retries_exhausted(aws, lambda_clock, monkeypatch):
    monkeypatch.setenv("MAX_ATTEMPTS", "1")
    workflow_id, key = new_workflow()
    processor.claim_attempt(processor.workflow_table(), workflow_id, "request-crashed", 30, 1)
    lambda_clock["now"] += 31
    assert invoke(s3_event(key)) == {"results": ["failed"]}
    detail = stages(workflow_id)["lambda"]["detail"]
    assert "All 1 attempts were used without a result" in detail and "Retries exhausted" in detail


def test_older_attempt_cannot_overwrite_a_newer_one(aws, lambda_clock):
    workflow_id, _ = new_workflow()
    table = processor.workflow_table()
    assert processor.claim_attempt(table, workflow_id, "request-old", 30, 3) == 1
    lambda_clock["now"] += 31
    assert processor.claim_attempt(table, workflow_id, "request-new", 30, 3) == 2  # Took over.
    # The old attempt wakes up late: its Failed or Retrying result is refused.
    assert not processor.finish_attempt(table, workflow_id, 1, processor.FAILED, "old failure")
    assert not processor.finish_attempt(table, workflow_id, 1, processor.RETRYING, "old retry")
    assert processor.finish_attempt(table, workflow_id, 2, processor.COMPLETED, "new success")
    # Nothing can overwrite the success afterwards, from any attempt.
    for attempt, state in ((1, processor.COMPLETED), (2, processor.FAILED), (3, processor.RETRYING)):
        assert not processor.finish_attempt(table, workflow_id, attempt, state, "late")
    assert stages(workflow_id)["lambda"]["detail"] == "new success"


def test_older_attempt_success_still_counts(aws, lambda_clock):
    # Proven success is evidence, whichever attempt found it. It finishes the stage for good.
    workflow_id, _ = new_workflow()
    table = processor.workflow_table()
    processor.claim_attempt(table, workflow_id, "request-old", 30, 3)
    lambda_clock["now"] += 31
    processor.claim_attempt(table, workflow_id, "request-new", 30, 3)
    assert processor.finish_attempt(table, workflow_id, 1, processor.COMPLETED, "old attempt verified it")
    assert not processor.finish_attempt(table, workflow_id, 2, processor.FAILED, "new attempt failed later")
    assert stages(workflow_id)["lambda"]["state"] == "Completed"


def test_late_completion_after_timeout(aws, monkeypatch):
    workflow_id, key = new_workflow()
    later = datetime.now(timezone.utc) + timedelta(seconds=400)  # Past both deadlines.
    monkeypatch.setattr(rules, "utc_now", lambda: later)
    assert live_workflow.describe(live_workflow.refresh(workflow_id), later)["overall"] == "Timed Out"
    assert invoke(s3_event(key)) == {"results": ["completed"]}  # The event arrives late.
    view = live_workflow.describe(live_workflow.refresh(workflow_id), later)
    assert view["overall"] == "Completed"  # Timed Out was never proof of failure.


def test_lease_outlasts_the_function_run_time(monkeypatch):
    monkeypatch.delenv("LEASE_SECONDS", raising=False)
    long_run = SimpleNamespace(get_remaining_time_in_millis=lambda: 60_000)
    assert processor.lease_length(long_run) == 65  # 60 s left + 5 s margin, more than the 30 s setting.
    assert processor.lease_length(SimpleNamespace()) == 30


def test_dynamodb_problems(aws, monkeypatch):
    _, key = new_workflow()
    event = s3_event(key)

    def throttled(*args, **kwargs):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"},
                           "ResponseMetadata": {"HTTPStatusCode": 400}}, "GetItem")
    monkeypatch.setattr(processor, "read_item", throttled)
    with pytest.raises(processor.TemporaryProcessingError):  # Temporary: Lambda retries.
        invoke(event)

    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"},
                           "ResponseMetadata": {"HTTPStatusCode": 400}}, "GetItem")
    monkeypatch.setattr(processor, "read_item", denied)
    assert invoke(event) == {"results": ["cannot_record"]}  # Permanent: retrying would not help.


# ---------------------------------------------------------------- consistency and safety

def test_rules_and_keys_match_the_app():
    # The Lambda function is deployed on its own, so it carries a copy of the rules. They must not drift.
    assert processor.ALLOWED_PREVIOUS == rules.ALLOWED_PREVIOUS
    assert processor.FINAL_STATES == rules.FINAL_STATES
    assert processor.EVENT_STAGE == workflow_dynamodb.stage_key("event")
    assert processor.LAMBDA_STAGE == workflow_dynamodb.stage_key("lambda")
    assert processor.KEY_PATTERN.fullmatch(f"{live_workflow.S3_PREFIX}2026/11/08/{'a' * 32}/notes.txt")


def test_never_reads_content_and_has_no_credentials():
    source = open(processor.__file__, encoding="utf-8").read()
    assert "get_object(" not in source  # Metadata only (HeadObject); file content is never downloaded.
    assert "aws_access_key_id" not in source and "aws_secret_access_key" not in source
    assert "ChecksumMode" not in source  # No checksum is requested.
    assert "VpcConfig" not in source
