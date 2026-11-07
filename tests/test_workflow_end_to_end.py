"""END-TO-END tests of the Live Workflow Monitor, exactly as a user would see it:

    browser (HTTP) -> FastAPI -> S3 PutObject (SSE-KMS) -> HeadObject checks -> DynamoDB
    -> S3 ObjectCreated event -> Lambda -> DynamoDB -> the status JSON the browser polls

Everything is fake: moto (S3, KMS, DynamoDB) and botocore's Stubber. No real AWS account is used.
The S3 notification is delivered by calling the Lambda handler with the same JSON AWS would send.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from deploy.lambda_function import workflow_processor as processor
from experiments import workflow_states as rules
from integrations import s3
from main import app
from tests.test_workflow_processor import s3_event, stub_head_object_errors

BUCKET = "lab-workflow-bucket"
REGION = "us-east-1"
# Distinctive fake values: if any of them ever appears in a page, a JSON answer, or a log, a test fails.
FAKE_SECRET_KEY = "FAKE-secret-access-key-must-never-appear-0123"
FAKE_SESSION_TOKEN = "FAKE-session-token-must-never-appear-4567"
RAW_AWS_MESSAGE = "User: arn:aws:sts::123456789012:assumed-role/internal-role/i-0abc is not authorized"


class Clock:
    """The web app's clock (deadlines and polling). advance() moves it forward without waiting."""

    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(rules, "utc_now", lambda: fake.now)
    return fake


@pytest.fixture
def aws(workflow_table, monkeypatch):
    """Production-like configuration for the app AND the Lambda function, all inside moto."""
    boto3.client("s3", region_name=REGION).create_bucket(Bucket=BUCKET)
    key_arn = boto3.client("kms", region_name=REGION).create_key()["KeyMetadata"]["Arn"]
    for name, value in {"S3_BUCKET_NAME": BUCKET, "S3_KMS_KEY_ARN": key_arn,
                        "WORKFLOW_LAMBDA_NAME": "aws-lab-workflow-processor",  # The app expects Lambda stages.
                        "EXPECTED_KMS_KEY_ARN": key_arn, "WORKFLOW_BUCKET_NAME": BUCKET,  # The Lambda's settings.
                        "AWS_SECRET_ACCESS_KEY": FAKE_SECRET_KEY, "AWS_SESSION_TOKEN": FAKE_SESSION_TOKEN}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("MAX_ATTEMPTS", raising=False)
    return key_arn


@pytest.fixture
def browser():
    client = TestClient(app)
    client.post("/login", data={"username": f"user-{uuid.uuid4().hex[:8]}", "password": "x"})
    return client


def upload(browser, mode="aws", scenario="success"):
    """The user starts a workflow from the page. Returns the workflow ID."""
    workflow_id = uuid.uuid4().hex
    response = browser.post("/workflows", data={"workflow_id": workflow_id, "mode": mode, "scenario": scenario},
                            files={"document": ("notes.txt", b"hello workflow", "text/plain")})
    assert response.status_code == 200 and response.url.path == f"/workflows/{workflow_id}"
    return workflow_id


def object_key(workflow_id):
    return f"workflows/{datetime.now(timezone.utc):%Y/%m/%d}/{workflow_id}/notes.txt"


def deliver_s3_event(workflow_id):
    """S3 notifies Lambda. Every delivery is a new invocation with a new request ID, as in AWS."""
    context = SimpleNamespace(aws_request_id=uuid.uuid4().hex)
    return processor.lambda_handler(s3_event(object_key(workflow_id)), context)


def status(browser, workflow_id):
    response = browser.get(f"/workflows/{workflow_id}/status")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    return response.json()


def states(view):
    return {stage["id"]: stage["state"] for stage in view["stages"]}


def detail(view, stage_id):
    return next(stage["detail"] for stage in view["stages"] if stage["id"] == stage_id)


# ---------------------------------------------------------------- the main scenarios

def test_successful_workflow(browser, aws, clock):
    workflow_id = upload(browser)
    view = status(browser, workflow_id)
    # After the upload: FastAPI's own stages are proven; the event has not arrived, so it is NOT shown as done.
    assert states(view) == {"created": "Completed", "upload": "Completed", "verify": "Completed", "kms": "Completed",
                            "event": "Pending", "lambda": "Pending"}
    assert view["overall"] == "Running" and view["poll"] is True and view["next_poll_ms"] == 2000

    assert deliver_s3_event(workflow_id) == {"results": ["completed"]}
    view = status(browser, workflow_id)
    assert view["overall"] == "Completed" and view["poll"] is False  # Polling stops by itself.
    assert set(states(view).values()) == {"Completed"}
    assert not any(stage["simulated"] for stage in view["stages"])  # Every state came from real (fake) AWS.


def test_failed_workflow(browser, aws, clock, monkeypatch):
    workflow_id = upload(browser)
    monkeypatch.setenv("EXPECTED_KMS_KEY_ARN", "arn:aws:kms:us-east-1:123456789012:key/not-the-real-key-abc123")
    assert deliver_s3_event(workflow_id) == {"results": ["failed"]}
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Completed"  # The event really arrived ...
    assert states(view)["lambda"] == "Failed"  # ... but Lambda's own check found the wrong key.
    assert view["overall"] == "Failed" and view["poll"] is False
    assert "Failed" in browser.get("/workflows").text  # Still listed, with its failure.


def test_retried_workflow(browser, aws, clock, monkeypatch):
    workflow_id = upload(browser)
    stub_head_object_errors(monkeypatch, "SlowDown", 503)
    with pytest.raises(processor.TemporaryProcessingError):
        deliver_s3_event(workflow_id)
    view = status(browser, workflow_id)
    assert view["overall"] == "Retrying" and view["poll"] is True  # The page keeps watching.
    assert "not a failure yet" in detail(view, "lambda")
    monkeypatch.setattr(processor, "s3_client", lambda: boto3.client("s3", config=processor.CLIENT_CONFIG))
    assert deliver_s3_event(workflow_id) == {"results": ["completed"]}  # Lambda's retry: a new request ID.
    view = status(browser, workflow_id)
    assert view["overall"] == "Completed"
    assert next(stage["attempt"] for stage in view["stages"] if stage["id"] == "lambda") == 2


def test_retries_exhausted(browser, aws, clock, monkeypatch):
    workflow_id = upload(browser)
    stub_head_object_errors(monkeypatch, "ThrottlingException", 503, count=3)
    for _ in range(2):
        with pytest.raises(processor.TemporaryProcessingError):
            deliver_s3_event(workflow_id)
    assert deliver_s3_event(workflow_id) == {"results": ["failed"]}
    view = status(browser, workflow_id)
    assert view["overall"] == "Failed" and "Retries exhausted" in detail(view, "lambda")


def test_duplicate_delivery(browser, aws, clock):
    workflow_id = upload(browser)
    assert deliver_s3_event(workflow_id) == {"results": ["completed"]}
    first = status(browser, workflow_id)
    assert deliver_s3_event(workflow_id) == {"results": ["duplicate"]}
    second = status(browser, workflow_id)
    assert first["stages"] == second["stages"]  # The duplicate changed nothing at all.


def test_delayed_event(browser, aws, clock):
    workflow_id = upload(browser)
    clock.advance(121)  # Past WORKFLOW_EVENT_DEADLINE_SECONDS (120), before the processing deadline (300).
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Timed Out" and "NOT proof of failure" in detail(view, "event")
    assert view["overall"] == "Running" and view["poll"] is True  # Lambda's stage may still finish.
    assert view["next_poll_ms"] == 5000  # Slower polling after the first minute.
    assert deliver_s3_event(workflow_id) == {"results": ["completed"]}  # The event arrives late.
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Completed" and view["overall"] == "Completed"


def test_missing_event_times_out_then_a_late_event_is_shown(browser, aws, clock):
    workflow_id = upload(browser)
    clock.advance(301)  # Past both deadlines. The S3 trigger is missing, so no event ever came.
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Timed Out" and states(view)["lambda"] == "Timed Out"
    assert view["overall"] == "Timed Out" and view["poll"] is False  # Not "Failed": there is no evidence of failure.
    assert deliver_s3_event(workflow_id) == {"results": ["completed"]}  # Fixed trigger, late event.
    assert status(browser, workflow_id)["overall"] == "Completed"  # A reload shows the real result.


def test_upload_failure_never_waits_for_an_event(browser, aws, clock, monkeypatch):
    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": RAW_AWS_MESSAGE}}, "PutObject")
    monkeypatch.setattr(s3, "upload_document", denied)
    workflow_id = upload(browser)
    view = status(browser, workflow_id)
    assert states(view)["upload"] == "Failed"
    assert states(view)["event"] == "Skipped" and states(view)["lambda"] == "Skipped"  # No object, so no event.
    assert view["overall"] == "Failed" and view["poll"] is False


# ---------------------------------------------------------------- security

def test_ownership_and_api_security(browser, aws, clock):
    workflow_id = upload(browser)
    stranger = TestClient(app)
    stranger.post("/login", data={"username": "stranger", "password": "x"})
    for url in (f"/workflows/{workflow_id}", f"/workflows/{workflow_id}/status"):
        response = stranger.get(url)
        assert response.status_code == 404 and workflow_id not in response.text  # Looks like a missing workflow.
    anonymous = TestClient(app).get(f"/workflows/{workflow_id}/status", follow_redirects=False)
    assert anonymous.status_code == 303 and anonymous.headers["location"] == "/login"
    # A stranger who reuses someone's workflow ID gets a NEW workflow; the original is untouched.
    response = stranger.post("/workflows", data={"workflow_id": workflow_id, "mode": "simulated",
                                                 "scenario": "success"},
                             files={"document": ("notes.txt", b"other content!", "text/plain")})
    assert response.url.path != f"/workflows/{workflow_id}"
    assert status(browser, workflow_id)["file_size"] == 14
    for bad in ("../etc/passwd", "A" * 32, "a" * 33, "%2e%2e"):
        assert browser.get(f"/workflows/{bad}/status").status_code == 404


def test_no_credentials_secrets_or_raw_aws_errors_in_responses_or_logs(browser, aws, clock, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)  # Capture everything the app and the Lambda function log.
    responses = []

    # 1. A successful workflow, through the whole chain.
    workflow_id = upload(browser)
    deliver_s3_event(workflow_id)
    responses += [browser.get(f"/workflows/{workflow_id}").text, str(status(browser, workflow_id))]

    # 2. Failures whose raw AWS message contains an internal ARN.
    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": RAW_AWS_MESSAGE}}, "PutObject")
    monkeypatch.setattr(s3, "upload_document", denied)
    failed_id = upload(browser)
    responses += [browser.get(f"/workflows/{failed_id}").text, str(status(browser, failed_id)),
                  browser.get("/workflows").text, browser.get("/history").text]

    everything = "\n".join(responses) + "\n" + caplog.text
    for forbidden in (FAKE_SECRET_KEY, FAKE_SESSION_TOKEN, RAW_AWS_MESSAGE, "internal-role", aws):
        assert forbidden not in everything, f"{forbidden!r} leaked"
    assert "AccessDenied" in everything  # Only the short error code is shown.


# ---------------------------------------------------------------- polling

def test_polling_stops_and_resumes_correctly(browser, aws, clock):
    workflow_id = upload(browser)
    page = browser.get(f"/workflows/{workflow_id}").text
    assert 'data-poll="true"' in page and 'data-limit="300"' in page  # Per page view; a reload resumes.
    clock.advance(301)
    assert 'data-poll="false"' in browser.get(f"/workflows/{workflow_id}").text  # Timed Out: stop asking.
    deliver_s3_event(workflow_id)
    view = status(browser, workflow_id)
    assert view["poll"] is False and view["poll_limit_seconds"] == 300 and view["next_poll_ms"] is None


# ---------------------------------------------------------------- simulation stays separate

def test_simulation_is_separate_from_real_dynamodb_workflows(browser, aws, clock, workflow_table):
    real_id = upload(browser)
    simulated_id = upload(browser, mode="simulated")
    items = boto3.client("dynamodb", region_name=REGION).scan(TableName=workflow_table)["Items"]
    stored_ids = {item["workflow_id"]["S"] for item in items}
    assert real_id in stored_ids and simulated_id not in stored_ids  # Simulations stay in local SQLite.
    # Even a (fake) S3 event naming the simulated workflow is ignored by the Lambda function.
    event = s3_event(object_key(simulated_id), size=14, etag="x")
    assert processor.lambda_handler(event, SimpleNamespace(aws_request_id="r")) == {"results": ["ignored"]}
    clock.advance(7)
    view = status(browser, simulated_id)
    assert view["simulated"] is True and view["overall"] == "Simulated"
    assert all(stage["simulated"] for stage in view["stages"] if stage["id"] != "created")
    assert not any(stage["simulated"] for stage in status(browser, real_id)["stages"])
