"""The service catalog: every AWS service on the dashboard and the operations it offers.

Each operation links to a Python function in integrations/. "kind" is "read" (safe) or "write"
(creates, changes, or deletes resources). Write operations are blocked unless ALLOW_WRITE_OPERATIONS=true.
"inputs" describes the form fields shown for the operation (see core/inputs.py).
"""

from catalog.categories import CATEGORIES  # The category list, used to group services.
from integrations import cloudwatch, dynamodb, ec2, iam, lambda_functions, s3, sts  # The AWS functions.

# Reusable form field: an optional region. Left empty, the default region from .env is used.
REGION_FIELD = {"name": "region", "label": "Region (empty = default)", "type": "region"}

SERVICES = [
    {"id": "sts", "name": "AWS STS", "category": "security",
     "description": "Security Token Service. Shows which identity your credentials belong to.",
     "operations": [
         {"id": "get_caller_identity", "label": "Who am I?", "kind": "read", "inputs": [],
          "function": sts.get_caller_identity},
     ]},
    {"id": "iam", "name": "AWS IAM", "category": "security",
     "description": "Identity and Access Management. Users, roles, and permissions.",
     "operations": [
         {"id": "list_users", "label": "List users", "kind": "read", "inputs": [], "function": iam.list_users},
         {"id": "list_roles", "label": "List roles", "kind": "read", "inputs": [], "function": iam.list_roles},
         {"id": "get_account_summary", "label": "Account summary", "kind": "read", "inputs": [],
          "function": iam.get_account_summary},
     ]},
    {"id": "s3", "name": "Amazon S3", "category": "storage",
     "description": "Simple Storage Service. Stores files (objects) inside buckets.",
     "operations": [
         {"id": "list_buckets", "label": "List buckets", "kind": "read", "inputs": [], "function": s3.list_buckets},
         {"id": "count_objects", "label": "Count objects in a bucket", "kind": "read",
          "inputs": [{"name": "bucket_name", "label": "Bucket name", "type": "text", "required": True},
                     REGION_FIELD],
          "function": s3.count_objects},
     ]},
    {"id": "ec2", "name": "Amazon EC2", "category": "compute",
     "description": "Elastic Compute Cloud. Virtual servers and their firewalls (security groups).",
     "operations": [
         {"id": "list_instances", "label": "List instances", "kind": "read", "inputs": [REGION_FIELD],
          "function": ec2.list_instances},
         {"id": "list_security_groups", "label": "List security groups", "kind": "read",
          "inputs": [REGION_FIELD], "function": ec2.list_security_groups},
         {"id": "list_regions", "label": "List enabled regions", "kind": "read", "inputs": [],
          "function": ec2.list_regions},
     ]},
    {"id": "lambda", "name": "AWS Lambda", "category": "compute",
     "description": "Runs code without managing servers. You pay only while it runs.",
     "operations": [
         {"id": "list_functions", "label": "List functions", "kind": "read", "inputs": [REGION_FIELD],
          "function": lambda_functions.list_functions},
     ]},
    {"id": "dynamodb", "name": "Amazon DynamoDB", "category": "database",
     "description": "Fast NoSQL key-value database.",
     "operations": [
         {"id": "list_tables", "label": "List tables", "kind": "read", "inputs": [REGION_FIELD],
          "function": dynamodb.list_tables},
         {"id": "describe_table", "label": "Describe a table", "kind": "read",
          "inputs": [{"name": "table_name", "label": "Table name", "type": "text", "required": True},
                     REGION_FIELD],
          "function": dynamodb.describe_table},
     ]},
    {"id": "cloudwatch", "name": "Amazon CloudWatch", "category": "monitoring",
     "description": "Collects metrics and raises alarms when something looks wrong.",
     "operations": [
         {"id": "list_alarms", "label": "List alarms", "kind": "read", "inputs": [REGION_FIELD],
          "function": cloudwatch.list_alarms},
     ]},
    # The next two are listed for learning only. They have no operations yet.
    # docs/CODE_CUSTOMIZATION_GUIDE.md shows how to add one.
    {"id": "sqs", "name": "Amazon SQS", "category": "integration",
     "description": "Simple Queue Service. Message queues between parts of an application.", "operations": []},
    {"id": "sns", "name": "Amazon SNS", "category": "integration",
     "description": "Simple Notification Service. Sends messages to email, SMS, and other services.",
     "operations": []},
]


def get_service(service_id):
    """Returns the service dictionary whose id matches, or None if there is none."""
    for service in SERVICES:
        if service["id"] == service_id:
            return service
    return None


def get_operation(service, operation_id):
    """Returns the operation dictionary within a service whose id matches, or None."""
    for operation in service["operations"]:
        if operation["id"] == operation_id:
            return operation
    return None


def services_by_category():
    """Groups services for the dashboard. Returns a list of (category, list_of_services) pairs."""
    groups = []
    for category in CATEGORIES:
        services_in_category = []
        for service in SERVICES:
            if service["category"] == category["id"]:
                services_in_category.append(service)
        groups.append((category, services_in_category))
    return groups
