"""Experiment: Account Overview. Combines STS, IAM, S3, and EC2 into one snapshot of the account."""

from experiments.steps import count_or_unknown, run_step  # Shared experiment helpers.
from integrations import ec2, iam, s3, sts  # The AWS services this experiment uses.


def run(region=None):
    """Builds the account snapshot.

    region: the region to count EC2 instances in. Empty means the default region.
    Returns: an experiment result dictionary (see experiments/steps.py).
    """
    errors = []

    # Each call runs on its own. If one fails, the rest still produce results.
    identity = run_step(errors, "STS identity", sts.get_caller_identity)
    users = run_step(errors, "IAM users", iam.list_users)
    roles = run_step(errors, "IAM roles", iam.list_roles)
    buckets = run_step(errors, "S3 buckets", s3.list_buckets)
    instances = run_step(errors, "EC2 instances", ec2.list_instances, region)

    # Counts how many instances are currently running (those are the ones that cost money).
    running_count = "unknown"
    if instances is not None:
        running_count = 0
        for instance in instances:
            if instance["state"] == "running":
                running_count = running_count + 1

    summary = {
        "Account ID": identity["account"] if identity else "unknown",
        "Identity ARN": identity["arn"] if identity else "unknown",
        "IAM users": count_or_unknown(users),
        "IAM roles": count_or_unknown(roles),
        "S3 buckets": count_or_unknown(buckets),
        "EC2 instances (all states)": count_or_unknown(instances),
        "EC2 instances running": running_count,
    }

    return {
        "summary": summary,
        "tables": [
            {"title": "EC2 instances", "rows": instances or []},
            {"title": "S3 buckets", "rows": buckets or []},
        ],
        "notes": ["IAM and S3 are global. EC2 numbers cover only the selected region."],
        "errors": errors,
    }
