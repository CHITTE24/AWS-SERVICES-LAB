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


def head_bucket(bucket_name, config=None):
    """Checks that a bucket exists and this identity may access it. Reads no objects. Needs s3:ListBucket.

    Returns: the region the bucket lives in, for example "ap-south-1".
    Raises ClientError "404" (no such bucket) or "403" (access denied OR another account's bucket).
    """
    s3 = get_client("s3", config=config)
    response = s3.head_bucket(Bucket=bucket_name)
    # Newer Boto3 versions return "BucketRegion". Older ones only have the HTTP header.
    headers = response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    return response.get("BucketRegion") or headers.get("x-amz-bucket-region", "")


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


def upload_document(bucket_name, object_key, content, content_type, kms_key_arn, metadata=None):
    """Uploads one private document, encrypted with SSE-KMS (server-side encryption with an AWS KMS key).

    bucket_name / object_key: where to store the file. The key is the file's "path" inside the bucket.
    content: the file's bytes.
    content_type: for example "application/pdf".
    kms_key_arn: the customer-managed KMS key that S3 must use to encrypt the file.
    metadata: optional small labels stored with the object, for example {"workflow-id": "..."}.
    Returns: what S3 reports it actually did, so the caller can VERIFY the encryption.
    """
    s3 = get_client("s3")
    # No ACL is sent, so the object stays private (only your account's IAM identities can read it).
    response = s3.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=content,
        ContentType=content_type,
        ServerSideEncryption="aws:kms",  # Ask S3 to encrypt with KMS ...
        SSEKMSKeyId=kms_key_arn,  # ... using exactly this key.
        Metadata=metadata or {},
    )
    return {
        # S3 answers with the encryption it applied. "aws:kms" means SSE-KMS.
        "encryption": response.get("ServerSideEncryption", ""),
        # The ARN of the KMS key S3 used.
        "kms_key_id": response.get("SSEKMSKeyId", ""),
        # S3's identifier for this exact upload. (With SSE-KMS it is NOT an MD5 checksum of the file.)
        "etag": response.get("ETag", ""),
    }


def head_object(bucket_name, object_key):
    """Reads an object's METADATA only (no content is downloaded or decrypted). Needs s3:GetObject.

    No checksum is requested (ChecksumMode is not set), so this is the cheapest possible check.
    Returns: size, ETag, encryption, KMS key, and the object's own metadata labels.
    Raises ClientError "404" if the object does not exist.
    """
    response = get_client("s3").head_object(Bucket=bucket_name, Key=object_key)
    return {
        "size": response.get("ContentLength", 0),
        "etag": response.get("ETag", ""),
        "encryption": response.get("ServerSideEncryption", ""),
        "kms_key_id": response.get("SSEKMSKeyId", ""),
        "metadata": response.get("Metadata", {}),
    }
