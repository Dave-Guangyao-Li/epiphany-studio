from __future__ import annotations

import pytest

from epiphany.state_machine import (
    InvalidTransition,
    RunStatus,
    TaskStatus,
    validate_run_transition,
    validate_task_transition,
)


def test_valid_run_and_task_transitions() -> None:
    validate_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
    validate_run_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)
    validate_task_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    validate_task_transition(TaskStatus.RUNNING, TaskStatus.QUEUED)
    validate_task_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (RunStatus.SUCCEEDED, RunStatus.RUNNING),
        (RunStatus.FAILED, RunStatus.QUEUED),
        (RunStatus.CANCELLED, RunStatus.RUNNING),
    ],
)
def test_run_terminal_states_cannot_transition(
    source: RunStatus,
    target: RunStatus,
) -> None:
    with pytest.raises(InvalidTransition):
        validate_run_transition(source, target)


def test_task_terminal_state_cannot_transition() -> None:
    with pytest.raises(InvalidTransition):
        validate_task_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)
