"""Every valid transition is allowed and every other one is refused."""

import itertools

import pytest

from opsapp.domain.errors import InvalidTransition
from opsapp.domain.states import TERMINAL, TRANSITIONS, State, check_transition

PAIRS = list(itertools.product(State, State))


@pytest.mark.parametrize("cur, nxt", PAIRS, ids=[f"{a.value}->{b.value}" for a, b in PAIRS])
def test_transition_table(cur: State, nxt: State) -> None:
    if nxt in TRANSITIONS[cur]:
        assert check_transition(cur, nxt) == nxt
    else:
        with pytest.raises(InvalidTransition):
            check_transition(cur, nxt)


def test_named_invalid_transitions_from_plan() -> None:
    for cur, nxt in [
        (State.RECEIVED, State.APPROVED),
        (State.DRAFT_READY, State.EXECUTING),
        (State.AWAITING_APPROVAL, State.COMPLETED),
        (State.EXECUTING, State.CANCELED),
    ]:
        with pytest.raises(InvalidTransition):
            check_transition(cur, nxt)


def test_terminal_states_have_no_exits() -> None:
    assert {State.COMPLETED, State.CANCELED} == TERMINAL
