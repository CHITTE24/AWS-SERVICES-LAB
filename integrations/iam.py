"""AWS IAM (Identity and Access Management): read-only users, roles, and account limits.

IAM is a global service, so the region does not matter for these calls.
"""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def list_users():
    """Lists IAM users. Returns a list of dictionaries with name, ARN, and creation date."""
    iam = get_client("iam")
    # A paginator reads every page of results, because AWS returns at most 100 users per call.
    paginator = iam.get_paginator("list_users")

    users = []
    for page in paginator.paginate():
        for user in page["Users"]:
            users.append({
                "user_name": user["UserName"],
                "arn": user["Arn"],
                "created": str(user["CreateDate"]),
            })
    return users


def list_roles():
    """Lists IAM roles. Returns a list of dictionaries with name, ARN, and description."""
    iam = get_client("iam")
    paginator = iam.get_paginator("list_roles")

    roles = []
    for page in paginator.paginate():
        for role in page["Roles"]:
            roles.append({
                "role_name": role["RoleName"],
                "arn": role["Arn"],
                "description": role.get("Description", ""),
            })
    return roles


def get_account_summary():
    """Returns counts and limits for the account's IAM entities (users, roles, policies, MFA, ...)."""
    iam = get_client("iam")
    response = iam.get_account_summary()

    # SummaryMap holds many numbers. Keep the ones a beginner will find meaningful.
    summary = response["SummaryMap"]
    wanted_keys = ["Users", "Groups", "Roles", "Policies", "MFADevices", "AccountMFAEnabled"]

    result = {}
    for key in wanted_keys:
        result[key] = summary.get(key, 0)
    return result
