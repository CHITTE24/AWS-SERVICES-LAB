"""Turns technical AWS and Python errors into short messages a person can act on.

friendly_error_message(error) -> readable text (used everywhere).
classify_error(error)         -> one of the connectivity STATUS words below (used by "Check AWS environment").
"""

import socket  # socket.timeout is raised by the EC2 metadata request when it times out.
import urllib.error  # URLError is raised when the EC2 metadata service cannot be reached.

# Error types raised by botocore (the library underneath Boto3).
from botocore.exceptions import (
    ClientError,  # AWS received the request but refused it (permissions, missing resource, ...).
    ConnectTimeoutError,  # Connecting to AWS took too long.
    CredentialRetrievalError,  # The IAM role's temporary credentials could not be fetched.
    EndpointConnectionError,  # The computer could not reach AWS (network or bad region).
    NoCredentialsError,  # Boto3 could not find any AWS credentials.
    NoRegionError,  # No AWS region was configured.
    ParamValidationError,  # A value in .env has the wrong format (checked before anything is sent).
    PartialCredentialsError,  # Only part of a set of credentials was found.
    ProfileNotFound,  # AWS_PROFILE names a profile that does not exist.
    ReadTimeoutError,  # AWS took too long to answer.
)

# ---- Connectivity status words shown by the "Check AWS environment" button ----
CONNECTED = "Connected"  # A real read-only AWS request proved the resource exists and is reachable.
NOT_CONFIGURED = "Not Configured"  # The setting is empty, so nothing was checked.
ACCESS_DENIED = "Access Denied"  # AWS refused the request: the IAM role lacks a permission.
NOT_FOUND = "Not Found"  # AWS says the resource does not exist (or is not visible to this account).
WRONG_REGION = "Wrong Region"  # The resource lives in a different region than AWS_REGION.
TIMED_OUT = "Timed Out"  # AWS did not answer in time.
UNAVAILABLE = "Unavailable"  # The service could not be reached, or the resource is disabled.
CONFIGURATION_ERROR = "Configuration Error"  # A setting is wrong (bad format, bad credentials, ...).
# Two extra words for rows that are not a pass/fail of a connection:
NO_DATA = "No Data"  # The API works, but no metrics or logs have arrived yet (delivery not proven).
SKIPPED = "Skipped"  # Not applicable here (for example "not running on EC2" in local mode).

# Any of these makes the whole environment check count as an "error" in the run history.
PROBLEM_STATUSES = (ACCESS_DENIED, NOT_FOUND, WRONG_REGION, TIMED_OUT, UNAVAILABLE, CONFIGURATION_ERROR)

# AWS error codes that mean "your identity is not allowed to do this".
PERMISSION_ERROR_CODES = (
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "AuthorizationError",
    "AuthorizationErrorException",
    "403",  # S3 HeadBucket sends only the HTTP status code, without a text code.
)

# AWS error codes that mean "your credentials are invalid or expired".
CREDENTIAL_ERROR_CODES = (
    "ExpiredToken",
    "ExpiredTokenException",
    "InvalidClientTokenId",
    "SignatureDoesNotMatch",
    "UnrecognizedClientException",
)

# AWS error codes that mean "this resource does not exist".
NOT_FOUND_CODES = (
    "404",  # S3 HeadBucket.
    "NoSuchBucket",
    "NotFound",  # SNS.
    "NotFoundException",  # KMS.
    "ResourceNotFoundException",  # Lambda, Secrets Manager, CloudWatch Logs.
    "AWS.SimpleQueueService.NonExistentQueue",  # SQS (older query protocol).
    "QueueDoesNotExist",  # SQS (newer JSON protocol).
    "InvalidInstanceID.NotFound",  # EC2.
)

# AWS error codes that mean "you asked the wrong region".
WRONG_REGION_CODES = (
    "301",
    "PermanentRedirect",
    "AuthorizationHeaderMalformed",
    "IllegalLocationConstraintException",
)

# AWS error codes that mean "AWS is busy or having a problem right now; try again later".
UNAVAILABLE_CODES = (
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "ServiceUnavailable",
    "ServiceUnavailableException",
    "InternalError",
    "InternalFailure",
    "KMSInternalException",
)


def friendly_error_message(error):
    """Returns a beginner-friendly explanation for an exception.

    error: any exception caught while calling AWS.
    Returns: a single string that can be shown on a web page.
    """
    if isinstance(error, NoCredentialsError):
        return ("No AWS credentials were found. Locally, run 'aws configure' or set AWS_PROFILE "
                "in .env. On EC2, attach an IAM role to the instance.")

    if isinstance(error, ProfileNotFound):
        return f"The AWS profile in AWS_PROFILE does not exist on this computer: {error}"

    if isinstance(error, NoRegionError):
        return "No AWS region is configured. Set AWS_REGION in .env (for example us-east-1)."

    # Timeouts are checked BEFORE EndpointConnectionError, so they get their own message.
    if isinstance(error, (ConnectTimeoutError, ReadTimeoutError)):
        return f"AWS did not answer in time. Check the network, security groups, and region. Details: {error}"

    if isinstance(error, EndpointConnectionError):
        return f"Could not reach AWS. Check your internet connection and the region name. Details: {error}"

    if isinstance(error, ParamValidationError):
        return f"A value in .env has the wrong format, so nothing was sent to AWS. Details: {error}"

    if isinstance(error, ClientError):
        return describe_client_error(error)

    # PermissionError and ValueError are raised by this app's own safety and input checks.
    if isinstance(error, (PermissionError, ValueError)):
        return str(error)

    # Anything else is unexpected, so show its type to help with troubleshooting.
    return f"Unexpected error ({type(error).__name__}): {error}"


def describe_client_error(error):
    # AWS puts the error code and message inside error.response["Error"].
    code = error.response.get("Error", {}).get("Code", "Unknown")
    message = error.response.get("Error", {}).get("Message", "")
    # The Boto3 method that failed, for example "ListBuckets".
    operation = error.operation_name

    if code in PERMISSION_ERROR_CODES:
        return (f"Permission denied for {operation}. Your IAM user or role needs the matching "
                f"permission (see deploy/iam-readonly-policy.json). AWS said: {message}")

    if code in CREDENTIAL_ERROR_CODES:
        return f"Your AWS credentials are invalid or expired ({code}). Refresh them and try again."

    # Any other AWS error: show the code and message exactly as AWS sent them.
    return f"AWS returned {code} during {operation}: {message}"


def classify_code(code, http_status=0):
    """Turns an AWS error code (for example "AccessDeniedException") into a connectivity status word."""
    if code in PERMISSION_ERROR_CODES:
        return ACCESS_DENIED
    if code in NOT_FOUND_CODES:
        return NOT_FOUND
    if code in WRONG_REGION_CODES:
        return WRONG_REGION
    if code in CREDENTIAL_ERROR_CODES:
        return CONFIGURATION_ERROR
    if code in UNAVAILABLE_CODES or http_status >= 500:
        return UNAVAILABLE
    # Anything else (for example ValidationException or InvalidParameterValue) means a setting is wrong.
    return CONFIGURATION_ERROR


def classify_error(error):
    """Decides which connectivity status an exception means.

    error: any exception raised while checking one service.
    Returns: one of the status words at the top of this file, for example ACCESS_DENIED.
    """
    if isinstance(error, (ConnectTimeoutError, ReadTimeoutError, socket.timeout, TimeoutError)):
        return TIMED_OUT
    # urllib (used for EC2 metadata) wraps a timeout inside URLError; its .reason holds the real cause.
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, (socket.timeout, TimeoutError)):
        return TIMED_OUT
    if isinstance(error, (EndpointConnectionError, urllib.error.URLError, ConnectionError)):
        return UNAVAILABLE
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "")
        http_status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0
        return classify_code(code, http_status)
    # core/secret_store.SecretError keeps the AWS error code (without any secret value) in .aws_code.
    aws_code = getattr(error, "aws_code", "")
    if aws_code:
        return classify_code(aws_code)
    # An app error that wraps a network problem ("raise ... from error"): classify the original problem.
    if isinstance(error, ValueError) and error.__cause__ is not None:
        return classify_error(error.__cause__)
    if isinstance(error, (NoCredentialsError, PartialCredentialsError, CredentialRetrievalError,
                          ProfileNotFound, NoRegionError, ParamValidationError, ValueError)):
        return CONFIGURATION_ERROR
    return UNAVAILABLE
