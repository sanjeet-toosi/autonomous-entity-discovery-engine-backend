"""Run lifecycle. All transitions are validated centrally."""
from __future__ import annotations

from app.core.exceptions import InvalidTransition

DRAFT, STARTING, RUNNING, PAUSING, PAUSED, RESUMING, STOPPING, STOPPED, COMPLETED, FAILED = (
    "DRAFT", "STARTING", "RUNNING", "PAUSING", "PAUSED", "RESUMING", "STOPPING", "STOPPED", "COMPLETED", "FAILED",
)

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    DRAFT: {STARTING, FAILED},
    STARTING: {RUNNING, FAILED, STOPPING},
    RUNNING: {PAUSING, STOPPING, FAILED, COMPLETED},
    PAUSING: {PAUSED, FAILED, STOPPING},
    PAUSED: {RESUMING, STOPPING},
    RESUMING: {RUNNING, FAILED},
    STOPPING: {STOPPED, FAILED},
    STOPPED: set(),
    COMPLETED: set(),
    FAILED: set(),
}

TERMINAL = {STOPPED, COMPLETED, FAILED}
ACTIVE = {STARTING, RUNNING, PAUSING, PAUSED, RESUMING, STOPPING}
# Workers may claim work only in these states
CLAIMABLE = {RUNNING}


def assert_transition(current: str, target: str) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"cannot transition run from {current} to {target}")
