# Architecture Map

Open **Architecture** in the menu (`/architecture`). You design your AWS architecture as boxes and arrows;
the app colours each part **only** from recorded verification evidence for that exact resource or operation.

> **The map never creates, connects, changes, or deletes anything in AWS.** You build the real architecture
> yourself in the AWS Console. Drawing an arrow proves nothing: a new arrow is always grey.

## Reading the map

| Look | Meaning |
|---|---|
| **Green, solid** ✓ | Automatic evidence: the app observed this exact resource / operation succeed, within the freshness window |
| **Red** ✗ | The latest evidence is a failure (for example Access Denied) |
| **Yellow** ! | Partial (for example Timed Out, Retrying, No Data) or **stale**: older than `ARCH_EVIDENCE_MAX_AGE_HOURS` (default 24) |
| **Grey, dashed** – | No evidence yet, design only, or not saved yet |
| **Dotted line + MANUAL badge** | A person attested the result (manual verification). Never counted as automatic |

* A **box** is one resource (`type:identifier`, for example `s3:lab-docs-ab12`). Several buckets, functions, or
  instances are separate boxes.
* An **arrow** is one operation between two exact resources (for example `ec2:i-0abc… → s3:PutObject → s3:lab-docs-ab12`).
* A box's colour comes from checks of that resource only (for example HeadBucket). An arrow's colour comes from evidence
  of that exact operation only. Box checks are listed under an arrow as **context**, explicitly not as proof:
  "the bucket is reachable" does not prove "uploads work".
* Simulated workflows never produce evidence.

### Where evidence comes from

| Source | Evidence it records |
|---|---|
| **Check AWS environment** (main page) | Resource checks (HeadBucket, DescribeKey, …), and operations it really performs: reading the secret value, the RDS query, reading CloudWatch metrics, the instance role |
| **Live Workflow Monitor** (real workflows) | Upload (PutObject), HeadObject, SSE-KMS, the DynamoDB record, the S3 event reaching Lambda, the Lambda's HeadObject and DynamoDB write |
| **Lab form** | Upload, SSE-KMS, Lambda invocation, SES acceptance, SNS / SQS messages, the RDS insert (statuses only: no names, emails, or file names) |
| **Verify Now** (a box's panel) | One read-only metadata check of that resource |
| **Manual verification** (a box's or arrow's panel) | Your attested result, time, and evidence notes |

Evidence is **append-only**: every success, failure, and recovery stays in the history. Manual records are visible only
to the person who recorded them.

## Design and Presentation modes

* **Design** (`/architecture`): add boxes from the palette, give each the real resource identifier, draw arrows with
  **Connect** (click the source, then the target), choose the arrow's operation, group boxes in VPC / subnet boxes,
  Auto-layout, Save, Export / Import JSON, Verify Now, and manual records.
* **Presentation** (`/architecture?mode=present`): read-only, larger labels, the legend, and the details panel. For
  showing the architecture to an interviewer.
* **New from this app's settings** draws the connections this app's code makes with your current `.env`. It is a
  drawing only: every arrow starts grey until real evidence exists.

## Verify Now (strictly read-only)

Available for S3 (HeadBucket), KMS (DescribeKey), Lambda (GetFunctionConfiguration), DynamoDB (DescribeTable),
Secrets Manager (DescribeSecret: metadata only, never the value), SNS (GetTopicAttributes), SQS (GetQueueUrl +
GetQueueAttributes), EC2 (DescribeInstances), SES (GetAccount), and CloudWatch log groups (DescribeLogStreams).
Nothing is created, changed, sent, or invoked. It checks a **box**, never an arrow. At most 20 checks per user per
10 minutes. A resource that is not in `.env` may answer **Access Denied**, because the health-check policy is scoped
to your configured resources; that is shown honestly, never as Connected.

## Official AWS icons (optional)

Until icons are present, boxes are labelled rectangles. To use the official icons:

1. Download the **AWS Architecture Icons** package from <https://aws.amazon.com/architecture/icons/> (use them as AWS's
   terms describe: unmodified, in architecture diagrams).
2. Unzip it, and copy these SVG files into `static/icons/aws/`, **renaming** them as shown. File names differ slightly
   between package versions: search the package for the service name and use the 48 or 64 px "Arch_" service icon.

| Save as | Find in the package (service icon) |
|---|---|
| `ec2.svg` | Amazon EC2 |
| `s3.svg` | Amazon Simple Storage Service |
| `lambda.svg` | AWS Lambda |
| `dynamodb.svg` | Amazon DynamoDB |
| `rds.svg` | Amazon RDS |
| `kms.svg` | AWS Key Management Service |
| `secrets-manager.svg` | AWS Secrets Manager |
| `sns.svg` | Amazon Simple Notification Service |
| `sqs.svg` | Amazon Simple Queue Service |
| `ses.svg` | Amazon Simple Email Service |
| `cloudwatch.svg` | Amazon CloudWatch |
| `iam.svg` | AWS Identity and Access Management |
| `api-gateway.svg` | Amazon API Gateway |
| `alb.svg` | Elastic Load Balancing (Application Load Balancer) |
| `vpc.svg` | Amazon Virtual Private Cloud |
| `subnet.svg` | (group icons) Private subnet |
| `external.svg` | optional: any generic "client" / "users" icon |

3. Reload the page. Missing files simply keep the labelled box. The folder is in `.gitignore`, so the icon package is
   never committed.

## Exact identity, account and region

* Evidence is matched to the **exact** resource or operation, and each record also stores the AWS **account** and
  **region** the operation used.
* Each diagram has a **scope** (click an empty part of the canvas: *Diagram settings*). New diagrams use the account and
  region the app uses now. Evidence from another known account never matches; evidence from another region never
  matches a regional resource (S3 bucket names and IAM roles are global, so the region is not compared for them).
* Identities are captured **when the operation happens**: Check AWS environment and the lab form record evidence in the
  same request; a real Live Workflow saves its exact resources at upload time (the `workflow_context` table), and the
  Lambda function writes its own ARN and the event's bucket on its stages. Editing `.env` later never moves evidence.
* If an identity cannot be established (EC2 metadata unavailable in aws mode, unknown account, a Lambda stage without
  its ARN), the record is **Unattributed**: kept in the history and counted in the legend / Diagram settings, but never
  shown on a box.
* The Architecture Map brings recent real workflows up to date by itself (at most once a minute, 20 workflows from the
  last 24 hours), so their Lambda results are recorded even if nobody opens each workflow page.

## How results are classified

| Result of the real call | Evidence |
|---|---|
| The call succeeded | pass, *Success* (green) |
| The call worked, but the object did not match (size, ETag, label, KMS key) | partial, *Verification Mismatch* (yellow) |
| A setting is wrong (for example invalid parameter, invalid credentials) | partial, *Configuration Error* (yellow) |
| AWS did not answer in time | partial, *Timed Out* (yellow) |
| AWS refused or failed (Access Denied, Not Found, Unavailable, retries exhausted by throttling) | fail (red) |
| A configuration error stopped the call from being made (for example the Lambda's `EXPECTED_KMS_KEY_ARN` is empty) | nothing about that call |

## Security of the page and API

* Every endpoint requires the login; diagrams are visible only to their owner (others get "not found").
* Requests that change anything must be JSON **and** come from this app's own page: a browser request whose `Origin`
  (or `Referer`) names another host is refused. Opening the app as `http://127.0.0.1:8000`, `http://localhost:8000`, or
  through your load balancer's name all work.
* Manual records are visible only to the person who recorded them. Verify Now: at most 20 checks per user and 60 for
  everyone together per 10 minutes.

## Data, migration, and rollback

* All Architecture Map data is in its **own** SQLite file: `ARCHITECTURE_DATABASE_PATH` (default `data/architecture.db`).
  The existing `data/lab.db` (run history, submissions, workflows) is **not changed in any way**.
* **Schema version 2** (tables `schema_info`, `diagrams`, `evidence` with `account` / `region`, `workflow_context`).
  A missing file is created directly at version 2.
* An **existing file at another version is never changed automatically**: the app stops at start-up with a message.
  Upgrade explicitly (additive only; nothing is changed or deleted; existing records keep account/region = unknown):

  ```powershell
  python scripts/migrate_architecture_db.py            # dry run: shows the plan, changes nothing
  python scripts/migrate_architecture_db.py --apply    # copies the file to architecture.db.v1-backup-<time>, then upgrades
  ```
* **Rollback:** stop the app and copy the backup file back over `data/architecture.db` (the app will then ask for the
  migration again), or move `data/architecture.db` away to start empty. The rest of the app is unaffected.
* Settings: `ARCHITECTURE_DATABASE_PATH`, `ARCH_EVIDENCE_MAX_AGE_HOURS` (default 24). Both are optional.

## Third-party code

Two MIT-licensed libraries, stored locally (no CDN): Cytoscape.js 3.34.4 and cytoscape-dagre 4.0.1 (which includes
the dagre layout engine). Versions, npm integrity values, and SHA-256 checksums are in `static/vendor/VERSIONS.txt`;
`tests/test_vendor_files.py` re-checks them. `.gitattributes` keeps these files byte-for-byte identical in Git.
