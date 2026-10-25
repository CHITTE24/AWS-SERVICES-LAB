"""Amazon SNS (notifications) and Amazon SQS (queues): send a small "submission received" message.

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
