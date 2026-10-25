"""The list of experiments shown on the Experiments page.

To add an experiment: create a file in experiments/ with a run() function, then add a dictionary here.
"inputs" uses the same field format as the service catalog (see core/inputs.py). Each input's
"name" must match a parameter name of the run() function.
"""

from experiments import account_overview, ec2_security_review, lambda_health, s3_storage_report

# Reusable form field: an optional region. Left empty, the default region from .env is used.
REGION_FIELD = {"name": "region", "label": "Region (empty = default)", "type": "region"}

EXPERIMENTS = [
    {"id": "account_overview", "title": "Account Overview",
     "description": "Who am I, how many IAM users and roles, S3 buckets, and EC2 instances?",
     "services": ["STS", "IAM", "S3", "EC2"],
     "inputs": [REGION_FIELD],
     "run": account_overview.run},
    {"id": "s3_storage_report", "title": "S3 Storage Report",
     "description": "Region, object count, and size for each bucket (sampled).",
     "services": ["S3"],
     "inputs": [{"name": "max_buckets", "label": "Max buckets to inspect", "type": "number",
                 "default": 10, "min": 1, "max": 50}],
     "run": s3_storage_report.run},
    {"id": "ec2_security_review", "title": "EC2 Security Review",
     "description": "Finds firewall rules open to the whole internet, such as SSH on 0.0.0.0/0.",
     "services": ["EC2"],
     "inputs": [REGION_FIELD],
     "run": ec2_security_review.run},
    {"id": "lambda_health", "title": "Lambda Health",
     "description": "Invocations and errors per Lambda function, from CloudWatch metrics.",
     "services": ["Lambda", "CloudWatch"],
     "inputs": [{"name": "hours", "label": "Hours to look back", "type": "number",
                 "default": 24, "min": 1, "max": 168},
                REGION_FIELD],
     "run": lambda_health.run},
]


def get_experiment(experiment_id):
    """Returns the experiment dictionary whose id matches, or None if there is none."""
    for experiment in EXPERIMENTS:
        if experiment["id"] == experiment_id:
            return experiment
    return None
