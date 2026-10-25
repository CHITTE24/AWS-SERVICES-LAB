"""Tests for the multi-service experiments."""

import boto3
from botocore.exceptions import ClientError

from experiments import account_overview, ec2_security_review, lambda_health, s3_storage_report
from integrations import cloudwatch, iam, lambda_functions


def make_access_denied(*args):
    # Imitates AWS refusing a request because of missing IAM permissions.
    raise ClientError({"Error": {"Code": "AccessDenied", "Message": "not authorized"}}, "ListUsers")


def test_account_overview(fake_aws):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="lab-bucket")
    result = account_overview.run()
    assert result["errors"] == []
    assert result["summary"]["Account ID"] == "123456789012"
    assert result["summary"]["S3 buckets"] == 1


def test_account_overview_continues_after_permission_error(fake_aws, monkeypatch):
    # monkeypatch temporarily replaces iam.list_users with a function that raises AccessDenied.
    monkeypatch.setattr(iam, "list_users", make_access_denied)
    result = account_overview.run()
    assert result["summary"]["IAM users"] == "unknown"
    assert "Permission denied" in result["errors"][0]
    # The other steps still worked.
    assert result["summary"]["Account ID"] == "123456789012"


def test_s3_storage_report(fake_aws):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="lab-report")
    client.put_object(Bucket="lab-report", Key="a.txt", Body=b"x" * 2048)
    result = s3_storage_report.run(max_buckets=5)
    assert result["errors"] == []
    assert result["tables"][0]["rows"][0]["objects_counted"] == 1


def test_ec2_security_review_flags_open_ssh(fake_aws):
    client = boto3.client("ec2", region_name="us-east-1")
    group = client.create_security_group(GroupName="open-ssh", Description="test")
    client.authorize_security_group_ingress(
        GroupId=group["GroupId"],
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
    )
    result = ec2_security_review.run()
    assert result["summary"]["HIGH severity findings"] >= 1
    assert "SSH" in result["tables"][0]["rows"][0]["reason"]


def test_rule_severity_levels():
    web_rule = {"protocol": "tcp", "from_port": 443, "to_port": 443, "sources": ["0.0.0.0/0"]}
    all_traffic = {"protocol": "-1", "from_port": None, "to_port": None, "sources": ["0.0.0.0/0"]}
    assert ec2_security_review.rule_severity(web_rule)[0] == "MEDIUM"
    assert ec2_security_review.rule_severity(all_traffic)[0] == "HIGH"


def test_lambda_health(monkeypatch):
    # Replaces the AWS calls with fake answers: one function, 10 invocations, 2 errors.
    def fake_list_functions(region=None):
        return [{"function_name": "orders", "runtime": "python3.12"}]

    def fake_metric_sum(namespace, metric_name, dimension_name, dimension_value, hours, region):
        return 10 if metric_name == "Invocations" else 2

    monkeypatch.setattr(lambda_functions, "list_functions", fake_list_functions)
    monkeypatch.setattr(cloudwatch, "get_metric_sum", fake_metric_sum)

    result = lambda_health.run(hours=24)
    row = result["tables"][0]["rows"][0]
    assert row["error_rate"] == "20.0%"
    assert result["summary"]["Functions with errors (last 24h)"] == 1
