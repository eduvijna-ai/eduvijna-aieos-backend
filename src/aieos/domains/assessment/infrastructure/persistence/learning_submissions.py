"""Read-only Learning submission adapter for Assessment evaluation composition.

Does not mutate Learning. Does not manufacture AttemptResponseItem rows.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Connection

from aieos.domains.assessment.application.evaluation_views import (
    EvaluationSubmissionView,
)
from aieos.domains.assessment.application.errors import PersistenceOperationFailed
from aieos.domains.assessment.domain.evaluation_input import EvaluationSubmittedResponse
from aieos.domains.learning.domain.identities import SubmissionId
from aieos.domains.learning.infrastructure.persistence.models import submissions_table
from aieos.domains.learning.infrastructure.persistence.repositories import (
    SqlAlchemyLearnerSubmissionRepository,
    learner_submission_from_row,
)


def _to_view(submission) -> EvaluationSubmissionView:
    return EvaluationSubmissionView(
        submission_id=submission.submission_id.value,
        tenant_id=submission.tenant_id,
        attempt_id=submission.attempt_id.value,
        learner_principal_id=submission.learner_principal_id,
        teaching_assignment_id=submission.teaching_assignment_id,
        content_id=submission.content_id,
        content_version_id=submission.content_version_id,
        class_ref=submission.class_ref,
        responses=tuple(
            EvaluationSubmittedResponse(
                question_id=item.question_id,
                response_kind=str(item.response_kind),
                value=item.value,
            )
            for item in submission.response_snapshot
        ),
        submitted_at=submission.submitted_at,
    )


class SqlAlchemyAssessmentLearnerSubmissionAdapter:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id
        self._submissions = SqlAlchemyLearnerSubmissionRepository(
            connection, execution_tenant_id
        )

    def get(self, submission_id: UUID) -> EvaluationSubmissionView | None:
        loaded = self._submissions.get(SubmissionId(submission_id))
        if loaded is None:
            return None
        return _to_view(loaded)

    def list_for_teaching_assignment(
        self, teaching_assignment_id: UUID
    ) -> tuple[EvaluationSubmissionView, ...]:
        try:
            rows = (
                self._connection.execute(
                    select(submissions_table)
                    .where(
                        submissions_table.c.tenant_id == self._execution_tenant_id,
                        submissions_table.c.teaching_assignment_id
                        == teaching_assignment_id,
                    )
                    .order_by(
                        submissions_table.c.submitted_at,
                        submissions_table.c.submission_id,
                    )
                )
                .mappings()
                .all()
            )
        except Exception as exc:
            raise PersistenceOperationFailed(
                "failed to list LearnerSubmissions for TeachingAssignment"
            ) from exc
        return tuple(_to_view(learner_submission_from_row(row)) for row in rows)
