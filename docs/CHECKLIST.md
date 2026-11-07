# Verification Checklist

Status as of the initial build (v1). Update this file as items are verified.

## Verified locally (by automated tests or by running the app)

- [x] Dependencies install into `.venv` on Python 3.14.4
- [x] 46 automated tests pass (`python -m pytest`) with moto fake AWS
- [x] App starts with `uvicorn`; `/health` returns `{"status":"ok"}`
- [x] `/` redirects to `/login` when not logged in; the login page renders
- [x] Login, logout, and wrong-password rejection (tests)
- [x] Every integration function works against moto (STS, S3, EC2, IAM, Lambda, CloudWatch, DynamoDB)
- [x] All four experiments run against moto or mocked data; a permission error in one step does not stop the others
- [x] Invalid form input shows a friendly message and never reaches AWS
- [x] The write-operation safety switch blocks writes by default
- [x] Run history is saved and displayed

## Lab form, demo login, and security features (v2), verified locally

- [x] 101 automated tests pass (`python -m pytest`), including the 46 original tests (3 login tests rewritten for the demo login, 1 dashboard test moved to `/services`)
- [x] Started with Uvicorn on localhost: any non-empty login works, an empty field is rejected, logout works
- [x] A real local submission with a CSV document: redirect to the result, statuses Simulated/Success, mobile `01234` kept, a resubmit did not duplicate, a 5 MB + 1 byte file was rejected
- [x] The login password never appears in the page, the session cookie, or `logs/app.log` (test)
- [x] S3 SSE-KMS upload and verification, Secrets Manager retrieval and caching, SNS/SQS, and SES in the Lambda handler, against **moto fakes only**
- [x] The RDS connection takes its password from Secrets Manager in aws mode and fails safely without it (tested with a fake driver, not a real database)

## Connectivity dashboard (v3), verified locally

- [x] The full test suite passes (`python -m pytest`); the AWS checks run against moto, botocore Stubber, and test doubles only
- [x] *Check AWS environment* makes fresh read-only requests on every click: HTTPS, STS, EC2, Secrets Manager, SQLite, RDS, S3, KMS, Lambda, SES, SNS, SQS, CloudWatch
- [x] Statuses Connected / Not Configured / Access Denied / Not Found / Wrong Region / Timed Out / Unavailable / Configuration Error / No Data, each tested
- [x] Connect → disconnect → recover is reflected on the very next click (S3 and Secrets Manager tests); no cached secret result
- [x] The Security Configuration panel separates *In .env* (Configured, blue) from *Connection* (Connected, green, only after a real check)
- [x] A forged `X-Forwarded-Proto` header is never reported as HTTPS unless `TRUST_PROXY_HEADERS=true`
- [x] `Cache-Control: no-store` and a *Last checked* time on the main page
- [x] The secret value never appears in the page, the run history, or the log file, even with `LOG_LEVEL=DEBUG`
- [x] `deploy/*.json` policies are valid JSON; the health-check policy's actions exist in the AWS API models and match the code

## Not yet verified (needs you)

- [ ] **Real S3 + KMS:** an upload shows *AWS KMS → Verified*; `head-object` confirms `aws:kms` with your key
- [ ] **Real RDS PostgreSQL:** `scripts/init_database.py` succeeds on EC2; *Test connection* shows `TLS in use: yes`
- [ ] **Real Secrets Manager:** *Check AWS environment* shows *Describe secret* and *Retrieve value* **Connected**
- [ ] **Health-check policy** attached (placeholders replaced) and validated with `aws accessanalyzer validate-policy`
- [ ] Every row of *Check AWS environment* on EC2 (docs/EC2_DEPLOYMENT.md, section 15), including one disconnect test
- [ ] **Real Lambda + SES:** a confirmation email arrives (SES sandbox: recipient verified)
- [ ] **HTTPS:** ALB + ACM certificate, HTTP→HTTPS redirect, `TRUST_PROXY_HEADERS=true` with `--forwarded-allow-ips` limited to the ALB subnets, Security Configuration shows *Connected*
- [ ] Optional SNS/SQS against real resources
- [ ] The S3 bucket policy in `deploy/s3-bucket-policy.json` (if you apply it) blocks a plain upload

- [ ] **Real AWS account:** run `python scripts/live_aws_check.py`, then try each service page. *(Not done: no credentials configured, and live tests need your explicit request.)*
- [ ] Real IAM permission errors display correctly. Try it with an identity that lacks one permission.
- [ ] The pages look right in your browser. Only the HTML was tested, not the visual appearance.
- [ ] Experiments on an account with many resources (speed, pagination). moto data is tiny.
- [ ] The Lambda Health experiment against real CloudWatch data. Tests used mocked numbers.
- [ ] EC2 deployment (docs/EC2_DEPLOYMENT.md), including every item in its section 15 verification checklist.
- [ ] Python 3.12 on EC2 (Ubuntu 24.04 LTS). Developed on 3.14; the full test suite also passed on Ubuntu 24.04 with Python 3.12 (WSL), but not yet on a real EC2 instance.

## Known limitations and possible next steps

- **Demonstration login**: any non-empty username and password are accepted. This is not authentication.
- If the S3 upload succeeds but the database save fails, the uploaded object stays in S3 without a database record (clean it up by hand).
- Two exactly simultaneous submissions of the same form can race past the duplicate check. The second one then fails on the primary key, and only one record is kept. The Submit button is disabled after one click to make this unlikely.
- No CSRF tokens. Forms rely on the `SameSite=Lax` session cookie, which blocks cross-site form posts in modern browsers.
- Plain HTTP by default. On EC2, use the SSH tunnel (encrypted), or the ALB + ACM setup in README → *AWS Security Setup*, step 10. TLS has not been verified on a real deployment.
- SQS and SNS are catalog entries with no operations yet (good practice exercises: see CODE_CUSTOMIZATION_GUIDE §3).
- `count_objects` samples at most 1000 objects per bucket, by design, to limit cost.
- The test run shows a deprecation warning from Starlette about `httpx` (it suggests `httpx2`). It is harmless today and can be revisited when upgrading libraries.
- Library versions use `>=` minimums. Run `pip freeze > requirements-lock.txt` if you want exact, repeatable versions.
