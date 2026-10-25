"""Tests for the SQLite run history."""

from repositories.history_repository import get_run, list_runs, save_run


def test_save_and_read_run():
    run_id = save_run("operation", "s3 / list_buckets", {"region": None}, "success",
                      [{"name": "bucket-a"}], "admin")
    run = get_run(run_id)
    assert run["name"] == "s3 / list_buckets"
    assert run["result"] == [{"name": "bucket-a"}]
    assert run["inputs"] == {"region": None}


def test_list_runs_newest_first():
    first_id = save_run("experiment", "first", {}, "success", {}, "admin")
    second_id = save_run("experiment", "second", {}, "warning", {}, "admin")
    ids = [run["id"] for run in list_runs(limit=10)]
    assert ids.index(second_id) < ids.index(first_id)


def test_missing_run_returns_none():
    assert get_run(999999) is None
