"""Tests for the integrations/ functions, using moto's fake AWS (the fake_aws fixture)."""

from datetime import datetime, timedelta, timezone  # Used to date the fake CloudWatch metric.

import boto3  # Used here only to create fake resources inside moto.

from integrations import cloudwatch, dynamodb, ec2, iam, lambda_functions, s3, sts


def test_sts_identity(fake_aws):
    identity = sts.get_caller_identity()
    # moto always uses this fake account number.
    assert identity["account"] == "123456789012"


def test_s3_functions(fake_aws):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="lab-test-bucket")
    client.put_object(Bucket="lab-test-bucket", Key="hello.txt", Body=b"hello")

    names = [bucket["name"] for bucket in s3.list_buckets()]
    assert "lab-test-bucket" in names
    assert s3.get_bucket_region("lab-test-bucket") == "us-east-1"

    counts = s3.count_objects("lab-test-bucket")
    assert counts["objects_counted"] == 1
    assert counts["total_bytes"] == 5  # "hello" is 5 bytes.


def test_ec2_instances_and_regions(fake_aws):
    client = boto3.client("ec2", region_name="us-east-1")
    # Launches a fake instance with a Name tag. moto accepts any image id.
    client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro",
                         TagSpecifications=[{"ResourceType": "instance",
                                             "Tags": [{"Key": "Name", "Value": "lab-server"}]}])
    instances = ec2.list_instances()
    assert len(instances) == 1
    assert instances[0]["name"] == "lab-server"
    assert "us-east-1" in ec2.list_regions()


def test_ec2_security_groups(fake_aws):
    client = boto3.client("ec2", region_name="us-east-1")
    group = client.create_security_group(GroupName="lab-sg", Description="test")
    client.authorize_security_group_ingress(
        GroupId=group["GroupId"],
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
    )
    groups = ec2.list_security_groups()
    lab_group = [g for g in groups if g["group_name"] == "lab-sg"][0]
    assert lab_group["inbound_rules"][0]["sources"] == ["0.0.0.0/0"]


def test_iam_functions(fake_aws):
    boto3.client("iam").create_user(UserName="lab-user")
    assert iam.list_users()[0]["user_name"] == "lab-user"
    assert isinstance(iam.list_roles(), list)
    assert iam.get_account_summary()["Users"] == 1


def test_dynamodb_functions(fake_aws):
    boto3.client("dynamodb", region_name="us-east-1").create_table(
        TableName="lab-table",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    assert dynamodb.list_tables() == [{"table_name": "lab-table"}]
    details = dynamodb.describe_table("lab-table")
    assert details["keys"] == "id (HASH)"
    assert details["billing_mode"] == "PAY_PER_REQUEST"


def test_cloudwatch_functions(fake_aws):
    client = boto3.client("cloudwatch", region_name="us-east-1")
    client.put_metric_alarm(AlarmName="lab-alarm", MetricName="Errors", Namespace="Lab/Test",
                            Statistic="Sum", Period=60, EvaluationPeriods=1, Threshold=1,
                            ComparisonOperator="GreaterThanThreshold")
    assert cloudwatch.list_alarms()[0]["alarm_name"] == "lab-alarm"

    # The data point is dated 5 minutes ago. AWS rounds the query's end time to whole seconds,
    # so a point stamped "right now" could fall just outside the window.
    five_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    client.put_metric_data(Namespace="Lab/Test", MetricData=[
        {"MetricName": "Errors", "Dimensions": [{"Name": "FunctionName", "Value": "f1"}],
         "Value": 3, "Timestamp": five_minutes_ago},
    ])
    assert cloudwatch.get_metric_sum("Lab/Test", "Errors", "FunctionName", "f1", hours=1) == 3


def test_lambda_list_is_empty_in_new_account(fake_aws):
    assert lambda_functions.list_functions() == []
