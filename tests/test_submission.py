"""Tests for the lab form submission workflow: S3, database, Lambda, SNS/SQS, and the statuses shown.

AWS is always fake here: moto (the fake_aws fixture) or monkeypatch replacements. No real email is sent.
"""

import uuid  # Creates submission IDs for the tests.

import boto3  # Used only to create fake resources inside moto.
import pytest
from botocore.exceptions import ClientError

from experiments import environment_check
from integrations import ec2, lambda_functions
from repositories import submission_repository

VALID = {"username": "student_1", "first_name": "Mary", "last_name": "Smith",
         "mobile_number": "01234", "email": "mary@example.com"}


def submit(client, files=None, submission_id=None, **changes):
    # Sends the lab form. Returns the final page (redirects are followed).
    data = dict(VALID, submission_id=submission_id or uuid.uuid4().hex, **changes)
    return client.post("/submit", data=data, files=files)


def count_rows(submission_id):
    # How many database rows have this submission ID.
    rows = submission_repository.run_sql("SELECT COUNT(*) AS total FROM lab_submissions WHERE id = ?",
                                         (submission_id,), fetch=True)
    return rows[0]["total"]


def make_bucket_and_key():
    # Creates a fake bucket and KMS key inside moto. Returns the key ARN.
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="lab-test-documents")
    return boto3.client("kms", region_name="us-east-1").create_key()["KeyMetadata"]["Arn"]


def fake_lambda_success(function_name, event):
    # Stands in for the real Lambda function (the real one would send an email with SES).
    return {"processing_status": "Success", "email_status": "Sent", "email_detail": "Accepted by Amazon SES (test)."}


# ---------- Main page ----------

def test_main_page_shows_form_and_sections(logged_in_client):
    page = logged_in_client.get("/").text
    assert "Welcome to AWS Service Lab" in page
    assert "This application demonstrates how AWS compute, storage, databases" in page
    assert "Upload a document (optional, maximum 5 MB)" in page
    for section in ("AWS Service Activity", "Security Configuration", "Log out", ">Submit<"):
        assert section in page


# ---------- Validation ----------

def test_invalid_fields_show_messages_beside_fields(logged_in_client):
    response = submit(logged_in_client, email="not-an-email", mobile_number="12")
    assert response.status_code == 400
    assert "Enter a valid email address" in response.text
    assert "exactly 5 digits" in response.text
    # The typed values are kept, so the user does not have to type everything again.
    assert 'value="Mary"' in response.text


def test_missing_fields_are_rejected(logged_in_client):
    response = logged_in_client.post("/submit", data={"submission_id": uuid.uuid4().hex})
    assert response.status_code == 400
    assert "Username is required" in response.text
    assert "Email address is required" in response.text


# ---------- Local mode: nothing configured ----------

def test_submission_without_document_in_local_mode(logged_in_client):
    submission_id = uuid.uuid4().hex
    response = submit(logged_in_client, submission_id=submission_id)
    assert response.status_code == 200
    page = response.text
    assert submission_id in page
    assert "No document uploaded." in page
    assert "Local SQLite file (NOT Amazon RDS)" in page
    # No AWS resources are configured, so Lambda and email are clearly labelled as simulated.
    assert "Local mode: LAMBDA_FUNCTION_NAME is not set, so nothing was sent to AWS." in page
    row = submission_repository.get_submission(submission_id, "student")
    assert row["upload_status"] == "Skipped"
    assert (row["processing_status"], row["email_status"]) == ("Simulated", "Simulated")
    assert row["mobile_number"] == "01234"  # Stored as text: the leading zero is kept.
    assert count_rows(submission_id) == 1


def test_upload_at_limit_is_accepted_and_simulated_without_bucket(logged_in_client):
    files = {"document": ("notes.txt", b"a" * (5 * 1024 * 1024), "text/plain")}
    response = submit(logged_in_client, files=files)
    assert response.status_code == 200
    assert "notes.txt" in response.text
    assert "S3_BUCKET_NAME is not set" in response.text


def test_upload_over_limit_is_rejected(logged_in_client):
    submission_id = uuid.uuid4().hex
    files = {"document": ("big.txt", b"a" * (5 * 1024 * 1024 + 1), "text/plain")}
    response = submit(logged_in_client, files=files, submission_id=submission_id)
    assert response.status_code == 400
    assert "larger than 5 MB" in response.text
    assert count_rows(submission_id) == 0  # Nothing was saved.


def test_duplicate_submission_is_processed_once(logged_in_client, monkeypatch):
    calls = []
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab-processor")
    monkeypatch.setattr(lambda_functions, "invoke_submission_processor",
                        lambda name, event: calls.append(event) or fake_lambda_success(name, event))
    submission_id = uuid.uuid4().hex
    submit(logged_in_client, submission_id=submission_id)
    second = submit(logged_in_client, submission_id=submission_id)  # Same form submitted again.
    assert second.status_code == 200
    assert count_rows(submission_id) == 1
    assert len(calls) == 1  # Lambda (and so the email) ran only once.


def test_refreshing_result_page_does_not_resubmit(logged_in_client):
    submission_id = uuid.uuid4().hex
    first = logged_in_client.post("/submit", data=dict(VALID, submission_id=submission_id), follow_redirects=False)
    # 303 = Post/Redirect/Get. Refreshing the result page repeats only the GET.
    assert first.status_code == 303
    assert logged_in_client.get(first.headers["location"]).status_code == 200
    assert count_rows(submission_id) == 1


def test_other_users_cannot_see_a_submission(client, logged_in_client):
    submission_id = uuid.uuid4().hex
    submit(logged_in_client, submission_id=submission_id)
    logged_in_client.post("/logout")
    logged_in_client.post("/login", data={"username": "someone-else", "password": "x"})
    page = logged_in_client.get(f"/?submission_id={submission_id}").text
    assert "Mary" not in page


# ---------- Amazon S3 with SSE-KMS (moto) ----------

def test_s3_upload_success_with_verified_sse_kms(logged_in_client, fake_aws, monkeypatch):
    key_arn = make_bucket_and_key()
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-test-documents")
    monkeypatch.setenv("S3_KMS_KEY_ARN", key_arn)
    submission_id = uuid.uuid4().hex
    files = {"document": ("..\\My Report.pdf", b"%PDF-1.7 test", "application/pdf")}
    page = submit(logged_in_client, files=files, submission_id=submission_id).text
    assert "S3 confirmed the upload" in page
    assert "Verified" in page

    # The object really is in the bucket, under a unique key, encrypted with the configured key.
    row = submission_repository.get_submission(submission_id, "student")
    assert row["upload_status"] == "Success"
    assert row["s3_object_key"].endswith(f"/{submission_id}/My_Report.pdf")
    head = boto3.client("s3", region_name="us-east-1").head_object(Bucket="lab-test-documents",
                                                                    Key=row["s3_object_key"])
    assert head["ServerSideEncryption"] == "aws:kms"
    assert head["SSEKMSKeyId"] == key_arn


def test_s3_upload_failure_is_reported(logged_in_client, fake_aws, monkeypatch):
    # The bucket does not exist in moto, so S3 returns NoSuchBucket.
    monkeypatch.setenv("S3_BUCKET_NAME", "missing-bucket-xyz")
    monkeypatch.setenv("S3_KMS_KEY_ARN", "arn:aws:kms:us-east-1:123456789012:key/test")
    submission_id = uuid.uuid4().hex
    page = submit(logged_in_client, files={"document": ("a.txt", b"hello", "text/plain")},
                  submission_id=submission_id).text
    assert "NoSuchBucket" in page
    assert submission_repository.get_submission(submission_id, "student")["upload_status"] == "Failed"


def test_s3_upload_refused_without_kms_key(logged_in_client, monkeypatch):
    # Never upload without SSE-KMS: no silent fallback to weaker encryption.
    monkeypatch.setenv("S3_BUCKET_NAME", "lab-test-documents")
    page = submit(logged_in_client, files={"document": ("a.txt", b"hello", "text/plain")}).text
    assert "S3_KMS_KEY_ARN is not set, so the upload was refused" in page


# ---------- Database ----------

def test_database_failure_is_reported_and_lambda_skipped(logged_in_client, monkeypatch):
    def broken_insert(record):
        raise RuntimeError("database is down at db.internal.example")
    monkeypatch.setattr(submission_repository, "insert_submission", broken_insert)
    response = submit(logged_in_client)
    assert response.status_code == 200
    assert "could not save the record" in response.text
    assert "Skipped because the database save failed" in response.text
    # Technical details such as host names stay in the log, not the page.
    assert "db.internal.example" not in response.text


# ---------- Lambda + email ----------

def test_lambda_and_email_success(logged_in_client, monkeypatch):
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab-processor")
    monkeypatch.setattr(lambda_functions, "invoke_submission_processor", fake_lambda_success)
    submission_id = uuid.uuid4().hex
    page = submit(logged_in_client, submission_id=submission_id).text
    assert "Delivery to the inbox is not confirmed" in page
    row = submission_repository.get_submission(submission_id, "student")
    assert (row["processing_status"], row["email_status"]) == ("Success", "Sent")


def test_lambda_failure(logged_in_client, monkeypatch):
    def access_denied(function_name, event):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no invoke"}}, "Invoke")
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab-processor")
    monkeypatch.setattr(lambda_functions, "invoke_submission_processor", access_denied)
    submission_id = uuid.uuid4().hex
    page = submit(logged_in_client, submission_id=submission_id).text
    assert "Permission denied for Invoke" in page
    row = submission_repository.get_submission(submission_id, "student")
    assert (row["processing_status"], row["email_status"]) == ("Failed", "Unknown")


def test_email_failure_reported_by_lambda(logged_in_client, monkeypatch):
    monkeypatch.setenv("LAMBDA_FUNCTION_NAME", "lab-processor")
    monkeypatch.setattr(lambda_functions, "invoke_submission_processor", lambda name, event: {
        "processing_status": "Success", "email_status": "Failed", "email_detail": "SES rejected the email."})
    page = submit(logged_in_client).text
    assert "SES rejected the email." in page


def test_invoke_reports_function_error(monkeypatch):
    # A fake Lambda client whose function crashed (AWS sets FunctionError in that case).
    class FakePayload:
        def read(self):
            return b'{"errorType": "KeyError", "errorMessage": "email"}'

    class FakeLambdaClient:
        def invoke(self, **arguments):
            assert arguments["InvocationType"] == "RequestResponse"
            assert b"secret" not in arguments["Payload"]
            return {"FunctionError": "Unhandled", "Payload": FakePayload()}

    monkeypatch.setattr(lambda_functions, "get_client", lambda *args, **kwargs: FakeLambdaClient())
    with pytest.raises(RuntimeError, match="KeyError"):
        lambda_functions.invoke_submission_processor("lab-processor", {"submission_id": "x"})


def test_aws_mode_reports_missing_settings_as_failed(logged_in_client, monkeypatch):
    monkeypatch.setenv("APP_MODE", "aws")
    submission_id = uuid.uuid4().hex
    page = submit(logged_in_client, submission_id=submission_id).text
    assert "LAMBDA_FUNCTION_NAME is not set. It is required when APP_MODE=aws." in page
    # Never "Simulated" in aws mode: a missing setting is a failure.
    assert submission_repository.get_submission(submission_id, "student")["processing_status"] == "Failed"


# ---------- SNS and SQS (moto) ----------

def test_sns_and_sqs_success(logged_in_client, fake_aws, monkeypatch):
    topic_arn = boto3.client("sns", region_name="us-east-1").create_topic(Name="lab-topic")["TopicArn"]
    queue_url = boto3.client("sqs", region_name="us-east-1").create_queue(QueueName="lab-queue")["QueueUrl"]
    monkeypatch.setenv("SNS_TOPIC_ARN", topic_arn)
    monkeypatch.setenv("SQS_QUEUE_URL", queue_url)
    submission_id = uuid.uuid4().hex
    page = submit(logged_in_client, submission_id=submission_id).text
    assert page.count("Accepted with MessageId") == 2
    # The queued message contains the submission ID, but not personal details or the document.
    messages = boto3.client("sqs", region_name="us-east-1").receive_message(QueueUrl=queue_url)["Messages"]
    assert submission_id in messages[0]["Body"]
    assert "mary@example.com" not in messages[0]["Body"]


def test_sns_failure(logged_in_client, fake_aws, monkeypatch):
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:does-not-exist")
    page = submit(logged_in_client).text
    assert "Publish notification" in page
    assert "NotFound" in page


# ---------- Security Configuration and environment check ----------

def test_security_configuration_statuses(logged_in_client, monkeypatch):
    page = logged_in_client.get("/").text
    assert "This page arrived over plain HTTP" in page
    assert "Not Configured" in page
    monkeypatch.setenv("S3_KMS_KEY_ARN", "arn:aws:kms:us-east-1:123456789012:key/abc")
    page = logged_in_client.get("/").text
    assert "Configured" in page
    # The key ARN itself is never displayed.
    assert "key/abc" not in page


def test_https_is_verified_only_for_https_requests(logged_in_client):
    # base_url="https://..." makes the fake browser use HTTPS.
    logged_in_client.base_url = "https://testserver"
    assert "This page arrived over HTTPS." in logged_in_client.get("/").text


def test_environment_check(logged_in_client, fake_aws, monkeypatch):
    def not_on_ec2():
        raise OSError("metadata service unreachable")
    monkeypatch.setattr(ec2, "get_current_instance", not_on_ec2)
    page = logged_in_client.post("/environment-check").text
    assert "123456789012" in page  # STS identity from moto.
    assert "not running on EC2" in page
    assert "AWS_SECRETS_MANAGER_SECRET_NAME is not set." in page
    assert "Connected to the local SQLite file" in page


def test_environment_check_on_ec2(fake_aws, monkeypatch):
    client = boto3.client("ec2", region_name="us-east-1")
    instance = client.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro",
                                    TagSpecifications=[{"ResourceType": "instance",
                                                        "Tags": [{"Key": "Name", "Value": "lab-web"}]}])
    instance_id = instance["Instances"][0]["InstanceId"]
    monkeypatch.setattr(ec2, "get_current_instance", lambda: {
        "instance_id": instance_id, "instance_type": "t3.micro", "availability_zone": "us-east-1a"})
    details = " ".join(row["detail"] for row in environment_check.run())
    assert instance_id in details
    assert "Name tag: lab-web" in details
