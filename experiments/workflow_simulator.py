"""OFFLINE simulation for the Live Workflow Monitor: shows how a workflow moves through its stages
without any AWS account. NOTHING here calls AWS.

Every simulated change is written with source "simulator", and the page labels it SIMULATED, so a
simulated event can never be mistaken for a real AWS event. Simulated workflows are always stored in
the local SQLite file, even when WORKFLOW_BACKEND=dynamodb.

Each scenario is a timeline: (seconds after start, stage, new state, explanation, attempt).
The page's status requests "advance" the timeline. Steps that were already applied are refused by the
normal state rules, so applying the timeline again is always safe (idempotent).
"""

from experiments import workflow_states as rules  # States and time helpers.
from repositories import workflow_repository  # Writes the stage changes.

# Short deadlines, so the timeout scenario finishes quickly in a demonstration.
SIMULATED_EVENT_DEADLINE_SECONDS = 12
SIMULATED_PROCESSING_DEADLINE_SECONDS = 25

EVENT_DELIVERED = (2, "event", rules.SIMULATED, "Simulated S3 ObjectCreated event delivered. No real event exists.", 1)
LAMBDA_ATTEMPT_1 = (3, "lambda", rules.RUNNING, "Simulated Lambda attempt 1 of 3 is running.", 1)

# scenario id -> (label shown in the form, timeline)
SCENARIOS = {
    "success": ("Success", [
        EVENT_DELIVERED,
        LAMBDA_ATTEMPT_1,
        (6, "lambda", rules.SIMULATED, "Simulated Lambda finished successfully. Nothing ran in AWS.", 1),
    ]),
    "retry": ("Temporary error, then success", [
        EVENT_DELIVERED,
        LAMBDA_ATTEMPT_1,
        (5, "lambda", rules.RETRYING, "Simulated temporary error (ThrottlingException) on attempt 1 of 3. "
                                      "A temporary error is retried, not marked Failed.", 1),
        (9, "lambda", rules.RUNNING, "Simulated Lambda attempt 2 of 3 is running.", 2),
        (11, "lambda", rules.SIMULATED, "Simulated Lambda succeeded on attempt 2. Nothing ran in AWS.", 2),
    ]),
    "failure": ("Permanent failure", [
        EVENT_DELIVERED,
        LAMBDA_ATTEMPT_1,
        (5, "lambda", rules.FAILED, "Simulated permanent error (AccessDenied). Permanent errors are not retried, "
                                    "because another attempt would fail the same way.", 1),
    ]),
    "timeout": ("No S3 event arrives (timeout)", []),  # Nothing happens: the deadlines produce Timed Out.
}


def advance(workflow, now):
    """Applies every timeline step that is due. Returns True if any stage changed."""
    elapsed = (now - rules.from_text(workflow["created_at"])).total_seconds()
    _, timeline = SCENARIOS.get(workflow["scenario"], SCENARIOS["success"])
    changed = False
    for seconds, stage_id, state, detail, attempt in timeline:
        if elapsed >= seconds:
            # Refused (returns False) when the step was already applied: that is expected and harmless.
            if workflow_repository.set_stage(workflow, stage_id, state, detail, rules.SOURCE_SIMULATOR,
                                             attempt=attempt, now=now):
                changed = True
    return changed
