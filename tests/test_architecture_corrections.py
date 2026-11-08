"""Regression tests for the Architecture Map readiness-review corrections (H1-H3, M1-M8, L1-L3).

Each test names the finding it protects. AWS is always fake (moto / test doubles).
"""

import shutil
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError
from fastapi.testclient import TestClient

from catalog import architecture_catalog as catalog
from core import instance_identity
from deploy.lambda_function import workflow_processor as processor
from experiments import architecture_evidence as evidence
from experiments import architecture_verify, live_workflow
from experiments import workflow_states as clock
from integrations import ec2, s3
from main import app
from repositories import architecture_repository as store
from repositories import workflow_repository
from routes import architecture_routes
from scripts import migrate_architecture_db
from tests.test_workflow_processor import aws, invoke, new_workflow, s3_event, stub_head_object_errors  # noqa: F401

INSTANCE = "i-0abc1234def567890"
APP = f"ec2:{INSTANCE}"
FUNCTION = "lambda:aws-lab-workflow-processor"
BUCKET = "s3:lab-workflow-bucket"
TABLE = "dynamodb:aws-lab-workflows"


def latest(key, viewer="system"):
    records = store.evidence_for([key], viewer)[key]
    return records[0] if records else None


def for_workflow(key, workflow_id):
    return [item for item in store.evidence_for([key], "system")[key] if item["reference"] == f"workflow:{workflow_id}"]


@pytest.fixture
def on_ec2():
    instance_identity._cache["key"] = APP


def login():
    client = TestClient(app)
    client.post("/login", data={"username": f"user-{uuid.uuid4().hex[:8]}", "password": "x"})
    return client


# ---------------------------------------------------------------- H1: identities captured at operation time

def test_h1_env_changes_after_upload_do_not_move_the_evidence(aws, on_ec2, monkeypatch):  # noqa: F811
    workflow_id, key = new_workflow()
    # .env is edited AFTER the upload: a different bucket and a different function name.
    monkeypatch.setenv("S3_BUCKET_NAME", "some-other-bucket")
    monkeypatch.setenv("WORKFLOW_LAMBDA_NAME", "some-other-function")
    invoke(s3_event(key))
    live_workflow.refresh(workflow_id)
    exact = catalog.operation_key(BUCKET, "s3:ObjectCreated", FUNCTION)
    assert for_workflow(exact, workflow_id)[0]["status"] == "Success"
    wrong = catalog.operation_key("s3:some-other-bucket", "s3:ObjectCreated", "lambda:some-other-function")
    assert store.evidence_for([wrong], "system")[wrong] == []
    snapshot = store.get_workflow_context(workflow_id)
    assert (snapshot["bucket"], snapshot["app_key"], snapshot["account"]) == ("lab-workflow-bucket", APP, "123456789012")


def test_h1_lambda_stage_records_its_own_identity(aws):  # noqa: F811
    workflow_id, key = new_workflow()
    invoke(s3_event(key))
    stages = {s["stage_id"]: s for s in workflow_repository.get_workflow(workflow_id)["stages"]}
    for stage_id in ("event", "lambda"):
        assert stages[stage_id]["processed_by_arn"].endswith(":function:aws-lab-workflow-processor")
        assert stages[stage_id]["event_bucket"] == "lab-workflow-bucket"
    assert stages["lambda"]["outcome_kind"] == "success"


def test_h1_missing_lambda_identity_is_unattributed_not_guessed(aws):  # noqa: F811
    workflow_id, key = new_workflow()
    processor.lambda_handler(s3_event(key), SimpleNamespace(aws_request_id="r1"))  # No function ARN.
    live_workflow.refresh(workflow_id)
    assert for_workflow(catalog.operation_key(BUCKET, "s3:ObjectCreated", FUNCTION), workflow_id) == []
    assert latest(f"unattributed|workflow.event|workflow:{workflow_id}")["status"] == "Success"


def test_h1_workflow_without_a_snapshot_is_unattributed(aws, monkeypatch):  # noqa: F811
    workflow_id, key = new_workflow()
    monkeypatch.setattr(store, "get_workflow_context", lambda workflow_id: None)  # e.g. created before this change
    processor.lambda_handler(s3_event(key), SimpleNamespace(aws_request_id="r", invoked_function_arn=""))
    live_workflow.refresh(workflow_id)
    assert latest(f"unattributed|workflow.lambda_write|workflow:{workflow_id}") is not None


# ---------------------------------------------------------------- H2: precise classification

def test_h2_verification_mismatch_is_not_an_aws_failure(aws, on_ec2, monkeypatch):  # noqa: F811
    from core.form_checks import check_document
    real_head = s3.head_object
    monkeypatch.setattr(s3, "head_object", lambda b, k: dict(real_head(b, k), size=999))  # HeadObject works ...
    workflow_id = uuid.uuid4().hex
    live_workflow.start_real(workflow_id, "u", check_document("notes.txt", b"hello workflow"))
    # ... but the object does not match: a mismatch (yellow), not "HeadObject failed" (red).
    record = for_workflow(catalog.operation_key(APP, "s3:HeadObject", BUCKET), workflow_id)[0]
    assert (record["outcome"], record["status"]) == ("partial", "Verification Mismatch")


@pytest.mark.parametrize("error, outcome, status", [
    (ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "PutObject"), "fail", "Access Denied"),
    (ConnectTimeoutError(endpoint_url="https://s3"), "partial", "Timed Out"),
    (ClientError({"Error": {"Code": "InvalidArgument", "Message": "x"}}, "PutObject"), "partial", "Configuration Error"),
])
def test_h2_upload_errors_are_classified(aws, on_ec2, monkeypatch, error, outcome, status):  # noqa: F811
    from core.form_checks import check_document

    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(s3, "upload_document", fail)
    workflow_id = uuid.uuid4().hex
    live_workflow.start_real(workflow_id, "u", check_document("notes.txt", b"hello workflow"))
    record = for_workflow(catalog.operation_key(APP, "s3:PutObject", BUCKET), workflow_id)[0]
    assert (record["outcome"], record["status"]) == (outcome, status)


def test_h2_lambda_configuration_error_records_no_headobject_result(aws, monkeypatch):  # noqa: F811
    workflow_id, key = new_workflow()
    monkeypatch.delenv("EXPECTED_KMS_KEY_ARN")
    invoke(s3_event(key))
    live_workflow.refresh(workflow_id)
    assert for_workflow(catalog.operation_key(FUNCTION, "s3:HeadObject", BUCKET), workflow_id) == []
    assert for_workflow(catalog.operation_key(FUNCTION, "dynamodb:UpdateItem", TABLE), workflow_id)[0]["outcome"] == "pass"


def test_h2_lambda_mismatch_and_exhausted_retries(aws, monkeypatch):  # noqa: F811
    workflow_id, key = new_workflow()
    monkeypatch.setenv("EXPECTED_KMS_KEY_ARN", "arn:aws:kms:us-east-1:123456789012:key/not-the-key-abc123")
    invoke(s3_event(key))
    live_workflow.refresh(workflow_id)
    record = for_workflow(catalog.operation_key(FUNCTION, "s3:HeadObject", BUCKET), workflow_id)[0]
    assert (record["outcome"], record["status"]) == ("partial", "Verification Mismatch")

    workflow_id, key = new_workflow()
    monkeypatch.setenv("MAX_ATTEMPTS", "1")
    stub_head_object_errors(monkeypatch, "ThrottlingException", 503)
    invoke(s3_event(key))
    live_workflow.refresh(workflow_id)
    record = for_workflow(catalog.operation_key(FUNCTION, "s3:HeadObject", BUCKET), workflow_id)[0]
    assert record["outcome"] == "fail"  # Retries exhausted by throttling: a real AWS failure.


# ---------------------------------------------------------------- M1: account and region scoping

def test_m1_evidence_from_another_account_or_region_never_matches():
    queue = f"q{uuid.uuid4().hex[:10]}"
    bucket = f"b{uuid.uuid4().hex[:10]}"
    store.add_evidence(f"sqs:{queue}", "resource", "t", "pass", "Connected", "d", account="111111111111",
                       region="eu-west-1")
    store.add_evidence(f"s3:{bucket}", "resource", "t", "pass", "Connected", "d", account="123456789012",
                       region="eu-west-1")
    body = {"nodes": [{"id": "q", "type": "sqs", "identifier": queue}, {"id": "b", "type": "s3", "identifier": bucket}],
            "edges": [], "scope": {"account": "123456789012", "region": "ap-south-1"}}
    result = evidence.evaluate_diagram(body, "system")
    assert result["nodes"]["q"]["colour"] == "gray"  # Another account.
    assert result["nodes"]["b"]["colour"] == "green"  # Same account; bucket names are global (no region filter).
    body["scope"] = {"account": "", "region": ""}
    unscoped = evidence.evaluate_diagram(body, "system")
    assert unscoped["nodes"]["q"]["colour"] == "green" and "No AWS account" in unscoped["scope_warning"]


def test_m1_unknown_account_makes_evidence_unattributed():
    instance_identity._cache.pop("account")
    instance_identity._cache["account_failed_at"] = __import__("time").monotonic()
    bucket = f"b{uuid.uuid4().hex[:10]}"
    evidence.record(f"s3:{bucket}", "resource", "test.check", "Connected", "d", "ref:1", instance_identity.scope())
    assert latest(f"s3:{bucket}") is None and latest("unattributed|test.check|ref:1") is not None


def test_m1_scope_is_validated_and_defaulted():
    client = login()
    bad = client.post("/api/architecture/diagrams", json={"name": "x", "body": {"nodes": [], "edges": [],
                                                                               "scope": {"account": "12"}}})
    assert bad.status_code == 400 and "12 digits" in bad.json()["error"]
    created = client.post("/api/architecture/diagrams", json={"name": "x"}).json()
    body = client.get(f"/api/architecture/diagrams/{created['id']}").json()["body"]
    assert body["scope"] == {"account": "123456789012", "region": "us-east-1"}  # The app's current scope.


# ---------------------------------------------------------------- M2 / M3: instance identity and instance role

def test_m2_unknown_instance_in_aws_mode_is_never_guessed(monkeypatch):
    instance_identity.reset()
    monkeypatch.setenv("APP_MODE", "aws")
    monkeypatch.setattr(ec2, "get_current_instance", lambda: (_ for _ in ()).throw(OSError("no metadata")))
    assert instance_identity.app_resource_key() is None  # Not "this computer".
    calls = []
    monkeypatch.setattr(ec2, "get_current_instance", lambda: calls.append(1) or {"instance_id": INSTANCE})
    assert instance_identity.app_resource_key() is None and calls == []  # Not asked again immediately ...
    monkeypatch.setattr(instance_identity, "RETRY_SECONDS", 0)
    assert instance_identity.app_resource_key() == APP  # ... but retried later.


def test_m2_local_mode_uses_this_computer(monkeypatch):
    instance_identity.reset()
    monkeypatch.setattr(ec2, "get_current_instance", lambda: (_ for _ in ()).throw(OSError("no metadata")))
    assert instance_identity.app_resource_key() == "external:this-computer"


def test_m3_instance_role_needs_the_session_to_be_this_instance(on_ec2):
    role = f"Role{uuid.uuid4().hex[:6]}"
    row = {"service": "AWS IAM / STS", "action": "Identify credentials", "status": "Connected",
           "detail": f"identity arn:aws:sts::123456789012:assumed-role/{role}/i-0fff9999aaaa00001."}
    evidence.record_environment_check([row], run_id=1)
    assert latest(catalog.operation_key(f"iam:{role}", "sts:AssumeRole", APP)) is None  # Another instance.
    row["detail"] = f"identity arn:aws:sts::123456789012:assumed-role/{role}/{INSTANCE}."
    evidence.record_environment_check([row], run_id=2)
    assert latest(catalog.operation_key(f"iam:{role}", "sts:AssumeRole", APP))["outcome"] == "pass"


def test_m8_region_mismatch_is_partial_not_a_failed_instance(on_ec2):
    evidence.record_environment_check([{"service": "Amazon EC2", "action": "Region match", "status": "Wrong Region",
                                        "detail": "x"}], run_id=f"r{uuid.uuid4().hex[:6]}")
    assert latest(APP)["outcome"] == "partial"


# ---------------------------------------------------------------- M4: no need to open workflow pages

def test_m4_map_records_workflow_evidence_without_opening_the_workflow(aws, on_ec2, monkeypatch):  # noqa: F811
    monkeypatch.setattr(architecture_routes, "SYNC_INTERVAL_SECONDS", 0)
    workflow_id, key = new_workflow()
    invoke(s3_event(key))  # Nobody opens the workflow page afterwards.
    client = login()
    diagram = client.post("/api/architecture/diagrams", json={"name": "x", "body": {
        "nodes": [{"id": "b", "type": "s3", "identifier": "lab-workflow-bucket"},
                  {"id": "f", "type": "lambda", "identifier": "aws-lab-workflow-processor"}],
        "edges": [{"id": "e", "source": "b", "target": "f", "operation": "s3:ObjectCreated"}]}}).json()
    result = client.get(f"/api/architecture/diagrams/{diagram['id']}/evidence").json()
    assert result["edges"]["e"]["colour"] == "green"
    assert store.get_workflow_context(workflow_id)["synced_final"] == 1  # Final: not synced again.


# ---------------------------------------------------------------- M7 / L1 / L2: API protections and performance

def test_m7_global_verify_now_limit(fake_aws, monkeypatch):
    monkeypatch.setattr(architecture_verify, "GLOBAL_RATE_LIMIT", 1)
    bucket = f"lab-docs-{uuid.uuid4().hex[:8]}"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
    body = {"nodes": [{"id": "b", "type": "s3", "identifier": bucket}], "edges": []}
    first, second = login(), login()  # Two different usernames: the per-user limit is not reached.
    first_id = first.post("/api/architecture/diagrams", json={"name": "a", "body": body}).json()["id"]
    second_id = second.post("/api/architecture/diagrams", json={"name": "b", "body": body}).json()["id"]
    first.post(f"/api/architecture/diagrams/{first_id}/verify", json={"element_id": "b"})
    blocked = second.post(f"/api/architecture/diagrams/{second_id}/verify", json={"element_id": "b"})
    assert blocked.status_code == 429


@pytest.mark.parametrize("headers, allowed", [
    ({}, True),  # Tools such as curl (still need the login cookie and JSON).
    ({"Origin": "http://testserver"}, True),  # This app's own page.
    ({"Referer": "http://testserver/architecture"}, True),
    ({"Origin": "https://evil.example"}, False),
    ({"Referer": "https://evil.example/page"}, False),
    ({"Origin": "null"}, False),  # Sandboxed or file:// pages.
])
def test_l1_same_origin_check(headers, allowed):
    client = login()
    response = client.post("/api/architecture/diagrams", json={"name": "x"}, headers=headers)
    assert (response.status_code == 201) is allowed
    if not allowed:
        assert response.status_code == 403
    diagram_id = client.post("/api/architecture/diagrams", json={"name": "y"}).json()["id"]
    assert (client.delete(f"/api/architecture/diagrams/{diagram_id}", headers=headers).status_code == 200) is allowed


def test_l2_evidence_is_read_with_one_query(monkeypatch):
    statements = []
    real_connect = store.connect

    def traced():
        connection = real_connect()
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(store, "connect", traced)
    nodes = [{"id": f"n{i}", "type": "s3", "identifier": f"bucket-{i:03d}"} for i in range(60)]
    evidence.evaluate_diagram({"nodes": nodes, "edges": []}, "system")
    assert len([s for s in statements if "FROM evidence" in s and "subject_key IN" in s]) == 1


# ---------------------------------------------------------------- H3 / M5 / M6 / L3: the page script

def test_page_script_guards():
    script = (Path(__file__).resolve().parent.parent / "static" / "architecture.js").read_text(encoding="utf-8")
    assert "if (automatic && (state.dirty || typingInPanel()))" in script  # H3: no refresh over unsaved edits.
    assert "if (!state.dirty) { cy.elements().forEach" in script  # H3: unsaved boxes stay "save to check".
    assert "children.move({ parent: node.data(\"parent\") || null })" in script and "window.confirm" in script  # M5
    assert "var moved = node.move(" in script and "showDetails(current)" in script  # M6
    assert "setTimeout(function () { URL.revokeObjectURL(url); }" in script  # L3


# ---------------------------------------------------------------- schema safety and the explicit migration

def make_v1_file(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE diagrams (id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, name TEXT NOT NULL,
            version INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_key TEXT NOT NULL,
            subject_kind TEXT NOT NULL, check_id TEXT NOT NULL, outcome TEXT NOT NULL, status TEXT NOT NULL,
            detail TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL, reference TEXT, notes TEXT);
        INSERT INTO evidence (subject_key, subject_kind, check_id, outcome, status, detail, source, observed_at,
            recorded_at, recorded_by, notes) VALUES ('s3:old', 'resource', 'manual', 'pass', 'Verified', 'd',
            'manual', '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00', 'alice', 'my old note');
    """)
    connection.commit()
    connection.close()


def test_an_older_architecture_file_is_never_changed_automatically(tmp_path, monkeypatch):
    path = tmp_path / "architecture.db"
    make_v1_file(path)
    before = path.read_bytes()
    monkeypatch.setenv("ARCHITECTURE_DATABASE_PATH", str(path))
    with pytest.raises(store.SchemaMismatch, match="migrate_architecture_db.py"):
        store.create_tables()
    assert path.read_bytes() == before


def test_explicit_migration_keeps_every_record_and_makes_a_backup(tmp_path, monkeypatch):
    path = tmp_path / "architecture.db"
    make_v1_file(path)
    before = path.read_bytes()
    assert migrate_architecture_db.migrate(path, apply=False) == 0 and path.read_bytes() == before  # Dry run.
    assert migrate_architecture_db.migrate(path, apply=True) == 0
    backups = list(tmp_path.glob("architecture.db.v1-backup-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before  # Rollback = copy the backup back.
    monkeypatch.setenv("ARCHITECTURE_DATABASE_PATH", str(path))
    store.create_tables()  # The app now starts normally.
    kept = store.evidence_for(["s3:old"], "alice")["s3:old"]
    assert kept[0]["notes"] == "my old note" and kept[0]["account"] is None  # Old record kept, account unknown.
    assert migrate_architecture_db.migrate(path, apply=True) == 0  # Running again changes nothing.
    shutil.copy2(backups[0], path)  # The documented rollback.
    with pytest.raises(store.SchemaMismatch):
        store.create_tables()


def test_a_new_file_is_created_at_the_current_version(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHITECTURE_DATABASE_PATH", str(tmp_path / "new.db"))
    store.create_tables()
    connection = sqlite3.connect(tmp_path / "new.db")
    assert store.file_version(connection) == store.SCHEMA_VERSION
    connection.close()
