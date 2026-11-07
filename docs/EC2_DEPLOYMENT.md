# Deploying to an Ubuntu EC2 Instance

This guide installs the app on **Ubuntu Server 24.04 LTS** by cloning
**https://github.com/CHITTE24/AWS-SERVICES-LAB** (a private repository).

> **You run every step yourself.** Nothing in this project creates AWS resources, launches
> instances, or pushes to GitHub automatically.
>
> **Plan:** first get the app running on EC2 with **no AWS services connected** (`APP_MODE=local`).
> Then connect and test the AWS services **one at a time** (section 12).

**Why Ubuntu 24.04?** Its built-in Python is **3.12**, which every dependency supports. The full test
suite passed on Ubuntu 24.04 with Python 3.12. **Do not use Ubuntu 22.04**: its Python 3.10 is too old
for the current `starlette` library (it needs 3.11 or newer). You do not need Python 3.14 on the server.

**Cost:** an EC2 instance costs money while it runs (`t3.micro` is roughly US$8–10/month on demand,
or free-tier eligible on some accounts), and its disk costs money until it is deleted.
Section 16 explains how to stop everything.

---

## 1. AWS Console: create the IAM role

On EC2 the app uses an **IAM role**, never access keys. Keys never exist on the server: Boto3 picks up
temporary role credentials from the instance automatically.

1. **IAM → Policies → Create policy → JSON.** Paste the contents of
   [deploy/iam-readonly-policy.json](../deploy/iam-readonly-policy.json). Name it `AwsServicesLabReadOnly`.
2. **IAM → Roles → Create role.** Trusted entity: **AWS service → EC2**. Attach `AwsServicesLabReadOnly`.
   Name it `AwsServicesLabRole`.
3. Later, when you connect the AWS services (section 12), add permissions from
   [deploy/iam-ec2-app-policy.json](../deploy/iam-ec2-app-policy.json) one statement at a time, and the
   read-only dashboard checks from [deploy/iam-ec2-healthcheck-policy.json](../deploy/iam-ec2-healthcheck-policy.json).
   You attach every policy yourself; the app never changes IAM.

## 2. AWS Console: create the security group

**EC2 → Security Groups → Create security group** (for example `aws-services-lab-sg`):

| Direction | Type | Port | Source | Why |
|---|---|---|---|---|
| Inbound | SSH | 22 | **My IP** | Only your computer can log in |
| Outbound | All traffic | All | 0.0.0.0/0 | Default. Needed for `apt`, GitHub, and AWS APIs |

**Do not** add an inbound rule for port 8000. You reach the app through an SSH tunnel (section 9),
or later through an HTTPS load balancer (section 13).

## 3. AWS Console: launch the instance

**EC2 → Launch instance:**

| Setting | Value |
|---|---|
| Name | `aws-services-lab` |
| AMI | **Ubuntu Server 24.04 LTS** (64-bit x86) |
| Instance type | `t3.micro` |
| Key pair | Create one (`.pem`), download it, and keep it private. **Never commit it.** |
| Network settings → Security group | `aws-services-lab-sg` (section 2) |
| Storage | 8–10 GiB gp3 is enough |
| Advanced details → IAM instance profile | `AwsServicesLabRole` |
| Advanced details → Metadata version | **V2 only (token required)** (IMDSv2) |

Forgot the role? **Instance → Actions → Security → Modify IAM role** attaches it without a restart.

## 4. Connect with SSH

From your computer (PowerShell on Windows), in the folder with your key file:

```bash
ssh -i aws-services-lab.pem ubuntu@<PUBLIC_IP>
```
*The Ubuntu user is `ubuntu` (not `ec2-user`, which is Amazon Linux). `<PUBLIC_IP>` is on the
instance's page in the console.*

## 5. Install Git, Python, and system packages

```bash
sudo apt update                                     # Refreshes the list of available packages
sudo apt install -y git python3 python3-venv curl   # Git, Python 3.12, the venv module, and curl
python3 --version                                   # Should print Python 3.12.x
```
*`python3-venv` is required: on a fresh Ubuntu, `python3 -m venv` fails with "ensurepip is not
available" without it.*

## 6. Clone the repository from GitHub

The repository is **private**, so the server needs read access. A **deploy key** is the safest way:
it is a read-only SSH key for this one repository, and no GitHub password or token is stored on the server.

```bash
ssh-keygen -t ed25519 -C "aws-services-lab-ec2" -f ~/.ssh/github_deploy -N ""   # Creates a key pair
cat ~/.ssh/github_deploy.pub                                                     # Prints the PUBLIC key
```
On github.com: **CHITTE24/AWS-SERVICES-LAB → Settings → Deploy keys → Add deploy key**. Paste the
public key, give it a title (`ec2-aws-services-lab`), and leave **Allow write access unticked**.

```bash
# Tells SSH to use the deploy key for github.com.
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
chmod 600 ~/.ssh/config
ssh -T git@github.com              # Type "yes" the first time. Expect: "...successfully authenticated..."

git clone git@github.com:CHITTE24/AWS-SERVICES-LAB.git ~/aws-services-lab
cd ~/aws-services-lab
git log --oneline -3               # Shows the latest commits, so you know which version you have
```
*The folder must be `/home/ubuntu/aws-services-lab`, because the systemd file in section 8 uses that path.*

> Alternative: `git clone https://github.com/CHITTE24/AWS-SERVICES-LAB.git ~/aws-services-lab`, and enter
> your GitHub username and a **fine-grained personal access token** (read-only "Contents" access to this
> repository) as the password. You then have to enter the token again on every `git pull`.

## 7. Virtual environment, dependencies, and `.env`

```bash
cd ~/aws-services-lab
python3 -m venv .venv                             # Creates a private Python environment in .venv/
.venv/bin/pip install --upgrade pip               # Updates the package installer
.venv/bin/pip install -r requirements.txt         # Installs the app's libraries (test tools are not needed)
.venv/bin/pip check                               # Should print "No broken requirements found."
```

Create the settings file and give it a random `SESSION_SECRET`. This command writes the secret straight
into `.env` **without printing it**:

```bash
cp .env.example .env
SECRET=$(.venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
sed -i "s/^SESSION_SECRET=.*/SESSION_SECRET=$SECRET/" .env
unset SECRET
chmod 600 .env                                    # Only the ubuntu user can read the file
nano .env                                         # Check the settings. Ctrl+O saves, Ctrl+X exits
```

For the first start, keep these values:

| Setting | Value | Why |
|---|---|---|
| `APP_MODE` | `local` | Unconfigured AWS services show **Simulated** instead of Failed |
| `SESSION_SECRET` | (the random value) | Logins survive restarts. Never share or commit it |
| `SESSION_HTTPS_ONLY` | `false` | Correct for the SSH tunnel. Set `true` only behind HTTPS (section 13) |
| `TRUST_PROXY_HEADERS` | `false` | Correct for the SSH tunnel. Set `true` only behind your own ALB (section 13) |
| `AWS_REGION` | your region, for example `ap-south-1` | Region of the AWS resources you create later |
| `AWS_PROFILE` | **empty** | Boto3 then uses the instance's IAM role |
| `DATABASE_BACKEND` | `sqlite` | Saved in `data/lab.db`, created automatically |
| Every other AWS setting | empty | Filled in one at a time in section 12 |

Test-run once by hand:

```bash
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```
In a **second** SSH window: `curl http://127.0.0.1:8000/health` should print `{"status":"ok"}`.
Then press **Ctrl+C** in the first window.

`--no-proxy-headers` makes uvicorn ignore `X-Forwarded-*` headers. Through the SSH tunnel there is no
proxy, so nobody should be able to make the app believe a request was HTTPS by sending such a header.

## 8. Keep the app running: choose 8A (by hand) or 8B (systemd)

Both options run the **same command**. Use **8A** if `systemctl` is not available or you prefer to start
the app yourself. Use **8B** to start it automatically at boot and after a crash.
Check whether systemd is available with `command -v systemctl` (no output means: use 8A).

### 8A. Manual: start uvicorn yourself (no systemd needed)

```bash
cd ~/aws-services-lab
mkdir -p logs
# Starts the app in the background. "nohup" keeps it running after you log out of SSH.
nohup .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-proxy-headers \
      > logs/uvicorn.out 2>&1 &
echo $! > logs/uvicorn.pid                        # Remembers the process ID, to stop it later
curl http://127.0.0.1:8000/health                 # {"status":"ok"}
```
Stop it: `kill "$(cat logs/uvicorn.pid)"`. Restart it: stop, then run the `nohup` command again.
It does **not** start again after a reboot or a crash; start it again by hand, or use 8B.
(Alternative: run the plain command inside `tmux` and detach with Ctrl+B, then D.)

### 8B. Optional: run it as a systemd service

systemd starts the app at boot, restarts it after a crash, and collects its logs.
If you used 8A, stop that copy first (`kill "$(cat logs/uvicorn.pid)"`), or port 8000 is already in use.

```bash
sudo cp ~/aws-services-lab/deploy/aws-services-lab.service /etc/systemd/system/
sudo systemctl daemon-reload                  # Tells systemd a new service file exists
sudo systemctl enable --now aws-services-lab  # Starts it now and on every boot
systemctl status aws-services-lab             # Should show "active (running)". Press q to exit
curl http://127.0.0.1:8000/health             # {"status":"ok"}
```
*The service runs as `ubuntu` from `/home/ubuntu/aws-services-lab`. If you used another folder, edit
those paths in `/etc/systemd/system/aws-services-lab.service`, then run `sudo systemctl daemon-reload`.*

## 9. Open the app from your computer (SSH tunnel)

The app listens only on the server itself (127.0.0.1), so it is not exposed to the internet. Reach it
through an encrypted SSH tunnel. On **your computer**:

```bash
ssh -i aws-services-lab.pem -L 8000:127.0.0.1:8000 ubuntu@<PUBLIC_IP>
```
Keep that window open and browse to **http://127.0.0.1:8000**. Log in, submit the form, and open
Services, Experiments, and History. Unconfigured AWS services show **Simulated** or **Not Configured**.
Through the tunnel the HTTPS rows correctly say **Not Configured** (the browser-to-app hop is plain HTTP
inside the encrypted SSH connection).

## 10. Logs and health

```bash
tail -f ~/aws-services-lab/logs/app.log               # The app's own log file (8A and 8B)
tail -n 50 ~/aws-services-lab/logs/uvicorn.out        # 8A only: uvicorn's start-up output
journalctl -u aws-services-lab -f                     # 8B only: live log (Ctrl+C to stop watching)
journalctl -u aws-services-lab -n 100 --no-pager      # 8B only: the last 100 lines
systemctl status aws-services-lab                     # 8B only: running? since when?
curl http://127.0.0.1:8000/health                     # {"status":"ok"} means the web server answers
```
*`/health` deliberately does not call AWS, so it works before any service is connected. Secrets and
passwords are never written to the logs: even with `LOG_LEVEL=DEBUG`, the AWS libraries are kept at INFO,
because their DEBUG output would include raw AWS responses (and so secret values).*

## 11. Restart, update from GitHub, and roll back

**`.env` is read only when the app starts.** After editing it, restart the app:

| | 8A (manual) | 8B (systemd) |
|---|---|---|
| Restart | `kill "$(cat logs/uvicorn.pid)"`, then the `nohup` command from 8A | `sudo systemctl restart aws-services-lab` |
| Stop | `kill "$(cat logs/uvicorn.pid)"` | `sudo systemctl stop aws-services-lab` |

Changes to **AWS resources** (a bucket created or deleted, a permission added or removed) need **no**
restart: the next click on **Check AWS environment** checks them again.

After you push new commits from your computer:

```bash
cd ~/aws-services-lab
git pull                                       # Downloads the latest commits
.venv/bin/pip install -r requirements.txt      # Installs any new libraries
# 8B only, if the service file changed:
sudo cp deploy/aws-services-lab.service /etc/systemd/system/ && sudo systemctl daemon-reload
# Then restart (8A or 8B, see the table above), and:
curl http://127.0.0.1:8000/health
```
`git pull` never touches `.env`, `data/`, or `logs/`: they are not in the repository.

Roll back to an earlier version: `git log --oneline`, then `git checkout <commit-id>`, then restart.
Return to the latest with `git checkout main && git pull`.

## 12. Connect the AWS services one at a time

Do these in order, **one at a time**. After each change: edit `.env` → restart the app (section 11) →
click **Check AWS environment** → check the log. The detailed console steps are in
[README.md → AWS Security Setup](../README.md#aws-security-setup).

**Permissions for the dashboard checks.** Besides the workflow policy, attach
[deploy/iam-ec2-healthcheck-policy.json](../deploy/iam-ec2-healthcheck-policy.json) (read-only) to the
instance role, after replacing every `YOUR_...` placeholder and deleting the optional SNS/SQS statements you
don't use. Without it, the affected rows correctly show **Access Denied**: nothing is reported as Connected
unless AWS confirmed it.

| # | Service | AWS Console | Workflow permission (`iam-ec2-app-policy.json`) | Health-check permission | `.env` setting | Dashboard row when it works |
|---|---|---|---|---|---|---|
| 1 | IAM role / STS | Section 1 | (none needed) | (none needed) | `AWS_PROFILE` empty | *AWS IAM / STS → Connected*, credential source `iam-role` |
| 2 | EC2 | (this instance) | (none) | `ec2:DescribeInstances` | `AWS_REGION` = the instance's region | *Amazon EC2 → Connected* (no *Wrong Region* row) |
| 3 | S3 + KMS | Bucket and KMS key (README steps 1–5) | `s3:PutObject`, `kms:GenerateDataKey` | `s3:ListBucket`, `kms:DescribeKey` | `S3_BUCKET_NAME`, `S3_KMS_KEY_ARN` | *Amazon S3* and *AWS KMS → Connected*; a submitted document shows *AWS KMS → Verified* |
| 4 | Lambda + SES | SES identities; the function (README step 11) | `lambda:InvokeFunction` | `lambda:GetFunctionConfiguration`, `ses:GetAccount` | `LAMBDA_FUNCTION_NAME` | *AWS Lambda* and *Amazon SES → Connected*; a submission shows email **Sent** |
| 5 | SNS / SQS (optional) | Topic / queue | `sns:Publish` / `sqs:SendMessage` | `sns:GetTopicAttributes` / `sqs:GetQueueAttributes` | `SNS_TOPIC_ARN`, `SQS_QUEUE_URL` | *Amazon SNS* / *Amazon SQS → Connected* |
| 6 | Secrets Manager | The DB password secret (README step 6) | `secretsmanager:GetSecretValue` | `secretsmanager:DescribeSecret` | `AWS_SECRETS_MANAGER_SECRET_NAME` | *Describe secret* and *Retrieve value → Connected* (values never shown) |
| 7 | RDS PostgreSQL | Private instance; 5432 **from `aws-services-lab-sg` only** (README step 9) | (uses the secret) | (none: network + password) | `DATABASE_BACKEND=postgres`, `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_SSLROOTCERT` | `scripts/init_database.py`, then *Amazon RDS (PostgreSQL) → Connected* with `TLS in use: yes` |
| 8 | CloudWatch | Automatic for Lambda | (none) | `cloudwatch:GetMetricStatistics`, `logs:DescribeLogStreams` | (uses `LAMBDA_FUNCTION_NAME`) | *Metrics API access → Connected*; after one submission, *metrics/logs delivered → Connected* |

For the RDS certificate bundle:
`curl --create-dirs -o ~/aws-services-lab/certs/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`,
then set `DB_SSLROOTCERT=/home/ubuntu/aws-services-lab/certs/global-bundle.pem`.

When **every** service you use is configured, set `APP_MODE=aws`, so a missing setting shows **Failed**
instead of Simulated. A service that is not configured never crashes the app: its row just says so.

**What each dashboard status means**

| Status | Meaning | Typical fix |
|---|---|---|
| **Connected** (green) | A real read-only AWS request proved it | — |
| **Not Configured** (grey) | The `.env` setting is empty, so nothing was checked | Fill it in, restart |
| **Access Denied** (red) | AWS refused: the role lacks a permission (for S3 403: or the bucket is another account's) | Add the health-check permission |
| **Not Found** (red) | The resource does not exist (typo, deleted, other account) | Fix the name/ARN in `.env` |
| **Wrong Region** (red) | The resource is in a different region than `AWS_REGION` | Make them match |
| **Timed Out** (amber) | AWS or RDS did not answer within a few seconds | Network, security groups, VPC endpoints |
| **Unavailable** (red) | Unreachable, or the resource is disabled / not active | Check the resource's state |
| **Configuration Error** (red) | A setting has the wrong format or contradicts another | Read the row's explanation |
| **No Data** (amber) | The API works, but no metrics/logs have arrived yet | Submit the form once, then check again |

## 13. Public access: HTTPS only, and protect the demo login

> **The login is a demonstration login: any username and password are accepted.** Anyone who can reach
> the app can use it. Keep using the SSH tunnel unless you really need browser access from elsewhere.

If you do need it, use HTTPS through an **Application Load Balancer** (README step 10). In short:
ACM certificate → internet-facing ALB with an HTTPS :443 listener (and HTTP :80 redirecting to 443) →
target group on port 8000 with health check path `/health`. Then:

- **Restrict the ALB's security group** to your own IP address on 443/80, not 0.0.0.0/0, until real
  authentication (for example Amazon Cognito) replaces the demo login.
- In `aws-services-lab-sg`, allow port **8000 only from the ALB's security group**.
- Start uvicorn with `--host 0.0.0.0 --proxy-headers --forwarded-allow-ips=<the ALB's subnet ranges>`
  (8A: in the `nohup` command; 8B: the commented ALB `ExecStart` line, then
  `sudo systemctl daemon-reload && sudo systemctl restart aws-services-lab`). List **only** the subnets the
  ALB runs in (EC2 → Load Balancers → your ALB → Network mapping), never `*`.
- In `.env`, set `TRUST_PROXY_HEADERS=true` and `SESSION_HTTPS_ONLY=true`, then restart.
  The app believes `X-Forwarded-Proto` only when **both** are true: you set `TRUST_PROXY_HEADERS=true`, and
  uvicorn accepted the header because it came from an address in `--forwarded-allow-ips`. A header sent by
  anyone else is ignored and the HTTPS row shows **Configuration Error**, never Connected.
- Go back to the SSH tunnel by reversing both: `--no-proxy-headers` on 127.0.0.1, and `TRUST_PROXY_HEADERS=false`.
- **Never** open port 8000 to the internet directly, and never serve the app publicly over plain HTTP.

## 14. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Permission denied (publickey)` on `ssh` | Use the user `ubuntu` and the right `.pem` file. On Windows, the key file must be readable only by you |
| SSH hangs / times out | The security group's port-22 rule must allow your **current** IP ("My IP" changes when your network changes) |
| `ensurepip is not available` | `sudo apt install -y python3-venv`, then delete `.venv` and create it again |
| `git clone` → `Permission denied (publickey)` | The deploy key is missing on GitHub, or `~/.ssh/config` is wrong. Test with `ssh -T git@github.com` |
| `systemctl status` shows `failed` | Read the reason: `journalctl -u aws-services-lab -n 50 --no-pager` |
| `status=203/EXEC` | The `ExecStart` path is wrong, or `.venv` does not exist (section 7) |
| `status=217/USER` | The `User=` in the service file does not exist on this server |
| `DB_PORT must be a whole number` | Fix that line in `.env` (an empty value is allowed and means the default) |
| `[Errno 98] Address already in use` | Something else uses port 8000, often a hand-started uvicorn: `sudo ss -ltnp \| grep 8000` |
| Logged out after every restart | `SESSION_SECRET` is empty in `.env` (section 7) |
| Login page keeps coming back | `SESSION_HTTPS_ONLY=true` while using `http://` through the tunnel. Set it to `false` for the tunnel |
| "No AWS credentials were found" | The IAM role is not attached (section 3), or `AWS_PROFILE` is not empty |
| `AccessDenied` / "not authorized to perform" | The role's policy lacks that action or resource. Add only that permission |
| RDS connection times out | The RDS security group must allow 5432 from `aws-services-lab-sg` |
| Changed `.env`, nothing happened | Settings are read at start-up: restart the app (section 11) |
| A dashboard row says **Access Denied** | Attach `deploy/iam-ec2-healthcheck-policy.json` (placeholders replaced) to the instance role |
| HTTPS row: **Configuration Error** behind the ALB | Set `TRUST_PROXY_HEADERS=true` **and** start uvicorn with `--proxy-headers --forwarded-allow-ips=<ALB subnets>` |
| HTTPS row: **Configuration Error** through the tunnel | Something sent `X-Forwarded-Proto`. Start uvicorn with `--no-proxy-headers` and keep `TRUST_PROXY_HEADERS=false` |
| EC2 shows **Wrong Region** | `AWS_REGION` differs from the instance's region. Set it to the instance's region (for example `ap-south-1`) |
| Disk filling up | `du -sh ~/aws-services-lab/logs` and `sudo journalctl --vacuum-size=200M`. Empty the log file with `truncate -s 0 ~/aws-services-lab/logs/app.log` |

Is the IAM role attached? On the instance:
```bash
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/info   # Shows the role's ARN
```

## 15. EC2 verification checklist

Local tests passing does **not** prove the EC2 deployment works. Check on the server:

- [ ] 8A: `curl http://127.0.0.1:8000/health` answers. 8B: also `systemctl is-active aws-services-lab` prints `active`
- [ ] In the browser's developer tools (Network tab), the main page's response has `Cache-Control: no-store`
- [ ] Through the tunnel: login, form, document checks, Services, Experiments, History, and logout all work
- [ ] `.venv/bin/python scripts/live_aws_check.py` shows the **role** ARN (`assumed-role/...`)
- [ ] 8B only: `sudo reboot`, reconnect, and the service is running again, earlier runs are still in History
- [ ] `ls -l .env` shows `-rw-------`
- [ ] The security group has **no** inbound rule for port 8000 from the internet

Click **Check AWS environment** and confirm each row (the *Last checked* time must change on every click):

- [ ] **HTTPS**: *Not Configured* through the tunnel; *Connected* behind the ALB with `TRUST_PROXY_HEADERS=true`
- [ ] **AWS IAM / STS**: *Connected*, credential source `iam-role`, identity `assumed-role/<your role>/i-...`
- [ ] **Amazon EC2**: *Connected* for metadata and *Describe this instance*; no *Wrong Region* row
- [ ] **AWS Secrets Manager**: *Describe secret* and *Retrieve value* both *Connected*; no value visible
- [ ] **SQLite (local file)**: *Connected*. **Amazon RDS**: *Connected* with `TLS in use: yes` (or *Not Configured* while on SQLite)
- [ ] **Amazon S3** and **AWS KMS**: *Connected*; then submit a document and see *AWS KMS → Verified*
- [ ] **AWS Lambda** and **Amazon SES**: *Connected*; SES says whether the account is in the sandbox
- [ ] **Amazon SNS / SQS** (if used): *Connected*
- [ ] **Amazon CloudWatch**: *Metrics API access → Connected*; after one submission, metrics and logs *delivered → Connected*
- [ ] **Disconnect test** (pick one service, for example S3): set a wrong bucket name, restart → *Not Found*;
      restore it, restart → *Connected*. Remove the setting, restart → *Not Configured*

## 16. Stop the app and clean up AWS resources

Stop the app on the server:
```bash
kill "$(cat ~/aws-services-lab/logs/uvicorn.pid)"   # 8A (started by hand)
sudo systemctl disable --now aws-services-lab       # 8B: stops it and stops it starting at boot
```

**Stop** the instance when you are not using it (EC2 → Instance state → Stop). You still pay for its
disk, and for a public IPv4 address while one is assigned. When you are finished, delete everything in
this order (in each service's console):

1. **ALB, target group, and listeners** (EC2 → Load Balancers / Target Groups). ALBs cost money every hour.
2. **EC2 instance:** Instance state → **Terminate** (deletes its disk by default). Release any **Elastic IP**:
   an unattached one is still charged.
3. **RDS:** Delete the database (choose whether to keep a final snapshot; snapshots cost money).
4. **S3:** Empty the bucket, then delete it.
5. **Lambda** function, **SNS** topic, **SQS** queue, and the **SES** identities you verified.
6. **Secrets Manager:** Delete the secret (it is kept for a 7–30-day recovery window).
7. **KMS:** Schedule key deletion (7–30-day waiting period). Do this **after** the bucket is gone.
8. **CloudWatch Logs:** delete the `/aws/lambda/...` log groups.
9. **ACM certificate**, **security groups**, the **key pair**, and the **IAM role and policies**.
10. On GitHub: **Settings → Deploy keys** → delete the EC2 deploy key.
