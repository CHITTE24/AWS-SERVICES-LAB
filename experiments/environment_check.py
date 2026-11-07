"""The "Check AWS environment" button: proves, service by service, which AWS connections really work.

Rules every check follows:
  - Every click makes NEW read-only AWS requests. Nothing is cached (not even the secret).
  - An empty setting gives "Not Configured". A setting that is merely present is never "Connected".
  - "Connected" means a real AWS answer proved it. Errors become a precise status (see core/aws_errors.py):
    Access Denied, Not Found, Wrong Region, Timed Out, Unavailable, or Configuration Error.
  - Nothing is created, changed, sent, uploaded, or invoked. No email, no Lambda run, no secret shown.
  - One failing check never stops the others.

Each check returns activity rows in the same format as experiments/submission_workflow.py:
    {"service": "Amazon S3", "action": "...", "status": "Connected", "detail": "..."}
"""

import logging  # Records failures in logs/app.log.
from urllib.parse import urlparse  # Reads the region out of an SQS queue URL.

import boto3  # Only used to ask which credential source Boto3 found (no AWS call).
from botocore.exceptions import ClientError  # The error type AWS services raise.

from core.aws_client import CHECK_CONFIG  # Short timeouts, so the page answers quickly.
from core.aws_errors import (ACCESS_DENIED, CONFIGURATION_ERROR, CONNECTED, NO_DATA, NOT_CONFIGURED, NOT_FOUND,
                             SKIPPED, UNAVAILABLE, WRONG_REGION, classify_error, friendly_error_message)
from core import request_security  # Decides HTTPS from trusted request information only.
from core.config import get_settings  # Reads which resources are configured.
from core.secret_store import describe_secret, get_secret_values  # Secrets Manager.
from experiments.submission_workflow import activity  # Builds one activity row.
from integrations import cloudwatch, ec2, kms, lambda_functions, messaging, s3, ses, sts  # Read-only AWS calls.
from repositories import submission_repository  # Database connection tests.

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- small helpers

def error_row(service, action, error):
    # Turns any exception into one row with a precise status and a readable explanation.
    logger.warning("Environment check %s / %s failed: %s", service, action, error)
    return activity(service, action, classify_error(error), friendly_error_message(error))


def region_from_arn(arn):
    # An ARN looks like arn:aws:sns:ap-south-1:123456789012:my-topic. The 4th part is the region.
    parts = arn.split(":")
    if arn.startswith("arn:") and len(parts) > 5:
        return parts[3]
    return ""  # Not an ARN (for example a plain name), so the default region is used.


def region_from_queue_url(queue_url):
    # Queue URLs look like https://sqs.ap-south-1.amazonaws.com/123456789012/my-queue.
    host_parts = (urlparse(queue_url).hostname or "").split(".")
    if len(host_parts) > 2 and host_parts[0] == "sqs":
        return host_parts[1]
    if len(host_parts) > 2 and host_parts[1] == "queue":  # Older style: ap-south-1.queue.amazonaws.com
        return host_parts[0]
    return ""


def wrong_region_row(service, action, setting_name, resource_region):
    """Returns a "Wrong Region" row if the resource is in another region than AWS_REGION, otherwise None.

    The app sends every request to AWS_REGION, so a resource in another region cannot be used.
    """
    app_region = get_settings()["aws_region"]
    if resource_region and resource_region != app_region:
        return activity(service, action, WRONG_REGION,
                        f"{setting_name} points to region {resource_region}, but AWS_REGION is {app_region}. "
                        f"The app calls AWS in {app_region}, so it cannot use this resource. "
                        "Change AWS_REGION or the setting so they match.")
    return None


def short_function_name(function_name):
    # CloudWatch needs the plain name. An ARN looks like arn:aws:lambda:REGION:ACCOUNT:function:NAME
    parts = function_name.split(":")
    if function_name.startswith("arn:") and len(parts) > 6:
        return parts[6]
    return function_name


# ---------------------------------------------------------------- the checks

def check_identity():
    # STS shows WHICH identity the app uses. GetCallerIdentity needs no permission and is free.
    identity = sts.get_caller_identity(config=CHECK_CONFIG)
    # Which credential source Boto3 used, for example "iam-role" on EC2 or "shared-credentials-file" on a PC.
    # Reading it makes no AWS request.
    session = boto3.DEFAULT_SESSION
    credentials = session.get_credentials() if session else None
    method = credentials.method if credentials else "unknown"
    detail = (f"Account {identity['account']}, identity {identity['arn']}. Credential source: {method}. "
              f"Region: {get_settings()['aws_region']}.")
    if ":assumed-role/" in identity["arn"]:
        detail += " Temporary IAM role credentials (no access keys). Boto3 renews them automatically."
    else:
        detail += " On EC2 this should be the instance's IAM role, not an IAM user."
    return [activity("AWS IAM / STS", "Identify credentials", CONNECTED, detail)]


def check_ec2():
    # Step 1: the EC2 metadata service (IMDSv2) says which instance this is. It only exists inside EC2.
    try:
        instance = ec2.get_current_instance()
    except Exception as error:
        if get_settings()["app_mode"] == "local":
            return [activity("Amazon EC2", "Instance metadata (IMDSv2)", SKIPPED,
                             "The metadata service did not answer, so this app is not running on EC2 "
                             "(for example, your PC).")]
        # In aws mode the app is expected to run on EC2, so silence from the metadata service is a problem.
        return [activity("Amazon EC2", "Instance metadata (IMDSv2)", classify_error(error),
                         "The metadata service did not answer. Either this app is not running on EC2, or "
                         "instance metadata is disabled for this instance. "
                         f"Details: {type(error).__name__}")]

    # Older test data may not include the region, so it is worked out from the zone ("ap-south-1a" -> "ap-south-1").
    region = instance.get("region") or instance["availability_zone"][:-1]
    rows = [activity("Amazon EC2", "Instance metadata (IMDSv2)", CONNECTED,
                     f"Running on {instance['instance_id']} ({instance['instance_type']}, "
                     f"{instance['availability_zone']}).")]
    mismatch = wrong_region_row("Amazon EC2", "Region match", "This EC2 instance", region)
    if mismatch:
        rows.append(mismatch)

    # Step 2: read this ONE instance with the EC2 API, in the region it really runs in.
    try:
        details = ec2.describe_instance(instance["instance_id"], region=region, config=CHECK_CONFIG)
        rows.append(activity("Amazon EC2", "Describe this instance", CONNECTED,
                             f"Name tag: {details['name'] or '(none)'}. State: {details['state']}."))
    except Exception as error:
        rows.append(error_row("Amazon EC2", "Describe this instance", error))
    return rows


def check_https(request_info):
    """Checks how THIS request reached the app, using trusted information only.

    request_info comes from core/request_security.describe_request(). An X-Forwarded-Proto header is
    only believed when TRUST_PROXY_HEADERS=true (and uvicorn accepted it from --forwarded-allow-ips).
    """
    https = request_security.https_status(request_info)
    cookie = request_security.cookie_status(request_info)
    return [activity("HTTPS", "This browser connection", https[0], https[1]),
            activity("HTTPS", "Secure session cookie", cookie[0], cookie[1])]


def check_secret():
    # Two separate questions: does the secret exist (metadata), and may the app read its value?
    secret_name = get_settings()["secret_name"]
    service = "AWS Secrets Manager"
    if not secret_name:
        return [activity(service, "Describe secret (metadata)", NOT_CONFIGURED,
                         "AWS_SECRETS_MANAGER_SECRET_NAME is not set.")]
    mismatch = wrong_region_row(service, "Describe secret (metadata)", "AWS_SECRETS_MANAGER_SECRET_NAME",
                                region_from_arn(secret_name))
    if mismatch:
        return [mismatch]

    rows = []
    # 1. Metadata only (secretsmanager:DescribeSecret). The value is NOT read.
    try:
        info = describe_secret(secret_name, config=CHECK_CONFIG)
        if info["scheduled_for_deletion"]:
            rows.append(activity(service, "Describe secret (metadata)", UNAVAILABLE,
                                 "The secret exists but is scheduled for deletion, so it cannot be read."))
        else:
            rotation = "on" if info["rotation_enabled"] else "off"
            rows.append(activity(service, "Describe secret (metadata)", CONNECTED,
                                 f"The secret exists (automatic rotation: {rotation}). Metadata only: this does "
                                 "NOT prove the app may read the value. See the next row."))
    except Exception as error:
        rows.append(error_row(service, "Describe secret (metadata)", error))

    # 2. Permission to read the value (secretsmanager:GetSecretValue), which the RDS connection needs.
    #    use_cache=False: always a FRESH request, so a revoked permission or deleted secret shows up at once.
    #    The values stay in memory. Only the number of keys is shown.
    try:
        values = get_secret_values(use_cache=False, config=CHECK_CONFIG)
        rows.append(activity(service, "Retrieve value (permission)", CONNECTED,
                             f"A fresh read succeeded ({len(values)} keys). Values are never displayed."))
    except Exception as error:
        logger.warning("Environment check: secret value could not be read: %s", error)
        rows.append(activity(service, "Retrieve value (permission)", classify_error(error), str(error)))
    return rows


def check_sqlite():
    # The local SQLite file always stores the run history. It is a separate database from Amazon RDS.
    service = "SQLite (local file)"
    try:
        path = submission_repository.check_sqlite()
    except Exception as error:
        logger.warning("Environment check: SQLite failed: %s", error)
        return [activity(service, "Test connection", UNAVAILABLE,
                         f"The local SQLite file could not be opened ({type(error).__name__}). "
                         "Check DATABASE_PATH and folder permissions.")]
    stores = "the run history" if submission_repository.using_rds() else "the run history and form submissions"
    return [activity(service, "Test connection", CONNECTED,
                     f"Connected to the local SQLite file {path.name}. It stores {stores}. "
                     "This is NOT Amazon RDS.")]


def check_rds():
    # Amazon RDS for PostgreSQL. Opens a real connection and reports whether TLS is really in use.
    service = "Amazon RDS (PostgreSQL)"
    if not submission_repository.using_rds():
        return [activity(service, "Test connection", NOT_CONFIGURED,
                         "DATABASE_BACKEND=sqlite, so submissions are saved to the local SQLite file, not RDS.")]
    try:
        detail = submission_repository.check_connection()
    except Exception as error:
        logger.warning("Environment check: RDS failed: %s", error)
        status, message = submission_repository.classify_database_error(error)
        return [activity(service, "Test connection", status, message)]
    # check_connection() reports TLS from the PostgreSQL client library itself.
    status = CONNECTED if "TLS in use: yes" in detail else CONFIGURATION_ERROR
    return [activity(service, "Test connection", status, detail)]


def check_s3():
    bucket = get_settings()["s3_bucket_name"]
    service, action = "Amazon S3", "HeadBucket (exists + access)"
    if not bucket:
        return [activity(service, action, NOT_CONFIGURED,
                         "S3_BUCKET_NAME is not set, so documents are not uploaded to S3.")]
    try:
        bucket_region = s3.head_bucket(bucket, config=CHECK_CONFIG)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        headers = error.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
        # S3 often names the bucket's region in a header, even when it refuses the request.
        region_note = f" S3 reports the bucket's region as {headers['x-amz-bucket-region']}." \
            if headers.get("x-amz-bucket-region") else ""
        if code == "403":
            # 403 is AMBIGUOUS: S3 gives the same answer for "you lack permission" and "someone else's bucket".
            return [activity(service, action, ACCESS_DENIED,
                             "S3 answered 403 Forbidden. Either the role lacks s3:ListBucket on this bucket, or "
                             "the bucket belongs to another AWS account. S3 does not say which, so access "
                             f"could not be confirmed.{region_note}")]
        if code in ("404", "NoSuchBucket"):
            return [activity(service, action, NOT_FOUND,
                             "S3 answered 404 Not Found: no bucket with this name exists. Check S3_BUCKET_NAME.")]
        bucket_region = headers.get("x-amz-bucket-region", "")
        other_region = bucket_region and bucket_region != get_settings()["aws_region"]
        if code in ("301", "PermanentRedirect") or (code == "400" and other_region):
            return [activity(service, action, WRONG_REGION,
                             f"S3 redirected the request: the bucket is in another region.{region_note} "
                             f"AWS_REGION is {get_settings()['aws_region']}.")]
        return [error_row(service, action, error)]

    mismatch = wrong_region_row(service, action, "S3_BUCKET_NAME", bucket_region)
    if mismatch:
        mismatch["detail"] = "The bucket exists and is accessible. " + mismatch["detail"] + \
            " SSE-KMS also needs the KMS key to be in the bucket's region."
        return [mismatch]
    return [activity(service, action, CONNECTED,
                     f"The bucket exists and the role can access it (region {bucket_region}). No objects were read. "
                     "Upload permission (s3:PutObject) is proven only by a real upload.")]


def check_kms():
    key_arn = get_settings()["s3_kms_key_arn"]
    service, action = "AWS KMS", "DescribeKey (metadata)"
    if not key_arn:
        return [activity(service, action, NOT_CONFIGURED,
                         "S3_KMS_KEY_ARN is not set, so S3 uploads are refused (they must be SSE-KMS encrypted).")]
    if not (key_arn.startswith("arn:") and ":key/" in key_arn):
        return [activity(service, action, CONFIGURATION_ERROR,
                         "S3_KMS_KEY_ARN must be a key ARN (arn:aws:kms:REGION:ACCOUNT:key/ID). An alias or a "
                         "bare key ID makes the SSE-KMS check after each upload fail.")]
    mismatch = wrong_region_row(service, action, "S3_KMS_KEY_ARN", region_from_arn(key_arn))
    if mismatch:
        return [mismatch]

    key = kms.describe_key(key_arn, config=CHECK_CONFIG)
    if key["state"] != "Enabled":
        return [activity(service, action, UNAVAILABLE,
                         f"The key exists but its state is {key['state']}, so S3 cannot encrypt with it.")]
    if key["usage"] and key["usage"] != "ENCRYPT_DECRYPT":
        return [activity(service, action, CONFIGURATION_ERROR,
                         f"The key's usage is {key['usage']}. S3 encryption needs an ENCRYPT_DECRYPT key.")]
    return [activity(service, action, CONNECTED,
                     f"Key metadata is readable: Enabled, ENCRYPT_DECRYPT, {key['manager'].lower()}-managed. "
                     "Permission to ENCRYPT is not tested here (the app may only encrypt through S3); "
                     "it is proven by the SSE-KMS row after each upload.")]


def check_lambda():
    function_name = get_settings()["lambda_function_name"]
    service, action = "AWS Lambda", "GetFunctionConfiguration"
    if not function_name:
        return [activity(service, action, NOT_CONFIGURED, "LAMBDA_FUNCTION_NAME is not set.")]
    mismatch = wrong_region_row(service, action, "LAMBDA_FUNCTION_NAME", region_from_arn(function_name))
    if mismatch:
        return [mismatch]

    info = lambda_functions.get_function_configuration(function_name, config=CHECK_CONFIG)
    if info["state"] != "Active":
        return [activity(service, action, UNAVAILABLE,
                         f"The function exists but its state is {info['state']}, so it cannot run yet.")]
    return [activity(service, action, CONNECTED,
                     f"The function exists and is Active (runtime {info['runtime']}). It was NOT invoked: "
                     "running it would process data and send email.")]


def check_ses():
    # The Lambda function sends the email, so SES only matters when Lambda is configured.
    service, action = "Amazon SES", "Account status"
    if not get_settings()["lambda_function_name"]:
        return [activity(service, action, NOT_CONFIGURED,
                         "Email is sent by the Lambda function. Set LAMBDA_FUNCTION_NAME first.")]
    account = ses.get_account(config=CHECK_CONFIG)
    region = get_settings()["aws_region"]
    if not account["sending_enabled"] or account["enforcement_status"] == "SHUTDOWN":
        return [activity(service, action, UNAVAILABLE, f"Email sending is disabled for this account in {region}.")]
    mode = ("production access" if account["production_access"]
            else "SANDBOX: email is only delivered to verified addresses")
    return [activity(service, action, CONNECTED,
                     f"SES sending is enabled in {region} ({mode}). This checks the account, not whether the "
                     "Lambda function's own role may call ses:SendEmail. No email was sent.")]


def check_sns():
    topic_arn = get_settings()["sns_topic_arn"]
    service, action = "Amazon SNS", "GetTopicAttributes"
    if not topic_arn:
        return [activity(service, action, NOT_CONFIGURED, "Optional: SNS_TOPIC_ARN is not set.")]
    mismatch = wrong_region_row(service, action, "SNS_TOPIC_ARN", region_from_arn(topic_arn))
    if mismatch:
        return [mismatch]
    info = messaging.get_topic_attributes(topic_arn, config=CHECK_CONFIG)
    note = "" if info["subscriptions"] else " A published message would currently reach nobody."
    return [activity(service, action, CONNECTED,
                     f"The topic exists with {info['subscriptions']} confirmed subscription(s).{note} "
                     "Nothing was published.")]


def check_sqs():
    queue_url = get_settings()["sqs_queue_url"]
    service, action = "Amazon SQS", "GetQueueAttributes"
    if not queue_url:
        return [activity(service, action, NOT_CONFIGURED, "Optional: SQS_QUEUE_URL is not set.")]
    mismatch = wrong_region_row(service, action, "SQS_QUEUE_URL", region_from_queue_url(queue_url))
    if mismatch:
        return [mismatch]
    info = messaging.get_queue_attributes(queue_url, config=CHECK_CONFIG)
    return [activity(service, action, CONNECTED,
                     f"The queue exists (about {info['messages']} message(s) waiting). Nothing was sent or received.")]


def check_cloudwatch():
    """Three separate questions: can we use the metrics API, have metrics arrived, have logs arrived?"""
    function_name = get_settings()["lambda_function_name"]
    service = "Amazon CloudWatch"
    if not function_name:
        return [activity(service, "Metrics and logs", NOT_CONFIGURED,
                         "LAMBDA_FUNCTION_NAME is not set, so there is nothing to monitor yet.")]
    name = short_function_name(function_name)

    # 1. API access (cloudwatch:GetMetricStatistics). Success proves permission, NOT that metrics exist.
    try:
        invocations = cloudwatch.get_metric_sum("AWS/Lambda", "Invocations", "FunctionName", name, config=CHECK_CONFIG)
        errors = cloudwatch.get_metric_sum("AWS/Lambda", "Errors", "FunctionName", name, config=CHECK_CONFIG)
        rows = [activity(service, "Metrics API access", CONNECTED,
                         "The CloudWatch metrics API answered. This proves permission only, not that metrics arrive.")]
        # 2. Metric delivery. CloudWatch returns no data (not an error) for a name that does not exist.
        if invocations > 0:
            rows.append(activity(service, "Lambda metrics delivered (24 h)", CONNECTED,
                                 f"{invocations:g} invocations, {errors:g} errors in the last 24 hours."))
        else:
            rows.append(activity(service, "Lambda metrics delivered (24 h)", NO_DATA,
                                 "No invocations recorded in 24 hours. Either the function has not run, or the "
                                 "name is wrong (CloudWatch returns no data, not an error, for unknown names: "
                                 "see the AWS Lambda row)."))
    except Exception as error:
        rows = [error_row(service, "Metrics API access", error)]

    # 3. Log delivery: when did the function's log group last receive a line? (logs:DescribeLogStreams)
    log_group = f"/aws/lambda/{name}"
    action = f"Logs delivered ({log_group})"
    try:
        latest = cloudwatch.get_latest_log_event_time(log_group, config=CHECK_CONFIG)
        if latest is None:
            rows.append(activity(service, action, NO_DATA, "The log group exists but has no log events yet."))
        else:
            rows.append(activity(service, action, CONNECTED, f"Last log event: {latest:%Y-%m-%d %H:%M} UTC."))
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            rows.append(activity(service, action, NOT_FOUND,
                                 "The log group does not exist yet. Lambda creates it on its first run, "
                                 "if its execution role may write logs."))
        else:
            rows.append(error_row(service, action, error))
    return rows


# The checks, in the order they appear on the page: (service name, action, function).
# "action" is only used if the check crashes with an error it did not handle itself.
CHECKS = [
    ("AWS IAM / STS", "Identify credentials", check_identity),
    ("Amazon EC2", "Instance metadata (IMDSv2)", check_ec2),
    ("AWS Secrets Manager", "Check secret", check_secret),
    ("SQLite (local file)", "Test connection", check_sqlite),
    ("Amazon RDS (PostgreSQL)", "Test connection", check_rds),
    ("Amazon S3", "HeadBucket (exists + access)", check_s3),
    ("AWS KMS", "DescribeKey (metadata)", check_kms),
    ("AWS Lambda", "GetFunctionConfiguration", check_lambda),
    ("Amazon SES", "Account status", check_ses),
    ("Amazon SNS", "GetTopicAttributes", check_sns),
    ("Amazon SQS", "GetQueueAttributes", check_sqs),
    ("Amazon CloudWatch", "Metrics and logs", check_cloudwatch),
]


def run(request_info=None):
    """Runs every check and returns all activity rows. Every call makes fresh AWS requests.

    request_info: how the browser reached the app (core/request_security.describe_request()),
        for the HTTPS check. None (for example in a script) skips the HTTPS check.
    """
    rows = []
    if request_info is not None:
        rows.extend(check_https(request_info))
    for service, action, check in CHECKS:
        try:
            rows.extend(check())
        except Exception as error:
            # One failing check (for example a missing permission) must not hide the others.
            rows.append(error_row(service, action, error))
    return rows
