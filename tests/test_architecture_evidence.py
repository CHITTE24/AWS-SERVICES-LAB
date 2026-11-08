"""Tests for Architecture Map evidence and conclusions (experiments/architecture_evidence.py).

The key promises: no colour without evidence, exact identities only, a resource check never proves an
operation, manual records are always marked MANUAL, nothing is ever overwritten, and recording evidence never
breaks the feature that produced it. Real producers run against moto only.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import boto3
import pytest

from catalog import architecture_catalog as catalog
from core import instance_identity
from experiments import architecture_evidence as evidence
from experiments import live_workflow
from experiments import workflow_states as clock
from repositories import architecture_repository as store
from tests.test_workflow_processor import aws, invoke, new_workflow, s3_event, stub_head_object_errors  # noqa: F401

NOW = clock.utc_now()
INSTANCE = "i-0abc1234def567890"


def unique(prefix="b"):
    # Every test uses its own resource names, because the evidence history is shared and never deleted.
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def add(key, outcome="pass", status="Connected", source="automatic", hours_ago=0.0, by="system", ref=None,
        notes=None, kind="resource"):
    store.add_evidence(key, kind, "test", outcome, status, f"{status} detail", source=source,
                       observed_at=clock.to_text(NOW - timedelta(hours=hours_ago)), recorded_by=by, reference=ref,
                       notes=notes)


def conclusion(key, viewer="system"):
    return evidence.conclude(store.evidence_for([key], viewer)[key], NOW, 24)


def diagram(*nodes, edges=()):
    return {"nodes": [{"id": n[0], "type": n[1], "identifier": n[2], "label": "", "parent": None, "x": 0, "y": 0}
                      for n in nodes],
            "edges": [{"id": e[0], "source": e[1], "target": e[2], "operation": e[3], "label": ""} for e in edges]}


# ---------------------------------------------------------------- conclusions

def test_no_evidence_is_grey():
    result = conclusion(f"s3:{unique()}")
    assert (result["colour"], result["basis"], result["label"]) == ("gray", "none", "Not verified")


@pytest.mark.parametrize("outcome, colour, label", [("pass", "green", "Verified"), ("fail", "red", "Failed"),
                                                    ("partial", "yellow", "Partial")])
def test_automatic_evidence_colours(outcome, colour, label):
    key = f"s3:{unique()}"
    add(key, outcome=outcome)
    result = conclusion(key)
    assert (result["colour"], result["label"], result["basis"]) == (colour, label, "automatic")


def test_stale_evidence_is_never_shown_as_current():
    key = f"s3:{unique()}"
    add(key, hours_ago=25)
    result = conclusion(key)
    assert result["colour"] == "yellow" and result["stale"] and result["label"] == "Stale (last: Verified)"


def test_manual_records_keep_their_colour_but_are_marked_and_private():
    key = f"s3:{unique()}"
    add(key, source="manual", by="alice", notes="Checked the bucket policy in the console.")
    mine = conclusion(key, viewer="alice")
    assert (mine["colour"], mine["basis"]) == ("green", "manual")
    assert mine["latest"]["recorded_by"] == "alice" and "console" in mine["latest"]["notes"]
    assert conclusion(key, viewer="bob")["basis"] == "none"  # Another user never sees Alice's manual records.


def test_automatic_evidence_decides_over_manual():
    key = f"s3:{unique()}"
    add(key, outcome="fail", status="Access Denied")
    add(key, outcome="pass", source="manual", by="alice", notes="I think it works.")
    result = conclusion(key, viewer="alice")
    assert result["colour"] == "red" and result["basis"] == "automatic"
    assert "MANUAL record disagrees" in result["note"]


def test_simulated_evidence_is_never_green():
    key = f"s3:{unique()}"
    add(key, source="simulated")
    assert conclusion(key)["colour"] == "gray" and conclusion(key)["label"] == "SIMULATED"


def test_history_is_append_only_and_keeps_failures_and_recovery():
    key = f"s3:{unique()}"
    add(key, outcome="fail", status="Access Denied", ref="run:1")
    add(key, outcome="pass", status="Connected", ref="run:2")
    result = conclusion(key)
    assert result["colour"] == "green"
    assert [item["status"] for item in result["history"]] == ["Connected", "Access Denied"]  # Failure still there.
    for forbidden in ("update_evidence", "delete_evidence", "clear_evidence"):
        assert not hasattr(store, forbidden)  # The repository cannot change or remove evidence.


def test_same_result_for_the_same_reference_is_not_duplicated_but_changes_are_kept():
    key = f"lambda:{unique()}"
    assert store.add_evidence(key, "resource", "workflow.x", "partial", "Retrying", "d", reference="workflow:1")
    assert not store.add_evidence(key, "resource", "workflow.x", "partial", "Retrying", "d", reference="workflow:1")
    assert store.add_evidence(key, "resource", "workflow.x", "pass", "Completed", "d", reference="workflow:1")
    assert [item["status"] for item in store.evidence_for([key], "system")[key]] == ["Completed", "Retrying"]


# ---------------------------------------------------------------- exact identities

def test_a_resource_check_never_proves_an_operation():
    bucket = unique()
    add(f"s3:{bucket}")  # HeadBucket succeeded.
    body = diagram(("app", "ec2", INSTANCE), ("bucket", "s3", bucket), edges=[("put", "app", "bucket", "s3:PutObject")])
    result = evidence.evaluate_diagram(body, "system", NOW)
    assert result["nodes"]["bucket"]["colour"] == "green"  # The BOX is verified ...
    arrow = result["edges"]["put"]
    assert arrow["colour"] == "gray"  # ... but the ARROW (PutObject) is not.
    assert {item["box"]: item["colour"] for item in arrow["context"]}["bucket"] == "green"  # Shown as context only.
    assert "Upload" not in arrow["proves"] or "accepted an upload" in arrow["proves"]


def test_evidence_matches_exact_resources_and_operations_only():
    bucket_a, bucket_b, other_instance = unique(), unique(), "i-0fff1234def567890"
    app = f"ec2:{INSTANCE}"
    add(catalog.operation_key(app, "s3:PutObject", f"s3:{bucket_a}"), kind="operation", status="Completed")
    body = diagram(("app", "ec2", INSTANCE), ("other", "ec2", other_instance), ("a", "s3", bucket_a), ("b", "s3", bucket_b),
                   edges=[("put-a", "app", "a", "s3:PutObject"), ("put-b", "app", "b", "s3:PutObject"),
                          ("head-a", "app", "a", "s3:HeadObject"), ("other-a", "other", "a", "s3:PutObject")])
    edges = evidence.evaluate_diagram(body, "system", NOW)["edges"]
    assert edges["put-a"]["colour"] == "green"
    assert edges["put-b"]["colour"] == "gray"  # Another bucket.
    assert edges["head-a"]["colour"] == "gray"  # Another operation on the same bucket.
    assert edges["other-a"]["colour"] == "gray"  # Another EC2 instance.


def test_unidentified_boxes_and_design_only_arrows_explain_themselves():
    body = diagram(("lb", "alb", "lab-alb"), ("app", "ec2", ""), edges=[("fwd", "lb", "app", "elb:ForwardRequest")])
    result = evidence.evaluate_diagram(body, "system", NOW)
    assert "identifier" in result["nodes"]["app"]["note"]
    assert result["edges"]["fwd"]["colour"] == "gray" and "identifier" in result["edges"]["fwd"]["note"]
    body = diagram(("lb", "alb", "lab-alb"), ("app", "ec2", INSTANCE), edges=[("fwd", "lb", "app", "elb:ForwardRequest")])
    assert "Design only" in evidence.evaluate_diagram(body, "system", NOW)["edges"]["fwd"]["note"]


# ---------------------------------------------------------------- recorders (real producers, moto only)

def test_environment_check_rows_become_resource_and_operation_evidence(monkeypatch):
    bucket, secret = unique(), unique("lab/")
    monkeypatch.setenv("S3_BUCKET_NAME", bucket)
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", secret)
    instance_identity._cache["key"] = f"ec2:{INSTANCE}"
    rows = [
        {"service": "AWS IAM / STS", "action": "Identify credentials", "status": "Connected",
         "detail": f"Account 123456789012, identity arn:aws:sts::123456789012:assumed-role/LabRole/{INSTANCE}. ..."},
        {"service": "Amazon S3", "action": "HeadBucket (exists + access)", "status": "Access Denied",
         "detail": "Permission denied. AWS said: User arn:aws:sts::123456789012:assumed-role/internal is not authorized"},
        {"service": "AWS Secrets Manager", "action": "Retrieve value (permission)", "status": "Connected",
         "detail": "A fresh read succeeded (2 keys)."},
        {"service": "HTTPS", "action": "This browser connection", "status": "Not Configured", "detail": "-"},
    ]
    evidence.record_environment_check(rows, run_id=42)
    found = store.evidence_for([f"s3:{bucket}", "iam:LabRole",
                                catalog.operation_key("iam:LabRole", "sts:AssumeRole", f"ec2:{INSTANCE}"),
                                catalog.operation_key(f"ec2:{INSTANCE}", "secretsmanager:GetSecretValue",
                                                      f"secretsmanager:{secret}")], "system")
    assert found[f"s3:{bucket}"][0]["status"] == "Access Denied"
    assert "not authorized" not in found[f"s3:{bucket}"][0]["detail"]  # AWS's raw message is not stored.
    assert all(found[key] for key in found)  # Role, instance role, and secret read were all recorded.
    assert found["iam:LabRole"][0]["reference"] == "environment-check:42"


def test_live_workflow_records_exact_operation_evidence(aws):  # noqa: F811
    instance_identity._cache["key"] = f"ec2:{INSTANCE}"
    workflow_id, key = new_workflow()
    invoke(s3_event(key))
    live_workflow.refresh(workflow_id)  # The page's status check records what the Lambda function reported.
    app, bucket = f"ec2:{INSTANCE}", "s3:lab-workflow-bucket"
    function, table = "lambda:aws-lab-workflow-processor", "dynamodb:aws-lab-workflows"
    expected = [catalog.operation_key(app, "s3:PutObject", bucket), catalog.operation_key(app, "s3:HeadObject", bucket),
                catalog.operation_key(app, "kms:GenerateDataKey", f"kms:{catalog.kms_key_id(aws)}"),
                catalog.operation_key(app, "dynamodb:TransactWriteItems", table),
                catalog.operation_key(bucket, "s3:ObjectCreated", function),
                catalog.operation_key(function, "s3:HeadObject", bucket),
                catalog.operation_key(function, "dynamodb:UpdateItem", table)]
    found = store.evidence_for(expected, "system")
    for operation_key in expected:
        assert found[operation_key] and found[operation_key][0]["outcome"] == "pass", operation_key
        assert found[operation_key][0]["reference"] == f"workflow:{workflow_id}"


def test_retry_then_success_keeps_both_steps(aws, monkeypatch):  # noqa: F811
    workflow_id, key = new_workflow()
    stub_head_object_errors(monkeypatch, "SlowDown", 503)
    with pytest.raises(Exception):
        invoke(s3_event(key))
    live_workflow.refresh(workflow_id)
    monkeypatch.setattr("deploy.lambda_function.workflow_processor.s3_client",
                        lambda: boto3.client("s3", region_name="us-east-1"))
    invoke(s3_event(key))
    live_workflow.refresh(workflow_id)
    operation_key = catalog.operation_key("lambda:aws-lab-workflow-processor", "s3:HeadObject", "s3:lab-workflow-bucket")
    statuses = [item["status"] for item in store.evidence_for([operation_key], "system")[operation_key]
                if item["reference"] == f"workflow:{workflow_id}"]
    assert statuses == ["Success", "Retrying"]  # The recovery AND the earlier temporary failure.


def test_simulated_workflows_produce_no_evidence(monkeypatch):
    calls = []
    monkeypatch.setattr(store, "add_evidence", lambda *args, **kwargs: calls.append(args))
    evidence.record_workflow({"mode": "simulated", "workflow_id": "x", "stages": []})
    assert calls == []


def test_submission_evidence_contains_no_personal_data(monkeypatch):
    bucket = unique()
    monkeypatch.setenv("S3_BUCKET_NAME", bucket)
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:lab-topic")
    evidence.record_submission({"id": "f" * 32, "activity": [
        {"service": "Amazon S3", "action": "Upload document", "status": "Success",
         "detail": "S3 confirmed the upload of s3://x/submissions/2026/mary-smith-passport.pdf"},
        {"service": "Amazon SNS", "action": "Publish notification", "status": "Failed",
         "detail": "AWS returned AuthorizationError: mary@example.com is not allowed"},
        {"service": "Amazon SQS", "action": "Queue message", "status": "Skipped", "detail": "Optional"}]})
    put = catalog.operation_key("external:this-computer", "s3:PutObject", f"s3:{bucket}")
    publish = catalog.operation_key("external:this-computer", "sns:Publish", "sns:lab-topic")
    found = store.evidence_for([put, publish], "system")
    assert found[put][0]["outcome"] == "pass" and found[publish][0]["outcome"] == "fail"
    stored = str(found)
    assert "mary" not in stored and "passport" not in stored  # Only statuses and generic text.


def test_recording_problems_never_break_the_original_feature(logged_in_client, fake_aws, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("evidence database unavailable")
    monkeypatch.setattr(store, "add_evidence", broken)
    monkeypatch.setenv("S3_BUCKET_NAME", unique())
    response = logged_in_client.post("/environment-check")
    assert response.status_code == 200 and "Amazon S3" in response.text
