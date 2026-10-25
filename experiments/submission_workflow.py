"""The form-submission workflow: S3 upload -> database save -> Lambda (which emails with SES) -> optional SNS/SQS.

routes/lab_routes.py calls process_submission() after the form has been validated.

Every step adds one row to the "AWS Service Activity" table, in this format:
    {"service": "Amazon S3", "action": "Upload document", "status": "Success", "detail": "..."}

Status words:
    Success   - AWS (or the database) CONFIRMED the operation.
    Verified  - A security setting was checked against AWS's own answer.
    Sent      - Amazon SES accepted the email. Delivery to the inbox is NOT confirmed.
    Failed    - The step was attempted and failed, or a required setting is missing in aws mode.
    Skipped   - Not needed (no document), optional and not configured, or an earlier step failed.
    Simulated - APP_MODE=local and the service is not configured. NOTHING was sent to AWS.
    Unknown   - The result could not be confirmed.
"""

import logging  # Records technical error details in logs/app.log (never shown in the browser).
from datetime import datetime, timezone  # The submission timestamp.

from core.aws_errors import friendly_error_message  # Readable error text for the page.
from core.config import get_settings  # Reads which AWS resources are configured.
from integrations import lambda_functions, messaging, s3  # The AWS calls.
from repositories import submission_repository  # Saves to SQLite or RDS.

logger = logging.getLogger(__name__)


def activity(service, action, status, detail):
    # Builds one row of the AWS Service Activity table.
    return {"service": service, "action": action, "status": status, "detail": detail}


def missing_setting(service, action, setting_name):
    # In local mode an unconfigured service is SIMULATED (and labelled so). In aws mode it is a failure.
    if get_settings()["app_mode"] != "local":  # Anything other than "local" counts as aws mode.
        return activity(service, action, "Failed", f"{setting_name} is not set. It is required when APP_MODE=aws.")
    return activity(service, action, "Simulated", f"Local mode: {setting_name} is not set, so nothing was sent to AWS.")


def upload_step(submission_id, submitted_at, document):
    """Uploads the optional document to S3 with SSE-KMS.

    Returns: (list of activity rows, bucket name or None, object key or None).
    """
    if document is None:
        return [activity("Amazon S3", "Upload document", "Skipped", "No document uploaded.")], None, None

    settings = get_settings()
    bucket = settings["s3_bucket_name"]
    kms_key_arn = settings["s3_kms_key_arn"]
    if not bucket:
        row = missing_setting("Amazon S3", "Upload document", "S3_BUCKET_NAME")
        row["detail"] += f" The file '{document['name']}' ({document['size']} bytes) was checked but not stored."
        return [row], None, None
    if not kms_key_arn:
        # Never upload without the required encryption, and never silently switch to a weaker setting.
        return [activity("Amazon S3", "Upload document", "Failed",
                         "S3_KMS_KEY_ARN is not set, so the upload was refused. "
                         "Documents are only stored with SSE-KMS encryption.")], None, None

    # A unique key organised by date and submission ID, for example
    # submissions/2026/10/25/<submission id>/report.pdf. The file name was made safe in core/form_checks.py.
    key = f"submissions/{submitted_at:%Y/%m/%d}/{submission_id}/{document['name']}"
    try:
        result = s3.upload_document(bucket, key, document["content"], document["content_type"], kms_key_arn)
    except Exception as error:
        logger.warning("S3 upload failed for submission %s: %s", submission_id, error)
        return [activity("Amazon S3", "Upload document", "Failed", friendly_error_message(error))], None, None

    rows = [activity("Amazon S3", "Upload document", "Success",
                     f"S3 confirmed the upload of s3://{bucket}/{key} (private object).")]
    # Checks S3's ANSWER, not just our request: did S3 really use SSE-KMS with our key?
    if result["encryption"] == "aws:kms" and result["kms_key_id"] == kms_key_arn:
        rows.append(activity("AWS KMS", "Verify SSE-KMS encryption", "Verified",
                             "S3 reported aws:kms encryption with the configured KMS key."))
    else:
        rows.append(activity("AWS KMS", "Verify SSE-KMS encryption", "Failed",
                             f"S3 reported encryption '{result['encryption']}' with key '{result['kms_key_id']}', "
                             "which does not match S3_KMS_KEY_ARN. Use the key ARN, not an alias."))
    return rows, bucket, key


def lambda_step(event):
    """Runs the Lambda function, which sends the confirmation email with SES.

    Returns: (activity rows, processing status, email status).
    """
    function_name = get_settings()["lambda_function_name"]
    if not function_name:
        row = missing_setting("AWS Lambda", "Process submission", "LAMBDA_FUNCTION_NAME")
        email_row = activity("Amazon SES", "Send confirmation email", row["status"],
                             "No email was sent because the Lambda function did not run.")
        return [row, email_row], row["status"], row["status"]

    try:
        answer = lambda_functions.invoke_submission_processor(function_name, event)
    except Exception as error:
        logger.warning("Lambda invocation failed for submission %s: %s", event["submission_id"], error)
        # The function may have stopped part-way, so we cannot know whether an email went out.
        return [activity("AWS Lambda", "Process submission", "Failed", friendly_error_message(error)),
                activity("Amazon SES", "Send confirmation email", "Unknown",
                         "The Lambda function failed, so the email result is not known.")], "Failed", "Unknown"

    processing_status = answer.get("processing_status", "Unknown")
    email_status = answer.get("email_status", "Unknown")
    return [activity("AWS Lambda", "Process submission", processing_status,
                     f"Function '{function_name}' ran and returned its result."),
            activity("Amazon SES", "Send confirmation email", email_status,
                     answer.get("email_detail", "The function did not report an email result."))], \
        processing_status, email_status


def messaging_steps(message):
    """Sends the optional SNS and SQS messages. Returns a list of activity rows."""
    settings = get_settings()
    rows = []
    # Each entry: (service name, action, setting key, setting name, function to call).
    for service, action, setting, setting_name, send in (
            ("Amazon SNS", "Publish notification", "sns_topic_arn", "SNS_TOPIC_ARN", messaging.publish_to_sns),
            ("Amazon SQS", "Queue message", "sqs_queue_url", "SQS_QUEUE_URL", messaging.send_to_sqs)):
        if not settings[setting]:
            rows.append(activity(service, action, "Skipped", f"Optional: {setting_name} is not set."))
            continue
        try:
            message_id = send(settings[setting], message)
            rows.append(activity(service, action, "Success", f"Accepted with MessageId {message_id}."))
        except Exception as error:
            logger.warning("%s failed for submission %s: %s", service, message["submission_id"], error)
            rows.append(activity(service, action, "Failed", friendly_error_message(error)))
    return rows


def process_submission(submission_id, login_username, values, document):
    """Runs the whole workflow and returns the result for the page.

    submission_id: a random ID from the form (it also prevents duplicate submissions).
    login_username: who is logged in. The login password is never passed here.
    values: the validated form fields. document: None, or the dictionary from core/form_checks.check_document().
    Returns: a dictionary with every database column, "activity" (a list), and "saved" (True/False).
    """
    # 1. Record the submission time (UTC, without fractions of a second).
    submitted_at = datetime.now(timezone.utc).replace(microsecond=0)

    # 2. Upload the optional document to S3.
    activities, bucket, key = upload_step(submission_id, submitted_at, document)
    upload_status = activities[0]["status"]

    # 3. Save the submission. Lambda and email are "Pending" until step 4 finishes.
    record = dict(values, id=submission_id, submitted_at=submitted_at, login_username=login_username,
                  document_name=document["name"] if document else None, s3_bucket=bucket, s3_object_key=key,
                  upload_status=upload_status, processing_status="Pending", email_status="Pending",
                  activity=activities)
    try:
        submission_repository.insert_submission(record)
        saved = True
        if submission_repository.using_rds():
            detail = "Saved to the lab_submissions table in Amazon RDS (PostgreSQL)."
        else:
            detail = "Saved to the local SQLite file. This is NOT an Amazon RDS record."
        activities.append(activity(submission_repository.backend_label(), "Save submission", "Success", detail))
    except Exception as error:
        saved = False
        logger.warning("Database save failed for submission %s: %s", submission_id, error)
        activities.append(activity(submission_repository.backend_label(), "Save submission", "Failed",
                                   submission_repository.describe_database_error(error)))

    # 4. Process the submission with Lambda (+ SES email) and the optional SNS/SQS messages.
    #    Skipped if the database save failed, because Lambda would process a record that does not exist.
    event = {"submission_id": submission_id, "submitted_at": submitted_at.isoformat(),
             "username": values["username"], "first_name": values["first_name"], "email": values["email"],
             "document_uploaded": upload_status == "Success", "document_name": record["document_name"]}
    if saved:
        rows, record["processing_status"], record["email_status"] = lambda_step(event)
        activities.extend(rows)
        activities.extend(messaging_steps({"submission_id": submission_id, "username": values["username"],
                                           "submitted_at": event["submitted_at"],
                                           "document_uploaded": event["document_uploaded"]}))
    else:
        record["processing_status"] = record["email_status"] = "Skipped"
        activities.append(activity("AWS Lambda", "Process submission", "Skipped",
                                   "Skipped because the database save failed."))

    # "Success" only if the database confirmed the INSERT in step 3.
    record["database_status"] = "Success" if saved else "Failed"

    # 5. Store the final statuses, so the result page can be reloaded later.
    if saved:
        try:
            submission_repository.update_submission_results(submission_id, record["processing_status"],
                                                            record["email_status"], activities)
        except Exception as error:
            logger.warning("Database update failed for submission %s: %s", submission_id, error)
            activities.append(activity(submission_repository.backend_label(), "Save final statuses", "Failed",
                                       submission_repository.describe_database_error(error)))
            saved = False  # The stored copy is incomplete, so the page shows this in-memory result instead.

    # True = the full result is in the database, so the page can redirect to it (see routes/lab_routes.py).
    record["saved"] = saved
    return record
