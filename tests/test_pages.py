"""End-to-end page tests: a fake browser clicks through the app, with moto standing in for AWS."""

from repositories.history_repository import list_runs


def test_dashboard_lists_services(logged_in_client):
    response = logged_in_client.get("/")
    assert "Amazon S3" in response.text
    assert "Read-only mode" in response.text


def test_service_page_and_unknown_service(logged_in_client):
    assert logged_in_client.get("/services/s3").status_code == 200
    assert logged_in_client.get("/services/not-real").status_code == 404


def test_run_operation_shows_result_and_saves_history(logged_in_client, fake_aws):
    response = logged_in_client.post("/services/sts/get_caller_identity", data={})
    assert response.status_code == 200
    assert "123456789012" in response.text
    # The new run appears on the history page.
    assert "sts / get_caller_identity" in logged_in_client.get("/history").text


def test_operation_with_invalid_input_shows_error(logged_in_client, fake_aws):
    # bucket_name is required, so an empty form must produce a friendly error, not a crash.
    response = logged_in_client.post("/services/s3/count_objects", data={"bucket_name": ""})
    assert response.status_code == 200
    assert "Bucket name is required" in response.text


def test_operation_aws_error_is_friendly(logged_in_client, fake_aws):
    # The bucket does not exist in moto, so AWS returns NoSuchBucket.
    response = logged_in_client.post("/services/s3/count_objects", data={"bucket_name": "missing-bucket"})
    assert "NoSuchBucket" in response.text


def test_experiment_flow(logged_in_client, fake_aws):
    assert logged_in_client.get("/experiments").status_code == 200
    assert logged_in_client.get("/experiments/account_overview").status_code == 200

    response = logged_in_client.post("/experiments/account_overview", data={"region": ""})
    assert response.status_code == 200
    assert "Account ID" in response.text


def test_experiment_rejects_invalid_input(logged_in_client):
    response = logged_in_client.post("/experiments/lambda_health", data={"hours": "999", "region": ""})
    assert response.status_code == 400
    assert "at most 168" in response.text


def test_history_detail_pages(logged_in_client, fake_aws):
    logged_in_client.post("/experiments/account_overview", data={"region": ""})
    response = logged_in_client.get("/history")
    assert response.status_code == 200
    newest_run_id = list_runs(limit=1)[0]["id"]
    assert logged_in_client.get(f"/history/{newest_run_id}").status_code == 200
    assert logged_in_client.get("/history/999999").status_code == 404
