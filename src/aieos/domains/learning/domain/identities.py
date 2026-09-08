"""Learning identity value objects.

Learning-owned semantic IDs only. Shared platform identities (tenant,
principal) and opaque Teaching/Content references are stdlib UUID values.
Do not import Teaching identities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from aieos.domains.learning.domain.errors import (
    InvalidAggregateRevisionError,
    InvalidLearningIdentityError,
)


def _require_uuid7(value: UUID | str, *, label: str) -> UUID:
    parsed: UUID
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise InvalidLearningIdentityError(f"{label} is not a valid UUID") from exc
    else:
        raise InvalidLearningIdentityError(f"{label} must be a UUID")
    if parsed.version != 7:
        raise InvalidLearningIdentityError(
            f"{label} must be UUIDv7; got version {parsed.version!r}"
        )
    return parsed


def require_foreign_uuid(value: UUID, *, label: str) -> UUID:
    """Accept a stdlib UUID for a non-Learning-owned identity field."""
    if not isinstance(value, UUID):
        raise InvalidLearningIdentityError(f"{label} must be a UUID")
    return value


@dataclass(frozen=True, slots=True)
class AttemptId:
    """Stable LearnerAttempt identity."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_uuid7(self.value, label="attempt_id"))

    @classmethod
    def generate(cls) -> AttemptId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class SubmissionId:
    """Stable LearnerSubmission identity."""

    value: UUID

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _require_uuid7(self.value, label="submission_id")
        )

    @classmethod
    def generate(cls) -> SubmissionId:
        return cls(uuid.uuid7())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class AggregateRevision:
    """Optimistic concurrency revision of a Learning-owned aggregate."""

    value: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, int)
            or self.value < 0
        ):
            raise InvalidAggregateRevisionError(
                "aggregate_revision must be a non-negative integer"
            )

    def next(self) -> AggregateRevision:
        return AggregateRevision(self.value + 1)

    def __int__(self) -> int:
        return self.value
