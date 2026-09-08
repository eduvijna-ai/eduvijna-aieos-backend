"""LearnerAttempt aggregate — Learning-domain System of Record for one attempt.

Intrinsic construction only. Does not prove current membership, ACTIVE HUMAN
Principal, TeachingAssignment ACTIVE / available_from, or HTTP authorization.
Those checks belong to S01-I03 authoritative orchestration.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from aieos.domains.learning.domain.errors import (
    AttemptAlreadySubmittedError,
    InvalidLearnerAttemptError,
)
from aieos.domains.learning.domain.identities import (
    AggregateRevision,
    AttemptId,
    SubmissionId,
    require_foreign_uuid,
)
from aieos.domains.learning.domain.lifecycle import (
    AttemptLifecycleState,
    parse_attempt_lifecycle_state,
)

MAX_CLASS_REF_LENGTH: Final = 512


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidLearnerAttemptError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidLearnerAttemptError(f"{label} must be timezone-aware")
    return value


def _require_class_ref(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidLearnerAttemptError("class_ref must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > MAX_CLASS_REF_LENGTH:
        raise InvalidLearnerAttemptError(
            f"class_ref must be at most {MAX_CLASS_REF_LENGTH} characters"
        )
    return stripped


def _require_attempt_number(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidLearnerAttemptError("attempt_number must be an integer >= 1")
    return value


@dataclass(frozen=True, slots=True)
class LearnerAttempt:
    """Durable learner attempt working-state aggregate."""

    attempt_id: AttemptId
    tenant_id: UUID
    learner_principal_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    attempt_number: int
    lifecycle_state: AttemptLifecycleState
    started_at: datetime
    last_saved_at: datetime | None
    submitted_at: datetime | None
    submission_id: SubmissionId | None
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.attempt_id, AttemptId):
            raise InvalidLearnerAttemptError("attempt_id must be an AttemptId")
        if not isinstance(self.aggregate_revision, AggregateRevision):
            raise InvalidLearnerAttemptError(
                "aggregate_revision must be an AggregateRevision"
            )
        set_(
            self,
            "lifecycle_state",
            parse_attempt_lifecycle_state(self.lifecycle_state),
        )
        set_(self, "class_ref", _require_class_ref(self.class_ref))
        set_(self, "attempt_number", _require_attempt_number(self.attempt_number))
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.learner_principal_id, label="learner_principal_id"
        )
        require_foreign_uuid(
            self.teaching_assignment_id, label="teaching_assignment_id"
        )
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        set_(self, "started_at", _require_aware(self.started_at, label="started_at"))
        set_(self, "created_at", _require_aware(self.created_at, label="created_at"))
        set_(self, "updated_at", _require_aware(self.updated_at, label="updated_at"))
        if self.updated_at < self.created_at:
            raise InvalidLearnerAttemptError("updated_at must not precede created_at")
        if self.last_saved_at is not None:
            set_(
                self,
                "last_saved_at",
                _require_aware(self.last_saved_at, label="last_saved_at"),
            )
            if self.last_saved_at < self.started_at:
                raise InvalidLearnerAttemptError(
                    "last_saved_at must not precede started_at"
                )
        if self.lifecycle_state is AttemptLifecycleState.IN_PROGRESS:
            if self.submitted_at is not None or self.submission_id is not None:
                raise InvalidLearnerAttemptError(
                    "IN_PROGRESS attempts must have null submitted_at and submission_id"
                )
            return
        if self.submitted_at is None or self.submission_id is None:
            raise InvalidLearnerAttemptError(
                "SUBMITTED attempts require submitted_at and submission_id"
            )
        if not isinstance(self.submission_id, SubmissionId):
            raise InvalidLearnerAttemptError("submission_id must be a SubmissionId")
        set_(
            self,
            "submitted_at",
            _require_aware(self.submitted_at, label="submitted_at"),
        )
        if self.submitted_at < self.started_at:
            raise InvalidLearnerAttemptError(
                "submitted_at must not precede started_at"
            )

    @classmethod
    def start_in_progress(
        cls,
        *,
        tenant_id: UUID,
        learner_principal_id: UUID,
        teaching_assignment_id: UUID,
        content_id: UUID,
        content_version_id: UUID,
        class_ref: str,
        started_at: datetime,
        attempt_number: int = 1,
        attempt_id: AttemptId | None = None,
    ) -> LearnerAttempt:
        """Materialize a new IN_PROGRESS LearnerAttempt.

        Intrinsic validation only. Not an authorized Student start command
        (S01-I03). S01 product policy remains one attempt; attempt_number > 1
        is persistence-forward-compatible only.
        """
        _require_aware(started_at, label="started_at")
        aid = AttemptId.generate() if attempt_id is None else attempt_id
        return cls(
            attempt_id=aid,
            tenant_id=tenant_id,
            learner_principal_id=learner_principal_id,
            teaching_assignment_id=teaching_assignment_id,
            content_id=content_id,
            content_version_id=content_version_id,
            class_ref=class_ref,
            attempt_number=attempt_number,
            lifecycle_state=AttemptLifecycleState.IN_PROGRESS,
            started_at=started_at,
            last_saved_at=None,
            submitted_at=None,
            submission_id=None,
            aggregate_revision=AggregateRevision(0),
            created_at=started_at,
            updated_at=started_at,
        )

    def record_material_response_save(
        self, *, last_saved_at: datetime
    ) -> LearnerAttempt:
        """Increment aggregate_revision once for a material working-state save."""
        if self.lifecycle_state is not AttemptLifecycleState.IN_PROGRESS:
            raise AttemptAlreadySubmittedError(
                "SUBMITTED LearnerAttempt cannot mutate response working state"
            )
        _require_aware(last_saved_at, label="last_saved_at")
        if last_saved_at < self.started_at:
            raise InvalidLearnerAttemptError(
                "last_saved_at must not precede started_at"
            )
        return dataclasses.replace(
            self,
            last_saved_at=last_saved_at,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=last_saved_at,
        )

    def bind_submitted_submission(
        self,
        *,
        submission_id: SubmissionId,
        submitted_at: datetime,
    ) -> LearnerAttempt:
        """Apply the terminal SUBMITTED identity after a pure submit transition."""
        if self.lifecycle_state is not AttemptLifecycleState.IN_PROGRESS:
            raise AttemptAlreadySubmittedError(
                "LearnerAttempt is already SUBMITTED; reopen is rejected"
            )
        _require_aware(submitted_at, label="submitted_at")
        if submitted_at < self.started_at:
            raise InvalidLearnerAttemptError(
                "submitted_at must not precede started_at"
            )
        return dataclasses.replace(
            self,
            lifecycle_state=AttemptLifecycleState.SUBMITTED,
            submitted_at=submitted_at,
            submission_id=submission_id,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=submitted_at,
        )
