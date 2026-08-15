from __future__ import annotations

from .enums import SetupStatus


ALLOWED_SETUP_TRANSITIONS: dict[SetupStatus, frozenset[SetupStatus]] = {
    SetupStatus.DETECTED: frozenset(
        {SetupStatus.FORMING, SetupStatus.INVALIDATED, SetupStatus.EXPIRED}
    ),
    SetupStatus.FORMING: frozenset(
        {SetupStatus.READY_FOR_LLM, SetupStatus.INVALIDATED, SetupStatus.EXPIRED}
    ),
    SetupStatus.READY_FOR_LLM: frozenset(
        {
            SetupStatus.ACCEPTED,
            SetupStatus.REJECTED,
            SetupStatus.INVALIDATED,
            SetupStatus.EXPIRED,
        }
    ),
    SetupStatus.ACCEPTED: frozenset(
        {
            SetupStatus.ENTERED,
            SetupStatus.RISK_REJECTED,
            SetupStatus.INVALIDATED,
            SetupStatus.EXPIRED,
        }
    ),
    SetupStatus.ENTERED: frozenset({SetupStatus.CLOSED}),
    SetupStatus.REJECTED: frozenset(),
    SetupStatus.CLOSED: frozenset(),
    SetupStatus.INVALIDATED: frozenset(),
    SetupStatus.EXPIRED: frozenset(),
    SetupStatus.RISK_REJECTED: frozenset(),
}


def can_transition_setup(current: SetupStatus, target: SetupStatus) -> bool:
    return current == target or target in ALLOWED_SETUP_TRANSITIONS[current]


def assert_setup_transition(current: SetupStatus, target: SetupStatus) -> None:
    if not can_transition_setup(current, target):
        raise ValueError(f"invalid setup transition: {current.value} -> {target.value}")

