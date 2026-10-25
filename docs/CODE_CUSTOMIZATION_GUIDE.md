# Code Customization Guide

Each example shows **which file to open**, **what to find**, and the **smallest safe change**.
After any Python change, run `python -m pytest`. With `--reload`, the app restarts by itself when you save.

---

## 1. Change the dashboard title

**File:** `.env` (no code change needed)

```ini
APP_TITLE=My AWS Playground
```

Restart the app. `core/config.py` reads `APP_TITLE`, and `core/templates.py` passes it to every page as `app_title`.

---

## 2. Add a new service category

**File:** `catalog/categories.py`. Find the `CATEGORIES` list and add one dictionary:

```python
CATEGORIES = [
    ...
    {"id": "integration", "name": "Application Integration", "description": "Queues and notifications."},
    {"id": "networking", "name": "Networking", "description": "VPCs, load balancers, DNS."},  # NEW
]
```

A category appears on the dashboard only when at least one service in `catalog/services.py` uses its `id`. The test `test_every_service_is_well_formed` checks that every service uses a category that exists.

---

## 3. Add a new AWS service to the catalog (example: Amazon SQS)

SQS is already listed with no operations. To make it work:

**Step 1. Create `integrations/sqs.py`:**

```python
"""Amazon SQS (message queues): read-only queue list."""

from core.aws_client import get_client  # Creates Boto3 clients with the app's settings.


def list_queues(region=None):
    """Lists SQS queue URLs in one region."""
    sqs = get_client("sqs", region)
    response = sqs.list_queues()  # Returns up to 1000 queue URLs.

    queues = []
    for queue_url in response.get("QueueUrls", []):  # The key is missing when there are no queues.
        queues.append({"queue_url": queue_url})
    return queues
```

**Step 2. In `catalog/services.py`**, add `sqs` to the integrations import and fill in the operations:

```python
from integrations import cloudwatch, dynamodb, ec2, iam, lambda_functions, s3, sqs, sts

    {"id": "sqs", "name": "Amazon SQS", "category": "integration",
     "description": "Simple Queue Service. Message queues between parts of an application.",
     "operations": [
         {"id": "list_queues", "label": "List queues", "kind": "read", "inputs": [REGION_FIELD],
          "function": sqs.list_queues},
     ]},
```

**Step 3.** Add `"sqs:ListQueues"` to `deploy/iam-readonly-policy.json` and to your own IAM policy.

**Step 4.** Add a test (see §9).

To add a brand-new service, also add a whole new dictionary to `SERVICES`, using an existing one as a template.

---

## 4. Add a new operation to an existing integration (example: S3 bucket versioning)

**Step 1. `integrations/s3.py`.** Add a function at the end:

```python
def get_bucket_versioning(bucket_name, region=None):
    """Returns whether versioning (keeping old copies of objects) is turned on for a bucket."""
    s3 = get_client("s3", region)
    response = s3.get_bucket_versioning(Bucket=bucket_name)
    # "Status" is missing if versioning was never turned on.
    return {"bucket": bucket_name, "versioning": response.get("Status", "Never enabled")}
```

**Step 2. `catalog/services.py`.** Find the `"id": "s3"` service and add to its `"operations"` list:

```python
         {"id": "get_bucket_versioning", "label": "Bucket versioning status", "kind": "read",
          "inputs": [{"name": "bucket_name", "label": "Bucket name", "type": "text", "required": True},
                     REGION_FIELD],
          "function": s3.get_bucket_versioning},
```

Each input's `"name"` **must match** a parameter name of the function (`bucket_name`, `region`).

**Step 3.** Add `"s3:GetBucketVersioning"` to your IAM policy.

> **Write operations:** if a new function creates, changes, or deletes something, set `"kind": "write"`. The route then calls `ensure_writes_allowed()`, which blocks it unless `ALLOW_WRITE_OPERATIONS=true`. Think about cost before enabling that.

---

## 5. Change the default AWS region

**File:** `.env`

```ini
AWS_REGION=eu-west-2
```

Restart the app. Any form with a "Region" field can still override it for one run.

---

## 6. Change an experiment's input fields

**File:** `experiments/registry.py`. Find the experiment, for example `lambda_health`:

```python
     "inputs": [{"name": "hours", "label": "Hours to look back", "type": "number",
                 "default": 24, "min": 1, "max": 168},
                REGION_FIELD],
```

- Change a limit: edit `"max": 168` (7 days) to `"max": 336` (14 days).
- Change the pre-filled value: edit `"default"`.
- Add a field: add a dictionary **and** add a parameter with the same name to that experiment's `run()` function, for example `def run(hours=24, region=None, max_functions=25):`.

Field types are `"text"`, `"number"`, and `"region"`. Validation lives in `core/inputs.py`.

---

## 7. Modify a result page

| Result page | Template |
|---|---|
| Single operation | `templates/operation_result.html` |
| Experiment | `templates/experiment_result.html` (the body comes from `render_experiment` in `_macros.html`) |
| Any table | the `render_data` macro in `templates/_macros.html` |

Example: show the run time on the operation result page. In `routes/dashboard_routes.py`, the route already passes `run_id`. Add a sentence to `templates/operation_result.html`:

```html
<p class="muted small">Saved to history as <a href="/history/{{ run_id }}">run #{{ run_id }}</a>.</p>
<p class="small">Tip: open the History page to compare runs over time.</p>   <!-- NEW -->
```

Anything in `{{ ... }}` must be passed from the route's `context` dictionary, or added globally in `core/templates.py → render()`.

---

## 8. Change logging behaviour

- **More or less detail:** set `LOG_LEVEL=DEBUG` (most) or `LOG_LEVEL=WARNING` (least) in `.env`.
- **Change the log line format:** edit `format=` in `core/logging_setup.py`.
- **Log from a new file:** follow the pattern used in `routes/dashboard_routes.py`:

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Something normal happened: %s", value)
logger.warning("Something unexpected happened: %s", value)
```

Never log passwords, secrets, or session cookies.

---

## 9. Add a new automated test

**File:** a `tests/test_*.py` file (pytest finds any function whose name starts with `test_`).

Example: a test for the SQS function from §3, using the fake AWS:

```python
import boto3
from integrations import sqs


def test_list_queues(fake_aws):              # fake_aws comes from tests/conftest.py
    boto3.client("sqs", region_name="us-east-1").create_queue(QueueName="lab-queue")
    queues = sqs.list_queues()
    assert queues[0]["queue_url"].endswith("/lab-queue")
```

Run only this test: `python -m pytest tests/test_integrations.py -k list_queues -v`.

If moto needs extra packages for a service, add it to the `moto[...]` list in `requirements-dev.txt`.

---

## 10. Add a new multi-service experiment (example: Storage Overview = S3 + DynamoDB)

**Step 1. Create `experiments/storage_overview.py`:**

```python
"""Experiment: Storage Overview. Counts S3 buckets and DynamoDB tables together."""

from experiments.steps import count_or_unknown, run_step
from integrations import dynamodb, s3


def run(region=None):
    errors = []
    buckets = run_step(errors, "List S3 buckets", s3.list_buckets)
    tables = run_step(errors, "List DynamoDB tables", dynamodb.list_tables, region)

    summary = {"S3 buckets": count_or_unknown(buckets), "DynamoDB tables": count_or_unknown(tables)}
    return {
        "summary": summary,
        "tables": [{"title": "DynamoDB tables", "rows": tables or []}],
        "notes": ["S3 is global; DynamoDB numbers are for the selected region only."],
        "errors": errors,
    }
```

**Step 2. Register it in `experiments/registry.py`.** Add it to the import line and add one dictionary to `EXPERIMENTS`:

```python
from experiments import account_overview, ec2_security_review, lambda_health, s3_storage_report, storage_overview

    {"id": "storage_overview", "title": "Storage Overview",
     "description": "S3 buckets and DynamoDB tables at a glance.",
     "services": ["S3", "DynamoDB"],
     "inputs": [REGION_FIELD],
     "run": storage_overview.run},
```

**Step 3.** Add a test in `tests/test_experiments.py` that calls `storage_overview.run()` with `fake_aws`.

---

## 11. Change application configuration safely

1. Add the new setting with a **placeholder** to `.env.example` and a comment explaining it.
2. Put your real value in `.env` (never commit `.env`).
3. Read it in `core/config.py → get_settings()`:

```python
        "max_history_rows": int(os.getenv("MAX_HISTORY_ROWS", "100")),
```

4. Use it wherever needed: `get_settings()["max_history_rows"]`.
5. Restart the app. `.env` is read once, at startup.

Rules: secrets go only in `.env` (or AWS Secrets Manager later). AWS access keys never go into the project. Locally, use `aws configure`; on EC2, use an IAM role.

---

## 12. Troubleshoot a Python import error

| Error | Meaning and fix |
|---|---|
| `ModuleNotFoundError: No module named 'fastapi'` (or boto3, moto, ...) | The virtual environment is not active, or the library is not installed. Run `.venv\Scripts\Activate.ps1`, then `pip install -r requirements-dev.txt`. In VS Code, select the `.venv` interpreter. |
| `ModuleNotFoundError: No module named 'core'` | You started Python from the wrong folder. `cd "D:\AWS SERVICES LAB"` first. Scripts in `scripts/` handle this themselves. |
| `ImportError: cannot import name 'list_queues' from 'integrations.sqs'` | The name is misspelled, or the function does not exist in that file yet. |
| `ImportError: ... (most likely due to a circular import)` | Two files import each other. Rule of thumb: `integrations/` imports only `core/`; `catalog/` and `experiments/` import `integrations/`; `routes/` import everything else. Never import `routes/` or `main` from lower layers. |
| `SyntaxError` / `IndentationError` | Read the line number in the message. Usually a missing `:`, bracket, or comma, or mixed indentation. VS Code underlines it in red. |

Quick check that every module imports: `python -c "import main"`.

---

## 13. Identify and fix common AWS permission errors

The app shows messages like:

> Permission denied for ListBuckets. Your IAM user or role needs the matching permission ... AWS said: User: arn:aws:iam::123456789012:user/lab is not authorized to perform: s3:ListAllMyBuckets

How to fix it:

1. **Read the action** after "not authorized to perform:". Here it is `s3:ListAllMyBuckets`. (The Boto3 method name and the IAM action name are not always the same.)
2. **Check who you are:** run *AWS STS → Who am I?* in the app, or `python scripts/live_aws_check.py`.
3. In the **IAM Console**, open that user or role → *Add permissions* → add the action to its policy. Compare with `deploy/iam-readonly-policy.json`.
4. Wait about 10 seconds and run the operation again.

| Message | Likely cause |
|---|---|
| "No AWS credentials were found" | `aws configure` not run, or (on EC2) no IAM role attached to the instance |
| "invalid or expired" | Temporary credentials (SSO, `AWS_SESSION_TOKEN`) have expired. Log in again. |
| "AWS returned AuthFailure" (EC2) | Wrong or expired keys, or the computer clock is badly out of sync |
| "Could not reach AWS" | No internet, a proxy, or a misspelled region |
| "AWS returned OptInRequired" | That region is not enabled for your account |
| Explicit `Deny` mentioned in the message | An organisation SCP or permission boundary blocks it. Adding Allow will not help. Ask the account administrator. |
