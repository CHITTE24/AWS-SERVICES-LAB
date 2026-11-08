# Browser Audit and Repair Report: AWS Services Lab

**Date:** 2026-11-08 · **Scope:** local repository `D:\AWS SERVICES LAB` (branch `main`, commit `515d23f` + uncommitted repairs) · **Nothing committed, pushed, or deployed.**

---

## 1. Executive summary

- The application **runs correctly locally**. A real browser (Microsoft Edge, driven by Playwright) tested every page and workflow: **28 of 28 browser checks pass** after the repairs, with **no HTTP 5xx responses, no failed network requests, and no JavaScript errors**.
- The automated test suite passes: **438 passed** (431 existing + 7 new regression tests).
- **3 application defects were repaired** (all low-risk, in `main.py` and two templates):
  1. An unexpected error showed a bare "Internal Server Error" and its cause was **never written to `logs/app.log`**. This is why the earlier HTTP 500 on EC2 could not be diagnosed.
  2. Unknown addresses showed raw JSON (`{"detail":"Not Found"}`) instead of the app's page.
  3. Every page caused a `/favicon.ico` 404 in the browser console.
- **The EC2 server, live AWS services, and the earlier HTTP 500 could NOT be tested.** No SSH private key, AWS CLI, or verified AWS credentials were available to this audit (see section 6). Those items are marked **BLOCKED**, not passed.
- **Most likely EC2 problem found by inspection (High):** the systemd file uses `/home/ubuntu/aws-services-lab`, but your server's folder is `/home/ubuntu/AWS-SERVICES-LAB`. Linux folder names are case-sensitive, so a service installed unchanged cannot start.

## 2. Problems discovered

| ID | Severity | Problem | Status |
|---|---|---|---|
| F1 | **High** | Unhandled errors give a bare "Internal Server Error"; the traceback is not in `logs/app.log` | **Fixed** |
| F2 | **High** | `deploy/aws-services-lab.service` paths (`/home/ubuntu/aws-services-lab`) do not match the EC2 folder `/home/ubuntu/AWS-SERVICES-LAB` | Needs your action on EC2 (section 12) |
| F3 | Medium | The earlier HTTP 500 on EC2 is still unexplained; likely causes listed in section 3 | BLOCKED (needs EC2 logs) |
| F4 | Medium | `SESSION_SECRET` is empty in the local `.env`: a temporary secret is used, so every restart logs everyone out | Needs your action (`.env` not modified) |
| F5 | Medium | `requirements.txt` uses minimum versions (`>=`), so EC2 may install different versions than were tested here (FastAPI 0.162.0, Starlette 1.10.0, Uvicorn 0.54.0) | Recommendation |
| F6 | Low | Unknown URLs showed raw JSON in the browser | **Fixed** |
| F7 | Low | Missing favicon caused a 404 console error on every page | **Fixed** |
| F8 | Low | When AWS is unreachable, an experiment can take up to ~60 s (account overview: 5 calls × up to 3 attempts), longer than an ALB's default 60 s idle timeout, which would give a 504 | Recommendation |
| F9 | Low | `/history/abc` returns a JSON 422 page instead of HTML | Not changed (harmless) |
| F10 | Low | `/health` reports `ok` even when the database is not writable | Recommendation |
| F11 | Low | Test warning: Starlette deprecates `httpx` in its TestClient | Not changed (no package changes made) |

## 3. Root causes

- **F1:** `main.py` had no handler for unexpected exceptions. Starlette then answers with plain text, and Uvicorn prints the traceback only to the terminal / `journalctl`. `core/logging_setup.py` writes only the app's own loggers to `logs/app.log`, so the cause never reached the file you look at.
- **F2:** `docs/EC2_DEPLOYMENT.md` clones into `~/aws-services-lab`. A plain `git clone` of the repository creates `~/AWS-SERVICES-LAB` instead. The unit's `WorkingDirectory` and `ExecStart` then point to a folder that does not exist (systemd shows `status=200/CHDIR` or `203/EXEC`). This matches the "incorrect working directories" and "startup problems" you reported before.
- **F3 (unconfirmed):** the code paths that can still produce a 500 are the SQLite reads and writes (`history_repository`, `workflow_repository`, `architecture_repository`), which are not wrapped in error handling. Likely triggers on EC2:
  1. `data/` or `data/*.db` owned by `root` after the app was once started with `sudo`. Start-up succeeds, but the first write fails with `attempt to write a readonly database`.
  2. `DATABASE_PATH` pointing to `/mnt/efs/...`. SQLite on NFS/EFS can fail with `database is locked` or `disk I/O error`, and with `no such file` if EFS is not mounted yet at boot.
  3. Starting from the wrong folder, so `.env` is not loaded and different settings are used.

  **Note:** this application's code does not use `/mnt/efs` at all. EFS only matters if your EC2 `.env` points a path there.
- **F6/F7:** FastAPI's default 404 is JSON; `templates/base.html` declared no icon.

## 4. Files modified

| File | Change |
|---|---|
| `main.py` | Added `wants_html()`, a 404 handler (`friendly_http_error`), and a catch-all 500 handler (`unexpected_error`); 3 imports |
| `templates/error.html` | Optional `heading` (default "Not found") so it can also show "Server error" |
| `templates/base.html` | `<link rel="icon" href="data:,">` (empty icon, no request) |
| `tests/test_error_pages.py` | **New**: 7 regression tests |
| `BROWSER_AUDIT_AND_REPAIR_REPORT.md` | **New**: this report |

Not touched: `.env`, `data/`, databases, uploads, authentication, security settings, dependencies, the `.venv`.

## 5. Repairs completed

1. **Catch-all error handler (F1).** Any unexpected error now:
   - writes the full traceback to `logs/app.log` as `Unexpected error <reference> on <METHOD> <path>`;
   - shows the user a friendly "Server error" page with the same 8-character reference (JSON for API requests);
   - **never** shows technical details in the browser.

   To find a 500's cause: `grep <reference> logs/app.log`.
2. **HTML 404 page (F6).** A browser that opens an unknown address gets the app's "Not found" page. API paths (`/api/...`, `.../status`), scripts, and non-404 errors (for example `GET /logout` → 405) keep FastAPI's normal answers. The login redirect is unchanged.
3. **Favicon (F7).** Empty inline icon; the console 404 is gone.

**Regression tests:** 4 of the 7 new tests fail on the original code and pass after the repair. The other 3 confirm that existing behaviour (JSON for APIs, 405 for `GET /logout`, the login redirect) is unchanged.

## 6. Browser test results

**Tool:** Playwright 1.x in a throwaway virtual environment (scratchpad, not the project `.venv`), driving the installed Microsoft Edge, headless.
**Target:** the app started locally with Uvicorn (`--no-proxy-headers`) in `APP_MODE=local`, with:
- your `.env` **skipped**;
- temporary databases;
- dummy AWS credentials and an unreachable AWS endpoint, so **no request could reach your AWS account**.

AWS-dependent results therefore show the app's *error handling*, not real AWS behaviour.

**Not available:** EC2 SSH access (no private key on this PC), the AWS CLI, and the claude-in-chrome browser tools. **The deployed site on EC2 was not browser-tested.**

| # | Browser test | Before repair | After repair |
|---|---|---|---|
| 1 | Logged out: `/` redirects to `/login` | PASS | PASS |
| 2 | Logged out: 5 protected pages redirect to `/login` | PASS | PASS |
| 3 | Login validation: empty password → 400, message shown, password not echoed | PASS | PASS |
| 4 | Login validation: 101-character username rejected | PASS | PASS |
| 5 | Login success; session cookie HttpOnly + SameSite=Lax (Secure off, correct for HTTP/tunnel) | PASS | PASS |
| 6 | Navigation menu: all 6 links return 200 | PASS | PASS |
| 7–9 | Responsive: no horizontal scroll at 1280 px, 768 px, 375 px on all 6 pages | PASS | PASS |
| 10 | Lab form: invalid mobile/email rejected by the server with field messages (browser checks bypassed) | PASS | PASS |
| 11 | Lab form: valid submission → Post/Redirect/Get; leading zero `01234` kept | PASS | PASS |
| 12 | Refresh after submit does not resubmit | PASS | PASS |
| 13 | Upload valid `.txt` (S3 shown as Simulated in local mode) | PASS | PASS |
| 14 | Upload `.exe` rejected | PASS | PASS |
| 15 | Upload fake PDF (content ≠ extension) rejected | PASS | PASS |
| 16 | Upload > 5 MB rejected | PASS | PASS |
| 17 | "Check AWS environment" returns a results table (AWS unreachable: failures reported, no crash) | PASS | PASS |
| 18 | Services: 9 service pages load; first operation of each runs and shows errors cleanly | PASS | PASS |
| 19 | Experiments: 4 forms load and run (no 500) | FAIL* | PASS |
| 20 | History list and run details | PASS | PASS |
| 21 | Live Monitor: simulated workflow starts and auto-polls to "Finished" | PASS | PASS |
| 22 | Live Monitor: unknown workflow → 404 (page and status API) | PASS | PASS |
| 23 | Architecture Map: design buttons (new from settings, layout, fit, zoom, save, refresh), presentation mode, no JS errors | PASS | PASS |
| 24 | Unknown URLs return 404/422, never 500 | PASS | PASS |
| 25 | Unknown URL shows the HTML error page | **FAIL** (raw JSON) | PASS |
| 26 | `/health` → 200 `{"status":"ok"}` | PASS | PASS |
| 27 | Logout clears the session | PASS | PASS |
| 28 | Logout requires POST (`GET` → 405) | PASS | PASS |

\*Test 19 failed first because of the **test script's** 30-second click timeout: with AWS unreachable, the account overview took 61 s. It was not an app error (all 4 experiments returned 200, see F8).

**Console after repair:** only the deliberate 400/404/422 responses from tests 3, 4, 10, 14–16, 22, 24 and 25. **Failed network requests: 0. 5xx responses: 0. Server log errors: 0.**

## 7. Backend test results

| Suite | Result |
|---|---|
| Existing tests (`python -m pytest`), before changes | 431 passed |
| Full suite after repairs | **438 passed**, 0 failed (1 deprecation warning, F11) |
| New `tests/test_error_pages.py` | 7 passed (4 fail on the original code, proving they cover the fix) |
| App start-up (Uvicorn, local) | Starts without errors; `/health` 200 |

## 8. AWS integration status

**No live AWS call was made by this audit.** The existing test suite covers S3, KMS, Secrets Manager, Lambda, SNS/SQS, DynamoDB, CloudWatch, STS and EC2 with **moto fakes and test doubles. These are SIMULATED results, not proof that your AWS resources work.**

| Service | Configured (local `.env`) | Connected | Functional | Basis |
|---|---|---|---|---|
| EC2 (app host) | n/a | NOT TESTED | NOT TESTED | No SSH access. You report HTTP 303 from `/` (expected: redirect to login) |
| IAM / STS | Unknown on EC2 | NOT TESTED | NOT TESTED | Policies in `deploy/` validated by unit tests only |
| S3 | Not configured locally | NOT TESTED | Simulated tests pass | moto |
| EFS | Not used by app code | NOT TESTED | n/a | You report it mounted and readable |
| CloudWatch | Not configured locally | NOT TESTED | Simulated tests pass | moto |
| SNS | Not configured locally | NOT TESTED | Simulated tests pass | test doubles |
| SQS | Not configured locally | NOT TESTED | Simulated tests pass | test doubles |
| Lambda | Not configured locally | NOT TESTED | Simulated tests pass | test doubles |
| RDS (PostgreSQL) | `DATABASE_BACKEND=sqlite` locally | NOT TESTED | Simulated tests pass | test doubles |
| KMS | Not configured locally | NOT TESTED | Simulated tests pass | moto |
| Secrets Manager | Not configured locally | NOT TESTED | Simulated tests pass | moto |

The app's own **Check AWS environment** button is the right tool for the live check. It is read-only and distinguishes Connected / Not Configured / Access Denied / Not Found / Timed Out. Run it on EC2 through the SSH tunnel (section 11).

## 9. Remaining issues

- **F2** systemd path mismatch: fix on EC2 (section 12, step 1).
- **F3** earlier HTTP 500: unexplained. After deploying the F1 fix, any new 500 shows a reference code; `grep` it in `logs/app.log`.
- **F4** set `SESSION_SECRET`. **F5** pin dependency versions. **F8** slow experiments when AWS is unreachable. **F10** `/health` does not check the database.
- All EC2 and live AWS rows remain **BLOCKED / NOT TESTED**.

## 10. Security concerns

- **Demo login (by design, documented):** any username/password is accepted. With a public hostname (`ec2-43-204-229-14...`), **port 8000 must not be open to the internet**. Confirm the security group has no inbound 8000 rule (or only from an ALB security group), and that Uvicorn listens on `127.0.0.1`.
- **Plain HTTP:** acceptable only through the SSH tunnel. For public access use an ALB + ACM certificate, `SESSION_HTTPS_ONLY=true`, `TRUST_PROXY_HEADERS=true` (README step 10).
- **Session secret (F4):** without `SESSION_SECRET`, a random one is generated per start. Set a long random value in the EC2 `.env` (`python -c "import secrets; print(secrets.token_hex(32))"`), with file mode `600`.
- **Positive findings:** HttpOnly + SameSite=Lax cookie; logout is POST-only; Jinja auto-escaping; parameterised SQL; upload type/content/size checks; boto3 debug logs suppressed (no secrets in logs); write operations disabled by default; the new 500 page never reveals internals.
- No secrets, keys or `.env` values are included in this report.

## 11. Deployment instructions (after your approval)

Run on the EC2 instance as `ubuntu`. **None of this was done by the audit.**

```bash
cd /home/ubuntu/AWS-SERVICES-LAB
git status                                   # Keep any local changes on the server
git pull                                     # After you commit and push the repairs yourself
.venv/bin/python -m pip install -r requirements.txt   # Only if requirements changed (they did not)
.venv/bin/python -m pytest -q                # Optional: run the tests on the server too
sudo systemctl restart aws-services-lab      # Or restart your manual uvicorn (docs/EC2_DEPLOYMENT.md 8A)
curl -s http://127.0.0.1:8000/health         # {"status":"ok"}
```

Then, from your PC: `ssh -i <your-key>.pem -L 8000:127.0.0.1:8000 ubuntu@ec2-43-204-229-14.ap-south-1.compute.amazonaws.com`, open `http://127.0.0.1:8000`, log in, and click **Check AWS environment**.

**Recommended systemd configuration** (copy of `deploy/aws-services-lab.service` with your folder):

```ini
[Unit]
Description=AWS Services Lab (FastAPI)
After=network-online.target remote-fs.target
Wants=network-online.target
# Only if .env points DATABASE_PATH or other files to EFS:
# RequiresMountsFor=/mnt/efs

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/AWS-SERVICES-LAB
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/ubuntu/AWS-SERVICES-LAB/.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

What it provides:
- **Start at boot** (`enable`) and **restart on failure**.
- The correct folder and virtual environment. `.env` is loaded by the app from `WorkingDirectory`.
- **Logs:** `journalctl -u aws-services-lab` plus `logs/app.log`. Optionally ship `logs/app.log` to CloudWatch Logs with the CloudWatch agent.
- **Health check:** `curl http://127.0.0.1:8000/health`, or the ALB target group's `/health`.

**Dependency pinning (F5):** on a working server run `.venv/bin/pip freeze > requirements.lock`, commit it, and install with `pip install -r requirements.lock`, so every deployment uses the tested versions.

## 12. Manual actions required (EC2 / AWS Console)

1. **Fix the service path (F2):**
   ```bash
   systemctl cat aws-services-lab | grep -E "WorkingDirectory|ExecStart"
   sudo sed -i 's#/home/ubuntu/aws-services-lab#/home/ubuntu/AWS-SERVICES-LAB#g' /etc/systemd/system/aws-services-lab.service
   sudo systemctl daemon-reload && sudo systemctl restart aws-services-lab && systemctl status aws-services-lab
   ```
2. **Diagnose the earlier 500 (F3):**
   ```bash
   journalctl -u aws-services-lab --since "2 days ago" | grep -B2 -A25 "Traceback" | tail -80
   grep -E "^(APP_MODE|DATABASE_PATH|DATABASE_BACKEND|ARCHITECTURE_DATABASE_PATH)=" .env   # settings only, no secrets
   ls -la data/ logs/          # Everything must be owned by ubuntu
   sudo chown -R ubuntu:ubuntu data logs   # Only if something is owned by root
   ```
   Keep SQLite on the instance's EBS disk (`data/lab.db`), not on `/mnt/efs`.
3. **Set `SESSION_SECRET`** in the EC2 `.env` (F4), then restart the app. Run `chmod 600 .env`.
4. **AWS Console → EC2 → Security groups:** confirm there is no inbound rule for port 8000 from `0.0.0.0/0`. SSH (22) should be limited to your IP.
5. **AWS Console → IAM → the instance role:** attach the read-only `deploy/iam-ec2-healthcheck-policy.json` (placeholders replaced) if the environment check shows Access Denied. **The audit did not change any IAM policy.**
6. Run **Check AWS environment** through the tunnel and record each row (Connected / Not Configured / Access Denied …) as the live AWS status. This report deliberately leaves those rows NOT TESTED.

---

## Final verification table

| Check | Result |
|---|---|
| Repository audit | PASS |
| Existing tests (431) | PASS |
| New regression tests (7) | PASS |
| Full suite after repairs (438) | PASS |
| App starts locally without errors | PASS |
| Login page loads; validation; session cookie flags | PASS |
| Dashboard / lab form after login | PASS |
| Navigation (all menu links) | PASS |
| Forms submit and validate (server side) | PASS |
| Upload workflow (valid, wrong type, fake content, > 5 MB) | PASS |
| Experiments, services, history, Live Monitor, Architecture Map | PASS |
| Responsive layout (desktop / tablet / mobile) | PASS |
| Browser console: no unexplained errors | PASS |
| Error pages (404 HTML, 500 logged with reference) | PASS |
| Browser test of the deployed EC2 site | BLOCKED (no SSH key / tunnel) |
| EC2 systemd service, reboot behaviour | BLOCKED (no EC2 access); path mismatch found by inspection |
| Root cause of earlier HTTP 500 | BLOCKED (needs EC2 logs, section 12 step 2) |
| Live IAM, S3, EFS, CloudWatch, SNS, SQS, Lambda, RDS, KMS, Secrets Manager | NOT TESTED (no live AWS access; simulated tests only) |
| Deployment of repairs to EC2 | NOT TESTED (awaiting your approval) |
