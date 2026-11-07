"""Shared test setup. pytest loads this file automatically before running any test.

The settings below are applied BEFORE the app is imported, so tests never use your real
.env file, your real AWS credentials, or your real database.
"""

import os  # Sets environment variables for the test run.
import tempfile  # Creates a throwaway folder for the test database.

import pytest  # The test framework.

# A throwaway folder for this test run.
TEST_FOLDER = tempfile.mkdtemp(prefix="aws-lab-tests-")
# The demo login accepts any non-empty password. This one is used to prove it is never stored or shown.
TEST_PASSWORD = "test-password-123"

# --- App settings for tests ---
os.environ["LAB_SKIP_DOTENV"] = "1"  # Ignores your personal .env file.
os.environ["SESSION_SECRET"] = "test-session-secret"
os.environ["DATABASE_PATH"] = os.path.join(TEST_FOLDER, "test.db")
os.environ["ALLOW_WRITE_OPERATIONS"] = "false"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["APP_MODE"] = "local"
os.environ["DATABASE_BACKEND"] = "sqlite"
# Tests start with NO AWS resources configured. Individual tests add them with monkeypatch.setenv.
for name in ("S3_BUCKET_NAME", "S3_KMS_KEY_ARN", "LAMBDA_FUNCTION_NAME", "SNS_TOPIC_ARN", "SQS_QUEUE_URL",
             "AWS_SECRETS_MANAGER_SECRET_NAME", "DB_HOST", "DB_PASSWORD", "SESSION_HTTPS_ONLY",
             "WORKFLOW_BACKEND", "WORKFLOW_LAMBDA_NAME", "WORKFLOW_MAX_PER_HOUR"):
    os.environ.pop(name, None)

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

from core import secret_store  # noqa: E402  Its cache is cleared between tests.
from main import app  # noqa: E402  The web application under test.


@pytest.fixture(autouse=True)
def clear_secret_cache():
    # autouse=True runs this for every test, so a secret cached by one test never leaks into another.
    secret_store.clear_cache()
    yield
    secret_store.clear_cache()


@pytest.fixture
def fake_aws():
    # Every AWS call made inside a test that uses this fixture goes to moto, not to real AWS.
    with mock_aws():
        yield


@pytest.fixture
def workflow_table(fake_aws, monkeypatch):
    # A FAKE DynamoDB table (inside moto) with the same design as the real aws-lab-workflows table.
    import boto3  # Imported here because it is only needed by this fixture.
    boto3.client("dynamodb", region_name="us-east-1").create_table(
        TableName="aws-lab-workflows", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "workflow_id", "KeyType": "HASH"}, {"AttributeName": "item", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "workflow_id", "AttributeType": "S"},
                              {"AttributeName": "item", "AttributeType": "S"}])
    monkeypatch.setenv("WORKFLOW_BACKEND", "dynamodb")
    return "aws-lab-workflows"


@pytest.fixture
def client():
    # A fake browser that is not logged in.
    return TestClient(app)


@pytest.fixture
def logged_in_client(client):
    # A fake browser that has logged in. The demo login accepts any non-empty username and password.
    response = client.post("/login", data={"username": "student", "password": TEST_PASSWORD})
    assert response.status_code == 200  # 200 means the login redirect led to the main page.
    return client
