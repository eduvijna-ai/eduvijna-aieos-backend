"""LearnerAttempt lifecycle states.

Frozen by ADR-AIEOS-058 / AIEOS360-S01: IN_PROGRESS / SUBMITTED only.
SUBMITTED is terminal. ABANDONED / GRADED / MASTERED / CANCELLED are not S01 states.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.learning.domain.errors import InvalidLearnerAttemptError


class AttemptLifecycleState(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"


def parse_attempt_lifecycle_state(
    value: AttemptLifecycleState | str,
) -> AttemptLifecycleState:
    if isinstance(value, AttemptLifecycleState):
        return value
    if not isinstance(value, str):
        raise InvalidLearnerAttemptError("lifecycle_state must be a string")
    try:
        return AttemptLifecycleState(value)
    except ValueError as exc:
        raise InvalidLearnerAttemptError(
            f"lifecycle_state is not a frozen S01 LearnerAttempt state: {value!r}"
        ) from exc
