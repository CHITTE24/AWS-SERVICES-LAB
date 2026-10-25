"""AWS Lambda function: processes one AWS Service Lab submission and emails a confirmation with Amazon SES.

You deploy this file yourself (README -> "AWS Security Setup", step 11). It is NOT run by the web app.
    Runtime: Python 3.12 or newer      Handler: submission_processor.lambda_handler
    Timeout: 15 seconds                Environment variable: SES_SENDER_EMAIL (a verified SES identity)

The web app (experiments/submission_workflow.py) sends an event like this. It never contains the
document itself or the login password:
    {"submission_id": "...", "submitted_at": "...", "username": "...", "first_name": "...",
     "email": "...", "document_uploaded": true, "document_name": "report.pdf"}

It returns:
    {"processing_status": "Success", "email_status": "Sent" | "Failed" | "Skipped", "email_detail": "..."}
"Sent" means SES ACCEPTED the email. It does not prove the email reached the inbox.
"""

import logging  # print() and logging output go to CloudWatch Logs (/aws/lambda/<function name>).
import os  # Reads SES_SENDER_EMAIL.

import boto3  # Already installed in the Lambda Python runtime.
from botocore.exceptions import ClientError  # The error type AWS services raise.

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Friendly explanations for common SES error codes.
SES_ERROR_HELP = {
    "MessageRejected": "SES rejected the email. In the SES sandbox, the recipient address must also be verified.",
    "MailFromDomainNotVerifiedException": "The sender's MAIL FROM domain is not verified in SES.",
    "Throttling": "SES is throttling: the sending rate limit was reached. Try again later.",
    "AccessDenied": "The Lambda execution role is not allowed to call ses:SendEmail for this sender.",
    "AccessDeniedException": "The Lambda execution role is not allowed to call ses:SendEmail for this sender.",
}


def build_email(event):
    # The email's subject and plain-text body. Only the first name and document name are personalised.
    if event.get("document_uploaded"):
        document_line = f"Your document '{event.get('document_name')}' was uploaded successfully."
    else:
        document_line = "No document was uploaded with this submission."
    subject = "AWS Service Lab: submission received"
    body = (f"Hello {event.get('first_name', '')},\n\n"
            f"We received your AWS Service Lab submission.\n"
            f"Submission ID: {event['submission_id']}\n"
            f"Submitted at: {event.get('submitted_at', '')}\n"
            f"{document_line}\n\n"
            "This is an automated message from an educational demo.")
    return subject, body


def lambda_handler(event, context):
    """Lambda calls this function once for each submission."""
    if not event.get("submission_id") or not event.get("email"):
        # Raising an error makes the web app show "Lambda processing: Failed".
        raise ValueError("The event must contain submission_id and email.")

    # Only the ID is logged, not the person's email address or other details.
    logger.info("Processing submission %s", event["submission_id"])

    sender = os.environ.get("SES_SENDER_EMAIL", "")
    if not sender:
        return {"processing_status": "Success", "email_status": "Skipped",
                "email_detail": "SES_SENDER_EMAIL is not set on the Lambda function, so no email was sent."}

    subject, body = build_email(event)
    try:
        # The app calls this function only once per submission ID and never retries, so one email is sent.
        response = boto3.client("ses").send_email(
            Source=sender,
            Destination={"ToAddresses": [event["email"]]},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        logger.warning("SES send failed for submission %s: %s", event["submission_id"], code)
        return {"processing_status": "Success", "email_status": "Failed",
                "email_detail": SES_ERROR_HELP.get(code, f"SES returned the error {code}.")}

    return {"processing_status": "Success", "email_status": "Sent",
            "email_detail": f"Accepted by Amazon SES (MessageId {response['MessageId']}). "
                            "Delivery to the inbox is not confirmed."}
