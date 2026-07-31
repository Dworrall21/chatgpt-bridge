import pytest

from chatgpt_bridge.state_machine import (
    IllegalTransitionError,
    RequestState,
    can_transition,
    requires_reconciliation,
    transition,
)


def test_valid_forward_chain():
    s = RequestState.RECEIVED
    for nxt in [
        RequestState.VALIDATED,
        RequestState.QUEUED,
        RequestState.UI_LOCKED,
        RequestState.PROJECT_VERIFIED,
        RequestState.CONVERSATION_BOUND,
        RequestState.MODEL_VERIFIED,
        RequestState.SEND_INTENT_RECORDED,
        RequestState.SENT,
        RequestState.WAITING,
        RequestState.COMPLETED,
    ]:
        s = transition(s, nxt)
    assert s == RequestState.COMPLETED


def test_illegal_skip():
    with pytest.raises(IllegalTransitionError):
        transition(RequestState.RECEIVED, RequestState.SENT)


def test_terminal_is_sticky():
    for s in [RequestState.COMPLETED, RequestState.FAILED, RequestState.CANCELLED]:
        assert not can_transition(s, RequestState.QUEUED)


def test_durable_after_send():
    assert requires_reconciliation(RequestState.SEND_INTENT_RECORDED)
    assert requires_reconciliation(RequestState.SENT)
    assert requires_reconciliation(RequestState.WAITING)
    assert not requires_reconciliation(RequestState.QUEUED)


def test_reconciliation_recovery_paths():
    assert can_transition(RequestState.NEEDS_RECONCILIATION, RequestState.SENT)
    assert can_transition(RequestState.NEEDS_RECONCILIATION, RequestState.WAITING)
    assert can_transition(RequestState.NEEDS_RECONCILIATION, RequestState.COMPLETED)
