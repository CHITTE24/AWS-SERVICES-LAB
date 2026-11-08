"""Checks diagrams sent by the browser, and builds the optional starter diagram.

A diagram is plain data:
    {"nodes": [{"id": "n1", "type": "s3", "identifier": "lab-docs", "label": "Docs", "parent": null,
                "x": 100, "y": 50}],
     "edges": [{"id": "e1", "source": "n2", "target": "n1", "operation": "s3:PutObject", "label": "Upload Object"}]}
Everything is validated here before it is stored, so a stored diagram is always well-formed.
A diagram is only a DRAWING: saving it never creates, connects, or changes anything in AWS.
"""

import math  # Checks that positions are real numbers.
import re  # Checks the account and region format.

from catalog import architecture_catalog as catalog  # Types, operations, limits, identifier rules.
from core.config import get_settings  # The configured resources (starter diagram only).
from core.instance_identity import app_resource_key, scope  # Which machine and account this app uses.


def clean_text(value, limit, field):
    """A short, single-line text without control characters."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{field} must be at most {limit} characters.")
    if not catalog.PRINTABLE.fullmatch(value):
        raise ValueError(f"{field} contains control characters.")
    return value


def normalise_identifier(resource_type, identifier):
    """Accepts a pasted ARN or URL where people often have one, and keeps only the identifier part."""
    if resource_type == "kms":
        return catalog.kms_key_id(identifier)
    if resource_type in ("sns", "sqs", "lambda") and (identifier.startswith("arn:") or identifier.startswith("https://")):
        return catalog.name_from_arn_or_url(identifier)
    return identifier


def validate_diagram(data):
    """Returns a cleaned copy of the diagram, or raises ValueError with a clear message."""
    if not isinstance(data, dict) or not isinstance(data.get("nodes", []), list) or \
            not isinstance(data.get("edges", []), list):
        raise ValueError("The diagram must contain a list of nodes and a list of edges.")
    raw_nodes, raw_edges = data.get("nodes", []), data.get("edges", [])
    if len(raw_nodes) > catalog.MAX_NODES or len(raw_edges) > catalog.MAX_EDGES:
        raise ValueError(f"At most {catalog.MAX_NODES} boxes and {catalog.MAX_EDGES} arrows are allowed.")

    seen_ids = set()
    nodes = []
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise ValueError("Each box must be an object.")
        node_id = str(raw.get("id", ""))
        if not catalog.ELEMENT_ID.fullmatch(node_id) or node_id in seen_ids:
            raise ValueError(f"Box id '{node_id[:40]}' is missing, invalid, or used twice.")
        seen_ids.add(node_id)
        node_type = raw.get("type")
        if node_type not in catalog.RESOURCE_TYPES:
            raise ValueError(f"Box {node_id}: unknown type '{str(node_type)[:30]}'.")
        identifier = normalise_identifier(node_type, clean_text(raw.get("identifier"), 512, f"Box {node_id} identifier"))
        if not catalog.valid_identifier(node_type, identifier):
            raise ValueError(f"Box {node_id}: '{identifier[:60]}' is not a valid "
                             f"{catalog.RESOURCE_TYPES[node_type]['label']} identifier "
                             f"({catalog.RESOURCE_TYPES[node_type]['identifier']}).")
        position = []
        for axis in ("x", "y"):
            value = raw.get(axis, 0)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) \
                    or abs(value) > 1_000_000:
                raise ValueError(f"Box {node_id}: invalid position.")
            position.append(round(float(value), 1))
        nodes.append({"id": node_id, "type": node_type, "identifier": identifier,
                      "label": clean_text(raw.get("label"), catalog.MAX_LABEL, f"Box {node_id} label"),
                      "parent": raw.get("parent") or None, "x": position[0], "y": position[1]})

    by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        # A parent must be an existing VPC or subnet box, and boxes may not contain each other in a loop.
        parent, steps = node["parent"], 0
        if parent is not None:
            if parent not in by_id or not catalog.RESOURCE_TYPES[by_id[parent]["type"]].get("container"):
                raise ValueError(f"Box {node['id']}: its parent must be a VPC or subnet box.")
        while parent is not None:
            steps += 1
            if parent == node["id"] or steps > len(nodes):
                raise ValueError(f"Box {node['id']}: boxes cannot contain each other in a loop.")
            parent = by_id[parent]["parent"]

    edges = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise ValueError("Each arrow must be an object.")
        edge_id = str(raw.get("id", ""))
        if not catalog.ELEMENT_ID.fullmatch(edge_id) or edge_id in seen_ids:
            raise ValueError(f"Arrow id '{edge_id[:40]}' is missing, invalid, or used twice.")
        seen_ids.add(edge_id)
        source, target = by_id.get(raw.get("source")), by_id.get(raw.get("target"))
        if source is None or target is None or source is target:
            raise ValueError(f"Arrow {edge_id}: it must connect two different, existing boxes.")
        operation = raw.get("operation")
        if not catalog.operation_allowed(operation, source["type"], target["type"]):
            raise ValueError(f"Arrow {edge_id}: '{str(operation)[:40]}' is not a valid operation from "
                             f"{source['type']} to {target['type']}. Use 'custom' for a design-only arrow.")
        label = clean_text(raw.get("label"), catalog.MAX_LABEL, f"Arrow {edge_id} label")
        edges.append({"id": edge_id, "source": source["id"], "target": target["id"], "operation": operation,
                      "label": label or catalog.OPERATIONS[operation]["label"]})
    return {"nodes": nodes, "edges": edges, "scope": validate_scope(data.get("scope"))}


def validate_scope(raw):
    """The diagram's AWS account and region. Evidence from another known account or region never matches.

    Both may be empty (then a warning is shown, because evidence from any account could match).
    """
    raw = raw if isinstance(raw, dict) else {}
    account = clean_text(raw.get("account"), 12, "Account")
    region = clean_text(raw.get("region"), 30, "Region")
    if account and not re.fullmatch(r"\d{12}", account):
        raise ValueError("The AWS account must be 12 digits.")
    if region and not re.fullmatch(catalog.RESOURCE_TYPES["ses"]["pattern"], region):
        raise ValueError("The region must look like ap-south-1.")
    return {"account": account, "region": region}


def current_scope():
    """The account and region the app uses right now (the account is empty if it cannot be found)."""
    now = scope()
    return {"account": now["account"] or "", "region": now["region"] or ""}


# ---------------------------------------------------------------- the optional starter diagram

def starter_diagram():
    """A starting point showing the connections THIS app's code makes with the current .env settings.

    Only resources that are configured are included, and every arrow starts grey ("not verified"):
    drawing it proves nothing. It is a drawing only; nothing in AWS is created or changed.
    """
    settings = get_settings()
    # An unknown app identity (EC2 metadata unavailable) gives an unidentified box: nothing is guessed.
    app_type, app_identifier = (app_resource_key() or "external:").split(":", 1)
    nodes = [{"id": "app", "type": app_type, "identifier": app_identifier,
              "label": "Web app (FastAPI)" if app_identifier else "Web app (identity unknown)"}]
    edges = []

    def add_node(node_id, node_type, identifier, label):
        identifier = normalise_identifier(node_type, identifier)
        if identifier and catalog.valid_identifier(node_type, identifier):
            nodes.append({"id": node_id, "type": node_type, "identifier": identifier, "label": label})
            return True
        return False

    def add_edge(source, target, operation):
        if {source, target} <= {node["id"] for node in nodes}:
            edges.append({"id": f"{source}-{target}-{operation.split(':')[-1]}"[:40].replace(".", "-"),
                          "source": source, "target": target, "operation": operation, "label": ""})

    workflow_lambda = catalog.name_from_arn_or_url(settings["workflow_lambda_name"])
    submission_lambda = catalog.name_from_arn_or_url(settings["lambda_function_name"])
    add_node("bucket", "s3", settings["s3_bucket_name"], "Documents bucket")
    add_node("key", "kms", settings["s3_kms_key_arn"], "SSE-KMS key")
    if settings["workflow_backend"] == "dynamodb":
        add_node("table", "dynamodb", settings["workflow_table_name"], "Workflow table")
    add_node("workflow_fn", "lambda", workflow_lambda, "Workflow processor")
    add_node("submission_fn", "lambda", submission_lambda, "Submission processor")
    if submission_lambda:
        add_node("ses", "ses", settings["aws_region"], "Email (SES)")
    add_node("topic", "sns", catalog.name_from_arn_or_url(settings["sns_topic_arn"]), "Notifications")
    add_node("queue", "sqs", catalog.name_from_arn_or_url(settings["sqs_queue_url"]), "Queue")
    add_node("secret", "secretsmanager", settings["secret_name"], "Database password")
    if settings["database_backend"] == "postgres":
        add_node("database", "rds", settings["db_host"], "Database")
    if workflow_lambda:
        add_node("logs", "cloudwatch", f"/aws/lambda/{workflow_lambda}", "Workflow logs")

    add_edge("app", "bucket", "s3:PutObject")
    add_edge("app", "bucket", "s3:HeadObject")
    add_edge("app", "key", "kms:GenerateDataKey")
    add_edge("app", "table", "dynamodb:TransactWriteItems")
    add_edge("bucket", "workflow_fn", "s3:ObjectCreated")
    add_edge("workflow_fn", "bucket", "s3:HeadObject")
    add_edge("workflow_fn", "table", "dynamodb:UpdateItem")
    add_edge("workflow_fn", "logs", "logs:PutLogEvents")
    add_edge("app", "submission_fn", "lambda:InvokeFunction")
    add_edge("submission_fn", "ses", "ses:SendEmail")
    add_edge("app", "topic", "sns:Publish")
    add_edge("app", "queue", "sqs:SendMessage")
    add_edge("app", "secret", "secretsmanager:GetSecretValue")
    add_edge("app", "database", "rds:SqlQuery")
    # Positions 0: the page arranges the boxes automatically the first time it opens them.
    return validate_diagram({"nodes": [dict(node, x=0, y=0, parent=None) for node in nodes], "edges": edges,
                             "scope": current_scope()})
