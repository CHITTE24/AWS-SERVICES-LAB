"""OPTIONAL live test against your REAL AWS account. Read-only and free.

It checks that your credentials work by calling:
  1. STS GetCallerIdentity (needs no permissions)
  2. S3 ListBuckets (needs s3:ListAllMyBuckets)
Nothing is created, changed, or deleted.

Usage (with the virtual environment activated and AWS credentials configured):
    python scripts/live_aws_check.py
"""

import sys  # Lets the script exit with an error code.
from pathlib import Path  # Builds file paths.

# Lets this script import project packages (one level above scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.aws_errors import friendly_error_message  # noqa: E402
from core.config import get_settings  # noqa: E402  (also loads .env, for example AWS_PROFILE)
from integrations import s3, sts  # noqa: E402


def main():
    print(f"Default region: {get_settings()['aws_region']}")

    # Step 1: confirm the credentials work at all.
    try:
        identity = sts.get_caller_identity()
    except Exception as error:
        print(f"FAILED  STS identity: {friendly_error_message(error)}")
        sys.exit(1)
    print(f"OK      Account {identity['account']} as {identity['arn']}")

    # Step 2: a simple read that needs an IAM permission.
    try:
        buckets = s3.list_buckets()
        print(f"OK      S3 ListBuckets: {len(buckets)} bucket(s)")
    except Exception as error:
        print(f"FAILED  S3 ListBuckets: {friendly_error_message(error)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
