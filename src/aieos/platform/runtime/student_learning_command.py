"""Composed Student Learning command Unit of Work.

One SQLAlchemy Connection/transaction binds TeachingAssignment lock/read,
Learning attempt/response/submission writes, Content exact-version load,
platform idempotency, security mutation audit, and transactional outbox.

Does not import Teaching or Learning Units of Work. Membership is evaluated
before this transaction begins.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Transaction

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version import thaw_json_value
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyContentRepository,
    SqlAlchemyContentVersionRepository,
)
from aieos.domains.learning.application.errors import (
    AttemptAlreadySubmitted,
    AttemptConcurrencyConflict,
    PersistenceOperationFailed,
)
from aieos.domains.learning.application.models import (
    AssignmentConsumptionView,
    ExactAssignedContent,
)
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.identities import AggregateRevision
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from aieos.domains.learning.domain.submission import LearnerSubmission
from aieos.domains.learning.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.learning.infrastructure.persistence.repositories import (
    SqlAlchemyAttemptResponseItemRepository,
    SqlAlchemyLearnerAttemptRepository,
    SqlAlchemyLearnerSubmissionRepository,
)
from aieos.domains.teaching.domain.identities import AssignmentId
from aieos.domains.teaching.infrastructure.persistence.repositories import (
    SqlAlchemyTeachingAssignmentRepository,
)
from aieos.platform.api.infrastructure.persistence.repositories import (
    SqlAlchemyIdempotencyRepository,
)
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from aieos.platform.security.audit.persistence.errors import (
    SecurityAuditPersistenceError,
)
from aieos.platform.security.audit.persistence.repositories import (
    SqlAlchemySecurityMutationAuditRepository,
)


class _LearningAuditRepository:
    def __init__(self, connection: Connection) -> None:
        self._delegate = SqlAlchemySecurityMutationAuditRepository(connection)

    def insert(self, record) -> None:
        try:
            self._delegate.insert(record)
        except SecurityAuditPersistenceError as exc:
            raise PersistenceOperationFailed(
                "learning persistence operation failed"
            ) from exc


class SqlAlchemyStudentLearningCommandUnitOfWork:
    def __init__(self, engine: Engine, execution_tenant_id: UUID) -> None:
        self._engine = engine
        self._execution_tenant_id = execution_tenant_id
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.assignments: SqlAlchemyTeachingAssignmentRepository
        self.attempts: SqlAlchemyLearnerAttemptRepository
        self.responses: SqlAlchemyAttemptResponseItemRepository
        self.submissions: SqlAlchemyLearnerSubmissionRepository
        self.contents: SqlAlchemyContentRepository
        self.content_versions: SqlAlchemyContentVersionRepository
        self.idempotency: SqlAlchemyIdempotencyRepository
        self.outbox: SqlAlchemyOutboxRepository
        self.audit: _LearningAuditRepository

    def __enter__(self) -> SqlAlchemyStudentLearningCommandUnitOfWork:
        try:
            self._connection = self._engine.connect()
            self._transaction = self._connection.begin()
            self._connection.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(self._execution_tenant_id)},
            )
            self._bind_repositories(self._connection)
            return self
        except Exception as exc:
            self._cleanup(suppress=True)
            reraise_as_application_error(exc)

    @property
    def connection(self) -> Connection:
        if self._connection is None:
            raise PersistenceOperationFailed(
                "Student Learning command Unit of Work is not active"
            )
        return self._connection

    def _bind_repositories(self, connection: Connection) -> None:
        self.assignments = SqlAlchemyTeachingAssignmentRepository(
            connection, self._execution_tenant_id
        )
        self.attempts = SqlAlchemyLearnerAttemptRepository(
            connection, self._execution_tenant_id
        )
        self.responses = SqlAlchemyAttemptResponseItemRepository(
            connection, self._execution_tenant_id
        )
        self.submissions = SqlAlchemyLearnerSubmissionRepository(
            connection, self._execution_tenant_id
        )
        self.contents = SqlAlchemyContentRepository(
            connection, self._execution_tenant_id
        )
        self.content_versions = SqlAlchemyContentVersionRepository(connection)
        self.idempotency = SqlAlchemyIdempotencyRepository(
            connection, self._execution_tenant_id
        )
        self.outbox = SqlAlchemyOutboxRepository(connection)
        self.audit = _LearningAuditRepository(connection)

    def get_assignment(self, assignment_id: UUID) -> AssignmentConsumptionView | None:
        loaded = self.assignments.get(AssignmentId(assignment_id))
        if loaded is None:
            return None
        return _assignment_view(loaded)

    def get_assignment_for_update(
        self, assignment_id: UUID
    ) -> AssignmentConsumptionView | None:
        loaded = self.assignments.get_for_update(AssignmentId(assignment_id))
        if loaded is None:
            return None
        return _assignment_view(loaded)

    def list_assignments_for_class_refs(
        self, class_refs: Sequence[str], *, limit: int
    ) -> list[AssignmentConsumptionView]:
        rows = self.assignments.list_for_class_refs(
            class_refs=class_refs, limit=limit
        )
        return [_assignment_view(row) for row in rows]

    def load_exact_assigned_content(
        self, content_id: UUID, content_version_id: UUID
    ) -> ExactAssignedContent | None:
        try:
            content = self.contents.get(ContentId(content_id))
            version = self.content_versions.get(ContentVersionId(content_version_id))
        except Exception as exc:
            reraise_as_application_error(exc)
        if content is None or version is None:
            return None
        if version.content_id.value != content_id:
            return None
        if version.tenant_id != self._execution_tenant_id:
            return None
        payload = thaw_json_value(version.payload.body)
        if not isinstance(payload, dict):
            return None
        return ExactAssignedContent(
            content_id=content_id,
            content_version_id=content_version_id,
            content_type=str(content.content_type),
            schema_id=str(version.schema_id),
            schema_version=int(version.schema_version),
            payload=payload,
        )

    def persist_working_response_save(
        self,
        updated_attempt: LearnerAttempt,
        items: Sequence[AttemptResponseItem],
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        if updated_attempt.lifecycle_state is not AttemptLifecycleState.IN_PROGRESS:
            raise AttemptAlreadySubmitted(
                "SUBMITTED LearnerAttempt cannot mutate response working state"
            )
        if updated_attempt.last_saved_at is None:
            raise PersistenceOperationFailed(
                "material response save requires last_saved_at"
            )
        applied = self.attempts.update(
            updated_attempt, expected_revision=expected_revision
        )
        if not applied:
            raise AttemptConcurrencyConflict(
                "LearnerAttempt aggregate_revision did not match expected_revision"
            )
        self.responses.replace_for_attempt(
            updated_attempt,
            items,
            saved_at=updated_attempt.last_saved_at,
        )
        return True

    def persist_pure_submit_transition(
        self,
        submitted_attempt: LearnerAttempt,
        submission: LearnerSubmission,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        if submitted_attempt.lifecycle_state is not AttemptLifecycleState.SUBMITTED:
            raise PersistenceOperationFailed(
                "pure submit persistence requires a SUBMITTED LearnerAttempt"
            )
        if submitted_attempt.submission_id != submission.submission_id:
            raise PersistenceOperationFailed(
                "submitted attempt submission_id must equal LearnerSubmission"
            )
        self.submissions.insert(submission)
        applied = self.attempts.update(
            submitted_attempt, expected_revision=expected_revision
        )
        if not applied:
            raise AttemptConcurrencyConflict(
                "LearnerAttempt aggregate_revision did not match expected_revision"
            )
        return True

    def commit(self) -> None:
        if self._transaction is None:
            raise PersistenceOperationFailed(
                "Student Learning command Unit of Work is not active"
            )
        try:
            self._transaction.commit()
        except Exception as exc:
            reraise_as_application_error(exc)

    def rollback(self) -> None:
        if self._transaction is None or not self._transaction.is_active:
            return
        try:
            self._transaction.rollback()
        except Exception as exc:
            reraise_as_application_error(exc)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        self._cleanup(suppress=exc_type is not None)

    def _cleanup(self, *, suppress: bool) -> None:
        try:
            if self._transaction is not None and self._transaction.is_active:
                try:
                    self._transaction.rollback()
                except Exception as rollback_exc:
                    if not suppress:
                        reraise_as_application_error(rollback_exc)
        finally:
            try:
                if self._connection is not None:
                    self._connection.close()
            except Exception as close_exc:
                if not suppress:
                    reraise_as_application_error(close_exc)
            finally:
                self._connection = None
                self._transaction = None


class SqlAlchemyStudentLearningCommandUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(
        self, execution_tenant_id: UUID
    ) -> SqlAlchemyStudentLearningCommandUnitOfWork:
        return SqlAlchemyStudentLearningCommandUnitOfWork(
            self._engine, execution_tenant_id
        )


def _assignment_view(assignment) -> AssignmentConsumptionView:
    return AssignmentConsumptionView(
        assignment_id=assignment.assignment_id.value,
        class_ref=assignment.class_ref,
        content_id=assignment.content_id,
        content_version_id=assignment.content_version_id,
        lifecycle_state=str(assignment.lifecycle_state),
        available_from=assignment.available_from,
        due_at=assignment.due_at,
        aggregate_revision=int(assignment.aggregate_revision),
    )
