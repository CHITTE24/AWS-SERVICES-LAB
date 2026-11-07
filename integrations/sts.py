"""AWS STS (Security Token Service): tells you which AWS identity the app is using."""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def get_caller_identity(config=None):
    """Asks AWS who the current credentials belong to.

    This call needs no IAM permission and costs nothing, so it is the best first test of credentials.
    config: Boto3 timeouts and retries (empty = the normal settings).
    Returns: a dictionary with the account ID, the identity ARN, and the user ID.
    """
    # Creates an STS client using the configured AWS credentials.
    sts = get_client("sts", config=config)

    # Asks AWS which account and identity these credentials belong to.
    identity = sts.get_caller_identity()

    # Keeps only the useful fields and gives them readable names.
    return {
        "account": identity["Account"],
        "arn": identity["Arn"],
        "user_id": identity["UserId"],
    }
