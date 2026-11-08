"""The Architecture Map's catalog: which boxes (resource types) and arrows (operations) exist.

A BOX is one specific AWS resource, identified by "type:identifier", for example "s3:lab-docs-ab12".
An ARROW is one specific OPERATION from one resource to another, for example
    "ec2:i-0abc1234" --s3:PutObject--> "s3:lab-docs-ab12"
Evidence is matched to these exact keys only (see experiments/architecture_evidence.py).

For each operation the catalog says plainly what its evidence PROVES and what it does NOT prove,
so a resource check (for example HeadBucket) is never presented as proof of an operation (PutObject).

Nothing here talks to AWS.
"""

import re  # Checks identifier formats.

# ---------------------------------------------------------------- resource types (boxes)
# type id -> label, identifier explanation, identifier pattern, icon file name, container (can hold other boxes)
RESOURCE_TYPES = {
    "ec2": {"label": "Amazon EC2", "identifier": "Instance ID, for example i-0abc1234def567890",
            "pattern": r"i-[0-9a-f]{8,17}", "icon": "ec2.svg"},
    "s3": {"label": "Amazon S3", "identifier": "Bucket name", "pattern": r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
           "icon": "s3.svg"},
    "lambda": {"label": "AWS Lambda", "identifier": "Function name", "pattern": r"[A-Za-z0-9_-]{1,64}",
               "icon": "lambda.svg"},
    "dynamodb": {"label": "Amazon DynamoDB", "identifier": "Table name", "pattern": r"[A-Za-z0-9_.-]{3,255}",
                 "icon": "dynamodb.svg"},
    "rds": {"label": "Amazon RDS", "identifier": "Endpoint host name (DB_HOST)",
            "pattern": r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", "icon": "rds.svg"},
    "kms": {"label": "AWS KMS", "identifier": "Key ID, for example 1234abcd-12ab-34cd-56ef-1234567890ab",
            "pattern": r"(mrk-)?[0-9a-f-]{32,36}", "icon": "kms.svg"},
    "secretsmanager": {"label": "AWS Secrets Manager", "identifier": "Secret name",
                       "pattern": r"[A-Za-z0-9/_+=.@-]{1,512}", "icon": "secrets-manager.svg"},
    "sns": {"label": "Amazon SNS", "identifier": "Topic name", "pattern": r"[A-Za-z0-9_-]{1,256}(\.fifo)?",
            "icon": "sns.svg"},
    "sqs": {"label": "Amazon SQS", "identifier": "Queue name", "pattern": r"[A-Za-z0-9_-]{1,80}(\.fifo)?",
            "icon": "sqs.svg"},
    "ses": {"label": "Amazon SES", "identifier": "Region of the SES account, for example ap-south-1",
            "pattern": r"[a-z]{2}(-[a-z]+)+-\d", "icon": "ses.svg"},
    "cloudwatch": {"label": "Amazon CloudWatch", "identifier": "Log group name, or \"metrics\"",
                   "pattern": r"metrics|[A-Za-z0-9_./#-]{1,512}", "icon": "cloudwatch.svg"},
    "iam": {"label": "IAM role", "identifier": "Role name", "pattern": r"[A-Za-z0-9+=,.@_-]{1,64}",
            "icon": "iam.svg"},
    "apigateway": {"label": "Amazon API Gateway", "identifier": "API ID", "pattern": r"[a-z0-9]{1,32}",
                   "icon": "api-gateway.svg"},
    "alb": {"label": "Application Load Balancer", "identifier": "Load balancer name",
            "pattern": r"[A-Za-z0-9-]{1,32}", "icon": "alb.svg"},
    "vpc": {"label": "VPC", "identifier": "VPC ID, for example vpc-0abc1234", "pattern": r"vpc-[0-9a-f]{8,17}",
            "icon": "vpc.svg", "container": True},
    "subnet": {"label": "Subnet", "identifier": "Subnet ID, for example subnet-0abc1234",
               "pattern": r"subnet-[0-9a-f]{8,17}", "icon": "subnet.svg", "container": True},
    "external": {"label": "External / this computer", "identifier": "A short name, for example this-computer",
                 "pattern": r"[A-Za-z0-9_.-]{1,64}", "icon": "external.svg"},
}

# The identity of the computer the web app runs on, when it is NOT an EC2 instance (for example your PC).
LOCAL_APP_KEY = "external:this-computer"
# Boxes that the web app itself can be (evidence for operations the app performs comes from it).
APP_TYPES = ("ec2", "external")

# ---------------------------------------------------------------- operations (arrows)
# Each operation: label, allowed source and target types, automatic (does the app produce evidence for it?),
# what evidence proves / does not prove, the IAM permission needed, and how to produce evidence.
OPERATIONS = {
    "s3:PutObject": {
        "label": "Upload Object", "sources": APP_TYPES, "targets": ("s3",), "automatic": True,
        "proves": "S3 accepted an upload from this app to this bucket (Live Workflow upload or lab-form document).",
        "not_proves": "That later reads work, or that the object is still there.",
        "iam": "App role: s3:PutObject on arn:aws:s3:::BUCKET/workflows/* (and /submissions/*). "
               "See deploy/iam-ec2-app-policy.json.",
        "how": "Start a REAL workflow on the Live Workflow Monitor, or submit the lab form with a document."},
    "s3:HeadObject": {
        "label": "Verify Object (HeadObject)", "sources": APP_TYPES + ("lambda",), "targets": ("s3",),
        "automatic": True,
        "proves": "This resource read the uploaded object's metadata (size, ETag, label) without reading content.",
        "not_proves": "That the content can be downloaded or decrypted.",
        "iam": "Role of the caller: s3:GetObject on arn:aws:s3:::BUCKET/workflows/*.",
        "how": "Start a REAL workflow; the app (stage 3) and the Lambda function (stage 6) both run HeadObject."},
    "kms:GenerateDataKey": {
        "label": "Encrypt via S3 (SSE-KMS)", "sources": APP_TYPES, "targets": ("kms",), "automatic": True,
        "proves": "S3 reported that an upload from this app was encrypted with exactly this KMS key.",
        "not_proves": "That anyone can decrypt the object.",
        "iam": "App role: kms:GenerateDataKey on the key, with kms:ViaService = s3.REGION.amazonaws.com, "
               "and the same in the key policy.",
        "how": "Start a REAL workflow (stage 4) or submit the lab form with a document."},
    "s3:ObjectCreated": {
        "label": "Event Notification (ObjectCreated)", "sources": ("s3",), "targets": ("lambda",),
        "automatic": True,
        "proves": "This Lambda function received an S3 ObjectCreated event for an object in this bucket.",
        "not_proves": "That the function processed it successfully (see the Lambda's own arrows).",
        "iam": "Lambda resource policy: s3.amazonaws.com may call lambda:InvokeFunction "
               "(aws:SourceArn = the bucket, aws:SourceAccount = your account).",
        "how": "Start a REAL workflow with WORKFLOW_LAMBDA_NAME set and the S3 event notification configured."},
    "dynamodb:TransactWriteItems": {
        "label": "Create Workflow Record", "sources": APP_TYPES, "targets": ("dynamodb",), "automatic": True,
        "proves": "This app created a workflow record in this table.",
        "not_proves": "That other applications can write to it.",
        "iam": "App role: dynamodb:PutItem (used by transactions) and dynamodb:GetItem on the table.",
        "how": "Start a REAL workflow with WORKFLOW_BACKEND=dynamodb."},
    "dynamodb:UpdateItem": {
        "label": "Update Workflow Stage", "sources": ("lambda",), "targets": ("dynamodb",), "automatic": True,
        "proves": "This Lambda function wrote a workflow stage to this table.",
        "not_proves": "Anything about the other stages.",
        "iam": "Lambda role: dynamodb:GetItem and dynamodb:UpdateItem on the table.",
        "how": "Start a REAL workflow; the Lambda function writes stages 5 and 6."},
    "lambda:InvokeFunction": {
        "label": "Invoke Function", "sources": APP_TYPES, "targets": ("lambda",), "automatic": True,
        "proves": "This app invoked this function and it returned a result (lab-form submission).",
        "not_proves": "That the function's own downstream calls (for example SES) succeeded.",
        "iam": "App role: lambda:InvokeFunction on the function.",
        "how": "Submit the lab form with LAMBDA_FUNCTION_NAME set."},
    "ses:SendEmail": {
        "label": "Send Email", "sources": ("lambda",), "targets": ("ses",), "automatic": True,
        "proves": "SES ACCEPTED an email from this function. Delivery to the inbox is never proven.",
        "not_proves": "That the email reached the inbox.",
        "iam": "Lambda role: ses:SendEmail for the verified sender identity.",
        "how": "Submit the lab form; the submission Lambda function sends the confirmation email."},
    "sns:Publish": {
        "label": "Publish Message", "sources": APP_TYPES, "targets": ("sns",), "automatic": True,
        "proves": "SNS accepted a message from this app (it returned a MessageId).",
        "not_proves": "That subscribers received it.",
        "iam": "App role: sns:Publish on the topic.",
        "how": "Submit the lab form with SNS_TOPIC_ARN set."},
    "sqs:SendMessage": {
        "label": "Send Message", "sources": APP_TYPES, "targets": ("sqs",), "automatic": True,
        "proves": "SQS stored a message from this app (it returned a MessageId).",
        "not_proves": "That a consumer processed it.",
        "iam": "App role: sqs:SendMessage on the queue.",
        "how": "Submit the lab form with SQS_QUEUE_URL set."},
    "secretsmanager:GetSecretValue": {
        "label": "Read Secret", "sources": APP_TYPES, "targets": ("secretsmanager",), "automatic": True,
        "proves": "This app read the secret's value (never displayed).",
        "not_proves": "That the value is correct (for example that the database accepts the password).",
        "iam": "App role: secretsmanager:GetSecretValue on the secret.",
        "how": "Click Check AWS environment (\"Retrieve value\")."},
    "rds:SqlQuery": {
        "label": "Database Query", "sources": APP_TYPES, "targets": ("rds",), "automatic": True,
        "proves": "This app connected to the database with TLS and ran a query.",
        "not_proves": "That every table or permission inside the database is correct.",
        "iam": "No IAM permission: network access (security group, port 5432) and the password from Secrets Manager.",
        "how": "Click Check AWS environment with DATABASE_BACKEND=postgres."},
    "logs:PutLogEvents": {
        "label": "Write Logs", "sources": ("lambda",), "targets": ("cloudwatch",), "automatic": True,
        "proves": "Log lines from this function arrived in this log group.",
        "not_proves": "What the log lines say.",
        "iam": "Lambda role: logs:CreateLogStream and logs:PutLogEvents on its log group.",
        "how": "Run the function once, then click Check AWS environment (\"Logs delivered\")."},
    "cloudwatch:GetMetricStatistics": {
        "label": "Read Metrics", "sources": APP_TYPES, "targets": ("cloudwatch",), "automatic": True,
        "proves": "This app could read CloudWatch metrics.",
        "not_proves": "That any metric data exists.",
        "iam": "App role: cloudwatch:GetMetricStatistics.",
        "how": "Click Check AWS environment (\"Metrics API access\")."},
    "sts:AssumeRole": {
        "label": "Instance Role (credentials)", "sources": ("iam",), "targets": ("ec2",), "automatic": True,
        "proves": "The app on this instance is using temporary credentials of this role.",
        "not_proves": "Which permissions the role has.",
        "iam": "The role must be attached to the instance (instance profile).",
        "how": "Click Check AWS environment on the EC2 instance."},
    # Design-only operations: drawn for completeness; the app never produces automatic evidence for them.
    "elb:ForwardRequest": {
        "label": "Forward HTTPS Request", "sources": ("alb",), "targets": APP_TYPES, "automatic": False,
        "proves": "", "not_proves": "No automatic check exists.",
        "iam": "Security group: allow the app port only from the load balancer's security group.",
        "how": "Record a manual verification after testing in a browser."},
    "apigateway:Invoke": {
        "label": "Invoke via API Gateway", "sources": ("apigateway",), "targets": ("lambda",), "automatic": False,
        "proves": "", "not_proves": "No automatic check exists.",
        "iam": "Lambda resource policy: apigateway.amazonaws.com may call lambda:InvokeFunction.",
        "how": "Record a manual verification after testing the API."},
    "sns:Subscription": {
        "label": "Deliver to Subscriber", "sources": ("sns",), "targets": ("sqs", "lambda"), "automatic": False,
        "proves": "", "not_proves": "No automatic check exists.",
        "iam": "Queue policy / Lambda resource policy allowing sns.amazonaws.com.",
        "how": "Record a manual verification after testing the subscription."},
    "custom": {
        "label": "Custom connection", "sources": tuple(RESOURCE_TYPES), "targets": tuple(RESOURCE_TYPES),
        "automatic": False, "proves": "", "not_proves": "Design only: no automatic check exists.",
        "iam": "Depends on the connection.", "how": "Record a manual verification with your evidence."},
}

# ---------------------------------------------------------------- limits for user-supplied diagrams
MAX_NODES = 200
MAX_EDGES = 400
MAX_DIAGRAM_BYTES = 200_000
MAX_LABEL = 60
MAX_NAME = 80
MAX_NOTES = 2000
ELEMENT_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
PRINTABLE = re.compile(r"[^\x00-\x1f\x7f]*")  # No control characters.


def resource_key(resource_type, identifier):
    """The exact identity of a box, for example "s3:lab-docs-ab12", or None if it has no identifier yet."""
    return f"{resource_type}:{identifier}" if identifier else None


def operation_key(source_key, operation, target_key):
    """The exact identity of an arrow, for example "ec2:i-0abc|s3:PutObject|s3:lab-docs"."""
    if not source_key or not target_key:
        return None
    return f"{source_key}|{operation}|{target_key}"


def valid_identifier(resource_type, identifier):
    """True if the identifier has the right format for its type (or is empty: an unidentified box)."""
    if identifier == "":
        return True
    pattern = RESOURCE_TYPES[resource_type]["pattern"]
    return bool(re.fullmatch(pattern, identifier))


def operation_allowed(operation, source_type, target_type):
    spec = OPERATIONS.get(operation)
    return bool(spec) and source_type in spec["sources"] and target_type in spec["targets"]


def kms_key_id(key_arn_or_id):
    # "arn:aws:kms:ap-south-1:123456789012:key/1234-..." -> "1234-...". A plain key ID stays as it is.
    return key_arn_or_id.split("key/")[-1] if key_arn_or_id else ""


def name_from_arn_or_url(value):
    # The last part of an ARN or queue URL: "arn:aws:sns:...:lab-topic" -> "lab-topic".
    return value.rstrip("/").replace("/", ":").split(":")[-1] if value else ""
