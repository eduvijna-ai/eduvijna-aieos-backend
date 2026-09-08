"""SQLAlchemy Core Learning repositories. They never commit or rollback.

Repositories accept a SQLAlchemy Connection so S01-I03 can compose Teaching
authority and Learning writes on one tenant-scoped transaction. Learning UoW
does not import Teaching UoW. TeachingAssignment lock/order is not implemented
in I02.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Connection

from aieos.domains.learning.application.errors import (
    AttemptAlreadySubmitted,
    AttemptConcurrencyConflict,
    InvalidResponse,
    PersistenceInvariantViolation,
    SubmissionImmutable,
)
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.errors import (
    AttemptAlreadySubmittedError,
    InvalidAttemptResponseError,
    InvalidLearnerAttemptError,
    InvalidLearnerSubmissionError,
)
from aieos.domains.learning.domain.identities import (
    AggregateRevision,
    AttemptId,
    SubmissionId,
)
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from aieos.domains.learning.domain.submission import LearnerSubmission
from aieos.domains.learning.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.learning.infrastructure.persistence.models import (
    attempt_response_items_table,
    attempts_table,
    submissions_table,
)


def learner_attempt_from_row(row) -> LearnerAttempt:
    try:
        submission_id = row["submission_id"]
        return LearnerAttempt(
            attempt_id=AttemptId(row["attempt_id"]),
            tenant_id=row["tenant_id"],
            learner_principal_id=row["learner_principal_id"],
            teaching_assignment_id=row["teaching_assignment_id"],
            content_id=row["content_id"],
            content_version_id=row["content_version_id"],
            class_ref=row["class_ref"],
            attempt_number=int(row["attempt_number"]),
            lifecycle_state=row["lifecycle_state"],
            started_at=row["started_at"],
            last_saved_at=row["last_saved_at"],
            submitted_at=row["submitted_at"],
            submission_id=None
            if submission_id is None
            else SubmissionId(submission_id),
            aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    except (InvalidLearnerAttemptError, ValueError, TypeError) as exc:
        raise PersistenceInvariantViolation(
            "stored LearnerAttempt row violates the aggregate contract"
        ) from exc


def attempt_response_item_from_row(row) -> AttemptResponseItem:
    try:
        return AttemptResponseItem(
            attempt_id=AttemptId(row["attempt_id"]),
            question_id=row["question_id"],
            response_kind=row["response_kind"],
            choice_value=row["choice_value"],
            text_value=row["text_value"],
            boolean_value=row["boolean_value"],
        )
    except InvalidAttemptResponseError as exc:
        raise InvalidResponse(
            "stored AttemptResponseItem row violates the typed contract"
        ) from exc


def learner_submission_from_row(row) -> LearnerSubmission:
    try:
        return LearnerSubmission(
            submission_id=SubmissionId(row["submission_id"]),
            tenant_id=row["tenant_id"],
            attempt_id=AttemptId(row["attempt_id"]),
            learner_principal_id=row["learner_principal_id"],
            teaching_assignment_id=row["teaching_assignment_id"],
            content_id=row["content_id"],
            content_version_id=row["content_version_id"],
            class_ref=row["class_ref"],
            response_snapshot=list(row["response_snapshot"] or []),
            submitted_at=row["submitted_at"],
            assignment_revision_at_submit=int(row["assignment_revision_at_submit"]),
            due_at_at_submit=row["due_at_at_submit"],
            created_at=row["created_at"],
        )
    except (InvalidLearnerSubmissionError, ValueError, TypeError) as exc:
        raise PersistenceInvariantViolation(
            "stored LearnerSubmission row violates the evidence contract"
        ) from exc


class SqlAlchemyLearnerAttemptRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, attempt: LearnerAttempt) -> None:
        try:
            self._connection.execute(
                attempts_table.insert().values(
                    attempt_id=attempt.attempt_id.value,
                    tenant_id=attempt.tenant_id,
                    learner_principal_id=attempt.learner_principal_id,
                    teaching_assignment_id=attempt.teaching_assignment_id,
                    content_id=attempt.content_id,
                    content_version_id=attempt.content_version_id,
                    class_ref=attempt.class_ref,
                    attempt_number=attempt.attempt_number,
                    lifecycle_state=attempt.lifecycle_state.value,
                    started_at=attempt.started_at,
                    last_saved_at=attempt.last_saved_at,
                    submitted_at=attempt.submitted_at,
                    submission_id=None
                    if attempt.submission_id is None
                    else attempt.submission_id.value,
                    aggregate_revision=int(attempt.aggregate_revision),
                    created_at=attempt.created_at,
                    updated_at=attempt.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(self, attempt_id: AttemptId) -> LearnerAttempt | None:
        try:
            row = (
                self._connection.execute(
                    select(attempts_table).where(
                        attempts_table.c.attempt_id == attempt_id.value,
                        attempts_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return learner_attempt_from_row(row)

    def get_for_update(self, attempt_id: AttemptId) -> LearnerAttempt | None:
        try:
            row = (
                self._connection.execute(
                    select(attempts_table)
                    .where(
                        attempts_table.c.attempt_id == attempt_id.value,
                        attempts_table.c.tenant_id == self._execution_tenant_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return learner_attempt_from_row(row)

    def update(
        self,
        attempt: LearnerAttempt,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        """Compare-and-set LearnerAttempt mutation. False means a lost race."""
        if attempt.lifecycle_state is AttemptLifecycleState.SUBMITTED:
            values = {
                "lifecycle_state": attempt.lifecycle_state.value,
                "last_saved_at": attempt.last_saved_at,
                "submitted_at": attempt.submitted_at,
                "submission_id": None
                if attempt.submission_id is None
                else attempt.submission_id.value,
                "aggregate_revision": int(attempt.aggregate_revision),
                "updated_at": attempt.updated_at,
            }
        else:
            values = {
                "lifecycle_state": attempt.lifecycle_state.value,
                "last_saved_at": attempt.last_saved_at,
                "submitted_at": None,
                "submission_id": None,
                "aggregate_revision": int(attempt.aggregate_revision),
                "updated_at": attempt.updated_at,
            }
        try:
            result = self._connection.execute(
                update(attempts_table)
                .where(
                    attempts_table.c.attempt_id == attempt.attempt_id.value,
                    attempts_table.c.tenant_id == self._execution_tenant_id,
                    attempts_table.c.aggregate_revision == int(expected_revision),
                    attempts_table.c.lifecycle_state
                    == AttemptLifecycleState.IN_PROGRESS.value,
                )
                .values(**values)
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1


class SqlAlchemyAttemptResponseItemRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def list_for_attempt(self, attempt_id: AttemptId) -> list[AttemptResponseItem]:
        try:
            rows = (
                self._connection.execute(
                    select(attempt_response_items_table)
                    .where(
                        attempt_response_items_table.c.attempt_id == attempt_id.value,
                        attempt_response_items_table.c.tenant_id
                        == self._execution_tenant_id,
                    )
                    .order_by(attempt_response_items_table.c.question_id)
                )
                .mappings()
                .all()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return [attempt_response_item_from_row(row) for row in rows]

    def replace_for_attempt(
        self,
        attempt: LearnerAttempt,
        items: Sequence[AttemptResponseItem],
        *,
        saved_at: datetime,
    ) -> None:
        if attempt.lifecycle_state is not AttemptLifecycleState.IN_PROGRESS:
            raise AttemptAlreadySubmitted(
                "SUBMITTED LearnerAttempt cannot mutate response working state"
            )
        for item in items:
            if item.attempt_id != attempt.attempt_id:
                raise InvalidResponse(
                    "response item attempt_id must match the parent LearnerAttempt"
                )
        try:
            self._connection.execute(
                delete(attempt_response_items_table).where(
                    attempt_response_items_table.c.attempt_id
                    == attempt.attempt_id.value,
                    attempt_response_items_table.c.tenant_id
                    == self._execution_tenant_id,
                )
            )
            for item in items:
                self._connection.execute(
                    attempt_response_items_table.insert().values(
                        tenant_id=self._execution_tenant_id,
                        attempt_id=item.attempt_id.value,
                        question_id=item.question_id,
                        response_kind=item.response_kind.value,
                        choice_value=item.choice_value,
                        text_value=item.text_value,
                        boolean_value=item.boolean_value,
                        created_at=saved_at,
                        updated_at=saved_at,
                    )
                )
        except AttemptAlreadySubmittedError as exc:
            raise AttemptAlreadySubmitted(str(exc)) from exc
        except Exception as exc:
            reraise_as_application_error(exc)


class SqlAlchemyLearnerSubmissionRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, submission: LearnerSubmission) -> None:
        snapshot = [
            item.as_persistable_mapping() for item in submission.response_snapshot
        ]
        try:
            self._connection.execute(
                submissions_table.insert().values(
                    submission_id=submission.submission_id.value,
                    tenant_id=submission.tenant_id,
                    attempt_id=submission.attempt_id.value,
                    learner_principal_id=submission.learner_principal_id,
                    teaching_assignment_id=submission.teaching_assignment_id,
                    content_id=submission.content_id,
                    content_version_id=submission.content_version_id,
                    class_ref=submission.class_ref,
                    response_snapshot=snapshot,
                    submitted_at=submission.submitted_at,
                    assignment_revision_at_submit=(
                        submission.assignment_revision_at_submit
                    ),
                    due_at_at_submit=submission.due_at_at_submit,
                    created_at=submission.created_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(self, submission_id: SubmissionId) -> LearnerSubmission | None:
        try:
            row = (
                self._connection.execute(
                    select(submissions_table).where(
                        submissions_table.c.submission_id == submission_id.value,
                        submissions_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return learner_submission_from_row(row)

    def get_for_attempt(self, attempt_id: AttemptId) -> LearnerSubmission | None:
        try:
            row = (
                self._connection.execute(
                    select(submissions_table).where(
                        submissions_table.c.attempt_id == attempt_id.value,
                        submissions_table.c.tenant_id == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return learner_submission_from_row(row)

    def update(self, *_args, **_kwargs) -> None:
        raise SubmissionImmutable("learning.submissions has no UPDATE path")

    def delete(self, *_args, **_kwargs) -> None:
        raise SubmissionImmutable("learning.submissions has no DELETE path")
