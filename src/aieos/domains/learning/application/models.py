"""Learner-facing application DTOs. Not HTTP models and not persistence rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping
from uuid import UUID

from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.response_item import AttemptResponseItem


ATTEMPT_SUMMARY_NOT_STARTED = "NOT_STARTED"
ATTEMPT_SUMMARY_IN_PROGRESS = "IN_PROGRESS"
ATTEMPT_SUMMARY_SUBMITTED = "SUBMITTED"

ASSIGNMENT_LIFECYCLE_ACTIVE = "ACTIVE"
ASSIGNMENT_LIFECYCLE_CLOSED = "CLOSED"
ASSIGNMENT_LIFECYCLE_CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class LearnerObjective:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class LearnerQuestion:
    id: str
    prompt: str
    question_type: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LearnerResource:
    content_id: UUID
    content_version_id: UUID
    content_type: str
    schema_id: str
    schema_version: int
    title: str
    learning_objectives: tuple[LearnerObjective, ...]
    instructions: str
    questions: tuple[LearnerQuestion, ...]


@dataclass(frozen=True, slots=True)
class AssignmentConsumptionView:
    """TeachingAssignment facts needed for learner consumption. Opaque IDs only."""

    assignment_id: UUID
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    lifecycle_state: str
    available_from: datetime
    due_at: datetime | None
    aggregate_revision: int


@dataclass(frozen=True, slots=True)
class ExactAssignedContent:
    content_id: UUID
    content_version_id: UUID
    content_type: str
    schema_id: str
    schema_version: int
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ResponseWrite:
    question_id: str
    response_kind: str
    choice_value: str | None
    text_value: str | None
    boolean_value: bool | None


@dataclass(frozen=True, slots=True)
class AttemptResponseRead:
    question_id: str
    response_kind: str
    choice_value: str | None
    text_value: str | None
    boolean_value: bool | None


@dataclass(frozen=True, slots=True)
class AttemptReadModel:
    attempt_id: UUID
    teaching_assignment_id: UUID
    learner_principal_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    attempt_number: int
    lifecycle_state: str
    started_at: datetime
    last_saved_at: datetime | None
    submitted_at: datetime | None
    submission_id: UUID | None
    aggregate_revision: int
    responses: tuple[AttemptResponseRead, ...]


@dataclass(frozen=True, slots=True)
class StudentAssignmentReadModel:
    assignment_id: UUID
    class_ref: str
    available_from: datetime
    due_at: datetime | None
    lifecycle_state: str
    currently_consumable: bool
    content_id: UUID
    content_version_id: UUID
    attempt_summary: str
    attempt_id: UUID | None
    resource: LearnerResource | None = None


@dataclass(frozen=True, slots=True)
class StudentAssignmentListResult:
    items: tuple[StudentAssignmentReadModel, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class StudentHomeReadModel:
    current_assignment_count: int
    items: tuple[StudentAssignmentReadModel, ...]


@dataclass(frozen=True, slots=True)
class MutationAuditProvenance:
    executing_principal_id: UUID
    execution_channel: object
    delegation_id: UUID | None = None
    trace_id: str | None = None


def derive_attempt_summary(attempts: tuple[LearnerAttempt, ...]) -> str:
    if any(
        item.lifecycle_state is AttemptLifecycleState.IN_PROGRESS for item in attempts
    ):
        return ATTEMPT_SUMMARY_IN_PROGRESS
    if any(
        item.lifecycle_state is AttemptLifecycleState.SUBMITTED for item in attempts
    ):
        return ATTEMPT_SUMMARY_SUBMITTED
    return ATTEMPT_SUMMARY_NOT_STARTED


def attempt_read_model(
    attempt: LearnerAttempt,
    responses: tuple[AttemptResponseItem, ...] = (),
) -> AttemptReadModel:
    return AttemptReadModel(
        attempt_id=attempt.attempt_id.value,
        teaching_assignment_id=attempt.teaching_assignment_id,
        learner_principal_id=attempt.learner_principal_id,
        content_id=attempt.content_id,
        content_version_id=attempt.content_version_id,
        class_ref=attempt.class_ref,
        attempt_number=attempt.attempt_number,
        lifecycle_state=attempt.lifecycle_state.value,
        started_at=attempt.started_at,
        last_saved_at=attempt.last_saved_at,
        submitted_at=attempt.submitted_at,
        submission_id=(
            None if attempt.submission_id is None else attempt.submission_id.value
        ),
        aggregate_revision=int(attempt.aggregate_revision),
        responses=tuple(
            AttemptResponseRead(
                question_id=item.question_id,
                response_kind=item.response_kind.value,
                choice_value=item.choice_value,
                text_value=item.text_value,
                boolean_value=item.boolean_value,
            )
            for item in responses
        ),
    )


def currently_consumable(assignment: AssignmentConsumptionView, now: datetime) -> bool:
    if assignment.lifecycle_state != ASSIGNMENT_LIFECYCLE_ACTIVE:
        return False
    return assignment.available_from <= now
