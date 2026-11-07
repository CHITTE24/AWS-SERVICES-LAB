"""End-to-end tests for the Live Workflow Monitor pages (routes/workflow_routes.py, experiments/live_workflow.py).

Real workflows run against moto's fake S3 and KMS. Simulated workflows run with every AWS client
FORBIDDEN, to prove they never contact AWS. A fake clock moves time forward for deadlines.
"""

import uuid
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from experiments import workflow_states as rules
from integrations import s3
from main import app
from repositories import workflow_dynamodb, workflow_repository
from repositories.history_repository import list_runs

BUCKET = "lab-workflow-bucket"
CONTENT = b"hello workflow"  # 14 bytes, a valid .txt file.
SENSITIVE = "arn:aws:iam::123456789012:role/internal-secret-role"  # Must never reach the browser.


class Clock:
    """A fake clock. advance() moves time forward without waiting."""

    def __init__(self):
        self.now = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(rules, "utc_now", lambda: fake.now)
    return fake


@pytest.fixture
def browser():
    # A logged-in browser with its OWN username, so each test sees only its own workflows.
    client = TestClient(app)
    client.post("/login", data={"username": f"user-{uuid.uuid4().hex[:8]}", "password": "x"})
    return client


@pytest.fixture
def real_aws(fake_aws, monkeypatch):
    # A fake bucket and KMS key inside moto, configured like the real ones.
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    key_arn = boto3.client("kms", region_name="us-east-1").create_key()["KeyMetadata"]["Arn"]
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET)
    monkeypatch.setenv("S3_KMS_KEY_ARN", key_arn)
    return key_arn


@pytest.fixture
def no_aws_allowed(monkeypatch):
    # Any attempt to create an AWS client fails the test.
    def forbidden(*args, **kwargs):
        raise AssertionError("A simulated workflow tried to contact AWS")
    monkeypatch.setattr(s3, "get_client", forbidden)
    monkeypatch.setattr(workflow_dynamodb, "get_client", forbidden)


def start(browser, mode="aws", scenario="success", workflow_id=None, content=CONTENT, name="notes.txt"):
    """Submits the start form. Returns (response, workflow_id)."""
    workflow_id = workflow_id or uuid.uuid4().hex
    response = browser.post("/workflows", data={"workflow_id": workflow_id, "mode": mode, "scenario": scenario},
                            files={"document": (name, content, "text/plain")})
    return response, workflow_id


def status(browser, workflow_id):
    response = browser.get(f"/workflows/{workflow_id}/status")
    assert response.status_code == 200, response.text
    return response.json()


def states(view):
    return {stage["id"]: stage["state"] for stage in view["stages"]}


def stage(view, stage_id):
    return next(item for item in view["stages"] if item["id"] == stage_id)


# ---------------------------------------------------------------- real workflows (moto)

def test_real_workflow_verifies_upload_and_kms(browser, real_aws, clock):
    response, workflow_id = start(browser)
    assert response.status_code == 200 and response.url.path == f"/workflows/{workflow_id}"
    view = status(browser, workflow_id)
    assert states(view) == {"created": "Completed", "upload": "Completed", "verify": "Completed", "kms": "Completed",
                            "event": "Skipped", "lambda": "Skipped"}  # No WORKFLOW_LAMBDA_NAME yet.
    assert view["overall"] == "Completed" and view["poll"] is False
    assert "only an acknowledgement" in stage(view, "upload")["detail"]
    assert "HeadObject found the object: 14 bytes" in stage(view, "verify")["detail"]
    assert f"...{real_aws[-4:]}" in stage(view, "kms")["detail"]
    assert not any(item["simulated"] for item in view["stages"])

    # The object really exists, under workflows/, encrypted with the key, labelled with the workflow ID.
    head = boto3.client("s3", region_name="us-east-1").head_object(
        Bucket=BUCKET, Key=f"workflows/2026/11/08/{workflow_id}/notes.txt")
    assert head["ServerSideEncryption"] == "aws:kms" and head["SSEKMSKeyId"] == real_aws
    assert head["Metadata"]["workflow-id"] == workflow_id
    assert list_runs(limit=1)[0]["status"] == "success"  # Also on the History page.


def test_status_json_contains_nothing_sensitive(browser, real_aws, clock):
    _, workflow_id = start(browser)
    view = status(browser, workflow_id)
    text = str(view)
    assert real_aws not in text  # Only the last 4 characters of the key ARN.
    assert BUCKET not in text and "s3_key" not in view and "owner" not in view


def test_waiting_for_lambda_then_deadlines(browser, real_aws, clock, monkeypatch):
    monkeypatch.setenv("WORKFLOW_LAMBDA_NAME", "aws-lab-workflow-processor")
    _, workflow_id = start(browser)
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Pending" and view["overall"] == "Running"
    assert view["poll"] is True and view["next_poll_ms"] == 2000

    clock.advance(121)  # Past WORKFLOW_EVENT_DEADLINE_SECONDS (120).
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Timed Out"
    assert "NOT proof of failure" in stage(view, "event")["detail"]
    assert states(view)["lambda"] == "Pending"  # Its own deadline (300 s) has not passed.
    assert view["next_poll_ms"] == 5000  # Slower polling after the first minute.

    clock.advance(200)  # 321 s: past the processing deadline and the poll limit.
    view = status(browser, workflow_id)
    assert view["overall"] == "Timed Out" and view["poll"] is False

    # A late Lambda result still updates the stage: a timeout was never proof of failure.
    workflow = workflow_repository.get_workflow(workflow_id)
    assert workflow_repository.set_stage(workflow, "event", rules.COMPLETED, "Late event.", rules.SOURCE_LAMBDA, 1)
    assert states(status(browser, workflow_id))["event"] == "Completed"


def test_upload_failure_is_safe_and_stays_in_history(browser, real_aws, clock, monkeypatch):
    def access_denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": f"User {SENSITIVE} is not authorized"}},
                          "PutObject")
    monkeypatch.setattr(s3, "upload_document", access_denied)
    response, workflow_id = start(browser)
    view = status(browser, workflow_id)
    assert states(view)["upload"] == "Failed"
    assert stage(view, "upload")["detail"].startswith("Access Denied (AccessDenied).")
    assert all(states(view)[name] == "Skipped" for name in ("verify", "kms", "event", "lambda"))
    assert view["overall"] == "Failed"
    # The raw AWS message (with an ARN) is never shown.
    assert SENSITIVE not in response.text and SENSITIVE not in str(view)
    # Still visible: on the monitor's list and on the History page.
    assert workflow_id in browser.get("/workflows").text
    assert list_runs(limit=1)[0]["status"] == "error"


def test_verification_is_independent_of_the_upload(browser, real_aws, clock, monkeypatch):
    # S3 acknowledged the upload, but the independent HeadObject finds something different.
    real_head = s3.head_object

    def different_object(bucket, key):
        head = real_head(bucket, key)
        return dict(head, size=head["size"] + 1, metadata={"workflow-id": "someone-else"})
    monkeypatch.setattr(s3, "head_object", different_object)
    _, workflow_id = start(browser)
    view = status(browser, workflow_id)
    assert states(view)["upload"] == "Completed"
    assert states(view)["verify"] == "Failed"
    detail = stage(view, "verify")["detail"]
    assert "15 bytes, but 14 were uploaded" in detail and "workflow-id label does not match" in detail


def test_missing_object_and_wrong_kms_key(browser, real_aws, clock, monkeypatch):
    monkeypatch.setattr(s3, "head_object", lambda bucket, key: (_ for _ in ()).throw(
        ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")))
    _, workflow_id = start(browser)
    view = status(browser, workflow_id)
    assert states(view)["verify"] == "Failed" and stage(view, "verify")["detail"].startswith("Not Found")
    assert states(view)["kms"] == "Skipped"

    other_key = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-00000000wxyz"
    monkeypatch.setattr(s3, "head_object", lambda bucket, key: {
        "size": 14, "etag": "", "encryption": "aws:kms", "kms_key_id": other_key, "metadata": {}})
    _, workflow_id = start(browser)
    view = status(browser, workflow_id)
    assert states(view)["kms"] == "Failed"
    assert "...wxyz" in stage(view, "kms")["detail"] and other_key not in str(view)


def test_real_mode_needs_s3_settings(browser, clock):
    page = browser.get("/workflows").text
    assert 'value="aws" disabled' in page  # The Real AWS choice is disabled without S3 settings.
    response, workflow_id = start(browser)
    assert response.status_code == 400
    assert "S3_BUCKET_NAME and S3_KMS_KEY_ARN" in response.text
    assert workflow_repository.get_workflow(workflow_id) is None  # Nothing was created.


def test_double_submit_creates_one_workflow_and_one_upload(browser, real_aws, clock, monkeypatch):
    calls = []
    real_upload = s3.upload_document

    def counting_upload(*args, **kwargs):
        calls.append(1)
        return real_upload(*args, **kwargs)
    monkeypatch.setattr(s3, "upload_document", counting_upload)
    _, workflow_id = start(browser)
    response, _ = start(browser, workflow_id=workflow_id)  # The same form submitted again.
    assert response.url.path == f"/workflows/{workflow_id}"
    assert len(calls) == 1


def test_only_the_owner_can_see_a_workflow(browser, real_aws, clock):
    _, workflow_id = start(browser)
    stranger = TestClient(app)
    stranger.post("/login", data={"username": "stranger", "password": "x"})
    assert stranger.get(f"/workflows/{workflow_id}").status_code == 404
    assert stranger.get(f"/workflows/{workflow_id}/status").status_code == 404
    assert browser.get("/workflows/not-a-valid-id/status").status_code == 404


def test_pages_are_never_cached(browser, real_aws, clock):
    _, workflow_id = start(browser)
    for url in ("/workflows", f"/workflows/{workflow_id}", f"/workflows/{workflow_id}/status"):
        assert browser.get(url).headers["cache-control"] == "no-store"


def test_hourly_limit_for_real_workflows(browser, real_aws, clock, monkeypatch):
    monkeypatch.setenv("WORKFLOW_MAX_PER_HOUR", "1")
    assert start(browser)[0].status_code == 200
    response, _ = start(browser)
    assert response.status_code == 429 and "Cost limit reached" in response.text
    assert start(browser, mode="simulated")[0].status_code == 200  # Simulations are free and not limited.


def test_record_failure_uploads_nothing(browser, real_aws, clock, monkeypatch):
    # WORKFLOW_BACKEND=dynamodb but the table does not exist: nothing may be uploaded untracked.
    monkeypatch.setenv("WORKFLOW_BACKEND", "dynamodb")
    response, workflow_id = start(browser)
    assert response.status_code == 503
    assert "nothing was uploaded" in response.text and "Not Found (ResourceNotFoundException)" in response.text
    assert "Contents" not in boto3.client("s3", region_name="us-east-1").list_objects_v2(Bucket=BUCKET)
    assert list_runs(limit=1)[0]["status"] == "error"  # The failed attempt is still in History.


def test_real_workflow_with_dynamodb_store(browser, real_aws, workflow_table, clock):
    _, workflow_id = start(browser)
    view = status(browser, workflow_id)
    assert view["overall"] == "Completed"
    assert "(DynamoDB)" in stage(view, "created")["detail"]
    items = boto3.client("dynamodb", region_name="us-east-1").scan(TableName=workflow_table)["Items"]
    assert {item["state"]["S"] for item in items if item["item"]["S"].startswith("STAGE#")} == {"Completed", "Skipped"}


def test_page_works_without_javascript(browser, real_aws, clock):
    _, workflow_id = start(browser)
    page = browser.get(f"/workflows/{workflow_id}").text
    assert 'data-stage="verify"' in page and "Object independently verified (HeadObject)" in page
    assert "/static/workflow.js" in page and "<noscript>" in page


# ---------------------------------------------------------------- simulated workflows (no AWS at all)

def test_simulated_success_is_labelled_and_offline(browser, clock, no_aws_allowed, monkeypatch):
    monkeypatch.setenv("WORKFLOW_BACKEND", "dynamodb")  # Ignored: simulations always stay local.
    _, workflow_id = start(browser, mode="simulated")
    view = status(browser, workflow_id)
    assert view["simulated"] is True
    assert states(view)["upload"] == "Simulated" and states(view)["event"] == "Pending"
    clock.advance(4)
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Simulated" and states(view)["lambda"] == "Running"
    assert stage(view, "lambda")["simulated"] is True  # Running, but clearly not a real AWS event.
    clock.advance(3)
    view = status(browser, workflow_id)
    assert view["overall"] == "Simulated" and view["poll"] is False
    assert all(item["simulated"] for item in view["stages"] if item["id"] != "created")
    assert "SIMULATED workflow" in browser.get(f"/workflows/{workflow_id}").text
    assert list_runs(limit=1)[0]["status"] == "simulated"


def test_simulated_retry_then_success(browser, clock, no_aws_allowed):
    _, workflow_id = start(browser, mode="simulated", scenario="retry")
    clock.advance(6)
    view = status(browser, workflow_id)
    assert states(view)["lambda"] == "Retrying" and view["overall"] == "Retrying"
    assert "retried, not marked Failed" in stage(view, "lambda")["detail"]
    clock.advance(4)
    assert (states(status(browser, workflow_id))["lambda"], stage(status(browser, workflow_id), "lambda")["attempt"]) \
        == ("Running", 2)
    clock.advance(2)
    view = status(browser, workflow_id)
    assert states(view)["lambda"] == "Simulated" and stage(view, "lambda")["attempt"] == 2


def test_simulated_jump_straight_to_the_end(browser, clock, no_aws_allowed):
    # The page was closed for a while: one status request applies every due step in order.
    _, workflow_id = start(browser, mode="simulated", scenario="retry")
    clock.advance(30)
    view = status(browser, workflow_id)
    assert states(view)["lambda"] == "Simulated" and view["overall"] == "Simulated"


def test_simulated_permanent_failure(browser, clock, no_aws_allowed):
    _, workflow_id = start(browser, mode="simulated", scenario="failure")
    clock.advance(6)
    view = status(browser, workflow_id)
    assert states(view)["lambda"] == "Failed" and view["overall"] == "Failed"
    assert stage(view, "lambda")["simulated"] is True


def test_simulated_timeout(browser, clock, no_aws_allowed):
    _, workflow_id = start(browser, mode="simulated", scenario="timeout")
    clock.advance(13)
    view = status(browser, workflow_id)
    assert states(view)["event"] == "Timed Out" and states(view)["lambda"] == "Pending"
    clock.advance(13)
    view = status(browser, workflow_id)
    assert view["overall"] == "Timed Out" and view["poll"] is False
    assert all(item["simulated"] for item in view["stages"] if item["id"] in ("event", "lambda"))


def test_invalid_uploads_are_rejected(browser, clock):
    response, _ = start(browser, mode="simulated", name="program.exe")
    assert response.status_code == 400
    response = browser.post("/workflows", data={"workflow_id": uuid.uuid4().hex, "mode": "nonsense"},
                            files={"document": ("notes.txt", CONTENT, "text/plain")})
    assert response.status_code == 400 and "Choose Real AWS or Simulated" in response.text
