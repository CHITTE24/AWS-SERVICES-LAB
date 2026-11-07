"""AWS KMS (Key Management Service): read-only key information. Nothing is encrypted or decrypted."""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def describe_key(key_id, config=None):
    """Reads a key's METADATA (state, usage, ARN). Needs kms:DescribeKey.

    This proves the key exists and is visible. It does NOT prove permission to encrypt with it:
    the app may only encrypt through S3 (kms:GenerateDataKey with kms:ViaService), which is
    proven by each upload's SSE-KMS check.
    key_id: the key ARN from S3_KMS_KEY_ARN.
    """
    metadata = get_client("kms", config=config).describe_key(KeyId=key_id)["KeyMetadata"]
    return {
        "arn": metadata["Arn"],
        "state": metadata.get("KeyState", ""),  # "Enabled", "Disabled", "PendingDeletion", ...
        "usage": metadata.get("KeyUsage", ""),  # "ENCRYPT_DECRYPT" is needed for S3 encryption.
        "manager": metadata.get("KeyManager", ""),  # "CUSTOMER" = a key you created.
    }
