# AWS Services Lab

A small, local web dashboard for learning AWS with Python. It:

- lists AWS services by category (Compute, Storage, Database, Security, Monitoring, ...)
- runs **read-only** operations against your AWS account through Boto3 (list S3 buckets, describe EC2 instances, list IAM users, and more)
- runs **experiments** that combine several services into one report (Account Overview, S3 Storage Report, EC2 Security Review, Lambda Health)
- saves every run to a local **history** (SQLite)
- shows a friendly explanation when AWS refuses a request (for example, a missing IAM permission)

Everything is read-only by default. The app never creates AWS resources.

## Quick start (Windows, VS Code terminal)

```powershell
python -m venv .venv                       # Create a private Python environment for this project
.venv\Scripts\Activate.ps1                 # Turn it on (your prompt shows "(.venv)")
pip install -r requirements-dev.txt        # Install the libraries
copy .env.example .env                     # Create your settings file, then edit it (see docs/LOCAL_SETUP.md)
python scripts/make_password_hash.py       # Create your login password hash and paste it into .env
python -m uvicorn main:app --reload        # Start the app
```

Open http://127.0.0.1:8000 and log in.

Run the tests with `python -m pytest`. They use a fake AWS (moto) and never touch your account.

## Documentation

| File | What it explains |
|---|---|
| [docs/LOCAL_SETUP.md](docs/LOCAL_SETUP.md) | Step-by-step local setup, AWS credentials, and troubleshooting |
| [docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) | How the project is organised and how a request flows through it |
| [docs/CODE_CUSTOMIZATION_GUIDE.md](docs/CODE_CUSTOMIZATION_GUIDE.md) | 13 worked examples of common changes |
| [docs/EC2_DEPLOYMENT.md](docs/EC2_DEPLOYMENT.md) | Manual GitHub publishing and EC2 deployment (after your approval) |
| [docs/CHECKLIST.md](docs/CHECKLIST.md) | What has been verified and what still needs to be checked |
