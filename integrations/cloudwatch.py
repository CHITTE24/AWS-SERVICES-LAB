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


def get_metric_sum(namespace, metric_name, dimension_name, dimension_value, hours=24, region=None, config=None):
    """Adds up a metric over the last few hours, for example the total Lambda Errors for one function.

    namespace: the metric group, for example "AWS/Lambda".
    metric_name: the metric, for example "Errors" or "Invocations".
    dimension_name / dimension_value: which resource, for example "FunctionName" / "my-function".
    hours: how far back to look.
    Returns: the total as a number (0 if there were no data points).
    NOTE: CloudWatch returns NO data points (not an error) for a resource that does not exist,
    so a result of 0 never proves that the resource exists.
    """
    cloudwatch = get_client("cloudwatch", region, config=config)
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


def get_latest_log_event_time(log_group_name, config=None):
    """Finds when a CloudWatch Logs group last received a log line. Reads no log content.

    Needs logs:DescribeLogStreams. Returns a UTC datetime, or None if the group exists but has no events.
    Raises ClientError "ResourceNotFoundException" if the log group does not exist.
    """
    logs = get_client("logs", config=config)
    # The newest stream first. Only one stream is needed to know the latest delivery time.
    response = logs.describe_log_streams(logGroupName=log_group_name, orderBy="LastEventTime",
                                         descending=True, limit=1)
    streams = response.get("logStreams", [])
    if not streams or not streams[0].get("lastEventTimestamp"):
        return None
    # AWS gives milliseconds since 1970. Dividing by 1000 gives seconds.
    return datetime.fromtimestamp(streams[0]["lastEventTimestamp"] / 1000, tz=timezone.utc)
