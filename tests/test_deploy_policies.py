"""Checks the IAM policy files in deploy/ WITHOUT contacting AWS.

- Every deploy/*.json file is valid JSON (no invalid escapes, no trailing commas).
- No access keys or real-looking account IDs were committed.
- The health-check policy is read-only, uses correct action names and ARN formats, limits every
  action to one resource where AWS allows it, and matches exactly the API calls the code makes.

For an extra check by AWS itself (free, read-only), run on a computer with AWS credentials:
    aws accessanalyzer validate-policy --policy-type IDENTITY_POLICY \\
        --policy-document file://deploy/iam-ec2-healthcheck-policy.json
"""

import json  # Reads the policy files.
import re  # Checks ARN formats.
from pathlib import Path  # Finds the files.

import botocore.session  # Botocore's built-in AWS API descriptions (offline, no credentials needed).
import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
HEALTHCHECK = DEPLOY / "iam-ec2-healthcheck-policy.json"
POLICY_FILES = sorted(DEPLOY.glob("*.json"))

# Every action in the health-check policy -> (Boto3 service, API operation the app calls, Python file that calls it).
# s3:ListBucket is the permission AWS checks for the HeadBucket API call.
EXPECTED_ACTIONS = {
    "s3:ListBucket": ("s3", "HeadBucket", "integrations/s3.py"),
    "kms:DescribeKey": ("kms", "DescribeKey", "integrations/kms.py"),
    "secretsmanager:DescribeSecret": ("secretsmanager", "DescribeSecret", "core/secret_store.py"),
    "lambda:GetFunctionConfiguration": ("lambda", "GetFunctionConfiguration", "integrations/lambda_functions.py"),
    "logs:DescribeLogStreams": ("logs", "DescribeLogStreams", "integrations/cloudwatch.py"),
    "sns:GetTopicAttributes": ("sns", "GetTopicAttributes", "integrations/messaging.py"),
    "sqs:GetQueueAttributes": ("sqs", "GetQueueAttributes", "integrations/messaging.py"),
    "ec2:DescribeInstances": ("ec2", "DescribeInstances", "integrations/ec2.py"),
    "cloudwatch:GetMetricStatistics": ("cloudwatch", "GetMetricStatistics", "integrations/cloudwatch.py"),
    "ses:GetAccount": ("sesv2", "GetAccount", "integrations/ses.py"),
}

# AWS does not support resource-level permissions for these, so "*" is the minimum possible.
STAR_ONLY_ACTIONS = {"ec2:DescribeInstances", "cloudwatch:GetMetricStatistics", "ses:GetAccount"}

# The expected ARN shape for each action's resource. YOUR_... placeholders are allowed.
NAME = r"[A-Za-z0-9_.-]+"
ARN_PATTERNS = {
    "s3:ListBucket": rf"arn:aws:s3:::{NAME}",  # The bucket itself, not its objects.
    "kms:DescribeKey": rf"arn:aws:kms:{NAME}:{NAME}:key/{NAME}",
    "secretsmanager:DescribeSecret": rf"arn:aws:secretsmanager:{NAME}:{NAME}:secret:{NAME}-\?{{6}}",
    "lambda:GetFunctionConfiguration": rf"arn:aws:lambda:{NAME}:{NAME}:function:{NAME}",
    "logs:DescribeLogStreams": rf"arn:aws:logs:{NAME}:{NAME}:log-group:/aws/lambda/{NAME}(:\*)?",
    "sns:GetTopicAttributes": rf"arn:aws:sns:{NAME}:{NAME}:{NAME}",
    "sqs:GetQueueAttributes": rf"arn:aws:sqs:{NAME}:{NAME}:{NAME}",
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def as_list(value):
    # IAM allows a single string or a list for Action and Resource.
    return value if isinstance(value, list) else [value]


def healthcheck_statements():
    return load(HEALTHCHECK)["Statement"]


@pytest.mark.parametrize("path", POLICY_FILES, ids=lambda path: path.name)
def test_every_policy_file_is_valid_json_with_unique_sids(path):
    policy = load(path)  # Fails on invalid escapes, comments, or trailing commas.
    sids = [statement["Sid"] for statement in policy["Statement"]]
    assert len(sids) == len(set(sids)), "Each Sid must be unique within a policy"
    for statement in policy["Statement"]:
        assert statement["Effect"] in ("Allow", "Deny")
        for action in as_list(statement["Action"]):
            assert re.fullmatch(r"[a-z0-9-]+:[A-Za-z0-9*]+", action), action


@pytest.mark.parametrize("path", POLICY_FILES, ids=lambda path: path.name)
def test_no_credentials_or_real_account_ids(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"(AKIA|ASIA)[0-9A-Z]{16}", text), "Looks like an AWS access key"
    # 123456789012 is AWS's documentation example. Any other 12-digit number may be a real account ID.
    assert set(re.findall(r"\b\d{12}\b", text)) <= {"123456789012"}


def test_healthcheck_policy_has_exactly_the_needed_actions():
    actions = set()
    for statement in healthcheck_statements():
        assert statement["Effect"] == "Allow"
        actions.update(as_list(statement["Action"]))
    assert actions == set(EXPECTED_ACTIONS)


def test_healthcheck_policy_is_read_only():
    for statement in healthcheck_statements():
        for action in as_list(statement["Action"]):
            verb = action.split(":")[1]
            assert verb.startswith(("Describe", "Get", "List")), f"{action} is not a read-only action"
            assert "*" not in action, "No wildcards in action names"


@pytest.mark.parametrize("action", sorted(EXPECTED_ACTIONS))
def test_action_names_match_real_aws_api_operations(action):
    service, operation, source_file = EXPECTED_ACTIONS[action]
    # The operation must exist in botocore's description of that AWS API ...
    model = botocore.session.get_session().get_service_model(service)
    assert operation in model.operation_names
    # ... and the app must really call it (Boto3 uses snake_case: HeadBucket -> head_bucket).
    snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", operation).lower()
    source = (DEPLOY.parent / source_file).read_text(encoding="utf-8")
    assert f".{snake_case}(" in source, f"{source_file} does not call {snake_case}"


def test_resources_are_as_narrow_as_aws_allows():
    for statement in healthcheck_statements():
        actions = as_list(statement["Action"])
        resources = as_list(statement["Resource"])
        if resources == ["*"]:
            # Only for actions without resource-level support, and then limited to one region.
            assert set(actions) <= STAR_ONLY_ACTIONS, f"{actions} should name a specific resource"
            assert "aws:RequestedRegion" in statement["Condition"]["StringEquals"]
            continue
        for action in actions:
            for resource in resources:
                assert re.fullmatch(ARN_PATTERNS[action], resource), f"{action}: bad ARN {resource}"
