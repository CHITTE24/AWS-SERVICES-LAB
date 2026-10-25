"""Amazon S3 (object storage): read-only bucket and object information."""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def list_buckets():
    """Lists every S3 bucket in the account.

    S3 bucket names are global, so this works from any region.
    Returns: a list of dictionaries, each with a bucket name and creation date.
    """
    s3 = get_client("s3")
    # One API call returns all buckets the credentials can see.
    response = s3.list_buckets()

    buckets = []
    for bucket in response.get("Buckets", []):
        # str() turns the datetime into text so it can be shown on a page and saved to history.
        buckets.append({"name": bucket["Name"], "created": str(bucket["CreationDate"])})
    return buckets


def get_bucket_region(bucket_name):
    """Returns the region a bucket lives in, for example "eu-west-1".

    bucket_name: the name of an existing bucket.
    """
    s3 = get_client("s3")
    response = s3.get_bucket_location(Bucket=bucket_name)
    region = response.get("LocationConstraint")

    # For historical reasons AWS returns an empty value for buckets in us-east-1.
    if not region:
        region = "us-east-1"
    return region


def count_objects(bucket_name, region=None, max_objects=1000):
    """Counts objects and their total size, looking at no more than max_objects objects.

    The limit keeps the call fast and cheap on very large buckets.
    bucket_name: the bucket to inspect.
    region: the bucket's region. Using the correct region avoids redirect errors.
    max_objects: the maximum number of objects to look at (AWS allows up to 1000 per call).
    Returns: a dictionary with the object count, total bytes, and whether more objects exist.
    """
    s3 = get_client("s3", region)
    # Lists objects. MaxKeys caps how many come back in this single call.
    response = s3.list_objects_v2(Bucket=bucket_name, MaxKeys=max_objects)
    objects = response.get("Contents", [])

    total_bytes = 0
    for item in objects:
        total_bytes = total_bytes + item["Size"]

    return {
        "bucket": bucket_name,
        "objects_counted": len(objects),
        "total_bytes": total_bytes,
        # True when the bucket has more objects than were counted.
        "more_objects_exist": response.get("IsTruncated", False),
    }
