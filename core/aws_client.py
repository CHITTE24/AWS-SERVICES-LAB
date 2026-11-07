"""The single place where the app creates Boto3 clients (how the app "logs in" to AWS).

The app never stores AWS access keys. Boto3 finds credentials automatically, in this order:
  1. Environment variables (AWS_ACCESS_KEY_ID, ...), which are used by tests
  2. The profile named in AWS_PROFILE, or the default profile from "aws configure"
  3. The IAM role attached to the EC2 instance, when running on EC2
"""

import boto3  # The AWS SDK for Python.
from botocore.config import Config  # Lets us set timeouts and retry behaviour.

from core.config import get_settings  # Reads the default region and the write-safety switch.

# Shared client settings: retry briefly on throttling, and give up quickly if AWS is unreachable.
CLIENT_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "standard"},  # Retry temporary errors up to 3 times.
    connect_timeout=5,  # Seconds to wait while connecting to AWS.
    read_timeout=20,  # Seconds to wait for AWS to answer.
)


# Used ONLY by the "Check AWS environment" button. A health check should answer quickly:
# one retry at most and short timeouts, so one unreachable service cannot freeze the page for a minute.
CHECK_CONFIG = Config(
    retries={"max_attempts": 2, "mode": "standard"},  # The first try plus one retry.
    connect_timeout=3,  # Seconds to wait while connecting to AWS.
    read_timeout=5,  # Seconds to wait for AWS to answer.
)


def get_client(service_name, region=None, config=None):
    """Creates a Boto3 client for one AWS service.

    service_name: the Boto3 name of the service, for example "s3", "ec2", or "iam".
    region: an AWS region such as "eu-west-1". If empty, the default region from settings is used.
    config: timeouts and retries. Empty means CLIENT_CONFIG. The environment check passes CHECK_CONFIG.
    Returns: a Boto3 client object whose methods call the AWS API.
    """
    # Falls back to the configured default region when the caller did not choose one.
    if not region:
        region = get_settings()["aws_region"]
    if config is None:
        config = CLIENT_CONFIG

    # Creates the client. Boto3 finds the credentials automatically (see the top of this file).
    return boto3.client(service_name, region_name=region, config=config)


def ensure_writes_allowed():
    """Stops any operation that could create, change, or delete AWS resources.

    Call this before every "write" operation. It raises PermissionError unless
    ALLOW_WRITE_OPERATIONS=true is set in .env. This is the app's cost-safety switch.
    """
    if not get_settings()["allow_write_operations"]:
        raise PermissionError(
            "Write operations are disabled. Set ALLOW_WRITE_OPERATIONS=true in .env "
            "only if you understand the AWS costs involved."
        )
