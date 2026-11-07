"""Amazon SES (email): read-only account status. No email is ever sent from here.

In this app the Lambda function sends the email, using ITS OWN execution role. This check runs
with the EC2 role, so it shows the account's SES status in this region, NOT whether the
Lambda function's role is allowed to send.
"""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def get_account(config=None):
    """Reads the SES account status for the default region. Needs ses:GetAccount (SES API v2)."""
    response = get_client("sesv2", config=config).get_account()
    return {
        "sending_enabled": response.get("SendingEnabled", False),
        # False = SES "sandbox": email can only be sent to verified addresses.
        "production_access": response.get("ProductionAccessEnabled", False),
        "enforcement_status": response.get("EnforcementStatus", ""),  # "HEALTHY", "PROBATION", "SHUTDOWN".
    }
