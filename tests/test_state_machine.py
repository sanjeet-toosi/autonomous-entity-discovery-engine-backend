import pytest

from app.core.exceptions import InvalidTransition
from app.runs import state_machine as sm


def test_happy_path_transitions():
    for a, b in [(sm.DRAFT, sm.STARTING), (sm.STARTING, sm.RUNNING), (sm.RUNNING, sm.PAUSING), (sm.PAUSING, sm.PAUSED),
                 (sm.PAUSED, sm.RESUMING), (sm.RESUMING, sm.RUNNING), (sm.RUNNING, sm.STOPPING), (sm.STOPPING, sm.STOPPED)]:
        sm.assert_transition(a, b)


@pytest.mark.parametrize("a,b", [(sm.STOPPED, sm.RUNNING), (sm.RUNNING, sm.PAUSED), (sm.PAUSED, sm.RUNNING), (sm.COMPLETED, sm.STARTING), (sm.DRAFT, sm.RUNNING)])
def test_invalid_transitions_rejected(a, b):
    with pytest.raises(InvalidTransition):
        sm.assert_transition(a, b)


def test_terminal_states_have_no_exits():
    for s in sm.TERMINAL:
        assert sm.ALLOWED_TRANSITIONS[s] == set()
