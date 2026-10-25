"""Turns technical AWS and Python errors into short messages a person can act on."""

# Error types raised by botocore (the library underneath Boto3).
from botocore.exceptions import (
    ClientError,  # AWS received the request but refused it (permissions, missing resource, ...).
    EndpointConnectionError,  # The computer could not reach AWS (network or bad region).
    NoCredentialsError,  # Boto3 could not find any AWS credentials.
    NoRegionError,  # No AWS region was configured.
    ProfileNotFound,  # AWS_PROFILE names a profile that does not exist.
)

# AWS error codes that mean "your identity is not allowed to do this".
PERMISSION_ERROR_CODES = (
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedOperation",
    "AuthorizationError",
)

# AWS error codes that mean "your credentials are invalid or expired".
CREDENTIAL_ERROR_CODES = (
    "ExpiredToken",
    "ExpiredTokenException",
    "InvalidClientTokenId",
    "SignatureDoesNotMatch",
    "UnrecognizedClientException",
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

    if isinstance(error, EndpointConnectionError):
        return f"Could not reach AWS. Check your internet connection and the region name. Details: {error}"

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
