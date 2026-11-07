"""Tests for the Live Workflow Monitor rules (experiments/workflow_states.py). No database, no AWS."""

from datetime import datetime, timezone

import pytest

from experiments import workflow_states as rules

T0 = datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("old, new, allowed", [
    (rules.PENDING, rules.RUNNING, True),
    (rules.RUNNING, rules.COMPLETED, True),
    (rules.RUNNING, rules.RETRYING, True),
    (rules.RETRYING, rules.RUNNING, True),
    (rules.TIMED_OUT, rules.COMPLETED, True),  # A late result still counts: a timeout is not proof of failure.
    (rules.TIMED_OUT, rules.RUNNING, True),
    (rules.PENDING, rules.SKIPPED, True),
    # Final states never change, so a duplicate or late event cannot undo a result.
    (rules.COMPLETED, rules.RUNNING, False),
    (rules.COMPLETED, rules.COMPLETED, False),
    (rules.COMPLETED, rules.FAILED, False),
    (rules.FAILED, rules.COMPLETED, False),
    (rules.SIMULATED, rules.RUNNING, False),
    (rules.SKIPPED, rules.RUNNING, False),
    # Nonsense changes.
    (rules.RUNNING, rules.RUNNING, False),
    (rules.RUNNING, rules.PENDING, False),
    (rules.RUNNING, rules.SKIPPED, False),
    (rules.TIMED_OUT, rules.TIMED_OUT, False),
])
def test_allowed_changes(old, new, allowed):
    assert rules.can_change(old, new) is allowed


def test_a_stale_retry_cannot_overwrite_a_newer_attempt():
    assert rules.can_change(rules.RETRYING, rules.RUNNING, old_attempt=2, new_attempt=2)
    assert not rules.can_change(rules.RETRYING, rules.RUNNING, old_attempt=2, new_attempt=1)


@pytest.mark.parametrize("recoverable, attempt, expected", [
    (True, 1, rules.RETRYING),  # A temporary error with attempts left: retry, do NOT fail yet.
    (True, 2, rules.RETRYING),
    (True, 3, rules.FAILED),  # Every attempt used up.
    (False, 1, rules.FAILED),  # A permanent error (for example AccessDenied) fails at once.
])
def test_failed_only_after_retries_are_used_up(recoverable, attempt, expected):
    assert rules.state_after_error(recoverable, attempt, max_attempts=3) == expected


@pytest.mark.parametrize("states, expected", [
    ([rules.COMPLETED, rules.COMPLETED], rules.COMPLETED),
    ([rules.COMPLETED, rules.SKIPPED], rules.COMPLETED),
    ([rules.COMPLETED, rules.PENDING], rules.RUNNING),
    ([rules.COMPLETED, rules.RETRYING, rules.PENDING], rules.RETRYING),
    ([rules.COMPLETED, rules.TIMED_OUT], rules.TIMED_OUT),
    ([rules.TIMED_OUT, rules.RUNNING], rules.RUNNING),
    ([rules.COMPLETED, rules.FAILED, rules.RUNNING], rules.FAILED),
    ([rules.COMPLETED, rules.SIMULATED], rules.SIMULATED),
    ([], rules.EXPIRED),
])
def test_overall_state(states, expected):
    assert rules.overall_state([{"state": state} for state in states]) == expected


def test_overdue_stages_only_waiting_ones_after_their_deadline():
    deadline = rules.to_text(rules.add_seconds(T0, 60))
    stages = [{"stage_id": "a", "state": rules.PENDING, "deadline_at": deadline},
              {"stage_id": "b", "state": rules.COMPLETED, "deadline_at": deadline},
              {"stage_id": "c", "state": rules.RUNNING, "deadline_at": None}]
    assert rules.overdue_stages(stages, rules.add_seconds(T0, 59)) == []
    assert [stage["stage_id"] for stage in rules.overdue_stages(stages, rules.add_seconds(T0, 61))] == ["a"]


def test_polling_only_while_in_progress():
    created = rules.to_text(T0)
    for in_progress in (rules.PENDING, rules.RUNNING, rules.RETRYING):
        assert rules.should_poll(in_progress)
    # Finished, or Timed Out (a late result is shown on the next reload): no more requests.
    for finished in (rules.COMPLETED, rules.FAILED, rules.TIMED_OUT, rules.SIMULATED, rules.EXPIRED):
        assert not rules.should_poll(finished)
    assert rules.next_poll_milliseconds(created, rules.add_seconds(T0, 10)) == 2000
    assert rules.next_poll_milliseconds(created, rules.add_seconds(T0, 61)) == 5000


def test_duration_and_time_text():
    stage = {"started_at": rules.to_text(T0), "finished_at": rules.to_text(rules.add_seconds(T0, 1.5))}
    assert rules.duration_seconds(stage) == 1.5
    assert rules.duration_seconds({"started_at": rules.to_text(T0), "finished_at": None}) is None
    assert rules.from_text(rules.to_text(T0)) == T0
