"""Tests for the "Check AWS environment" button (experiments/environment_check.py).

They prove the dashboard reflects REAL connectivity: a setting that only exists is never "Connected",
each problem gets a precise status, and every click checks again (connect -> disconnect -> recover).

AWS is always fake: moto (the fake_aws fixture), botocore's Stubber, or monkeypatch replacements.
No real AWS account, credentials, or billable resource is ever used.
"""

import io  # Builds a tiny zip file for the fake Lambda function.
import re  # Reads statuses out of the rendered HTML page.
import socket  # socket.timeout simulates the EC2 metadata service timing out.
import time  # CloudWatch Logs timestamps.
import urllib.error  # URLError simulates the EC2 metadata service being unreachable.
import zipfile  # Builds a tiny zip file for the fake Lambda function.
from datetime import datetime, timedelta, timezone  # CloudWatch metric timestamps.
from pathlib import Path  # Reads static/style.css.

import boto3  # Creates fake resources inside moto.
import pytest
from botocore.exceptions import (ClientError, ConnectTimeoutError, EndpointConnectionError, NoCredentialsError,
                                 ParamValidationError, ReadTimeoutError)
from botocore.stub import Stubber  # Returns pre-recorded AWS answers for services moto does not support.

from core import aws_errors
from core.config import clean_aws_profile
from core.secret_store import SecretError
from experiments import environment_check
from integrations import ec2, lambda_functions, s3, ses, sts
from repositories import submission_repository
from repositories.history_repository import list_runs

REGION = "us-east-1"  # The AWS_REGION used by every test (set in conftest.py).
SECRET_TEXT = "never-show-this-secret-456"  # Must never appear on any page.


# ---------------------------------------------------------------- helpers

@pytest.fixture(autouse=True)
def not_on_ec2(monkeypatch):
    # By default the tests act like "not running on EC2" (no 1-second metadata timeout). EC2 tests override it.
    def no_metadata():
        raise urllib.error.URLError("metadata service unreachable")
    monkeypatch.setattr(ec2, "get_current_instance", no_metadata)


def find(rows, service, action=""):
    """Returns the first row for a service (and optionally an action starting with the given text)."""
    for row in rows:
        if row["service"] == service and row["action"].startswith(action):
            return row
    raise AssertionError(f"No row for {service} / {action}. Rows: {rows}")


def status(rows, service, action=""):
    return find(rows, service, action)["status"]


def page_status(page, service):
    """Reads the status badge of a service's first row from the rendered HTML page."""
    match = re.search(rf"<td>{re.escape(service)}</td><td>[^<]*</td><td>\s*<span class=\"badge [^\"]+\">([^<]+)</span>",
                      page)
    assert match, f"{service} not found on the page"
    return match.group(1)


def client_error(code, operation="Operation", http_status=400):
    # Builds the same kind of error AWS returns, for example AccessDenied.
    return ClientError({"Error": {"Code": code, "Message": "test"},
                        "ResponseMetadata": {"HTTPStatusCode": http_status}}, operation)


def raises(error):
    # Returns a function that raises the given error, to replace an integration function.
    def replacement(*args, **kwargs):
        raise error
    return replacement


def make_lambda(name="lab-processor"):
    # Creates a fake Lambda function in moto. It is never invoked.
    role = boto3.client("iam").create_role(RoleName="lab-lambda-role", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("handler.py", "def handler(event, context):\n    return {}\n")
    boto3.client("lambda", region_name=REGION).create_function(
        FunctionName=name, Runtime="python3.12", Role=role, Handler="handler.handler",
        Code={"ZipFile": package.getvalue()})


def stub_ses(monkeypatch, response=None, error_code=None):
    # moto does not support SES GetAccount, so botocore's Stubber gives a pre-recorded answer instead.
    client = boto3.client("sesv2", region_name=REGION)
    stubber = Stubber(client)
    if error_code:
        stubber.add_client_error("get_account", service_error_code=error_code, http_status_code=403)
    else:
        stubber.add_response("get_account", response)
    stubber.activate()
    monkeypatch.setattr(ses, "get_client", lambda *args, **kwargs: client)


HEALTHY_SES = {"SendingEnabled": True, "ProductionAccessEnabled": False, "EnforcementStatus": "HEALTHY"}


# ---------------------------------------------------------------- nothing configured

def test_missing_settings_are_not_configured_never_connected(fake_aws):
    rows = environment_check.run()
    for service in ("Amazon S3", "AWS KMS", "AWS Lambda", "Amazon SES", "Amazon SNS", "Amazon SQS",
                    "Amazon CloudWatch", "AWS Secrets Manager", "Amazon RDS (PostgreSQL)"):
        assert status(rows, service) == "Not Configured", service
    assert status(rows, "AWS IAM / STS") == "Connected"
    assert status(rows, "SQLite (local file)") == "Connected"
    assert status(rows, "Amazon EC2") == "Skipped"  # Local mode, not on EC2.


def test_security_panel_never_shows_green_for_a_setting(logged_in_client, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-bucket")
    page = logged_in_client.get("/").text
    assert "badge-configured" in page  # The setting is shown as present ...
    assert "badge-connected" not in page  # ... but nothing claims a connection without a real check.
    # "Configured" must not share the green colour with Connected/Verified.
    css = Path("static/style.css").read_text(encoding="utf-8")
    green_rule = next(line for line in css.splitlines() if ".badge-connected" in line and "{" in line)
    assert "badge-configured" not in green_rule


# ---------------------------------------------------------------- everything connected

def test_every_service_connected(fake_aws, monkeypatch):
    s3_client = boto3.client("s3", region_name=REGION)
    s3_client.create_bucket(Bucket="lab-bucket")
    key_arn = boto3.client("kms", region_name=REGION).create_key()["KeyMetadata"]["Arn"]
    boto3.client("secretsmanager", region_name=REGION).create_secret(
        Name="lab/app", SecretString='{"password": "%s"}' % SECRET_TEXT)
    make_lambda()
    topic_arn = boto3.client("sns", region_name=REGION).create_topic(Name="lab-topic")["TopicArn"]
    queue_url = boto3.client("sqs", region_name=REGION).create_queue(QueueName="lab-queue")["QueueUrl"]
    # CloudWatch: one metric data point and one log line, as if the function had run.
    boto3.client("cloudwatch", region_name=REGION).put_metric_data(Namespace="AWS/Lambda", MetricData=[{
        "MetricName": "Invocations", "Dimensions": [{"Name": "FunctionName", "Value": "lab-processor"}],
        # 5 minutes ago: a point stamped "now" can fall just outside the query window (moto rounds to seconds).
        "Value": 3, "Timestamp": datetime.now(timezone.utc) - timedelta(minutes=5)}])
    logs = boto3.client("logs", region_name=REGION)
    logs.create_log_group(logGroupName="/aws/lambda/lab-processor")
    logs.create_log_stream(logGroupName="/aws/lambda/lab-processor", logStreamName="s1")
    logs.put_log_events(logGroupName="/aws/lambda/lab-processor", logStreamName="s1",
                        logEvents=[{"timestamp": int(time.time() * 1000), "message": "hello"}])
    stub_ses(monkeypatch, HEALTHY_SES)

    for name, value in {"S3_BUCKET_NAME": "lab-bucket", "S3_KMS_KEY_ARN": key_arn,
                        "AWS_SECRETS_MANAGER_SECRET_NAME": "lab/app", "LAMBDA_FUNCTION_NAME": "lab-processor",
                        "SNS_TOPIC_ARN": topic_arn, "SQS_QUEUE_URL": queue_url}.items():
        monkeypatch.setenv(name, value)

    rows = environment_check.run()
    for service in ("Amazon S3", "AWS KMS", "AWS Lambda", "Amazon SES", "Amazon SNS", "Amazon SQS",
                    "AWS IAM / STS", "SQLite (local file)"):
        assert status(rows, service) == "Connected", find(rows, service)
    assert status(rows, "AWS Secrets Manager", "Describe secret") == "Connected"
    assert status(rows, "AWS Secrets Manager", "Retrieve value") == "Connected"
    assert status(rows, "Amazon CloudWatch", "Metrics API") == "Connected"
    assert status(rows, "Amazon CloudWatch", "Lambda metrics delivered") == "Connected"
    assert status(rows, "Amazon CloudWatch", "Logs delivered") == "Connected"
    # The secret value is never shown, and nothing was sent or invoked.
    all_details = " ".join(row["detail"] for row in rows)
    assert SECRET_TEXT not in all_details
    assert "NOT invoked" in find(rows, "AWS Lambda")["detail"]
    assert "SANDBOX" in find(rows, "Amazon SES")["detail"]


# ---------------------------------------------------------------- S3

def test_s3_bucket_not_found(fake_aws, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-bucket-that-does-not-exist")
    assert status(environment_check.check_s3(), "Amazon S3") == "Not Found"


def test_s3_403_is_reported_as_ambiguous_access_denied(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "someone-elses-bucket")
    client = boto3.client("s3", region_name=REGION)
    stubber = Stubber(client)
    stubber.add_client_error("head_bucket", service_error_code="403", http_status_code=403,
                             response_meta={"HTTPHeaders": {"x-amz-bucket-region": REGION}})
    stubber.activate()
    monkeypatch.setattr(s3, "get_client", lambda *args, **kwargs: client)

    row = environment_check.check_s3()[0]
    assert row["status"] == "Access Denied"
    # It must NOT claim certainty: 403 can mean "no permission" or "another account's bucket".
    assert "could not be confirmed" in row["detail"]
    assert "another AWS account" in row["detail"]


def test_s3_bucket_in_another_region(fake_aws, monkeypatch):
    boto3.client("s3", region_name=REGION).create_bucket(
        Bucket="lab-bucket-eu", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-bucket-eu")
    row = environment_check.check_s3()[0]
    assert row["status"] == "Wrong Region"
    assert "eu-west-1" in row["detail"]


def test_s3_timeout_does_not_crash_other_checks(fake_aws, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-bucket")
    monkeypatch.setattr(s3, "head_bucket", raises(ConnectTimeoutError(endpoint_url="https://s3.amazonaws.com")))
    rows = environment_check.run()
    assert status(rows, "Amazon S3") == "Timed Out"
    assert status(rows, "AWS IAM / STS") == "Connected"  # The other checks still ran.


# ---------------------------------------------------------------- KMS

def test_kms_alias_is_a_configuration_error(monkeypatch):
    monkeypatch.setenv("S3_KMS_KEY_ARN", "alias/lab-key")
    assert status(environment_check.check_kms(), "AWS KMS") == "Configuration Error"


def test_kms_key_in_another_region(monkeypatch):
    monkeypatch.setenv("S3_KMS_KEY_ARN", "arn:aws:kms:eu-west-1:123456789012:key/11111111-2222-3333-4444-555555555555")
    assert status(environment_check.check_kms(), "AWS KMS") == "Wrong Region"


def test_kms_key_not_found_and_disabled(fake_aws, monkeypatch):
    monkeypatch.setenv("S3_KMS_KEY_ARN", f"arn:aws:kms:{REGION}:123456789012:key/11111111-2222-3333-4444-555555555555")
    assert status(environment_check.run(), "AWS KMS") == "Not Found"

    kms = boto3.client("kms", region_name=REGION)
    key = kms.create_key()["KeyMetadata"]
    kms.disable_key(KeyId=key["KeyId"])
    monkeypatch.setenv("S3_KMS_KEY_ARN", key["Arn"])
    row = environment_check.check_kms()[0]
    assert row["status"] == "Unavailable"
    assert "Disabled" in row["detail"]


def test_kms_metadata_is_separate_from_encrypt_permission(fake_aws, monkeypatch):
    monkeypatch.setenv("S3_KMS_KEY_ARN", boto3.client("kms", region_name=REGION).create_key()["KeyMetadata"]["Arn"])
    row = environment_check.check_kms()[0]
    assert row["status"] == "Connected"
    assert "Permission to ENCRYPT is not tested here" in row["detail"]


# ---------------------------------------------------------------- Secrets Manager

def test_secret_is_never_stale(fake_aws, monkeypatch):
    # Connected -> secret deleted -> the very next check shows it (no 15-minute cache) -> recreated -> Connected.
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/app")
    secrets = boto3.client("secretsmanager", region_name=REGION)
    secrets.create_secret(Name="lab/app", SecretString='{"password": "%s"}' % SECRET_TEXT)
    rows = environment_check.check_secret()
    assert status(rows, "AWS Secrets Manager", "Retrieve value") == "Connected"

    secrets.delete_secret(SecretId="lab/app", ForceDeleteWithoutRecovery=True)
    rows = environment_check.check_secret()
    assert status(rows, "AWS Secrets Manager", "Describe secret") == "Not Found"
    assert status(rows, "AWS Secrets Manager", "Retrieve value") == "Not Found"

    secrets.create_secret(Name="lab/app", SecretString='{"password": "%s"}' % SECRET_TEXT)
    rows = environment_check.check_secret()
    assert status(rows, "AWS Secrets Manager", "Retrieve value") == "Connected"
    assert SECRET_TEXT not in " ".join(row["detail"] for row in rows)


def test_secret_metadata_access_is_separate_from_value_access(fake_aws, monkeypatch):
    # The role may describe the secret but not read its value: the two rows must disagree.
    monkeypatch.setenv("AWS_SECRETS_MANAGER_SECRET_NAME", "lab/app")
    boto3.client("secretsmanager", region_name=REGION).create_secret(Name="lab/app", SecretString="{}")
    monkeypatch.setattr(environment_check, "get_secret_values", raises(
        SecretError("Could not read the secret from Secrets Manager (AccessDeniedException).",
                    "AccessDeniedException")))
    rows = environment_check.check_secret()
    assert status(rows, "AWS Secrets Manager", "Describe secret") == "Connected"
    assert status(rows, "AWS Secrets Manager", "Retrieve value") == "Access Denied"


# ---------------------------------------------------------------- Lambda, SNS, SQS, SES

def test_lambda_not_found_wrong_region_and_not_active(fake_aws, monkeypatch):
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "no-such-function")
    assert status(environment_check.run(), "AWS Lambda") == "Not Found"

    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "arn:aws:lambda:eu-west-1:123456789012:function:lab")
    assert status(environment_check.check_lambda(), "AWS Lambda") == "Wrong Region"

    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab")
    monkeypatch.setattr(lambda_functions, "get_function_configuration",
                        lambda *args, **kwargs: {"state": "Pending", "runtime": "python3.12", "last_update_status": ""})
    assert status(environment_check.check_lambda(), "AWS Lambda") == "Unavailable"


def test_lambda_access_denied(monkeypatch):
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab")
    monkeypatch.setattr(lambda_functions, "get_function_configuration",
                        raises(client_error("AccessDeniedException", "GetFunctionConfiguration", 403)))
    rows = environment_check.run()
    assert status(rows, "AWS Lambda") == "Access Denied"
    assert "Permission denied" in find(rows, "AWS Lambda")["detail"]


def test_sns_and_sqs_not_found_and_wrong_region(fake_aws, monkeypatch):
    monkeypatch.setenv("SNS_TOPIC_ARN", f"arn:aws:sns:{REGION}:123456789012:no-such-topic")
    monkeypatch.setenv("SQS_QUEUE_URL", f"https://sqs.{REGION}.amazonaws.com/123456789012/no-such-queue")
    rows = environment_check.run()
    assert status(rows, "Amazon SNS") == "Not Found"
    assert status(rows, "Amazon SQS") == "Not Found"

    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:123456789012:lab-topic")
    monkeypatch.setenv("SQS_QUEUE_URL", "https://sqs.eu-west-1.amazonaws.com/123456789012/lab-queue")
    assert status(environment_check.check_sns(), "Amazon SNS") == "Wrong Region"
    assert status(environment_check.check_sqs(), "Amazon SQS") == "Wrong Region"


def test_sns_topic_without_subscribers_is_explained(fake_aws, monkeypatch):
    monkeypatch.setenv("SNS_TOPIC_ARN", boto3.client("sns", region_name=REGION).create_topic(Name="t")["TopicArn"])
    row = environment_check.check_sns()[0]
    assert row["status"] == "Connected"
    assert "reach nobody" in row["detail"]


def test_ses_statuses(monkeypatch):
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab")
    stub_ses(monkeypatch, {"SendingEnabled": False})
    assert status(environment_check.check_ses(), "Amazon SES") == "Unavailable"
    stub_ses(monkeypatch, error_code="AccessDeniedException")
    assert status(environment_check.run(), "Amazon SES") == "Access Denied"


# ---------------------------------------------------------------- CloudWatch

def test_cloudwatch_separates_api_access_from_delivery(fake_aws, monkeypatch):
    # The function name is set, but nothing has ever run: the API works, but no metrics or logs arrived.
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "never-ran")
    rows = environment_check.check_cloudwatch()
    assert status(rows, "Amazon CloudWatch", "Metrics API") == "Connected"
    assert status(rows, "Amazon CloudWatch", "Lambda metrics delivered") == "No Data"  # Never "Success with 0".
    assert status(rows, "Amazon CloudWatch", "Logs delivered") == "Not Found"


def test_cloudwatch_access_denied(fake_aws, monkeypatch):
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab")
    monkeypatch.setattr(environment_check.cloudwatch, "get_metric_sum",
                        raises(client_error("AccessDenied", "GetMetricStatistics", 403)))
    rows = environment_check.check_cloudwatch()
    assert status(rows, "Amazon CloudWatch", "Metrics API") == "Access Denied"
    assert status(rows, "Amazon CloudWatch", "Logs delivered") == "Not Found"  # Still checked separately.


# ---------------------------------------------------------------- databases

def test_sqlite_and_rds_are_shown_separately(fake_aws):
    rows = environment_check.run()
    assert "Connected to the local SQLite file" in find(rows, "SQLite (local file)")["detail"]
    assert status(rows, "Amazon RDS (PostgreSQL)") == "Not Configured"


def test_rds_configuration_error_and_driver_errors(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "postgres")
    monkeypatch.delenv("DB_HOST", raising=False)
    assert status(environment_check.check_rds(), "Amazon RDS (PostgreSQL)") == "Configuration Error"

    # Each driver error text becomes a precise status, and the host name is never shown.
    host = "lab-db.abc123.us-east-1.rds.amazonaws.com"
    for text, expected in [("connection timeout expired", "Timed Out"),
                           ("password authentication failed for user", "Access Denied"),
                           (f'could not translate host name "{host}"', "Configuration Error"),
                           ('database "labdb" does not exist', "Not Found")]:
        monkeypatch.setattr(submission_repository, "check_connection", raises(Exception(text)))
        row = environment_check.check_rds()[0]
        assert row["status"] == expected, text
        assert host not in row["detail"]


def test_rds_connected_only_with_tls(monkeypatch):
    monkeypatch.setenv("DATABASE_BACKEND", "postgres")
    monkeypatch.setattr(submission_repository, "check_connection",
                        lambda: "Connected to Amazon RDS (PostgreSQL). TLS in use: yes.")
    assert status(environment_check.check_rds(), "Amazon RDS (PostgreSQL)") == "Connected"
    monkeypatch.setattr(submission_repository, "check_connection",
                        lambda: "Connected to Amazon RDS (PostgreSQL). TLS in use: NO (check DB_SSLMODE).")
    assert status(environment_check.check_rds(), "Amazon RDS (PostgreSQL)") == "Configuration Error"


# ---------------------------------------------------------------- EC2

def test_ec2_metadata_timeout_in_aws_mode(monkeypatch):
    monkeypatch.setenv("APP_MODE", "aws")
    monkeypatch.setattr(ec2, "get_current_instance", raises(urllib.error.URLError(socket.timeout("timed out"))))
    assert status(environment_check.check_ec2(), "Amazon EC2") == "Timed Out"


def test_ec2_wrong_region_and_unknown_instance(fake_aws, monkeypatch):
    monkeypatch.setattr(ec2, "get_current_instance", lambda: {
        "instance_id": "i-0123456789abcdef0", "instance_type": "t3.micro",
        "availability_zone": "eu-west-1a", "region": "eu-west-1"})
    rows = environment_check.check_ec2()
    assert status(rows, "Amazon EC2", "Instance metadata") == "Connected"
    assert status(rows, "Amazon EC2", "Region match") == "Wrong Region"
    assert status(rows, "Amazon EC2", "Describe this instance") == "Not Found"


# ---------------------------------------------------------------- HTTPS

# scheme = what uvicorn decided; forwarded = the X-Forwarded-Proto header; trusted = TRUST_PROXY_HEADERS.
@pytest.mark.parametrize("scheme, forwarded, trusted, cookie_secure, expected_https, expected_cookie", [
    ("http", "", False, "false", "Not Configured", "Not Configured"),  # SSH tunnel: honest "not enabled".
    ("https", "", False, "true", "Connected", "Connected"),  # Direct HTTPS, no proxy involved.
    ("https", "https", True, "true", "Connected", "Connected"),  # Behind a TRUSTED load balancer.
    ("https", "", False, "false", "Connected", "Configuration Error"),  # HTTPS, but the cookie is not Secure.
    ("http", "", False, "true", "Not Configured", "Configuration Error"),  # Secure cookie over plain HTTP.
    # Spoofing: uvicorn (trusting 127.0.0.1 by default) already turned a forged header into "https".
    # With TRUST_PROXY_HEADERS=false the app must still refuse to call it Connected.
    ("https", "https", False, "true", "Configuration Error", "Configuration Error"),
    ("http", "https", False, "false", "Configuration Error", "Not Configured"),  # Forged header, not believed.
    ("http", "https", True, "false", "Configuration Error", "Not Configured"),  # Proxy not in --forwarded-allow-ips.
    ("http", "http", True, "false", "Not Configured", "Not Configured"),  # ALB forwarded plain HTTP.
])
def test_https_checks(monkeypatch, scheme, forwarded, trusted, cookie_secure, expected_https, expected_cookie):
    monkeypatch.setenv("SESSION_HTTPS_ONLY", cookie_secure)
    rows = environment_check.check_https({"scheme": scheme, "forwarded_proto": forwarded,
                                          "trust_proxy_headers": trusted})
    assert status(rows, "HTTPS", "This browser connection") == expected_https
    assert status(rows, "HTTPS", "Secure session cookie") == expected_cookie


def test_https_row_follows_the_real_request(logged_in_client, fake_aws):
    assert page_status(logged_in_client.post("/environment-check").text, "HTTPS") == "Not Configured"
    logged_in_client.base_url = "https://testserver"  # The fake browser now uses HTTPS.
    assert page_status(logged_in_client.post("/environment-check").text, "HTTPS") == "Connected"


def test_forged_forwarded_header_is_never_trusted_by_default(logged_in_client, fake_aws):
    # Anyone can send this header. Without TRUST_PROXY_HEADERS=true it must never produce "Connected".
    forged = {"X-Forwarded-Proto": "https"}
    page = logged_in_client.post("/environment-check", headers=forged).text
    assert page_status(page, "HTTPS") == "Configuration Error"
    assert "TRUST_PROXY_HEADERS=false" in page


def test_trusted_load_balancer_header_is_accepted(logged_in_client, fake_aws, monkeypatch):
    # Simulates uvicorn behind the ALB: it already set the scheme to https from the trusted proxy's header.
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    logged_in_client.base_url = "https://testserver"
    page = logged_in_client.post("/environment-check", headers={"X-Forwarded-Proto": "https"}).text
    assert page_status(page, "HTTPS") == "Connected"


# ---------------------------------------------------------------- dynamic behaviour through the web page

def test_dashboard_follows_connect_disconnect_and_recovery(logged_in_client, fake_aws, monkeypatch):
    s3_client = boto3.client("s3", region_name=REGION)

    def check_page():
        return logged_in_client.post("/environment-check").text

    assert page_status(check_page(), "Amazon S3") == "Not Configured"  # 1. No setting.
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-bucket")
    assert page_status(check_page(), "Amazon S3") == "Not Found"  # 2. Setting, but no bucket yet.
    s3_client.create_bucket(Bucket="lab-bucket")
    assert page_status(check_page(), "Amazon S3") == "Connected"  # 3. Connected.
    s3_client.delete_bucket(Bucket="lab-bucket")
    assert page_status(check_page(), "Amazon S3") == "Not Found"  # 4. Disconnected: no stale success.
    s3_client.create_bucket(Bucket="lab-bucket")
    assert page_status(check_page(), "Amazon S3") == "Connected"  # 5. Recovered.
    monkeypatch.delenv("S3_BUCKET_NAME")
    assert page_status(check_page(), "Amazon S3") == "Not Configured"  # 6. Setting removed.


def test_problems_are_saved_as_error_in_history(logged_in_client, fake_aws, monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "missing-bucket")
    logged_in_client.post("/environment-check")
    assert list_runs(limit=1)[0]["status"] == "error"
    monkeypatch.delenv("S3_BUCKET_NAME")
    logged_in_client.post("/environment-check")
    assert list_runs(limit=1)[0]["status"] == "success"


def test_sts_timeout_is_reported_and_page_still_loads(logged_in_client, fake_aws, monkeypatch):
    monkeypatch.setattr(sts, "get_caller_identity", raises(ReadTimeoutError(endpoint_url="https://sts")))
    response = logged_in_client.post("/environment-check")
    assert response.status_code == 200
    assert page_status(response.text, "AWS IAM / STS") == "Timed Out"


def test_empty_aws_profile_still_finds_credentials(fake_aws, monkeypatch):
    # The line "AWS_PROFILE=" in .env must not break the checks.
    monkeypatch.setenv("AWS_PROFILE", "")
    clean_aws_profile()
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)  # Forces Boto3 to look for credentials again.
    rows = environment_check.run()
    assert status(rows, "AWS IAM / STS") == "Connected"
    assert "Credential source: env" in find(rows, "AWS IAM / STS")["detail"]


# ---------------------------------------------------------------- error classification

@pytest.mark.parametrize("error, expected", [
    (client_error("AccessDenied"), "Access Denied"),
    (client_error("AuthorizationError"), "Access Denied"),
    (client_error("403", http_status=403), "Access Denied"),
    (client_error("ResourceNotFoundException"), "Not Found"),
    (client_error("AWS.SimpleQueueService.NonExistentQueue"), "Not Found"),
    (client_error("PermanentRedirect"), "Wrong Region"),
    (client_error("ExpiredToken"), "Configuration Error"),
    (client_error("ValidationException"), "Configuration Error"),
    (client_error("ThrottlingException"), "Unavailable"),
    (client_error("SomethingNew", http_status=503), "Unavailable"),
    (ConnectTimeoutError(endpoint_url="https://x"), "Timed Out"),
    (ReadTimeoutError(endpoint_url="https://x"), "Timed Out"),
    (EndpointConnectionError(endpoint_url="https://x"), "Unavailable"),
    (urllib.error.URLError(socket.timeout("timed out")), "Timed Out"),
    (NoCredentialsError(), "Configuration Error"),
    (ParamValidationError(report="bad bucket name"), "Configuration Error"),
    (SecretError("Could not read the secret (ResourceNotFoundException).", "ResourceNotFoundException"), "Not Found"),
])
def test_classify_error(error, expected):
    assert aws_errors.classify_error(error) == expected


def test_timeouts_have_a_readable_message():
    message = aws_errors.friendly_error_message(ConnectTimeoutError(endpoint_url="https://x"))
    assert "did not answer in time" in message
    assert "Unexpected" not in message
