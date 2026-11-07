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
   [deploy/iam-ec2-app-policy.json](../deploy/iam-ec2-app-policy.json) one statement at a time.

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
| `AWS_REGION` | your region, for example `ap-south-1` | Region of the AWS resources you create later |
| `AWS_PROFILE` | **empty** | Boto3 then uses the instance's IAM role |
| `DATABASE_BACKEND` | `sqlite` | Saved in `data/lab.db`, created automatically |
| Every other AWS setting | empty | Filled in one at a time in section 12 |

Test-run once by hand:

```bash
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```
In a **second** SSH window: `curl http://127.0.0.1:8000/health` should print `{"status":"ok"}`.
Then press **Ctrl+C** in the first window.

## 8. Run it as a systemd service

systemd starts the app at boot, restarts it after a crash, and collects its logs.

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

## 10. Logs and health

```bash
journalctl -u aws-services-lab -f                     # Live log (Ctrl+C to stop watching)
journalctl -u aws-services-lab -n 100 --no-pager      # The last 100 lines
journalctl -u aws-services-lab --since "1 hour ago"   # Everything from the last hour
tail -f ~/aws-services-lab/logs/app.log               # The app's own log file (the same app messages)
systemctl status aws-services-lab                     # Running? Since when? Last few log lines
curl http://127.0.0.1:8000/health                     # {"status":"ok"} means the web server answers
```
*`/health` deliberately does not call AWS, so it works before any service is connected. Secrets and
passwords are never written to the logs.*

## 11. Restart, update from GitHub, and roll back

```bash
sudo systemctl restart aws-services-lab       # After editing .env (settings are read at start-up)
sudo systemctl stop aws-services-lab          # Stops it (it finishes requests in progress first)
sudo systemctl start aws-services-lab         # Starts it again
```

After you push new commits from your computer:

```bash
cd ~/aws-services-lab
git pull                                       # Downloads the latest commits
.venv/bin/pip install -r requirements.txt      # Installs any new libraries
sudo cp deploy/aws-services-lab.service /etc/systemd/system/ && sudo systemctl daemon-reload   # Only if the service file changed
sudo systemctl restart aws-services-lab
curl http://127.0.0.1:8000/health
```
`git pull` never touches `.env`, `data/`, or `logs/`: they are not in the repository.

Roll back to an earlier version: `git log --oneline`, then `git checkout <commit-id>`, then restart.
Return to the latest with `git checkout main && git pull`.

## 12. Connect the AWS services one at a time

Do these in order, **one at a time**. After each change: edit `.env` → `sudo systemctl restart
aws-services-lab` → test → check `journalctl -u aws-services-lab -n 50`. The detailed console steps are in
[README.md → AWS Security Setup](../README.md#aws-security-setup).

| # | Service | AWS Console | IAM permission for `AwsServicesLabRole` | `.env` setting | How to test |
|---|---|---|---|---|---|
| 1 | IAM role / STS | Section 1 | (none needed) | `AWS_PROFILE` empty | `.venv/bin/python scripts/live_aws_check.py` shows `assumed-role/AwsServicesLabRole/...` |
| 2 | S3 + KMS | Create a private bucket and a KMS key (README steps 1–5) | `s3:PutObject` + `kms:GenerateDataKey` statements; KMS key policy statements | `S3_BUCKET_NAME`, `S3_KMS_KEY_ARN` | Submit with a document: *AWS KMS → Verified* |
| 3 | Lambda + SES | Verify SES identities; create the function (README step 11) | `lambda:InvokeFunction` | `LAMBDA_FUNCTION_NAME` | Submit: email status **Sent** |
| 4 | SNS / SQS (optional) | Create a topic / queue | `sns:Publish` / `sqs:SendMessage` | `SNS_TOPIC_ARN`, `SQS_QUEUE_URL` | Submit: SNS / SQS rows **Success** |
| 5 | Secrets Manager | Store the DB password secret (README step 6) | `secretsmanager:GetSecretValue` | `AWS_SECRETS_MANAGER_SECRET_NAME` | **Check AWS environment**: *Secrets Manager → Verified* |
| 6 | RDS PostgreSQL | Private instance; its security group allows 5432 **from `aws-services-lab-sg` only** (README step 9) | (uses the secret above) | `DATABASE_BACKEND=postgres`, `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_SSLROOTCERT` | `.venv/bin/python scripts/init_database.py`, then **Check AWS environment** → *Database / Test connection*: `TLS in use: yes` |
| 7 | CloudWatch | Metrics are read automatically | read-only policy (section 1) | none | *Lambda Health* experiment |

For the RDS certificate bundle:
`curl --create-dirs -o ~/aws-services-lab/certs/global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem`,
then set `DB_SSLROOTCERT=/home/ubuntu/aws-services-lab/certs/global-bundle.pem`.

When **every** service you use is configured, set `APP_MODE=aws`, so a missing setting shows **Failed**
instead of Simulated. A service that is not configured never crashes the app: its row just says so.

## 13. Public access: HTTPS only, and protect the demo login

> **The login is a demonstration login: any username and password are accepted.** Anyone who can reach
> the app can use it. Keep using the SSH tunnel unless you really need browser access from elsewhere.

If you do need it, use HTTPS through an **Application Load Balancer** (README step 10). In short:
ACM certificate → internet-facing ALB with an HTTPS :443 listener (and HTTP :80 redirecting to 443) →
target group on port 8000 with health check path `/health`. Then:

- **Restrict the ALB's security group** to your own IP address on 443/80, not 0.0.0.0/0, until real
  authentication (for example Amazon Cognito) replaces the demo login.
- In `aws-services-lab-sg`, allow port **8000 only from the ALB's security group**.
- In the service file, switch to the commented ALB `ExecStart` line, and set `SESSION_HTTPS_ONLY=true`.
  Then `sudo systemctl daemon-reload && sudo systemctl restart aws-services-lab`.
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
| Changed `.env`, nothing happened | Settings are read at start-up: `sudo systemctl restart aws-services-lab` |
| Disk filling up | `du -sh ~/aws-services-lab/logs` and `sudo journalctl --vacuum-size=200M`. Empty the log file with `truncate -s 0 ~/aws-services-lab/logs/app.log` |

Is the IAM role attached? On the instance:
```bash
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/info   # Shows the role's ARN
```

## 15. EC2 verification checklist

Local tests passing does **not** prove the EC2 deployment works. Check on the server:

- [ ] `systemctl is-active aws-services-lab` prints `active`
- [ ] `curl http://127.0.0.1:8000/health` prints `{"status":"ok"}`
- [ ] Through the tunnel: login, form, document checks, Services, Experiments, History, and logout all work
- [ ] `.venv/bin/python scripts/live_aws_check.py` shows the **role** ARN (`assumed-role/AwsServicesLabRole/...`)
- [ ] `sudo reboot`, reconnect, and the service is running again, earlier runs are still listed in History, and you are still logged in
- [ ] `ls -l .env` shows `-rw-------`
- [ ] The security group has **no** inbound rule for port 8000 from the internet
- [ ] After each AWS service in section 12: its row shows the real result (Verified / Sent / Success)

## 16. Stop the app and clean up AWS resources

Stop the app on the server:
```bash
sudo systemctl disable --now aws-services-lab   # Stops it and stops it starting at boot
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
