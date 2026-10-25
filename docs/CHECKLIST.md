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

## Not yet verified (needs you)

- [ ] **Real AWS account:** run `python scripts/live_aws_check.py`, then try each service page. *(Not done: no credentials configured, and live tests need your explicit request.)*
- [ ] Real IAM permission errors display correctly. Try it with an identity that lacks one permission.
- [ ] The pages look right in your browser. Only the HTML was tested, not the visual appearance.
- [ ] Experiments on an account with many resources (speed, pagination). moto data is tiny.
- [ ] The Lambda Health experiment against real CloudWatch data. Tests used mocked numbers.
- [ ] EC2 deployment (Phase E), including every item in the EC2 verification checklist.
- [ ] Python 3.11 on EC2. Developed on 3.14; no 3.12+ features were used, but this is untested on 3.11.

## Known limitations and possible next steps

- Single admin user. There is no user management.
- No CSRF tokens. Forms rely on the `SameSite=Lax` session cookie, which blocks cross-site form posts in modern browsers.
- Login throttling is only a 1-second delay on failure, with no lockout.
- Plain HTTP. On EC2, use the SSH tunnel (encrypted). For public access, add HTTPS, for example an Application Load Balancer with an ACM certificate, or nginx with Let's Encrypt.
- SQS and SNS are catalog entries with no operations yet (good practice exercises: see CODE_CUSTOMIZATION_GUIDE §3).
- `count_objects` samples at most 1000 objects per bucket, by design, to limit cost.
- The test run shows a deprecation warning from Starlette about `httpx` (it suggests `httpx2`). It is harmless today and can be revisited when upgrading libraries.
- Library versions use `>=` minimums. Run `pip freeze > requirements-lock.txt` if you want exact, repeatable versions.
