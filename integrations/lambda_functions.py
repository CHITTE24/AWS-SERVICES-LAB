"""AWS Lambda (serverless functions): read-only function list.

This file is not named "lambda.py" because "lambda" is a reserved word in Python.
"""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


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
