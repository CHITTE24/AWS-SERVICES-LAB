# Local Setup (Windows + VS Code)

Run every command in the VS Code terminal (**Terminal → New Terminal**), from the project folder `D:\AWS SERVICES LAB`.

## 1. Create and activate the virtual environment

A virtual environment is a private folder (`.venv`) that holds this project's libraries, so they don't mix with other projects.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell says *"running scripts is disabled"*, run this once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Your prompt now starts with `(.venv)`. In VS Code, also choose this interpreter: **Ctrl+Shift+P → "Python: Select Interpreter" → `.venv`**.

## 2. Install the libraries

```powershell
pip install -r requirements-dev.txt
```

`requirements.txt` lists what the app needs. `requirements-dev.txt` adds the testing tools.

## 3. Create your `.env` settings file

```powershell
copy .env.example .env
```

The copied file works as it is (local mode, SQLite, no AWS resources). Optionally, add a session secret so you stay logged in when the app restarts:

```powershell
# Prints a long random value. Paste it after SESSION_SECRET= in .env
python -c "import secrets; print(secrets.token_hex(32))"
```

There is no password to configure. The login is a **demonstration login** that accepts any non-empty username and password (see README).

`.env` is listed in `.gitignore`, so it is never committed.

## 4. Start the app

```powershell
python -m uvicorn main:app --reload
```

- Open **http://127.0.0.1:8000** and log in with any username and password. You land on **Welcome to AWS Service Lab**.
- `--reload` restarts the app automatically whenever you save a Python file.
- Stop it with **Ctrl+C**.
- Health check: http://127.0.0.1:8000/health should show `{"status":"ok"}`.

The app runs without AWS credentials or resources. Unconfigured services on the lab form are shown as **Simulated**, and dashboard pages that call AWS show a clear "No AWS credentials" message.

## 5. Run the automated tests

```powershell
python -m pytest            # short output
python -m pytest -v         # one line per test
```

The tests use **moto**, a fake AWS that runs in memory. They never use your real credentials or account.

## 6. (Optional) Connect to your real AWS account, read-only

1. In the AWS Console, create an IAM user for this lab, or use an existing one. Attach a policy containing the actions in `deploy/iam-readonly-policy.json`. AWS's managed `ReadOnlyAccess` policy also works, but it grants more access than the app needs.
2. Install the AWS CLI (https://aws.amazon.com/cli/) and run `aws configure`. It stores your keys in `C:\Users\<you>\.aws\credentials`, **outside** this project.
3. If you used a named profile (`aws configure --profile lab`), set `AWS_PROFILE=lab` in `.env`.
4. Run the live check. It is read-only and free:

   ```powershell
   python scripts/live_aws_check.py
   ```

5. In the app, open **AWS STS → Who am I?**. It should show your account ID.

Never put access keys in `.env`, in the code, or in comments.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'fastapi'` | The virtual environment is not active. Run `.venv\Scripts\Activate.ps1`. |
| `ModuleNotFoundError: No module named 'core'` | Run commands from the project folder, not from inside a subfolder. |
| Login returns to the login page | `SESSION_HTTPS_ONLY=true` while browsing over plain `http://`: the browser drops the Secure cookie. Set it to `false` locally. |
| "Please enter both a username and a password" | One of the login fields was empty. Any non-empty values work. |
| Logged out after every restart | `SESSION_SECRET` is empty in `.env`. |
| "No AWS credentials were found" | Run `aws configure`, or set `AWS_PROFILE` in `.env`. |
| "Permission denied for ListBuckets" | Your IAM identity lacks that permission. See CODE_CUSTOMIZATION_GUIDE.md §13. |
| `[WinError 32] ... being used by another process` during pip install | Antivirus or VS Code was scanning the folder. Delete `.venv` and run the install again. |
