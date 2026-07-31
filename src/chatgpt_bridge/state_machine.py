"""Durable request state machine.

Transitions mirror the design:
RECEIVED -> VALIDATED -> QUEUED -> UI_LOCKED -> PROJECT_VERIFIED ->
CONVERSATION_BOUND -> MODEL_VERIFIED -> SEND_INTENT_RECORDED -> SENT -> WAITING ->
COMPLETED | FAILED | NEEDS_RECONCILIATION | CANCELLED
"""

from __future__ import annotations

from enum import Enum


class RequestState(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    QUEUED = "QUEUED"
    UI_LOCKED = "UI_LOCKED"
    PROJECT_VERIFIED = "PROJECT_VERIFIED"
    CONVERSATION_BOUND = "CONVERSATION_BOUND"
    MODEL_VERIFIED = "MODEL_VERIFIED"
    SEND_INTENT_RECORDED = "SEND_INTENT_RECORDED"
    SENT = "SENT"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEEDS_RECONCILIATION = "NEEDS_RECONCILIATION"
    CANCELLED = "CANCELLED"


_ORDER = {s: i for i, s in enumerate(RequestState)}

# Allowed transitions: source -> set of destinations.
_ALLOWED: dict[RequestState, set[RequestState]] = {
    RequestState.RECEIVED: {RequestState.VALIDATED, RequestState.FAILED, RequestState.CANCELLED},
    RequestState.VALIDATED: {RequestState.QUEUED, RequestState.FAILED, RequestState.CANCELLED},
    RequestState.QUEUED: {
        RequestState.UI_LOCKED,
        RequestState.FAILED,
        RequestState.CANCELLED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.UI_LOCKED: {
        RequestState.PROJECT_VERIFIED,
        RequestState.FAILED,
        RequestState.CANCELLED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.PROJECT_VERIFIED: {
        RequestState.CONVERSATION_BOUND,
        RequestState.FAILED,
        RequestState.CANCELLED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.CONVERSATION_BOUND: {
        RequestState.MODEL_VERIFIED,
        RequestState.FAILED,
        RequestState.CANCELLED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.MODEL_VERIFIED: {
        RequestState.SEND_INTENT_RECORDED,
        RequestState.FAILED,
        RequestState.CANCELLED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.SEND_INTENT_RECORDED: {
        RequestState.SENT,
        RequestState.FAILED,
        RequestState.CANCELLED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.SENT: {
        RequestState.WAITING,
        RequestState.COMPLETED,
        RequestState.FAILED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.WAITING: {
        RequestState.COMPLETED,
        RequestState.FAILED,
        RequestState.NEEDS_RECONCILIATION,
    },
    RequestState.NEEDS_RECONCILIATION: {
        RequestState.SENT,
        RequestState.WAITING,
        RequestState.COMPLETED,
        RequestState.FAILED,
        RequestState.CANCELLED,
    },
    RequestState.COMPLETED: set(),
    RequestState.FAILED: set(),
    RequestState.CANCELLED: set(),
}

TERMINAL = {RequestState.COMPLETED, RequestState.FAILED, RequestState.CANCELLED}

# States that survive daemon restart and require reconciliation-aware recovery.
DURABLE_AFTER_SEND = {
    RequestState.SEND_INTENT_RECORDED,
    RequestState.SENT,
    RequestState.WAITING,
    RequestState.NEEDS_RECONCILIATION,
}


class IllegalTransitionError(ValueError):
    pass


def can_transition(current: RequestState, nxt: RequestState) -> bool:
    return nxt in _ALLOWED.get(current, set())


def transition(current: RequestState, nxt: RequestState) -> RequestState:
    if not can_transition(current, nxt):
        raise IllegalTransitionError(f"illegal transition {current.value} -> {nxt.value}")
    return nxt


def requires_reconciliation(state: RequestState) -> bool:
    return state in DURABLE_AFTER_SEND
