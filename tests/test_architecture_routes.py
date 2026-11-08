"""Tests for the Architecture Map pages and API (routes/architecture_routes.py)."""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from experiments import architecture_verify
from integrations import s3
from main import app
from repositories import architecture_repository as store

API = "/api/architecture/diagrams"
INSTANCE = "i-0abc1234def567890"
RAW_AWS_MESSAGE = "User: arn:aws:sts::123456789012:assumed-role/internal-role/i-1 is not authorized"


def login(username=None):
    client = TestClient(app)
    client.post("/login", data={"username": username or f"user-{uuid.uuid4().hex[:8]}", "password": "x"})
    return client


@pytest.fixture
def browser():
    return login()


def node(node_id, node_type, identifier="", label=""):
    return {"id": node_id, "type": node_type, "identifier": identifier, "label": label, "parent": None, "x": 10, "y": 20}


def create(browser, body=None, name="Test", template=None):
    payload = {"name": name, "body": body or {"nodes": [], "edges": []}}
    if template:
        payload["template"] = template
    response = browser.post(API, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def bucket_diagram(bucket):
    return {"nodes": [node("app", "ec2", INSTANCE), node("bucket", "s3", bucket), node("db", "rds", "db.example.com")],
            "edges": [{"id": "put", "source": "app", "target": "bucket", "operation": "s3:PutObject", "label": ""}]}


# ---------------------------------------------------------------- login, ownership, privacy

def test_every_endpoint_needs_a_login():
    anonymous = TestClient(app)
    for method, url in (("get", "/architecture"), ("get", "/api/architecture/catalog"), ("get", API),
                        ("post", API), ("get", f"{API}/1"), ("put", f"{API}/1"), ("delete", f"{API}/1"),
                        ("get", f"{API}/1/evidence"), ("post", f"{API}/1/manual"), ("post", f"{API}/1/verify")):
        response = getattr(anonymous, method)(url, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/login", url


def test_diagrams_are_private_to_their_owner(browser):
    diagram_id = create(browser, bucket_diagram("lab-docs-private"))
    stranger = login()
    assert stranger.get(API).json()["diagrams"] == []
    for method, url in (("get", f"{API}/{diagram_id}"), ("get", f"{API}/{diagram_id}/evidence"),
                        ("delete", f"{API}/{diagram_id}")):
        assert getattr(stranger, method)(url).status_code == 404
    assert stranger.put(f"{API}/{diagram_id}", json={"name": "x", "version": 1, "body": {}}).status_code == 404
    assert stranger.post(f"{API}/{diagram_id}/manual", json={}).status_code == 404
    assert stranger.post(f"{API}/{diagram_id}/verify", json={}).status_code == 404
    assert browser.get(f"{API}/{diagram_id}").status_code == 200  # The owner still has it.


def test_responses_are_never_cached(browser):
    diagram_id = create(browser)
    for url in ("/architecture", "/api/architecture/catalog", API, f"{API}/{diagram_id}", f"{API}/{diagram_id}/evidence"):
        assert browser.get(url).headers["cache-control"] == "no-store", url


# ---------------------------------------------------------------- saving and validation

def test_save_load_and_version_conflict(browser):
    diagram_id = create(browser, bucket_diagram("lab-docs-version"))
    loaded = browser.get(f"{API}/{diagram_id}").json()
    assert loaded["version"] == 1 and len(loaded["body"]["nodes"]) == 3
    body = loaded["body"]
    body["nodes"][0]["label"] = "Web server"
    saved = browser.put(f"{API}/{diagram_id}", json={"name": "Renamed", "version": 1, "body": body})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = browser.put(f"{API}/{diagram_id}", json={"name": "Old tab", "version": 1, "body": body})
    assert stale.status_code == 409  # Another tab saved first: nothing is silently overwritten.
    assert browser.get(f"{API}/{diagram_id}").json()["name"] == "Renamed"


@pytest.mark.parametrize("body, message", [
    ({"nodes": [node("a", "nonsense")], "edges": []}, "unknown type"),
    ({"nodes": [node("a", "s3", "Not_A_Bucket")], "edges": []}, "not a valid"),
    ({"nodes": [node("a", "lambda", "fn"), node("b", "dynamodb", "table1")],
      "edges": [{"id": "e", "source": "a", "target": "b", "operation": "s3:PutObject"}]}, "not a valid operation"),
])
def test_invalid_diagrams_are_refused(browser, body, message):
    diagram_id = create(browser)
    response = browser.put(f"{API}/{diagram_id}", json={"name": "x", "version": 1, "body": body})
    assert response.status_code == 400 and message in response.json()["error"]


def test_request_format_and_size_limits(browser):
    diagram_id = create(browser)
    as_form = browser.put(f"{API}/{diagram_id}", data={"name": "x"})
    assert as_form.status_code == 415  # Only JSON: a form on another website cannot call the API.
    broken = browser.put(f"{API}/{diagram_id}", content=b"{not json", headers={"Content-Type": "application/json"})
    assert broken.status_code == 400
    huge = {"name": "x", "version": 1, "body": {"nodes": [], "edges": [], "padding": "x" * 300_000}}
    assert browser.put(f"{API}/{diagram_id}", json=huge).status_code == 413
    infinite = browser.put(f"{API}/{diagram_id}", content=json.dumps(
        {"name": "x", "version": 1, "body": {"nodes": [dict(node("a", "s3"), x=float("inf"))], "edges": []}}),
        headers={"Content-Type": "application/json"})
    assert infinite.status_code == 400


def test_labels_are_stored_as_plain_text(browser):
    script = "<script>alert(1)</script>"
    diagram_id = create(browser, {"nodes": [node("a", "s3", "lab-docs-xss", script[:60])], "edges": []})
    assert browser.get(f"{API}/{diagram_id}").json()["body"]["nodes"][0]["label"] == script
    assert script not in browser.get("/architecture").text  # Labels are only ever inserted as text by the script.


def test_deleting_a_drawing_keeps_the_evidence():
    owner = f"user-{uuid.uuid4().hex[:8]}"
    browser = login(owner)
    bucket = f"lab-docs-{uuid.uuid4().hex[:8]}"
    diagram_id = create(browser, bucket_diagram(bucket))
    browser.post(f"{API}/{diagram_id}/manual", json={"element_id": "bucket", "outcome": "pass", "notes": "Seen in console."})
    assert browser.delete(f"{API}/{diagram_id}").status_code == 200
    assert browser.get(f"{API}/{diagram_id}").status_code == 404  # The drawing is gone ...
    kept = store.evidence_for([f"s3:{bucket}"], owner)[f"s3:{bucket}"]
    assert len(kept) == 1 and kept[0]["notes"] == "Seen in console."  # ... the evidence history is not.
    assert store.evidence_for([f"s3:{bucket}"], "someone-else")[f"s3:{bucket}"] == []  # Manual records stay private.


def test_starter_template(browser, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-docs-starter")
    diagram_id = create(browser, template="starter")
    body = browser.get(f"{API}/{diagram_id}").json()["body"]
    assert {n["identifier"] for n in body["nodes"]} == {"this-computer", "lab-docs-starter"}
    assert {e["operation"] for e in body["edges"]} == {"s3:PutObject", "s3:HeadObject"}
    evidence = browser.get(f"{API}/{diagram_id}/evidence").json()
    assert all(edge["colour"] == "gray" for edge in evidence["edges"].values())  # Drawn, not verified.


# ---------------------------------------------------------------- manual records

def test_manual_record_is_marked_and_requires_notes(browser):
    bucket = f"lab-docs-{uuid.uuid4().hex[:8]}"
    diagram_id = create(browser, bucket_diagram(bucket))
    url = f"{API}/{diagram_id}/manual"
    assert browser.post(url, json={"element_id": "put", "outcome": "pass", "notes": " "}).status_code == 400
    assert browser.post(url, json={"element_id": "put", "outcome": "maybe", "notes": "x"}).status_code == 400
    assert browser.post(url, json={"element_id": "put", "outcome": "pass", "notes": "x",
                                   "observed_at": "2999-01-01T00:00:00Z"}).status_code == 400  # Future.
    assert browser.post(url, json={"element_id": "put", "outcome": "pass", "notes": "x",
                                   "observed_at": "yesterday"}).status_code == 400
    assert browser.post(url, data={"x": "y"}).status_code == 415
    an_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    good = browser.post(url, json={"element_id": "put", "outcome": "pass", "observed_at": an_hour_ago,
                                   "notes": "Uploaded a test file with the AWS CLI from the instance."})
    assert good.status_code == 201
    arrow = browser.get(f"{API}/{diagram_id}/evidence").json()["edges"]["put"]
    assert arrow["basis"] == "manual" and arrow["latest"]["source"] == "manual"  # Never shown as automatic.
    assert "AWS CLI" in arrow["latest"]["notes"]


def test_manual_record_needs_identified_boxes(browser):
    diagram_id = create(browser, {"nodes": [node("a", "ec2", ""), node("b", "s3", "lab-docs-noid")],
                                  "edges": [{"id": "e", "source": "a", "target": "b", "operation": "s3:PutObject"}]})
    response = browser.post(f"{API}/{diagram_id}/manual", json={"element_id": "e", "outcome": "pass", "notes": "x"})
    assert response.status_code == 400 and "identifier" in response.json()["error"]


# ---------------------------------------------------------------- Verify Now (read-only)

def test_verify_now_checks_a_box_but_never_proves_an_arrow(browser, fake_aws):
    bucket = f"lab-docs-{uuid.uuid4().hex[:8]}"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
    diagram_id = create(browser, bucket_diagram(bucket))
    result = browser.post(f"{API}/{diagram_id}/verify", json={"element_id": "bucket"})
    assert result.status_code == 200 and result.json()["status"] == "Connected"
    evidence = browser.get(f"{API}/{diagram_id}/evidence").json()
    assert evidence["nodes"]["bucket"]["colour"] == "green"
    assert evidence["edges"]["put"]["colour"] == "gray"  # HeadBucket does not prove PutObject.
    assert browser.post(f"{API}/{diagram_id}/verify", json={"element_id": "put"}).status_code == 400  # Arrows: no.
    assert browser.post(f"{API}/{diagram_id}/verify", json={"element_id": "db"}).status_code == 400  # RDS: no check.


def test_verify_now_reports_errors_safely(browser, monkeypatch):
    def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": RAW_AWS_MESSAGE},
                           "ResponseMetadata": {"HTTPStatusCode": 403}}, "HeadBucket")
    monkeypatch.setattr(s3, "head_bucket", denied)
    diagram_id = create(browser, bucket_diagram(f"lab-docs-{uuid.uuid4().hex[:8]}"))
    result = browser.post(f"{API}/{diagram_id}/verify", json={"element_id": "bucket"}).json()
    assert result["status"] == "Access Denied" and "health-check policy" in result["detail"]
    assert RAW_AWS_MESSAGE not in json.dumps(result)
    assert browser.get(f"{API}/{diagram_id}/evidence").json()["nodes"]["bucket"]["colour"] == "red"


def test_verify_now_is_rate_limited(browser, monkeypatch, fake_aws):
    monkeypatch.setattr(architecture_verify, "RATE_LIMIT", 1)
    bucket = f"lab-docs-{uuid.uuid4().hex[:8]}"
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
    diagram_id = create(browser, bucket_diagram(bucket))
    assert browser.post(f"{API}/{diagram_id}/verify", json={"element_id": "bucket"}).status_code == 200
    assert browser.post(f"{API}/{diagram_id}/verify", json={"element_id": "bucket"}).status_code == 429


def test_verify_now_code_is_read_only():
    source = Path(architecture_verify.__file__).read_text(encoding="utf-8")
    calls = set(re.findall(r"\.([a-z_]+)\(", source))
    writes = {call for call in calls if re.match(r"(put|delete|create|update|invoke|publish|send|upload|"
                                                  r"get_secret_value|tag|attach|modify|start|stop|terminate)", call)}
    assert writes == set(), writes


# ---------------------------------------------------------------- the page and existing features

def test_design_and_presentation_modes(browser):
    design = browser.get("/architecture").text
    assert 'data-mode="design"' in design and 'id="btn-save"' in design and 'id="palette-list"' in design
    present = browser.get("/architecture?mode=present").text
    assert 'data-mode="present"' in present and 'id="btn-save"' not in present and 'id="palette-list"' not in present
    assert "Architecture" in browser.get("/").text  # The menu link.


def test_catalog_lists_types_operations_and_falls_back_to_labelled_boxes(browser):
    catalog = browser.get("/api/architecture/catalog").json()
    assert {"ec2", "s3", "lambda", "sns", "sqs", "rds", "dynamodb", "cloudwatch", "iam", "kms", "secretsmanager",
            "apigateway", "alb", "vpc"} <= set(catalog["types"])
    assert catalog["types"]["s3"]["verify_now"] is True and catalog["types"]["rds"]["verify_now"] is False
    icons_present = (Path("static") / "icons" / "aws" / "s3.svg").is_file()
    assert (catalog["types"]["s3"]["icon"] is not None) == icons_present
    assert catalog["operations"]["s3:PutObject"]["automatic"] is True


def test_architecture_data_uses_its_own_database_file(browser):
    from core.config import get_settings
    settings = get_settings()
    assert settings["architecture_database_path"] != settings["database_path"]
    import sqlite3
    tables = {row[0] for row in sqlite3.connect(settings["database_path"]).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "evidence" not in tables and "diagrams" not in tables  # data/lab.db is untouched.
