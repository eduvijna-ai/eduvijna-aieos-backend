"""Learning application persistence ports. Runtime types are not part of this contract."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from aieos.domains.learning.application.models import (
    AssignmentConsumptionView,
    ExactAssignedContent,
)
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.identities import AggregateRevision, AttemptId
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from aieos.domains.learning.domain.submission import LearnerSubmission
from aieos.platform.events.ports import OutboxRepository
from aieos.platform.idempotency.ports import IdempotencyRepository
from aieos.platform.security.audit.ports import SecurityMutationAuditRepository


class StudentLearningAttemptRepository(Protocol):
    def get(self, attempt_id: AttemptId) -> LearnerAttempt | None: ...

    def get_for_update(self, attempt_id: AttemptId) -> LearnerAttempt | None: ...

    def insert(self, attempt: LearnerAttempt) -> None: ...

    def list_for_learner_assignment(
        self,
        learner_id: UUID,
        teaching_assignment_id: UUID,
    ) -> list[LearnerAttempt]: ...

    def list_for_learner(
        self,
        learner_id: UUID,
        assignment_ids: Sequence[UUID],
    ) -> list[LearnerAttempt]: ...


class StudentLearningResponseRepository(Protocol):
    def list_for_attempt(
        self, attempt_id: AttemptId
    ) -> list[AttemptResponseItem]: ...


class StudentLearningCommandUnitOfWork(Protocol):
    """One local command transaction for Student Learning mutations and reads."""

    attempts: StudentLearningAttemptRepository
    responses: StudentLearningResponseRepository
    idempotency: IdempotencyRepository
    outbox: OutboxRepository
    audit: SecurityMutationAuditRepository

    def get_assignment(
        self, assignment_id: UUID
    ) -> AssignmentConsumptionView | None: ...

    def get_assignment_for_update(
        self, assignment_id: UUID
    ) -> AssignmentConsumptionView | None: ...

    def list_current_assignments(
        self,
        class_refs: Sequence[str],
        *,
        now: datetime,
        limit: int,
        after_updated_at: datetime | None = None,
        after_assignment_id: UUID | None = None,
    ) -> list[AssignmentConsumptionView]: ...

    def count_current_assignments(
        self,
        class_refs: Sequence[str],
        *,
        now: datetime,
    ) -> int: ...

    def load_exact_assigned_content(
        self, content_id: UUID, content_version_id: UUID
    ) -> ExactAssignedContent | None: ...

    def persist_working_response_save(
        self,
        updated_attempt: LearnerAttempt,
        items: Sequence[AttemptResponseItem],
        *,
        expected_revision: AggregateRevision,
    ) -> bool: ...

    def persist_pure_submit_transition(
        self,
        submitted_attempt: LearnerAttempt,
        submission: LearnerSubmission,
        *,
        expected_revision: AggregateRevision,
    ) -> bool: ...

    def __enter__(self) -> StudentLearningCommandUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None: ...

    def commit(self) -> None: ...


class StudentLearningCommandUnitOfWorkFactory(Protocol):
    def __call__(
        self, execution_tenant_id: UUID
    ) -> StudentLearningCommandUnitOfWork: ...
