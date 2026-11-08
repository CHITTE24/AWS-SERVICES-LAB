"""Who and where the web app is, for Architecture Map evidence: its own resource key, AWS account, and region.

app_resource_key():
    On EC2: "ec2:<instance id>" (asked from the instance metadata service, IMDSv2).
    APP_MODE=local and not on EC2: "external:this-computer".
    APP_MODE=aws and the metadata service did not answer: None ("unknown"). Evidence about the app's own
    operations is then stored as UNATTRIBUTED instead of being credited to a guessed box. It is asked again
    after RETRY_SECONDS, so a short metadata outage does not last for the life of the app.
aws_account():
    The AWS account of the app's credentials (one free, read-only STS GetCallerIdentity call, remembered).
    None if it cannot be found (no credentials, no network); retried after RETRY_SECONDS.
The region is simply AWS_REGION at the moment of the operation (see core/config.py).
"""

import logging
import time

from catalog.architecture_catalog import LOCAL_APP_KEY  # "external:this-computer"
from core.aws_client import CHECK_CONFIG  # Short timeouts.
from core.config import get_settings
from integrations import ec2, sts

logger = logging.getLogger(__name__)
RETRY_SECONDS = 300
_cache = {}  # "key" / "account": the answer; "key_failed_at" / "account_failed_at": when a lookup last failed.


def _recently_failed(name):
    failed_at = _cache.get(f"{name}_failed_at")
    return failed_at is not None and time.monotonic() - failed_at < RETRY_SECONDS


def app_resource_key():
    """Returns "ec2:i-...", "external:this-computer" (local mode only), or None if it cannot be established."""
    if "key" in _cache:
        return _cache["key"]
    if _recently_failed("key"):
        return None
    try:
        _cache["key"] = f"ec2:{ec2.get_current_instance()['instance_id']}"
    except Exception:
        if get_settings()["app_mode"] == "local":
            _cache["key"] = LOCAL_APP_KEY  # Not on EC2, and that is expected locally.
        else:
            _cache["key_failed_at"] = time.monotonic()
            logger.warning("Architecture Map: the EC2 instance identity is unknown; app evidence is unattributed.")
            return None
    logger.info("Architecture Map: this app is %s", _cache["key"])
    return _cache["key"]


def aws_account():
    """The 12-digit AWS account of the app's credentials, or None if it cannot be established right now."""
    if "account" in _cache:
        return _cache["account"]
    if _recently_failed("account"):
        return None
    try:
        _cache["account"] = sts.get_caller_identity(config=CHECK_CONFIG)["account"]
    except Exception as error:
        _cache["account_failed_at"] = time.monotonic()
        logger.warning("Architecture Map: the AWS account is unknown (%s); evidence is unattributed.",
                       type(error).__name__)
        return None
    return _cache["account"]


def scope():
    """{"account": ..., "region": ...} for evidence recorded right now (account may be None)."""
    return {"account": aws_account(), "region": get_settings()["aws_region"]}


def reset():
    # Forgets every cached answer (used by tests).
    _cache.clear()
