"""AWS Lambda (serverless functions): function list, and running the submission processor.

This file is not named "lambda.py" because "lambda" is a reserved word in Python.
"""

import json  # The Lambda event and answer are JSON text.
import logging  # Records Lambda errors in logs/app.log.

from botocore.config import Config  # Used to switch off automatic retries for one call.

from core.aws_client import CLIENT_CONFIG, get_client  # Creates Boto3 clients with the app's settings.

logger = logging.getLogger(__name__)


def list_functions(region=None):
    """Lists Lambda functions in one region.

    Returns: a list of dictionaries with function name, runtime, memory size, and last modified time.
    """
    # The Boto3 service name is "lambda", even though the Python file cannot be named that.
    lambda_client = get_client("lambda", region)
    paginator = lambda_client.get_paginator("list_functions")

    functions = []
    for page in paginator.paginate():
        for function in page["Functions"]:
            functions.append({
                "function_name": function["FunctionName"],
                # Container-image functions have no runtime, so a default is used.
                "runtime": function.get("Runtime", "container image"),
                "memory_mb": function.get("MemorySize"),
                "last_modified": function.get("LastModified", ""),
            })
    return functions


def get_function_configuration(function_name, config=None):
    """Reads a function's settings WITHOUT running it. Needs lambda:GetFunctionConfiguration.

    Returns: its state ("Active", "Pending", "Inactive", or "Failed"), runtime, and last update status.
    """
    response = get_client("lambda", config=config).get_function_configuration(FunctionName=function_name)
    return {
        "state": response.get("State", "Active"),  # Very old functions have no State field.
        "runtime": response.get("Runtime", "container image"),
        "last_update_status": response.get("LastUpdateStatus", ""),
    }


# The submission processor sends an email, so a retry could send it twice. max_attempts=1 means
# "never retry automatically". read_timeout gives the function up to 30 seconds to answer.
SINGLE_ATTEMPT_CONFIG = CLIENT_CONFIG.merge(Config(retries={"max_attempts": 1, "mode": "standard"},
                                                   read_timeout=30))


def invoke_submission_processor(function_name, event):
    """Runs the submission-processing Lambda function and waits for its answer.

    function_name: the function name or ARN (LAMBDA_FUNCTION_NAME in .env).
    event: a small dictionary with the submission ID and details. Never the document itself.
    Returns: the dictionary the function returned, for example {"processing_status": "Success", ...}.
    """
    lambda_client = get_client("lambda", config=SINGLE_ATTEMPT_CONFIG)
    # "RequestResponse" waits for the function to finish, so the page can show a VERIFIED result.
    response = lambda_client.invoke(FunctionName=function_name, InvocationType="RequestResponse",
                                    Payload=json.dumps(event).encode("utf-8"))
    answer = json.loads(response["Payload"].read() or b"{}")

    # FunctionError is set when the function's code crashed. Its details stay in the logs.
    if response.get("FunctionError"):
        logger.warning("Lambda %s failed: %s", function_name, answer.get("errorMessage"))
        raise RuntimeError(f"The Lambda function failed ({answer.get('errorType', 'unknown error')}). "
                           "See its CloudWatch logs.")
    return answer
