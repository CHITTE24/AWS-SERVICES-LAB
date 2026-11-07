"""The DynamoDB store for Live Workflow Monitor stages (table aws-lab-workflows, WORKFLOW_TABLE_NAME).

Table design (you create the table yourself in Phase 5; nothing here creates AWS resources):
    Partition key "workflow_id" (String), sort key "item" (String), on-demand capacity,
    Time To Live on the attribute "expires_at_epoch" (old workflows are deleted automatically, for free).
Items of one workflow:
    item = "META"                        -> mode, s3_key, file_size, created_at (no owner, no file content)
    item = "STAGE#02#upload", ...        -> one item per stage: state, detail, source, attempt, times

Why this is safe with duplicates, retries, and concurrent writers:
    - Creation is ONE transaction that fails if the workflow already exists.
    - Every stage change is an UpdateItem with a ConditionExpression built from
      experiments/workflow_states.ALLOWED_PREVIOUS: DynamoDB itself refuses a change the rules do not allow.
    - Reads use ConsistentRead, so the page always sees the latest write.
"""

from decimal import Decimal  # DynamoDB returns numbers as Decimal.

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer  # Plain Python values <-> DynamoDB format.
from botocore.exceptions import ClientError  # The error type AWS services raise.

from core.aws_client import get_client  # Creates Boto3 clients (IAM role on EC2, never access keys).
from core.config import get_settings  # Reads WORKFLOW_TABLE_NAME.
from experiments import workflow_states as rules  # States and allowed changes.

SERIALIZER = TypeSerializer()
DESERIALIZER = TypeDeserializer()


def table_name():
    return get_settings()["workflow_table_name"]


def to_item(values):
    # {"state": "Running"} -> {"state": {"S": "Running"}}. Empty (None) values are left out.
    return {name: SERIALIZER.serialize(value) for name, value in values.items() if value is not None}


def from_item(item):
    # The reverse of to_item. Whole numbers come back as int instead of Decimal.
    values = {}
    for name, value in item.items():
        plain = DESERIALIZER.deserialize(value)
        values[name] = int(plain) if isinstance(plain, Decimal) else plain
    return values


def stage_key(stage_id):
    # "upload" -> "STAGE#02#upload". The number keeps the stages in display order.
    position = [stage[0] for stage in rules.STAGES].index(stage_id) + 1
    return f"STAGE#{position:02d}#{stage_id}"


def expires_epoch(meta):
    # DynamoDB Time To Live needs a number: seconds since 1970.
    return int(rules.from_text(meta["expires_at"]).timestamp())


def create_workflow(meta, stages):
    """Writes META and every stage in ONE transaction. Returns False if the workflow already exists."""
    expires = expires_epoch(meta)
    condition = {"ConditionExpression": "attribute_not_exists(workflow_id)"}
    meta_item = {"workflow_id": meta["workflow_id"], "item": "META", "mode": meta["mode"], "s3_key": meta["s3_key"],
                 "file_size": meta["file_size"], "created_at": meta["created_at"], "expires_at_epoch": expires}
    actions = [{"Put": {"TableName": table_name(), "Item": to_item(meta_item), **condition}}]
    for stage in stages:
        stage_item = dict(stage, workflow_id=meta["workflow_id"], item=stage_key(stage["stage_id"]),
                          expires_at_epoch=expires)
        actions.append({"Put": {"TableName": table_name(), "Item": to_item(stage_item), **condition}})
    client = get_client("dynamodb")
    try:
        client.transact_write_items(TransactItems=actions)
        return True
    except ClientError as error:
        # The transaction is cancelled when the workflow already exists (a repeated request).
        if error.response.get("Error", {}).get("Code") == "TransactionCanceledException" and \
                meta_exists(client, meta["workflow_id"]):
            return False
        raise


def meta_exists(client, workflow_id):
    response = client.get_item(TableName=table_name(), Key=to_item({"workflow_id": workflow_id, "item": "META"}),
                               ConsistentRead=True)
    return "Item" in response


def set_stage(workflow_id, stage_id, new_state, detail, source, attempt, deadline_at, now_text):
    """Conditional UpdateItem. Returns True if the stage changed, False if the rules refused it."""
    previous_states = rules.ALLOWED_PREVIOUS.get(new_state, ())
    if not previous_states:
        return False

    # DynamoDB rejects names and values that the expressions do not use, so both are built step by step.
    names = {"#wid": "workflow_id", "#state": "state", "#detail": "detail", "#source": "source",
             "#updated": "updated_at", "#finished": "finished_at"}
    values = {":state": new_state, ":detail": detail, ":source": source, ":now": now_text}
    assignments = ["#state = :state", "#detail = :detail", "#source = :source", "#updated = :now"]
    if attempt is not None:
        names["#attempt"] = "attempt"
        values[":attempt"] = attempt
        assignments.append("#attempt = :attempt")
    if deadline_at:
        names["#deadline"] = "deadline_at"
        values[":deadline"] = deadline_at
        assignments.append("#deadline = :deadline")
    if new_state in (rules.RUNNING, rules.RETRYING):
        names["#started"] = "started_at"
        assignments.append("#started = if_not_exists(#started, :now)")  # Keeps the FIRST start time.
    expression = "SET " + ", ".join(assignments)
    if new_state in rules.FINAL_STATES:
        expression += ", #finished = :now"
    else:
        expression += " REMOVE #finished"

    # The safety check, evaluated by DynamoDB itself at the moment of writing.
    allowed = []
    for number, state in enumerate(previous_states):
        values[f":p{number}"] = state
        allowed.append(f":p{number}")
    condition = f"attribute_exists(#wid) AND #state IN ({', '.join(allowed)})"
    if attempt is not None:
        condition += " AND (attribute_not_exists(#attempt) OR #attempt <= :attempt)"

    try:
        get_client("dynamodb").update_item(
            TableName=table_name(), Key=to_item({"workflow_id": workflow_id, "item": stage_key(stage_id)}),
            UpdateExpression=expression, ConditionExpression=condition,
            ExpressionAttributeNames=names, ExpressionAttributeValues=to_item(values))
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False  # Not allowed (duplicate, stale retry, or already final): ignored on purpose.
        raise


def get_stages(workflow_id):
    """All stages of a workflow, in display order. Empty if the items expired (Time To Live)."""
    client = get_client("dynamodb")
    stages = []
    request = {"TableName": table_name(), "ConsistentRead": True,
               "KeyConditionExpression": "#wid = :id AND begins_with(#item, :prefix)",
               "ExpressionAttributeNames": {"#wid": "workflow_id", "#item": "item"},
               "ExpressionAttributeValues": to_item({":id": workflow_id, ":prefix": "STAGE#"})}
    while True:
        response = client.query(**request)
        stages.extend(from_item(item) for item in response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        request["ExclusiveStartKey"] = response["LastEvaluatedKey"]  # The next page (rarely needed).
    return sorted(stages, key=lambda stage: stage["item"])
