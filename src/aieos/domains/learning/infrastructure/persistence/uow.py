"""SQLAlchemy Unit of Work for Learning. Owns one tenant-scoped transaction.

Repositories are bound to this connection so later S01-I03 can instead
construct the same repository classes on a composed Teaching+Learning
connection. This UoW does not import Teaching UoW and does not serialize
TeachingAssignment rows.

Learning-internal atomicity (response save; submission insert + attempt
transition) is provided here. Cross-domain assignment lock order is I03.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, Transaction

from aieos.domains.learning.application.errors import (
    AttemptAlreadySubmitted,
    AttemptConcurrencyConflict,
    AttemptNotFound,
    PersistenceOperationFailed,
)
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.identities import AggregateRevision, AttemptId
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


class SqlAlchemyLearningUnitOfWork:
    def __init__(self, engine: Engine, execution_tenant_id: UUID) -> None:
        self._engine = engine
        self._execution_tenant_id = execution_tenant_id
        self._connection: Connection | None = None
        self._transaction: Transaction | None = None
        self.attempts: SqlAlchemyLearnerAttemptRepository
        self.responses: SqlAlchemyAttemptResponseItemRepository
        self.submissions: SqlAlchemyLearnerSubmissionRepository

    def __enter__(self) -> SqlAlchemyLearningUnitOfWork:
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
            raise PersistenceOperationFailed("Learning Unit of Work is not active")
        return self._connection

    def _bind_repositories(self, connection: Connection) -> None:
        self.attempts = SqlAlchemyLearnerAttemptRepository(
            connection, self._execution_tenant_id
        )
        self.responses = SqlAlchemyAttemptResponseItemRepository(
            connection, self._execution_tenant_id
        )
        self.submissions = SqlAlchemyLearnerSubmissionRepository(
            connection, self._execution_tenant_id
        )

    def persist_working_response_save(
        self,
        updated_attempt: LearnerAttempt,
        items: Sequence[AttemptResponseItem],
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        """Replace working responses and CAS-update the parent attempt once.

        Not an authorized Student save command (S01-I03).
        """
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
        """Insert immutable submission and CAS-transition the attempt.

        Learning-internal atomicity only. Does not perform membership,
        TeachingAssignment ACTIVE, available_from, or assignment-row
        serialization (S01-I03).
        """
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

    def require_in_progress(self, attempt_id: AttemptId) -> LearnerAttempt:
        loaded = self.attempts.get_for_update(attempt_id)
        if loaded is None:
            raise AttemptNotFound("LearnerAttempt is not visible in this tenant")
        if loaded.lifecycle_state is AttemptLifecycleState.SUBMITTED:
            raise AttemptAlreadySubmitted(
                "SUBMITTED LearnerAttempt rejects mutation"
            )
        return loaded

    def commit(self) -> None:
        if self._transaction is None:
            raise PersistenceOperationFailed("Learning Unit of Work is not active")
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


class SqlAlchemyLearningUnitOfWorkFactory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self, execution_tenant_id: UUID) -> SqlAlchemyLearningUnitOfWork:
        return SqlAlchemyLearningUnitOfWork(self._engine, execution_tenant_id)
