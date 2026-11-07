"""Tests for the workflow store (repositories/workflow_repository.py and workflow_dynamodb.py).

Most tests run TWICE: once with the local SQLite store and once with DynamoDB (faked by moto), to prove
both stores enforce the same rules: idempotent creation, forward-only changes, stale retries ignored.
"""

import threading  # Several writers at the same time.
import uuid  # A different owner name for every test.
from datetime import datetime, timezone

import boto3
import pytest

from experiments import live_workflow
from experiments import workflow_states as rules
from repositories import workflow_repository

T0 = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)
DOCUMENT = {"name": "notes.txt", "size": 11, "content": b"hello world", "content_type": "text/plain"}


@pytest.fixture(params=["sqlite", "dynamodb"])
def store(request, monkeypatch):
    # Runs the test once per store. The DynamoDB run uses a fake table inside moto.
    if request.param == "dynamodb":
        request.getfixturevalue("workflow_table")
    else:
        monkeypatch.setenv("WORKFLOW_BACKEND", "sqlite")
    return request.param


# All tests share one SQLite file, so each test uses its own owner name to see only its own workflows.
OWNER = {"name": "student"}


@pytest.fixture(autouse=True)
def unique_owner():
    OWNER["name"] = f"student-{uuid.uuid4().hex[:8]}"


def new_workflow(mode="aws"):
    """Creates a workflow the way the app does. Returns it (with its stages)."""
    workflow_id = live_workflow.new_workflow_id()
    meta = live_workflow.build_meta(workflow_id, OWNER["name"], mode, "success" if mode == "simulated" else None,
                                    DOCUMENT, f"workflows/2026/11/08/{workflow_id}/notes.txt", T0)
    assert workflow_repository.create_workflow(meta, live_workflow.build_stages(mode, T0))
    return workflow_repository.get_workflow(workflow_id)


def stage(workflow, stage_id):
    fresh = workflow_repository.get_workflow(workflow["workflow_id"])
    return next(item for item in fresh["stages"] if item["stage_id"] == stage_id)


def change(workflow, stage_id, state, attempt=None, source=rules.SOURCE_LAMBDA, seconds=1):
    return workflow_repository.set_stage(workflow, stage_id, state, f"{state} test", source, attempt=attempt,
                                         now=rules.add_seconds(T0, seconds))


@pytest.fixture(autouse=True)
def lambda_name(monkeypatch):
    # With a Lambda name, the event and lambda stages start as Pending (they wait for Lambda).
    monkeypatch.setenv("WORKFLOW_LAMBDA_NAME", "lab-fn")


def test_creation_is_idempotent(store):
    workflow = new_workflow()
    assert workflow["store"] == store
    assert [item["stage_id"] for item in workflow["stages"]] == [stage_id for stage_id, _, _ in rules.STAGES]
    meta = live_workflow.build_meta(workflow["workflow_id"], OWNER["name"], "aws", None, DOCUMENT, "k", T0)
    # The same workflow_id again (double click, repeated request): refused, nothing duplicated.
    assert workflow_repository.create_workflow(meta, live_workflow.build_stages("aws", T0)) is False
    assert len(workflow_repository.list_workflows(OWNER["name"])) == 1


def test_forward_only_changes_and_duplicates_ignored(store):
    workflow = new_workflow()
    assert change(workflow, "event", rules.RUNNING, attempt=1)
    assert change(workflow, "event", rules.COMPLETED, attempt=1, seconds=2)
    # A duplicate S3 event or a late writer cannot undo or repeat a final result.
    assert not change(workflow, "event", rules.COMPLETED, attempt=1, seconds=3)
    assert not change(workflow, "event", rules.RUNNING, attempt=1, seconds=3)
    assert not change(workflow, "event", rules.FAILED, attempt=1, seconds=3)
    result = stage(workflow, "event")
    assert result["state"] == rules.COMPLETED
    assert result["started_at"] == rules.to_text(rules.add_seconds(T0, 1))
    assert result["finished_at"] == rules.to_text(rules.add_seconds(T0, 2))


def test_retries_and_stale_attempts(store):
    workflow = new_workflow()
    assert change(workflow, "lambda", rules.RUNNING, attempt=1)
    assert change(workflow, "lambda", rules.RETRYING, attempt=1, seconds=2)
    assert change(workflow, "lambda", rules.RUNNING, attempt=2, seconds=3)
    # A delayed write from attempt 1 arrives after attempt 2 started: ignored.
    assert not change(workflow, "lambda", rules.RETRYING, attempt=1, seconds=4)
    assert not change(workflow, "lambda", rules.FAILED, attempt=1, seconds=4)
    assert change(workflow, "lambda", rules.COMPLETED, attempt=2, seconds=5)
    result = stage(workflow, "lambda")
    assert (result["state"], result["attempt"]) == (rules.COMPLETED, 2)
    assert result["started_at"] == rules.to_text(rules.add_seconds(T0, 1))  # The FIRST start is kept.


def test_late_result_after_timeout_still_counts(store):
    workflow = new_workflow()
    assert change(workflow, "event", rules.TIMED_OUT, source=rules.SOURCE_FASTAPI)
    assert change(workflow, "event", rules.COMPLETED, attempt=1, seconds=200)
    assert stage(workflow, "event")["state"] == rules.COMPLETED


def test_a_result_wins_over_a_timeout_written_afterwards(store):
    # The page's timeout check runs a moment AFTER Lambda finished: the timeout must be refused.
    workflow = new_workflow()
    assert change(workflow, "event", rules.COMPLETED, attempt=1)
    assert not change(workflow, "event", rules.TIMED_OUT, source=rules.SOURCE_FASTAPI, seconds=2)
    assert stage(workflow, "event")["state"] == rules.COMPLETED


def test_concurrent_writers_only_one_wins(monkeypatch):
    # Eight writers try to finish the same stage at the same moment. Exactly one may succeed.
    monkeypatch.setenv("WORKFLOW_BACKEND", "sqlite")
    workflow = new_workflow()
    results = []

    def writer(number):
        state = rules.COMPLETED if number % 2 else rules.FAILED
        results.append(change(workflow, "event", state, attempt=1))

    threads = [threading.Thread(target=writer, args=(number,)) for number in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1


def test_simulated_workflows_never_touch_dynamodb(workflow_table):
    workflow = new_workflow(mode="simulated")
    assert workflow["store"] == "sqlite"  # Even though WORKFLOW_BACKEND=dynamodb.
    items = boto3.client("dynamodb", region_name="us-east-1").scan(TableName=workflow_table)["Items"]
    assert items == []


def test_dynamodb_items_have_ttl_and_no_owner(workflow_table):
    workflow = new_workflow()
    items = boto3.client("dynamodb", region_name="us-east-1").scan(TableName=workflow_table)["Items"]
    assert len(items) == 7  # META + 6 stages.
    for item in items:
        assert "expires_at_epoch" in item  # DynamoDB deletes it automatically after the retention period.
        assert "owner" not in item  # The username stays in the local index only.
    meta = next(item for item in items if item["item"]["S"] == "META")
    assert meta["s3_key"]["S"].startswith(f"workflows/2026/11/08/{workflow['workflow_id']}/")


def test_expired_dynamodb_workflow_stays_listed(workflow_table):
    workflow = new_workflow()
    client = boto3.client("dynamodb", region_name="us-east-1")
    for item in client.scan(TableName=workflow_table)["Items"]:  # What Time To Live does after 7 days.
        client.delete_item(TableName=workflow_table, Key={"workflow_id": item["workflow_id"], "item": item["item"]})
    expired = workflow_repository.get_workflow(workflow["workflow_id"])
    assert expired["stages"] == []
    assert rules.overall_state(expired["stages"]) == rules.EXPIRED
    assert workflow_repository.list_workflows(OWNER["name"])[0]["workflow_id"] == workflow["workflow_id"]


def test_count_real_workflows(store):
    new_workflow()
    new_workflow(mode="simulated")
    assert workflow_repository.count_real_workflows_since(OWNER["name"], rules.to_text(rules.add_seconds(T0, -1))) == 1
    assert workflow_repository.count_real_workflows_since("someone-else", rules.to_text(T0)) == 0


def test_unknown_backend_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("WORKFLOW_BACKEND", "mysql")
    with pytest.raises(ValueError, match="WORKFLOW_BACKEND"):
        workflow_repository.choose_store("aws")
