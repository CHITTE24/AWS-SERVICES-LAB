# Publishing to GitHub and Deploying to EC2

> **You run every step yourself.** Nothing in this project pushes to GitHub or creates AWS resources automatically.
> Do **not** start Phase C until you have used the app locally and are happy with it.

---

## Phase C: Review and commit locally (your approval step)

Run these in the VS Code terminal from the project folder.

```powershell
git init                          # Turns the folder into a Git repository (one time only)
git status                        # Lists the files Git will track. .env, .venv, data/, and logs/ must NOT appear
git check-ignore -v .env          # Confirms .env is ignored (prints the .gitignore line that matches)
git add .                         # Stages all non-ignored files
git diff --cached --stat          # Summary of what will be committed. Review it carefully
git commit -m "AWS Services Lab v1"
```

If a secret file ever appears in `git status`, **stop**: add it to `.gitignore`, then run `git rm --cached <file>`.

---

## Phase D: Push to GitHub (manual)

1. On github.com, create a **new private repository**, for example `aws-services-lab`. Do not add a README, because the project already has one.
2. Connect and push:

```powershell
git branch -M main
git remote add origin https://github.com/<your-username>/aws-services-lab.git
git push -u origin main
```

---

## Phase E: Deploy on EC2

The EC2 instance costs money while it is running (a `t3.micro` is roughly US$8–10/month on demand, or free-tier eligible on some accounts). **Stop or terminate it when you are not using it.**

### E1. Create an IAM role for the instance (AWS Console)

On EC2, the app uses an **IAM role** instead of access keys. Keys never need to exist on the server.

1. IAM → **Policies → Create policy → JSON**. Paste the contents of `deploy/iam-readonly-policy.json` and name it `AwsServicesLabReadOnly`.
2. IAM → **Roles → Create role** → Trusted entity: **AWS service → EC2** → attach `AwsServicesLabReadOnly` → name it `AwsServicesLabRole`.

### E2. Launch the instance (AWS Console)

| Setting | Value |
|---|---|
| AMI | Amazon Linux 2023 |
| Instance type | `t3.micro` |
| Key pair | Create or choose one, and keep the `.pem` file private |
| Security group inbound | **SSH (22) from "My IP" only**. Do not open port 8000. |
| Advanced → IAM instance profile | `AwsServicesLabRole` |
| Advanced → Metadata version | IMDSv2 only (V2 "required") |

### E3. Connect with SSH

```bash
ssh -i path/to/your-key.pem ec2-user@<PUBLIC_IP>
```
*Logs you into the server as the default `ec2-user` account, using your key file.*

### E4. Install Git and Python

```bash
sudo dnf install -y git python3.11
```
*`dnf` is Amazon Linux's package installer. `-y` answers "yes" automatically. Python 3.11 is used because the app needs 3.11 or newer.*

### E5. Get the code from GitHub

```bash
git clone https://github.com/<your-username>/aws-services-lab.git ~/aws-services-lab
cd ~/aws-services-lab
```
*Copies your repository into `/home/ec2-user/aws-services-lab`. For a private repository, GitHub asks for your username and a **personal access token** (GitHub → Settings → Developer settings → Fine-grained tokens, with read-only "Contents" access to this one repository) in place of your password.*

### E6. Create the virtual environment and install the libraries

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
*Only `requirements.txt` is needed on the server. The test tools are not.*

### E7. Configure `.env`

```bash
cp .env.example .env
.venv/bin/python scripts/make_password_hash.py                 # Prints ADMIN_PASSWORD_HASH=...
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"   # Prints a SESSION_SECRET value
nano .env                                                      # Paste both values. Ctrl+O saves, Ctrl+X exits
chmod 600 .env                                                 # Only ec2-user can read this file
```
Leave `AWS_PROFILE` **empty** on EC2, so Boto3 uses the instance's IAM role. Set `AWS_REGION` to the region you want to inspect.

### E8. Test-run once by hand

```bash
.venv/bin/python scripts/live_aws_check.py     # Should print OK lines with an "assumed-role/AwsServicesLabRole" ARN
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```
In a **second** SSH window, run `curl http://127.0.0.1:8000/health`. It should print `{"status":"ok"}`. Then press Ctrl+C in the first window.

### E9. Run it as a service (starts on boot, restarts on crash)

```bash
sudo cp deploy/aws-services-lab.service /etc/systemd/system/
sudo systemctl daemon-reload                  # Tells systemd a new service file exists
sudo systemctl enable --now aws-services-lab  # Starts it now and on every boot
sudo systemctl status aws-services-lab        # Should show "active (running)"
```
*If you cloned into a different folder or use a different username, edit those paths in the service file first.*

### E10. Open the dashboard from your computer (SSH tunnel)

The app listens only on the server itself (127.0.0.1), so it is not exposed to the internet. Reach it through an encrypted SSH tunnel:

```bash
ssh -i path/to/your-key.pem -L 8000:127.0.0.1:8000 ec2-user@<PUBLIC_IP>
```
*`-L 8000:127.0.0.1:8000` forwards your computer's port 8000 to the server's port 8000.* Keep this window open, then browse to **http://127.0.0.1:8000** on your computer.

---

## EC2 verification checklist

Local tests passing does **not** prove the EC2 deployment works. Check each item on the server:

- [ ] `systemctl is-active aws-services-lab` prints `active`
- [ ] `curl http://127.0.0.1:8000/health` prints `{"status":"ok"}`
- [ ] `.venv/bin/python scripts/live_aws_check.py` shows the **role** ARN (`assumed-role/AwsServicesLabRole/...`)
- [ ] Through the SSH tunnel: login works, and *AWS STS → Who am I?* shows the role
- [ ] An experiment runs, and missing permissions show as friendly messages, not crashes
- [ ] `sudo reboot`, reconnect, and the service is running again
- [ ] `ls -l .env` shows `-rw-------` (only you can read it)
- [ ] The security group has **no** inbound rule for port 8000

Logs: `journalctl -u aws-services-lab -f` (live) or `tail -f ~/aws-services-lab/logs/app.log`.

## Updating after you push new commits

```bash
cd ~/aws-services-lab
git pull                                       # Downloads the latest commits
.venv/bin/pip install -r requirements.txt      # Installs any new libraries
sudo systemctl restart aws-services-lab        # Restarts with the new code
curl http://127.0.0.1:8000/health
```
Roll back: `git log --oneline`, then `git checkout <older-commit-id>`, then restart.

## Stopping costs

- **Stop** the instance (EC2 → Instance state → Stop) when you are not using it. You still pay for its EBS disk.
- **Terminate** it when you are finished. This deletes the instance and, by default, its disk.
