"""Shared test setup. pytest loads this file automatically before running any test.

The settings below are applied BEFORE the app is imported, so tests never use your real
.env file, your real AWS credentials, or your real database.
"""

import os  # Sets environment variables for the test run.
import tempfile  # Creates a throwaway folder for the test database.

import pytest  # The test framework.

from auth.passwords import hash_password  # Creates the test user's password hash.

# A throwaway folder for this test run.
TEST_FOLDER = tempfile.mkdtemp(prefix="aws-lab-tests-")
TEST_PASSWORD = "test-password-123"

# --- App settings for tests ---
os.environ["LAB_SKIP_DOTENV"] = "1"  # Ignores your personal .env file.
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["ADMIN_USERNAME"] = "admin"
# A low iteration count keeps tests fast. Real hashes use 600,000.
os.environ["ADMIN_PASSWORD_HASH"] = hash_password(TEST_PASSWORD, iterations=1000)
os.environ["DATABASE_PATH"] = os.path.join(TEST_FOLDER, "test.db")
os.environ["ALLOW_WRITE_OPERATIONS"] = "false"
os.environ["AWS_REGION"] = "us-east-1"

# --- Fake AWS credentials, so a mistake can never reach a real account ---
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
# Points boto3 at files that do not exist, so your real ~/.aws files are never read.
os.environ["AWS_CONFIG_FILE"] = os.path.join(TEST_FOLDER, "no-config")
os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.path.join(TEST_FOLDER, "no-credentials")
os.environ.pop("AWS_PROFILE", None)  # Removes AWS_PROFILE if your shell had one set.

# These imports must come AFTER the settings above.
from fastapi.testclient import TestClient  # noqa: E402  Sends fake browser requests to the app.
from moto import mock_aws  # noqa: E402  Replaces AWS with an in-memory fake.

from main import app  # noqa: E402  The web application under test.


@pytest.fixture
def fake_aws():
    # Every AWS call made inside a test that uses this fixture goes to moto, not to real AWS.
    with mock_aws():
        yield


@pytest.fixture
def client():
    # A fake browser that is not logged in.
    return TestClient(app)


@pytest.fixture
def logged_in_client(client):
    # A fake browser that has logged in with the test password.
    response = client.post("/login", data={"username": "admin", "password": TEST_PASSWORD})
    assert response.status_code == 200  # 200 means the login redirect led to the dashboard.
    return client
