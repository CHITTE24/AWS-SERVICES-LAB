"""Amazon DynamoDB (NoSQL database): read-only table information."""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def list_tables(region=None):
    """Lists DynamoDB table names in one region. Returns a list of dictionaries with each table's name."""
    dynamodb = get_client("dynamodb", region)
    paginator = dynamodb.get_paginator("list_tables")

    tables = []
    for page in paginator.paginate():
        for table_name in page["TableNames"]:
            tables.append({"table_name": table_name})
    return tables


def describe_table(table_name, region=None):
    """Returns details for one table: status, approximate item count, size, billing mode, and keys.

    table_name: the exact table name (case-sensitive).
    """
    dynamodb = get_client("dynamodb", region)
    response = dynamodb.describe_table(TableName=table_name)
    table = response["Table"]

    # Builds text such as "id (HASH), created_at (RANGE)" from the key schema.
    key_parts = []
    for key in table["KeySchema"]:
        key_parts.append(f"{key['AttributeName']} ({key['KeyType']})")

    # Tables created long ago may have no BillingModeSummary. That means the PROVISIONED mode.
    billing = table.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")

    return {
        "table_name": table["TableName"],
        "status": table["TableStatus"],
        "item_count": table.get("ItemCount", 0),  # AWS updates this roughly every 6 hours.
        "size_bytes": table.get("TableSizeBytes", 0),
        "billing_mode": billing,
        "keys": ", ".join(key_parts),
    }
