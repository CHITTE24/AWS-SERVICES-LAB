"""Experiment: Lambda Health. Combines the Lambda function list with CloudWatch error and invocation metrics."""

from experiments.steps import run_step  # Shared experiment helper.
from integrations import cloudwatch, lambda_functions  # The two services this experiment combines.

# Upper limit on functions checked. Each function needs 2 CloudWatch calls, so this caps the API usage.
MAX_FUNCTIONS = 25


def run(hours=24, region=None):
    """Checks recent errors for each Lambda function.

    hours: how far back to look in CloudWatch.
    region: the region to check. Empty means the default region.
    Returns: an experiment result dictionary (see experiments/steps.py).
    """
    errors = []
    functions = run_step(errors, "List Lambda functions", lambda_functions.list_functions, region)
    if functions is None:
        return {"summary": {}, "tables": [], "notes": [], "errors": errors}

    rows = []
    for function in functions[:MAX_FUNCTIONS]:
        name = function["function_name"]
        # Asks CloudWatch for the total invocations and errors of this function.
        invocations = run_step(errors, f"Invocations of {name}", cloudwatch.get_metric_sum,
                               "AWS/Lambda", "Invocations", "FunctionName", name, hours, region)
        error_count = run_step(errors, f"Errors of {name}", cloudwatch.get_metric_sum,
                               "AWS/Lambda", "Errors", "FunctionName", name, hours, region)

        # Error rate = errors / invocations, as a percentage. Only possible when both numbers are known.
        error_rate = "n/a"
        if invocations and error_count is not None:
            error_rate = f"{round(error_count / invocations * 100, 1)}%"

        rows.append({
            "function_name": name,
            "runtime": function["runtime"],
            "invocations": invocations if invocations is not None else "unknown",
            "errors": error_count if error_count is not None else "unknown",
            "error_rate": error_rate,
        })

    # Functions with at least one error are listed as unhealthy.
    unhealthy = [row for row in rows if isinstance(row["errors"], (int, float)) and row["errors"] > 0]
    summary = {
        "Functions in region": len(functions),
        "Functions checked": len(rows),
        f"Functions with errors (last {hours}h)": len(unhealthy),
    }
    notes = [f"Only the first {MAX_FUNCTIONS} functions are checked, which limits CloudWatch API calls."]
    return {"summary": summary, "tables": [{"title": "Functions", "rows": rows}], "notes": notes, "errors": errors}
