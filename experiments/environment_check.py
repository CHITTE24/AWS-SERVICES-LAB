"""The "Check AWS environment" button: shows where the app runs and which AWS access works.

This is kept separate from the submission workflow, so a missing permission here never
affects form submissions. Each check returns activity rows in the same format as
experiments/submission_workflow.py, and a failure in one check does not stop the others.
"""

import logging  # Records failures in logs/app.log.

from core.aws_errors import friendly_error_message  # Readable error text.
from core.config import get_settings  # Reads which resources are configured.
from core.secret_store import get_secret_values  # Secrets Manager.
from experiments.submission_workflow import activity  # Builds one activity row.
from integrations import cloudwatch, ec2, sts  # The existing read-only integrations.
from repositories import submission_repository  # Database connection test.

logger = logging.getLogger(__name__)


def check_identity():
    # STS shows WHICH identity the app uses. On EC2 this should be the instance's IAM role.
    identity = sts.get_caller_identity()
    if ":assumed-role/" in identity["arn"]:
        detail = f"Using IAM role credentials (temporary, no access keys): {identity['arn']}"
    else:
        detail = f"Using {identity['arn']}. On EC2 this should be an assumed IAM role instead."
    return [activity("AWS IAM / STS", "Identify credentials", "Success", detail)]


def check_ec2():
    # Asks the EC2 metadata service which instance this is, then reads its details with the EC2 API.
    try:
        instance = ec2.get_current_instance()
    except Exception:
        return [activity("Amazon EC2", "Instance metadata (IMDSv2)", "Skipped",
                         "The metadata service did not answer, so this app is not running on EC2 (for example, your PC).")]
    rows = [activity("Amazon EC2", "Instance metadata (IMDSv2)", "Success",
                     f"Running on {instance['instance_id']} ({instance['instance_type']}, "
                     f"{instance['availability_zone']}).")]
    # The existing ec2.list_instances() needs the ec2:DescribeInstances permission.
    try:
        matches = [item for item in ec2.list_instances() if item["instance_id"] == instance["instance_id"]]
        name = matches[0]["name"] if matches else "(not found)"
        rows.append(activity("Amazon EC2", "Describe this instance", "Success", f"Name tag: {name or '(none)'}"))
    except Exception as error:
        rows.append(activity("Amazon EC2", "Describe this instance", "Failed", friendly_error_message(error)))
    return rows


def check_secret():
    # Proves the secret can be read WITHOUT showing any value. Only the number of keys is shown.
    if not get_settings()["secret_name"]:
        return [activity("AWS Secrets Manager", "Read secret", "Not Configured",
                         "AWS_SECRETS_MANAGER_SECRET_NAME is not set.")]
    values = get_secret_values()
    return [activity("AWS Secrets Manager", "Read secret", "Verified",
                     f"The secret was read successfully ({len(values)} keys; values are never displayed).")]


def check_database():
    # Opens a real connection. For RDS it also reports whether TLS is actually in use.
    return [activity(submission_repository.backend_label(), "Test connection", "Success",
                     submission_repository.check_connection())]


def check_lambda_metrics():
    # Reads 24 hours of CloudWatch metrics for the submission Lambda (needs cloudwatch:GetMetricStatistics).
    function_name = get_settings()["lambda_function_name"]
    if not function_name:
        return [activity("Amazon CloudWatch", "Lambda metrics (24 h)", "Skipped", "LAMBDA_FUNCTION_NAME is not set.")]
    invocations = cloudwatch.get_metric_sum("AWS/Lambda", "Invocations", "FunctionName", function_name)
    errors = cloudwatch.get_metric_sum("AWS/Lambda", "Errors", "FunctionName", function_name)
    return [activity("Amazon CloudWatch", "Lambda metrics (24 h)", "Success",
                     f"{invocations:g} invocations, {errors:g} errors. "
                     f"Function logs: CloudWatch Logs group /aws/lambda/{function_name}.")]


# The checks, in the order they appear on the page: (service name, action, function).
CHECKS = [
    ("AWS IAM / STS", "Identify credentials", check_identity),
    ("Amazon EC2", "Instance metadata (IMDSv2)", check_ec2),
    ("AWS Secrets Manager", "Read secret", check_secret),
    ("Database", "Test connection", check_database),
    ("Amazon CloudWatch", "Lambda metrics (24 h)", check_lambda_metrics),
]


def run():
    """Runs every check and returns all activity rows."""
    rows = []
    for service, action, check in CHECKS:
        try:
            rows.extend(check())
        except Exception as error:
            # One failing check (for example a missing permission) must not hide the others.
            logger.warning("Environment check %s failed: %s", action, error)
            if service == "Database":
                message = submission_repository.describe_database_error(error)
            else:
                message = friendly_error_message(error)
            rows.append(activity(service, action, "Failed", message))
    return rows
