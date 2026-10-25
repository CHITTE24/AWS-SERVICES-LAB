# Learning Guide

This guide explains how the project is organised, so you can find your way around, fix problems, and extend it yourself.

## 1. The big picture

```
Browser ──► FastAPI route ──► Python function ──► Boto3 ──► AWS service
   ▲          (routes/)      (integrations/ or     (core/aws_client.py)
   │                          experiments/)
   └──────── HTML page ◄──── template (templates/) ◄──── result dictionary
                                   │
                                   └──► saved to SQLite history (repositories/)
```

## 2. Directories

| Folder | Purpose |
|---|---|
| `main.py` | Starts the app: settings, logging, database, login cookie, and connects all routes. |
| `core/` | Shared building blocks: settings, Boto3 client creation, error messages, form validation, logging, templates. |
| `auth/` | Demonstration login (any non-empty username and password) and the "must be logged in" check. `passwords.py` is kept for adding real authentication later. |
| `catalog/` | *Describes* the AWS services and their operations (data only, plus small lookup functions). |
| `integrations/` | *Talks to* AWS. One file per service, with simple read-only Boto3 functions. |
| `experiments/` | Workflows that combine several integrations: the four reports, the lab form submission workflow, and the AWS environment check. |
| `repositories/` | Saves and loads the app's own data: run history (SQLite) and form submissions (SQLite or Amazon RDS). |
| `routes/` | FastAPI route functions: one function per URL. *(Added to the original plan so `main.py` stays short.)* |
| `templates/` | HTML pages (Jinja2). |
| `static/` | CSS styling and one small JavaScript file. |
| `scripts/` | Command-line helpers: password hash creation and an optional live AWS check. |
| `tests/` | Automated tests (pytest + moto fake AWS). |
| `deploy/` | Files used in AWS: the systemd service, IAM/KMS/S3 policies, and the Lambda function code. |
| `docs/` | These guides. |
| `data/` | *(Created at runtime, git-ignored.)* The SQLite database `lab.db`. |
| `logs/` | *(Created at runtime, git-ignored.)* `app.log`. |

## 3. Answers to "where is ...?"

| Question | File |
|---|---|
| Which file handles the web interface? | `routes/*.py` (URLs) + `templates/*.html` (pages) + `static/style.css` (look) |
| Which file handles **AWS authentication**? | `core/aws_client.py`. `get_client()` creates every Boto3 client. Credentials come from `aws configure`, `AWS_PROFILE`, or the EC2 IAM role. They are never stored in the app. |
| Which file handles **login**? | `auth/access.py` (demo check) + `routes/auth_routes.py` (pages) |
| Where is the **lab form** processed? | `routes/lab_routes.py` (page) → `core/form_checks.py` (validation) → `experiments/submission_workflow.py` (S3, database, Lambda, SNS/SQS) |
| Where are **secrets** read? | `core/secret_store.py` (AWS Secrets Manager, cached) |
| Where is the **database password** used? | `repositories/submission_repository.py → connect_to_rds()` |
| Which files talk to individual AWS services? | `integrations/sts.py`, `s3.py`, `ec2.py`, `iam.py`, `lambda_functions.py`, `cloudwatch.py`, `dynamodb.py` |
| Which files execute experiments? | `experiments/account_overview.py`, `s3_storage_report.py`, `ec2_security_review.py`, `lambda_health.py`. The list of experiments is in `experiments/registry.py`. |
| Where is configuration stored? | `.env` (your values, git-ignored). `.env.example` is the template. `core/config.py` reads them. |
| Where is data / experiment history stored? | `data/lab.db` (SQLite), written by `repositories/history_repository.py` |
| Where do logs go? | The terminal and `logs/app.log` (set up in `core/logging_setup.py`) |
| Where is the cost-safety switch? | `ensure_writes_allowed()` in `core/aws_client.py`, controlled by `ALLOW_WRITE_OPERATIONS` in `.env` |

## 4. How a browser request travels through the app

Example: you click **Run** on *Amazon S3 → List buckets*.

1. **Browser** sends `POST /services/s3/list_buckets` with the session cookie.
2. **FastAPI** matches the URL to `run_operation()` in `routes/dashboard_routes.py`.
3. `Depends(require_login)` runs `auth/access.py → require_login()`. With no logged-in user, the browser is redirected to `/login`.
4. The route looks up the service and operation in `catalog/services.py` (`get_service`, `get_operation`).
5. Form values are validated by `core/inputs.py → clean_inputs()`.
6. If the operation's `kind` is `"write"`, `ensure_writes_allowed()` blocks it unless the safety switch is on.
7. The route calls the operation's `function`, here `integrations/s3.py → list_buckets()`.
8. `list_buckets()` calls `core/aws_client.py → get_client("s3")`, which creates a **Boto3** client.
9. `s3.list_buckets()` sends an HTTPS request to the **AWS S3 API**. AWS checks the IAM permissions and answers.
10. The integration turns the AWS answer into a simple list of dictionaries and returns it.
11. If anything failed, `core/aws_errors.py → friendly_error_message()` explains it.
12. `repositories/history_repository.py → save_run()` stores the run in SQLite.
13. `core/templates.py → render()` fills `templates/operation_result.html`. The `render_data` macro in `_macros.html` draws the table.
14. The **browser** shows the page.

Experiments follow the same path. They use `routes/experiment_routes.py` and `experiments/registry.py`, and an experiment's `run()` function calls several integrations through `experiments/steps.py → run_step()`. If one step fails (for example, a missing IAM permission), it is reported and the others still run.

## 5. Python concepts used in this project

| Concept | Where to see it |
|---|---|
| Functions with parameters and return values | Every file in `integrations/` |
| Dictionaries and lists as simple "records" | `catalog/services.py`, `experiments/registry.py` |
| `for` loops and `if` conditions | `integrations/ec2.py`, `experiments/ec2_security_review.py` |
| `try` / `except` error handling | `experiments/steps.py`, `routes/dashboard_routes.py` |
| Functions stored in a dictionary and called later | `"function": s3.list_buckets` in the catalog; `operation["function"](**inputs)` in the route |
| `**inputs`: passing a dictionary as named arguments | `routes/dashboard_routes.py`, `routes/experiment_routes.py` |
| Environment variables and `.env` | `core/config.py` |
| Decorators (only the FastAPI ones) | `@router.get(...)` in `routes/` |
| `async` / `await` (only where FastAPI needs it) | Reading forms in `routes/dashboard_routes.py` |
| SQL with `?` placeholders | `repositories/history_repository.py` |
| Testing with fixtures and fakes | `tests/conftest.py`, `monkeypatch` in `tests/test_experiments.py` |

## 6. AWS concepts demonstrated

- **Credential chain**: how Boto3 finds credentials (`core/aws_client.py` docstring).
- **STS GetCallerIdentity**: "who am I?" with no permissions needed.
- **Regions vs global services**: IAM and S3 bucket listing are global; EC2, Lambda, DynamoDB, and CloudWatch are regional.
- **Pagination**: `get_paginator(...)` in `integrations/ec2.py` and `integrations/iam.py`.
- **IAM least privilege**: `deploy/iam-readonly-policy.json` lists only the actions this app calls.
- **AccessDenied errors**: how to read them (`core/aws_errors.py`).
- **Security groups**: what "open to 0.0.0.0/0" means (`experiments/ec2_security_review.py`).
- **CloudWatch metrics**: summing a metric over time (`integrations/cloudwatch.py`).
- **IAM roles on EC2**: no keys on the server (`docs/EC2_DEPLOYMENT.md`).

## 7. Which file do I edit to ...?

| I want to ... | Edit |
|---|---|
| change the title, region, or log level | `.env` |
| change colours or layout | `static/style.css` |
| change a page's text or layout | the matching file in `templates/` |
| add or change an AWS call | `integrations/<service>.py`, then `catalog/services.py` |
| add or change a service or category | `catalog/services.py`, `catalog/categories.py` |
| add or change an experiment | `experiments/<name>.py`, then `experiments/registry.py` |
| change what is stored in history | `repositories/history_repository.py` |
| change login behaviour | `auth/access.py`, `routes/auth_routes.py` |
| add a test | `tests/test_*.py` |

Step-by-step examples are in [CODE_CUSTOMIZATION_GUIDE.md](CODE_CUSTOMIZATION_GUIDE.md).

## 8. Project file inventory

*Keep this table updated when you add files.*

| File | Lines | Purpose |
|---|---:|---|
| `main.py` | 71 | Creates the app, session cookie, static files, routes, `/health` |
| `core/config.py` | 82 | Reads `.env` / environment variables into a settings dictionary |
| `core/secret_store.py` | 79 | Reads and caches the Secrets Manager secret |
| `core/form_checks.py` | 118 | Validates the lab form and the uploaded document |
| `core/aws_client.py` | 47 | `get_client()` for Boto3; `ensure_writes_allowed()` safety switch |
| `core/aws_errors.py` | 75 | Turns AWS/Python errors into readable messages |
| `core/inputs.py` | 70 | Validates form fields (text, number, region) |
| `core/logging_setup.py` | 27 | Logs to the terminal and `logs/app.log` |
| `core/templates.py` | 32 | `render()` helper for HTML pages |
| `auth/passwords.py` | 49 | PBKDF2 password hashing and checking |
| `auth/access.py` | 38 | `check_login_form()` (demo login), `require_login()` |
| `catalog/categories.py` | 18 | Category list |
| `catalog/services.py` | 105 | Service catalog with operations linked to integration functions |
| `integrations/sts.py` | 23 | Caller identity |
| `integrations/s3.py` | 62 | Buckets, bucket region, object count |
| `integrations/ec2.py` | 86 | Instances, security groups, regions |
| `integrations/iam.py` | 54 | Users, roles, account summary |
| `integrations/lambda_functions.py` | 28 | Lambda function list |
| `integrations/cloudwatch.py` | 52 | Alarms, metric sum |
| `integrations/dynamodb.py` | 42 | Tables, table details |
| `integrations/messaging.py` | 28 | Optional SNS publish and SQS send |
| `experiments/steps.py` | 41 | `run_step()` and the result format |
| `experiments/registry.py` | 45 | Experiment list and input fields |
| `experiments/account_overview.py` | 48 | STS + IAM + S3 + EC2 snapshot |
| `experiments/s3_storage_report.py` | 43 | Per-bucket region and size |
| `experiments/ec2_security_review.py` | 76 | Finds rules open to the internet |
| `experiments/lambda_health.py` | 52 | Lambda + CloudWatch error rates |
| `experiments/submission_workflow.py` | 200 | Lab form workflow: S3 (SSE-KMS) → database → Lambda/SES → SNS/SQS |
| `experiments/environment_check.py` | 102 | STS role, EC2 metadata, secret, database TLS, CloudWatch metrics |
| `repositories/history_repository.py` | 95 | SQLite table, save/list/get runs |
| `repositories/submission_repository.py` | 179 | `lab_submissions` table on SQLite or RDS PostgreSQL |
| `routes/auth_routes.py` | 44 | `/login`, `/logout` |
| `routes/lab_routes.py` | 141 | `/` (Welcome page), `/submit`, `/environment-check` |
| `routes/dashboard_routes.py` | 72 | `/services`, `/services/{id}`, run an operation |
| `routes/experiment_routes.py` | 62 | `/experiments`, run an experiment |
| `routes/history_routes.py` | 24 | `/history`, `/history/{id}` |
| `templates/base.html` | 46 | Page frame: header, menu, status bar |
| `templates/_macros.html` | 79 | Reusable pieces: tables, form fields, experiment results |
| `templates/*.html` (9 more) | 9–37 | One per page |
| `static/style.css` | 72 | Styling |
| `static/app.js` | 14 | Disables the Run button while running |
| `scripts/make_password_hash.py` | 36 | Creates a password hash (not used by the demo login) |
| `scripts/init_database.py` | 29 | Creates the `lab_submissions` table (run once on EC2 for RDS) |
| `scripts/live_aws_check.py` | 44 | Optional real-AWS read-only check |
| `tests/conftest.py` | 63 | Test settings, fake AWS, logged-in client |
| `tests/test_*.py` (12 files) | 14–315 | 101 tests in total |
| `deploy/aws-services-lab.service` | — | systemd service for EC2 |
| `deploy/iam-readonly-policy.json` | — | Least-privilege IAM policy (dashboard) |
| `deploy/iam-ec2-app-policy.json` | — | EC2 role: S3 upload, KMS, secret, Lambda, SNS/SQS |
| `deploy/iam-lambda-policy.json` | — | Lambda role: logs and SES |
| `deploy/kms-key-policy-statements.json` | — | Statements to add to the KMS key policy |
| `deploy/s3-bucket-policy.json` | — | Optional: require HTTPS and SSE-KMS |
| `deploy/lambda_function/submission_processor.py` | 82 | Lambda code: sends the SES confirmation email |
| `templates/lab.html` | 102 | Welcome page: form, results, activity, security |
| `requirements.txt` / `requirements-dev.txt` | — | Libraries for the app / for tests |
| `.env.example` | — | Settings template (placeholders only) |
| `pytest.ini` | — | Test runner settings |
