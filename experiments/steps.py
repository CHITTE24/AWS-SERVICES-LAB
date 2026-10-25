"""Shared helpers for experiments.

Every experiment returns a dictionary in this format (templates/experiment_result.html displays it):
    {
        "summary": {"Label": value, ...},             # Headline numbers.
        "tables":  [{"title": "...", "rows": [...]}],  # Each row is a dictionary.
        "notes":   ["..."],                            # Helpful explanations.
        "errors":  ["..."],                            # Steps that failed (for example, missing permissions).
    }
"""

import logging  # Records failed steps in logs/app.log.

from core.aws_errors import friendly_error_message  # Turns exceptions into readable text.

logger = logging.getLogger(__name__)  # A logger named after this file: "experiments.steps".


def run_step(errors, step_name, function, *args):
    """Runs one AWS call. If it fails, records the error and lets the experiment continue.

    errors: the experiment's list of error messages. A failure is appended to it.
    step_name: a short label for the error message, for example "List S3 buckets".
    function: the integration function to call, for example s3.list_buckets.
    *args: any values to pass to that function, for example a bucket name.
    Returns: the function's result, or None if it failed.
    """
    try:
        return function(*args)
    except Exception as error:
        # One failing step (for example a missing IAM permission) should not hide the other results.
        logger.warning("Experiment step failed: %s: %s", step_name, error)
        errors.append(f"{step_name}: {friendly_error_message(error)}")
        return None


def count_or_unknown(items):
    # Returns how many items a list has, or "unknown" if the step that should have produced it failed.
    if items is None:
        return "unknown"
    return len(items)
