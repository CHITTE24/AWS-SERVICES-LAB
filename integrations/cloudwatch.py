"""Amazon CloudWatch (monitoring): read-only alarms and metric totals."""

from datetime import datetime, timedelta, timezone  # Used to build the metric time window.

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def list_alarms(region=None):
    """Lists CloudWatch metric alarms. Returns name, state (OK / ALARM / INSUFFICIENT_DATA), and metric."""
    cloudwatch = get_client("cloudwatch", region)
    paginator = cloudwatch.get_paginator("describe_alarms")

    alarms = []
    for page in paginator.paginate():
        for alarm in page["MetricAlarms"]:
            alarms.append({
                "alarm_name": alarm["AlarmName"],
                "state": alarm["StateValue"],
                "metric": alarm.get("MetricName", ""),
                "namespace": alarm.get("Namespace", ""),
            })
    return alarms


def get_metric_sum(namespace, metric_name, dimension_name, dimension_value, hours=24, region=None):
    """Adds up a metric over the last few hours, for example the total Lambda Errors for one function.

    namespace: the metric group, for example "AWS/Lambda".
    metric_name: the metric, for example "Errors" or "Invocations".
    dimension_name / dimension_value: which resource, for example "FunctionName" / "my-function".
    hours: how far back to look.
    Returns: the total as a number (0 if there were no data points).
    """
    cloudwatch = get_client("cloudwatch", region)
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=hours)

    response = cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=[{"Name": dimension_name, "Value": dimension_value}],
        StartTime=start_time,
        EndTime=end_time,
        Period=3600,  # One data point per hour (in seconds).
        Statistics=["Sum"],  # Ask for the total within each hour.
    )

    # Adds the hourly totals together into one number.
    total = 0
    for point in response.get("Datapoints", []):
        total = total + point["Sum"]
    return total
