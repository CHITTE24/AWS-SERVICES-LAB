"""Verify Now: STRICTLY READ-ONLY checks of one resource box on the Architecture Map.

Each check only reads metadata (for example HeadBucket or DescribeKey). Nothing is created, changed,
uploaded, sent, or invoked, and no secret value is read. The result is stored as RESOURCE evidence, so it
can colour a box, but never an arrow: "the bucket is reachable" does not prove "uploads work".

Resources that are not in .env may answer Access Denied, because the health-check policy only allows the
configured resources. That is reported honestly, never as Connected.
"""

import logging

from catalog import architecture_catalog as catalog
from core import instance_identity  # The account and region of this check.
from core.aws_client import CHECK_CONFIG, get_client  # Short timeouts.
from core.aws_errors import ACCESS_DENIED, CONFIGURATION_ERROR, CONNECTED, UNAVAILABLE, WRONG_REGION, classify_error
from core.config import get_settings
from core.secret_store import describe_secret  # Metadata only, never the value.
from experiments import architecture_evidence as evidence
from experiments import workflow_states as clock
from experiments.live_workflow import safe_error_detail  # Short, safe error text (code only).
from integrations import cloudwatch, ec2, kms, lambda_functions, messaging, s3, ses, sts
from repositories import architecture_repository as store

logger = logging.getLogger(__name__)

RATE_LIMIT = 20  # Verify Now clicks per user ...
GLOBAL_RATE_LIMIT = 60  # ... and for ALL users together (the demo login accepts any username) ...
RATE_WINDOW_SECONDS = 600  # ... per 10 minutes.


def check_s3(identifier):
    region = s3.head_bucket(identifier, config=CHECK_CONFIG)
    if region and region != get_settings()["aws_region"]:
        return WRONG_REGION, f"HeadBucket answered: the bucket exists but is in {region}, not AWS_REGION."
    return CONNECTED, "HeadBucket answered: the bucket exists and this app may access it. No objects were read."


def check_kms(identifier):
    key = kms.describe_key(identifier, config=CHECK_CONFIG)
    if key["state"] != "Enabled":
        return UNAVAILABLE, f"DescribeKey answered: the key exists but its state is {key['state']}."
    return CONNECTED, "DescribeKey answered: the key exists and is enabled. Nothing was encrypted."


def check_lambda(identifier):
    info = lambda_functions.get_function_configuration(identifier, config=CHECK_CONFIG)
    if info["state"] != "Active":
        return UNAVAILABLE, f"The function exists but its state is {info['state']}."
    return CONNECTED, f"The function exists and is Active ({info['runtime']}). It was NOT invoked."


def check_dynamodb(identifier):
    table = get_client("dynamodb", config=CHECK_CONFIG).describe_table(TableName=identifier)["Table"]
    if table["TableStatus"] != "ACTIVE":
        return UNAVAILABLE, f"The table exists but its status is {table['TableStatus']}."
    return CONNECTED, "DescribeTable answered: the table exists and is ACTIVE. No items were read."


def check_secret(identifier):
    info = describe_secret(identifier, config=CHECK_CONFIG)
    if info["scheduled_for_deletion"]:
        return UNAVAILABLE, "The secret exists but is scheduled for deletion."
    return CONNECTED, "DescribeSecret answered (metadata only). The secret's value was NOT read."


def check_sns(identifier):
    account = sts.get_caller_identity(config=CHECK_CONFIG)["account"]
    topic_arn = f"arn:aws:sns:{get_settings()['aws_region']}:{account}:{identifier}"
    info = messaging.get_topic_attributes(topic_arn, config=CHECK_CONFIG)
    return CONNECTED, f"The topic exists ({info['subscriptions']} confirmed subscription(s)). Nothing was published."


def check_sqs(identifier):
    queue_url = get_client("sqs", config=CHECK_CONFIG).get_queue_url(QueueName=identifier)["QueueUrl"]
    messaging.get_queue_attributes(queue_url, config=CHECK_CONFIG)
    return CONNECTED, "The queue exists. Nothing was sent or received."


def check_ec2(identifier):
    info = ec2.describe_instance(identifier, config=CHECK_CONFIG)
    return CONNECTED, f"DescribeInstances answered: the instance exists (state {info['state']})."


def check_ses(identifier):
    if identifier != get_settings()["aws_region"]:
        return CONFIGURATION_ERROR, "Verify Now checks SES only in AWS_REGION."
    account = ses.get_account(config=CHECK_CONFIG)
    if not account["sending_enabled"]:
        return UNAVAILABLE, "SES sending is disabled for this account in this region."
    mode = "production access" if account["production_access"] else "sandbox"
    return CONNECTED, f"SES account status read ({mode}). No email was sent."


def check_log_group(identifier):
    if identifier == "metrics":
        raise LookupError("not supported")
    latest = cloudwatch.get_latest_log_event_time(identifier, config=CHECK_CONFIG)
    when = f"last log event {latest:%Y-%m-%d %H:%M} UTC" if latest else "no log events yet"
    return CONNECTED, f"The log group exists ({when}). No log content was read."


# Resource type -> its read-only check. Types not listed have no Verify Now (for example RDS, IAM, VPC).
CHECKS = {"s3": check_s3, "kms": check_kms, "lambda": check_lambda, "dynamodb": check_dynamodb,
          "secretsmanager": check_secret, "sns": check_sns, "sqs": check_sqs, "ec2": check_ec2, "ses": check_ses,
          "cloudwatch": check_log_group}


def supported(node):
    return node["type"] in CHECKS and bool(node["identifier"]) and node["identifier"] != "metrics"


def limit_reached(username):
    """True if this user, or everyone together, used Verify Now too often in the last 10 minutes."""
    since = clock.to_text(clock.add_seconds(clock.utc_now(), -RATE_WINDOW_SECONDS))
    return store.count_recent(username, "verify_now.", since) >= RATE_LIMIT or         store.count_recent(None, "verify_now.", since) >= GLOBAL_RATE_LIMIT


def verify_now(node, username):
    """Runs the read-only check for one box and appends the result as resource evidence. Returns the result."""
    if not catalog.valid_identifier(node["type"], node["identifier"]):  # Stored diagrams are valid; double-check.
        raise ValueError("Invalid identifier.")
    try:
        status, detail = CHECKS[node["type"]](node["identifier"])
    except Exception as error:
        logger.warning("Verify Now for %s failed (%s).", node["type"], type(error).__name__)
        status, detail = classify_error(error), safe_error_detail(error)
        if status == ACCESS_DENIED:
            detail += (" The app's role may only be allowed to check the resources configured in .env "
                       "(the health-check policy, deploy/iam-ec2-healthcheck-policy.json).")
    key = catalog.resource_key(node["type"], node["identifier"])
    outcome = evidence.outcome_of(status)
    # Recorded with the account and region the check really used. If the account is unknown, the record is
    # UNATTRIBUTED (kept, but never shown on a box). A unique reference per click keeps every check in history.
    evidence.record(key, "resource", f"verify_now.{node['type']}", status, detail,
                    f"verify-now:{clock.to_text(clock.utc_now())}", instance_identity.scope(), recorded_by=username)
    return {"status": status, "detail": detail, "outcome": outcome}
