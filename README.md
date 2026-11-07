# AWS Services Lab

A small FastAPI web app for learning how AWS services work together. After logging in you see
**Welcome to AWS Service Lab**: a form whose submission flows through several AWS services:

```
Browser ──HTTPS──► EC2 (FastAPI) ──► S3 (document, SSE-KMS encrypted with a KMS key)
                        │       ──► RDS PostgreSQL (lab_submissions; password from Secrets Manager)
                        │       ──► Lambda ──► SES (confirmation email)
                        │       ──► SNS / SQS (optional notifications)
                        └─ IAM role (no access keys) · CloudWatch (metrics and logs)
```

It also keeps the original **services dashboard** (`/services`), **experiments**, and **history** pages.

> **Demonstration login only.** Any non-empty username and password are accepted. Anyone who can reach
> the app can log in with made-up details. Do not put sensitive data in it, and do not expose it publicly
> without real authentication (for example Amazon Cognito). The password is never stored, logged, or sent to AWS.

## Quick start (Windows, VS Code terminal)

```powershell
python -m venv .venv                       # Create a private Python environment (first time only)
.venv\Scripts\Activate.ps1                 # Turn it on (your prompt shows "(.venv)")
pip install -r requirements-dev.txt        # Install the libraries
copy .env.example .env                     # Create your settings file (works as-is in local mode)
python -m uvicorn main:app --reload        # Start the app
```

Open **http://127.0.0.1:8000**, log in with any username and password, and submit the form.

Run the tests with `python -m pytest`. They use fakes (moto and test doubles) and never touch your AWS account or send email.

### Local mode vs AWS mode

| | `APP_MODE=local` (default) | `APP_MODE=aws` |
|---|---|---|
| Unconfigured AWS service | **Simulated** (labelled, nothing sent to AWS) | **Failed** (setting required) |
| Configured AWS service | Real call, real result | Real call, real result |
| Database | SQLite file `data/lab.db` (labelled "NOT Amazon RDS") | RDS when `DATABASE_BACKEND=postgres` |
| RDS password | `DB_PASSWORD` or Secrets Manager | **Always** Secrets Manager |

An AWS failure is never turned into "Simulated". Status words: **Success**, **Verified**, **Sent** (SES accepted the email; inbox delivery is *not* confirmed), **Failed**, **Skipped**, **Simulated**, **Unknown**, **Pending**.

## Configuration (`.env`)

| Variable | Purpose | Fill in when |
|---|---|---|
| `APP_MODE` | `local` or `aws` | `aws` on EC2 once resources exist |
| `SESSION_SECRET` | Signs the login cookie | Now (random value) |
| `SESSION_HTTPS_ONLY` | `true` adds the cookie's Secure flag | When served over HTTPS |
| `TRUST_PROXY_HEADERS` | `true` believes `X-Forwarded-Proto` from your load balancer (with uvicorn `--forwarded-allow-ips`) | Only behind your own HTTPS ALB |
| `AWS_REGION` | Region of all resources, e.g. `ap-south-1` | Now |
| `AWS_PROFILE` | Local AWS CLI profile (empty on EC2) | Local live testing only |
| `DATABASE_BACKEND` | `sqlite` or `postgres` | `postgres` after creating RDS |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | RDS connection (not secret) | After creating RDS |
| `DB_SSLMODE`, `DB_SSLROOTCERT` | TLS to RDS (`verify-full` + CA bundle path) | After creating RDS |
| `DB_PASSWORD` | Local PostgreSQL only; ignored in aws mode | Optional, local only |
| `AWS_SECRETS_MANAGER_SECRET_NAME` | Name/ARN of the secret holding the DB password | After creating the secret |
| `SECRET_CACHE_SECONDS` | How long the secret is cached (default 900) | Optional |
| `S3_BUCKET_NAME` | Private document bucket | After creating the bucket |
| `S3_KMS_KEY_ARN` | KMS key ARN for SSE-KMS (key ARN, not alias) | After creating the key |
| `LAMBDA_FUNCTION_NAME` | Submission-processor function | After deploying the Lambda |
| `SNS_TOPIC_ARN`, `SQS_QUEUE_URL` | Optional notifications | Optional |
| `SES_SENDER_EMAIL` | **Set on the Lambda function**, not in `.env` | After verifying the SES identity |

`.env` is read **when the app starts**: restart the app after editing it. Changes to AWS resources need no restart.

## The submission workflow

1. The form is validated on the server: all five fields required; the mobile number is exactly 5 digits and is stored as **text**, so leading zeros are kept; the email format is checked; the optional document must be PDF, TXT, DOCX, or CSV, at most 5 MB (5 × 1024 × 1024 bytes), and its content must match its type.
2. The document goes to `s3://<bucket>/submissions/YYYY/MM/DD/<submission id>/<safe name>` with SSE-KMS. The upload is **refused** if no KMS key is configured. The encryption S3 *reports back* is compared with `S3_KMS_KEY_ARN` ("Verified" or "Failed"). Nothing is kept on the server's disk.
3. The submission is saved to `lab_submissions` (SQLite or RDS) in a transaction.
4. The Lambda function is invoked synchronously, with **no automatic retry**, so one submission means at most one email. It sends the email with SES and returns the result. Optional SNS and SQS messages carry only the submission ID and metadata.
5. The browser is redirected to `/?submission_id=...` (Post/Redirect/Get), and a hidden random submission ID blocks duplicates. A refresh, a double click, or Back + Submit never processes the form twice.

The **AWS Service Activity** section shows each step's real outcome.

### Check AWS environment

The button runs **fresh, read-only checks on every click** (nothing cached, nothing created, sent, uploaded, or invoked) and shows a *Last checked* time:

| Row | AWS call | Proves |
|---|---|---|
| HTTPS | (the request itself) | The browser used HTTPS, from trusted information only; the cookie flag matches |
| AWS IAM / STS | `GetCallerIdentity` | Which identity and credential source (`iam-role` on EC2) |
| Amazon EC2 | IMDSv2 + `DescribeInstances` (this instance only) | Running on EC2, in the region `AWS_REGION` names |
| AWS Secrets Manager | `DescribeSecret`, then `GetSecretValue` (not cached) | The secret exists (metadata) **and** the app may read it. Values are never shown or logged |
| SQLite / Amazon RDS | `SELECT 1` | Each database separately; RDS only *Connected* with TLS in use |
| Amazon S3 | `HeadBucket` | The bucket exists and is accessible (a 403 is reported as ambiguous) |
| AWS KMS | `DescribeKey` | The key exists and is enabled (encrypting is proven per upload) |
| AWS Lambda | `GetFunctionConfiguration` | The function exists and is Active (never invoked) |
| Amazon SES | `GetAccount` | Sending enabled, sandbox or production (no email sent) |
| Amazon SNS / SQS | `GetTopicAttributes` / `GetQueueAttributes` | The topic / queue exists (nothing published or sent) |
| Amazon CloudWatch | `GetMetricStatistics`, `DescribeLogStreams` | API access, and separately whether metrics and logs actually arrived |

Statuses: **Connected** (green, proven by AWS), **Not Configured** (setting empty), **Access Denied**, **Not Found**, **Wrong Region**, **Timed Out**, **Unavailable**, **Configuration Error**, and **No Data** (API works, nothing delivered yet). A setting that merely exists is shown as **Configured** (blue) in the Security Configuration panel, never as Connected. The checks need the read-only [deploy/iam-ec2-healthcheck-policy.json](deploy/iam-ec2-healthcheck-policy.json); without it, rows show **Access Denied**.

## RDS database (PostgreSQL)

The project only had SQLite, so RDS uses **PostgreSQL** with the `psycopg` driver. Table (also in [repositories/submission_repository.py](repositories/submission_repository.py)):

```sql
CREATE TABLE IF NOT EXISTS lab_submissions (
    id VARCHAR(32) PRIMARY KEY,           submitted_at TIMESTAMPTZ NOT NULL,
    login_username VARCHAR(100) NOT NULL, username VARCHAR(50) NOT NULL,
    first_name VARCHAR(50) NOT NULL,      last_name VARCHAR(50) NOT NULL,
    mobile_number VARCHAR(5) NOT NULL,    email VARCHAR(254) NOT NULL,
    document_name VARCHAR(120),           s3_bucket VARCHAR(63),   s3_object_key VARCHAR(1024),
    upload_status VARCHAR(20) NOT NULL,   processing_status VARCHAR(20) NOT NULL,
    email_status VARCHAR(20) NOT NULL,    activity_json TEXT NOT NULL
);
```

The login password is never stored. Initialise the table once on the EC2 instance, because RDS is private:
`.venv/bin/python scripts/init_database.py`. It is safe to run again. Locally, the SQLite table is created automatically when the app starts.

## AWS Security Setup

Nothing here is done automatically. You create every resource yourself, and **each item stays unverified until you test it**.

1. **S3 bucket.** Create one in your region (for example `aws-service-lab-docs-<your-initials>`). Bucket names are global.
2. **Block Public Access.** Keep all four "Block public access" settings ON. Leave Object Ownership as "Bucket owner enforced" (ACLs disabled).
3. **KMS key.** KMS → Create key → Symmetric → Encrypt and decrypt. Add yourself as key administrator. Then add the statements from [deploy/kms-key-policy-statements.json](deploy/kms-key-policy-statements.json) to the key policy. **Keep** the default root-account statement, or you can lock everyone out of the key. KMS operations need permission in **both** the IAM policy and the key policy.
4. **Default bucket encryption.** Bucket → Properties → Default encryption → SSE-KMS with your key, and **Bucket Key** enabled (fewer KMS calls, lower cost).
   *Default encryption* encrypts any object uploaded without encryption headers. *Explicit SSE-KMS* (what this app sends) states the key in each request, so the app knows exactly what was used and can verify it. Using both gives defence in depth.
5. **App configuration.** Add `S3_BUCKET_NAME`, `AWS_REGION`, and `S3_KMS_KEY_ARN` (the key **ARN**) to `.env`.
6. **Secret.** Secrets Manager → Store a new secret → "Credentials for Amazon RDS database" (or "Other type"), with JSON `{"username": "labapp", "password": "..."}`. RDS can also create and rotate this secret for you ("Manage master credentials in AWS Secrets Manager").
7. **Secret reference.** Put the secret's **name or ARN** (never the value) in `AWS_SECRETS_MANAGER_SECRET_NAME`. The app caches it for `SECRET_CACHE_SECONDS`. After a password rotation it picks up the new value within that time, or immediately after a restart.
8. **EC2 instance role.** Attach [deploy/iam-readonly-policy.json](deploy/iam-readonly-policy.json) (services dashboard), [deploy/iam-ec2-app-policy.json](deploy/iam-ec2-app-policy.json) (workflow), and [deploy/iam-ec2-healthcheck-policy.json](deploy/iam-ec2-healthcheck-policy.json) (Check AWS environment) to the instance role, after replacing every `YOUR_...` placeholder. Remove the optional SNS/SQS/Logs statements you don't use. Never put access keys on the instance. You can validate a policy for free with `aws accessanalyzer validate-policy --policy-type IDENTITY_POLICY --policy-document file://deploy/iam-ec2-healthcheck-policy.json`.
9. **RDS.** Create a PostgreSQL instance with **Public access: No** in the same VPC. Give its security group an inbound rule for **5432 from the EC2 instance's security group only**. Download the CA bundle on EC2 (`curl --create-dirs -o certs/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`). Set `DATABASE_BACKEND=postgres`, `DB_HOST` (the endpoint), `DB_NAME`, `DB_USER`, and `DB_SSLROOTCERT`, then run `scripts/init_database.py`. This app uses password authentication through Secrets Manager, not IAM database authentication.
10. **HTTPS.** Remote traffic must use HTTPS. `http://127.0.0.1` is fine only on your own computer or through the SSH tunnel. Recommended setup:
    - Request a public certificate in **AWS Certificate Manager** for your domain (DNS validation).
    - Create an **Application Load Balancer** (internet-facing), with a security group allowing 443 (and 80) from the internet.
    - **HTTPS :443 listener** with the ACM certificate → **target group** (HTTP, port 8000, health check path `/health`) → the EC2 instance.
    - **HTTP :80 listener** → action "Redirect to HTTPS 443" (status 301).
    - The EC2 security group allows **8000 only from the ALB's security group**.
    - Start uvicorn with `--proxy-headers --forwarded-allow-ips=<the ALB's subnet ranges>` (the commented ALB `ExecStart` line in the systemd file, or the same options when starting it by hand), and set `TRUST_PROXY_HEADERS=true` and `SESSION_HTTPS_ONLY=true`.
    - Through the SSH tunnel instead, use `--no-proxy-headers` and keep `TRUST_PROXY_HEADERS=false`. The app then never believes an `X-Forwarded-Proto` header, because anyone can send one.
    The session cookie is always HttpOnly and SameSite=Lax. With `SESSION_HTTPS_ONLY=true` it is also Secure.
11. **Lambda + SES.** Verify a sender identity in SES. In the SES sandbox, **recipients must be verified too**, until you request production access. Create a Python Lambda function from [deploy/lambda_function/submission_processor.py](deploy/lambda_function/submission_processor.py) (handler `submission_processor.lambda_handler`, timeout 15 s), with the environment variable `SES_SENDER_EMAIL`. Give its execution role [deploy/iam-lambda-policy.json](deploy/iam-lambda-policy.json). Put the function name in `LAMBDA_FUNCTION_NAME`. Optionally create an SNS topic and an SQS queue and set their ARN/URL.
12. **Test S3 encryption.** Submit a document, then check that the Activity table shows *AWS KMS → Verified*. Confirm it independently with
    `aws s3api head-object --bucket <bucket> --key <key from the results>`: you should see `"ServerSideEncryption": "aws:kms"` and your key ARN.
13. **Test the secret.** Click **Check AWS environment**. *AWS Secrets Manager → Describe secret* and *Retrieve value* should both be **Connected**; only the number of keys is shown, never the values. *Amazon RDS (PostgreSQL) → Test connection* should be **Connected** with `TLS in use: yes`.
14. **Test HTTPS.** Open `http://your-domain`: it must redirect to `https://`. The Security Configuration panel must show *HTTPS / TLS for this page → Connected*. You can also run `curl -I http://your-domain`, which should return a 301 with `Location: https://...`.

### Optional: enforce S3 rules with a bucket policy

[deploy/s3-bucket-policy.json](deploy/s3-bucket-policy.json) denies non-HTTPS requests, uploads without SSE-KMS, and uploads using another KMS key. It contains **Deny statements only**, so it cannot lock administrators out of changing the policy. It does block *any* upload without the SSE-KMS header, including console uploads that don't choose the key. To test it before relying on it:

- run the S3 upload test (step 12) first, then apply the policy and repeat it;
- check that a plain upload is refused: `aws s3api put-object --bucket <bucket> --key test/plain.txt --body README.md` should return **AccessDenied**;
- if something breaks, delete the bucket policy (the root user can always do this).

## Minimum IAM permissions

| Who | Permissions | File |
|---|---|---|
| EC2 role: dashboard | read-only Describe/List actions | `deploy/iam-readonly-policy.json` |
| EC2 role: Check AWS environment | read-only, one resource each where AWS allows it: `s3:ListBucket`, `kms:DescribeKey`, `secretsmanager:DescribeSecret`, `lambda:GetFunctionConfiguration`, `logs:DescribeLogStreams`, optional `sns:GetTopicAttributes` / `sqs:GetQueueAttributes`; region-limited `ec2:DescribeInstances`, `cloudwatch:GetMetricStatistics`, `ses:GetAccount` | `deploy/iam-ec2-healthcheck-policy.json` |
| EC2 role: workflow | `s3:PutObject` (bucket/submissions/*), `kms:GenerateDataKey` (key, via S3 only), `secretsmanager:GetSecretValue` (one secret), `lambda:InvokeFunction` (one function), optional `sns:Publish`, `sqs:SendMessage`, CloudWatch Logs | `deploy/iam-ec2-app-policy.json` |
| Lambda execution role | its own CloudWatch Logs, `ses:SendEmail` only from your sender address | `deploy/iam-lambda-policy.json` |
| KMS key policy | lets the EC2 role use the key through S3 | `deploy/kms-key-policy-statements.json` |

The Lambda function never reads the document, so it gets no S3 or KMS permissions. If the secret uses a customer-managed KMS key, also allow `kms:Decrypt` on that key for the EC2 role.

## Documentation

| File | What it explains |
|---|---|
| [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) | Step-by-step local setup, AWS credentials, and troubleshooting |
| [docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) | How the project is organised and how a request flows through it |
| [docs/CODE_CUSTOMIZATION_GUIDE.md](docs/CODE_CUSTOMIZATION_GUIDE.md) | Worked examples of common changes |
| [docs/EC2_DEPLOYMENT.md](docs/EC2_DEPLOYMENT.md) | Step-by-step deployment to an Ubuntu EC2 instance |
| [docs/CHECKLIST.md](docs/CHECKLIST.md) | What has been verified and what still needs to be checked |
