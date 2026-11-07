"""The rules of the Live Workflow Monitor: stages, states, allowed changes, deadlines, and polling.

This file does no input/output (no AWS, no database), so every rule is easy to test.
repositories/workflow_repository.py enforces the same rules inside the database, with
conditional writes, so duplicate events, retries, and concurrent writers can never break them.

A workflow has a fixed list of STAGES. Each stage has a state:
    Pending    - not started yet
    Running    - in progress
    Retrying   - a temporary error happened; another attempt will follow
    Completed  - proven by a real AWS answer
    Failed     - a permanent error, or every retry was used up
    Timed Out  - no result before its deadline. NOT proof of failure: a late result still updates it
    Simulated  - offline simulation: nothing was sent to AWS
    Skipped    - not run (for example because an earlier stage failed)
"""

from datetime import datetime, timedelta, timezone  # Times are always UTC.

# ---- States
PENDING = "Pending"
RUNNING = "Running"
RETRYING = "Retrying"
COMPLETED = "Completed"
FAILED = "Failed"
TIMED_OUT = "Timed Out"
SIMULATED = "Simulated"
SKIPPED = "Skipped"
EXPIRED = "Expired"  # Only for a whole workflow whose stored record was deleted after the retention period.

# A stage in one of these states never changes again.
FINAL_STATES = (COMPLETED, FAILED, SIMULATED, SKIPPED)
# A stage in one of these states is still waiting for something, so it has a deadline.
ACTIVE_STATES = (PENDING, RUNNING, RETRYING)

# For each NEW state: the states it may replace. Anything else is rejected (and simply ignored).
# - Final states can never be replaced, so a late duplicate event cannot undo a result.
# - "Timed Out" can still become Running/Retrying/Completed/Failed, because a timeout is not proof of failure.
ALLOWED_PREVIOUS = {
    RUNNING: (PENDING, RETRYING, TIMED_OUT),
    RETRYING: (PENDING, RUNNING, RETRYING, TIMED_OUT),
    COMPLETED: (PENDING, RUNNING, RETRYING, TIMED_OUT),
    FAILED: (PENDING, RUNNING, RETRYING, TIMED_OUT),
    TIMED_OUT: (PENDING, RUNNING, RETRYING),
    SIMULATED: (PENDING, RUNNING, RETRYING),
    SKIPPED: (PENDING,),
}

# ---- Stages, in display order: (stage id, name shown on the page, who writes it)
STAGES = [
    ("created", "Workflow created", "fastapi"),
    ("upload", "S3 upload acknowledged (PutObject)", "fastapi"),
    ("verify", "Object independently verified (HeadObject)", "fastapi"),
    ("kms", "SSE-KMS encryption verified", "fastapi"),
    ("event", "S3 event delivered to Lambda", "lambda"),
    ("lambda", "Lambda processing", "lambda"),
]
STAGE_NAMES = {stage_id: name for stage_id, name, _ in STAGES}

# Who wrote a stage's current state. "simulator" is never an AWS event.
SOURCE_FASTAPI = "fastapi"
SOURCE_LAMBDA = "lambda"
SOURCE_SIMULATOR = "simulator"


# ---- Time helpers (everything is stored as text like "2026-11-08T10:42:05.123+00:00")

def utc_now():
    # The current time. Tests replace this function to "move the clock".
    return datetime.now(timezone.utc)


def to_text(moment):
    return moment.isoformat(timespec="milliseconds")


def from_text(text):
    return datetime.fromisoformat(text) if text else None


def add_seconds(moment, seconds):
    return moment + timedelta(seconds=seconds)


# ---- Rules

def can_change(old_state, new_state, old_attempt=0, new_attempt=None):
    """True if a stage may move from old_state to new_state.

    new_attempt: the attempt number of the writer (for example a Lambda retry). A write from an OLDER
    attempt than the one already stored is stale and rejected. None means "attempt not relevant".
    """
    if old_state not in ALLOWED_PREVIOUS.get(new_state, ()):
        return False
    if new_attempt is not None and new_attempt < (old_attempt or 0):
        return False
    return True


def state_after_error(recoverable, attempt, max_attempts):
    """What a stage becomes after an error.

    A recoverable (temporary) error becomes Retrying while attempts remain. Only when every attempt is
    used up, or the error is permanent (for example AccessDenied), does the stage become Failed.
    """
    if recoverable and attempt < max_attempts:
        return RETRYING
    return FAILED


def overall_state(stages):
    """One state for the whole workflow, from its stages (the most important one wins)."""
    states = [stage["state"] for stage in stages]
    if not states:
        return EXPIRED
    if FAILED in states:
        return FAILED
    if RETRYING in states:
        return RETRYING
    if RUNNING in states or PENDING in states:
        return RUNNING
    if TIMED_OUT in states:
        return TIMED_OUT
    if SIMULATED in states:
        return SIMULATED
    return COMPLETED


def overdue_stages(stages, now):
    """Returns the stages that are still waiting (Pending/Running/Retrying) after their deadline."""
    overdue = []
    for stage in stages:
        deadline = from_text(stage.get("deadline_at"))
        if stage["state"] in ACTIVE_STATES and deadline and now > deadline:
            overdue.append(stage)
    return overdue


def should_poll(overall):
    """Should the browser ask again? Only while something is still in progress.

    This cannot go on forever: every waiting stage has a deadline, after which it becomes Timed Out.
    The page itself also stops after WORKFLOW_POLL_LIMIT_SECONDS per page view (a reload resumes).
    Timed Out does not poll: a late result is shown on the next reload.
    """
    return overall in (PENDING, RUNNING, RETRYING)


def next_poll_milliseconds(created_at, now):
    """Every 2 seconds during the first minute, then every 5 seconds (fewer requests, lower cost)."""
    age = (now - from_text(created_at)).total_seconds()
    return 2000 if age < 60 else 5000


def duration_seconds(stage):
    """How long a stage took, or None if it has not both started and finished."""
    started, finished = from_text(stage.get("started_at")), from_text(stage.get("finished_at"))
    if started and finished:
        return round((finished - started).total_seconds(), 2)
    return None
