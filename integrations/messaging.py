"""Amazon SNS (notifications) and Amazon SQS (queues): send a small "submission received" message,
and read-only existence checks used by the "Check AWS environment" button.

Both are OPTIONAL. They only run when SNS_TOPIC_ARN or SQS_QUEUE_URL is set in .env.
Messages contain the submission ID and a few details, never the document or the login password.
"""

import json  # Messages are sent as JSON text.

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def publish_to_sns(topic_arn, message):
    """Publishes a message to an SNS topic. Every subscriber (email, SQS, Lambda, ...) receives a copy.

    Returns: the MessageId that SNS gives the accepted message.
    """
    response = get_client("sns").publish(TopicArn=topic_arn, Subject="AWS Service Lab submission",
                                         Message=json.dumps(message))
    return response["MessageId"]


def send_to_sqs(queue_url, message):
    """Puts a message on an SQS queue, where a worker (for example a Lambda function) can process it later.

    Returns: the MessageId that SQS gives the stored message.
    """
    response = get_client("sqs").send_message(QueueUrl=queue_url, MessageBody=json.dumps(message))
    return response["MessageId"]


def get_topic_attributes(topic_arn, config=None):
    """Checks that an SNS topic exists WITHOUT publishing. Needs sns:GetTopicAttributes.

    Returns: the number of confirmed subscriptions (how many receivers a published message would reach).
    """
    response = get_client("sns", config=config).get_topic_attributes(TopicArn=topic_arn)
    return {"subscriptions": int(response["Attributes"].get("SubscriptionsConfirmed", 0))}


def get_queue_attributes(queue_url, config=None):
    """Checks that an SQS queue exists WITHOUT sending or receiving. Needs sqs:GetQueueAttributes.

    Returns: the approximate number of messages waiting in the queue.
    """
    response = get_client("sqs", config=config).get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["ApproximateNumberOfMessages"])
    return {"messages": int(response["Attributes"].get("ApproximateNumberOfMessages", 0))}
