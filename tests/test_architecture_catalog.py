"""Tests for the Architecture Map catalog and diagram validation (no AWS, no database)."""

import pytest

from catalog import architecture_catalog as catalog
from core import instance_identity
from experiments import architecture_diagram as diagrams

KEY_ARN = "arn:aws:kms:us-east-1:123456789012:key/1234abcd-12ab-34cd-56ef-1234567890ab"


def node(node_id, node_type, identifier="", label="", parent=None, x=0, y=0):
    return {"id": node_id, "type": node_type, "identifier": identifier, "label": label, "parent": parent, "x": x, "y": y}


def edge(edge_id, source, target, operation, label=""):
    return {"id": edge_id, "source": source, "target": target, "operation": operation, "label": label}


@pytest.mark.parametrize("resource_type, identifier, valid", [
    ("ec2", "i-0abc1234def567890", True), ("ec2", "i-XYZ", False), ("ec2", "web-server", False),
    ("s3", "lab-docs-ab12", True), ("s3", "Lab_Docs", False), ("s3", "ab", False),
    ("lambda", "aws-lab-workflow-processor", True), ("lambda", "has space", False),
    ("kms", "1234abcd-12ab-34cd-56ef-1234567890ab", True), ("kms", "alias/lab", False),
    ("ses", "ap-south-1", True), ("ses", "mumbai", False),
    ("vpc", "vpc-0abc1234", True), ("vpc", "my-vpc", False),
    ("cloudwatch", "/aws/lambda/fn", True), ("cloudwatch", "metrics", True),
    ("s3", "", True),  # An unidentified box is allowed (it can never match evidence).
])
def test_identifier_formats(resource_type, identifier, valid):
    assert catalog.valid_identifier(resource_type, identifier) is valid


def test_keys_are_exact_identities():
    assert catalog.resource_key("s3", "lab-docs") == "s3:lab-docs"
    assert catalog.resource_key("s3", "") is None
    assert catalog.operation_key("ec2:i-1", "s3:PutObject", "s3:b") == "ec2:i-1|s3:PutObject|s3:b"
    assert catalog.operation_key(None, "s3:PutObject", "s3:b") is None


def test_every_operation_says_what_it_proves_and_its_iam_requirement():
    for operation_id, spec in catalog.OPERATIONS.items():
        assert spec["label"] and spec["iam"] and spec["how"] and spec["not_proves"], operation_id
        assert set(spec["sources"]) <= set(catalog.RESOURCE_TYPES) and set(spec["targets"]) <= set(catalog.RESOURCE_TYPES)
        if spec["automatic"]:
            assert spec["proves"], operation_id


def test_valid_diagram_is_cleaned_and_identifiers_normalised():
    body = diagrams.validate_diagram({
        "nodes": [node("vpc1", "vpc", "vpc-0abc1234"), node("web", "ec2", "i-0abc1234def567890", "  Web  ", "vpc1", 10.26, 5),
                  node("key", "kms", KEY_ARN), node("topic", "sns", "arn:aws:sns:us-east-1:123456789012:lab-topic")],
        "edges": [edge("e1", "web", "key", "kms:GenerateDataKey")]})
    by_id = {item["id"]: item for item in body["nodes"]}
    assert by_id["web"]["label"] == "Web" and by_id["web"]["x"] == 10.3 and by_id["web"]["parent"] == "vpc1"
    assert by_id["key"]["identifier"] == "1234abcd-12ab-34cd-56ef-1234567890ab"  # ARN -> key ID.
    assert by_id["topic"]["identifier"] == "lab-topic"
    assert body["edges"][0]["label"] == "Encrypt via S3 (SSE-KMS)"  # The operation's label by default.


@pytest.mark.parametrize("data, message", [
    ({"nodes": "x"}, "list of nodes"),
    ({"nodes": [node("a", "nonsense")]}, "unknown type"),
    ({"nodes": [node("a", "s3", "Bad_Bucket")]}, "not a valid Amazon S3 identifier"),
    ({"nodes": [node("a", "s3"), node("a", "s3")]}, "used twice"),
    ({"nodes": [node("bad id!", "s3")]}, "invalid"),
    ({"nodes": [node("a", "s3", label="x" * 61)]}, "at most 60"),
    ({"nodes": [node("a", "s3", label="bad\x00text")]}, "control characters"),
    ({"nodes": [node("a", "s3", x=float("inf"))]}, "invalid position"),
    ({"nodes": [node("a", "s3", x=True)]}, "invalid position"),
    ({"nodes": [node("a", "s3"), node("b", "s3", parent="a")]}, "VPC or subnet"),
    ({"nodes": [node("v", "vpc", parent="w"), node("w", "vpc", parent="v")]}, "loop"),
    ({"nodes": [node("a", "s3")], "edges": [edge("e", "a", "a", "custom")]}, "two different"),
    ({"nodes": [node("a", "s3"), node("b", "lambda")], "edges": [edge("e", "a", "missing", "custom")]}, "two different"),
    ({"nodes": [node("a", "lambda"), node("b", "dynamodb")], "edges": [edge("e", "a", "b", "s3:PutObject")]},
     "not a valid operation"),
    ({"nodes": [node("a", "s3"), node("b", "lambda")], "edges": [edge("e", "a", "b", "s3:DeleteBucket")]},
     "not a valid operation"),
    ({"nodes": [node(f"n{i}", "s3") for i in range(catalog.MAX_NODES + 1)]}, "At most"),
])
def test_invalid_diagrams_are_rejected(data, message):
    with pytest.raises(ValueError, match=message):
        diagrams.validate_diagram(data)


def test_multiple_and_branching_arrows_are_allowed():
    body = diagrams.validate_diagram({
        "nodes": [node("app", "ec2", "i-0abc1234def567890"), node("b1", "s3", "bucket-one"), node("b2", "s3", "bucket-two")],
        "edges": [edge("put1", "app", "b1", "s3:PutObject"), edge("head1", "app", "b1", "s3:HeadObject"),
                  edge("put2", "app", "b2", "s3:PutObject")]})
    assert len(body["edges"]) == 3


def test_starter_diagram_only_uses_configured_resources(monkeypatch):
    # Nothing configured: just the app itself (this computer), and no invented connections.
    body = diagrams.starter_diagram()
    assert [n["identifier"] for n in body["nodes"]] == ["this-computer"] and body["edges"] == []

    monkeypatch.setenv("S3_BUCKET_NAME", "lab-docs-ab12")
    monkeypatch.setenv("S3_KMS_KEY_ARN", KEY_ARN)
    monkeypatch.setenv("WORKFLOW_BACKEND", "dynamodb")
    monkeypatch.setenv("WORKFLOW_LAMBDA_NAME", "aws-lab-workflow-processor")
    instance_identity._cache["key"] = "ec2:i-0abc1234def567890"
    body = diagrams.starter_diagram()
    keys = {catalog.resource_key(n["type"], n["identifier"]) for n in body["nodes"]}
    assert {"ec2:i-0abc1234def567890", "s3:lab-docs-ab12", "kms:1234abcd-12ab-34cd-56ef-1234567890ab",
            "dynamodb:aws-lab-workflows", "lambda:aws-lab-workflow-processor"} <= keys
    operations = {e["operation"] for e in body["edges"]}
    assert {"s3:PutObject", "s3:ObjectCreated", "dynamodb:UpdateItem"} <= operations
    assert "sns:Publish" not in operations  # SNS is not configured, so it is not drawn.
