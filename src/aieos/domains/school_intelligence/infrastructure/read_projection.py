"""Read-only SQL adapter for Principal School Intelligence derived facts.

Queries existing authoritative schemas. Never mutates. Never commits.
Filters to the current authorized ClassRef set before aggregation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from aieos.domains.school_intelligence.application.errors import (
    SchoolIntelligenceReadUnavailable,
)
from aieos.domains.school_intelligence.application.models import (
    AssignmentLifecycleCounts,
    AuthorizedClassFacts,
    SchoolIntelligenceFactsSnapshot,
)

_UNAVAILABLE = "School Intelligence source is temporarily unavailable"

_ASSIGNMENT_SQL = text(
    """
    SELECT
        class_ref,
        COUNT(*) AS teaching_assignment_count,
        COUNT(*) FILTER (WHERE lifecycle_state = 'ACTIVE') AS active_count,
        COUNT(*) FILTER (WHERE lifecycle_state = 'CLOSED') AS closed_count,
        COUNT(*) FILTER (WHERE lifecycle_state = 'CANCELLED') AS cancelled_count
    FROM teaching.assignments
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND class_ref IN :class_refs
      AND assigned_at <= :generated_at
    GROUP BY class_ref
    """
).bindparams(bindparam("class_refs", expanding=True))

_SUBMISSION_SQL = text(
    """
    SELECT
        submissions.class_ref,
        COUNT(*) AS learner_submission_count
    FROM learning.submissions AS submissions
    INNER JOIN teaching.assignments AS assignments
      ON assignments.tenant_id = submissions.tenant_id
     AND assignments.assignment_id = submissions.teaching_assignment_id
     AND assignments.class_ref = submissions.class_ref
    WHERE submissions.tenant_id = CAST(:tenant_id AS uuid)
      AND submissions.class_ref IN :class_refs
      AND assignments.class_ref IN :class_refs
      AND submissions.submitted_at <= :generated_at
      AND assignments.assigned_at <= :generated_at
    GROUP BY submissions.class_ref
    """
).bindparams(bindparam("class_refs", expanding=True))

_EVALUATION_SQL = text(
    """
    SELECT
        submissions.class_ref,
        COUNT(evaluations.evaluation_id) AS current_policy_evaluation_count
    FROM learning.submissions AS submissions
    INNER JOIN teaching.assignments AS assignments
      ON assignments.tenant_id = submissions.tenant_id
     AND assignments.assignment_id = submissions.teaching_assignment_id
     AND assignments.class_ref = submissions.class_ref
    LEFT JOIN assessment.learner_assessment_evaluations AS evaluations
      ON evaluations.tenant_id = submissions.tenant_id
     AND evaluations.submission_id = submissions.submission_id
     AND evaluations.teaching_assignment_id = submissions.teaching_assignment_id
     AND evaluations.class_ref = submissions.class_ref
     AND evaluations.evaluation_policy_id = :policy_id
     AND evaluations.evaluation_policy_version = :policy_version
     AND evaluations.evaluated_at <= :generated_at
    WHERE submissions.tenant_id = CAST(:tenant_id AS uuid)
      AND submissions.class_ref IN :class_refs
      AND assignments.class_ref IN :class_refs
      AND submissions.submitted_at <= :generated_at
      AND assignments.assigned_at <= :generated_at
    GROUP BY submissions.class_ref
    """
).bindparams(bindparam("class_refs", expanding=True))

_CLASSROOM_SQL = text(
    """
    SELECT
        class_ref,
        COUNT(*) > 0 AS has_recorded,
        COUNT(DISTINCT assignment_id) FILTER (
            WHERE assignment_id IS NOT NULL
        ) AS assignments_with_recorded_count
    FROM assessment.classroom_assessments
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND class_ref IN :class_refs
      AND lifecycle_state = 'RECORDED'
      AND recorded_at <= :generated_at
    GROUP BY class_ref
    """
).bindparams(bindparam("class_refs", expanding=True))

_EXECUTION_SQL = text(
    """
    SELECT
        class_ref,
        COUNT(*) AS completed_teaching_execution_count
    FROM teaching.executions
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND class_ref IN :class_refs
      AND lifecycle_state = 'COMPLETED'
      AND completed_at <= :generated_at
    GROUP BY class_ref
    """
).bindparams(bindparam("class_refs", expanding=True))

_REMEDIATION_SQL = text(
    """
    SELECT
        source_class_ref AS class_ref,
        COUNT(*) AS remediation_activity_count
    FROM teaching.work_remediation_origins
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND source_class_ref IN :class_refs
      AND created_at <= :generated_at
    GROUP BY source_class_ref
    """
).bindparams(bindparam("class_refs", expanding=True))


class SqlAlchemySchoolIntelligenceFactsReader:
    """Short-lived read-only PostgreSQL adapter. Rollback/close only."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def read_authorized_class_facts(
        self,
        *,
        tenant_id: UUID,
        authorized_class_refs: Sequence[str],
        evaluation_policy_id: str,
        evaluation_policy_version: int,
    ) -> SchoolIntelligenceFactsSnapshot:
        class_refs = list(authorized_class_refs)
        connection: Connection | None = None
        transaction = None
        generated_at: datetime | None = None
        assignments: dict[str, Any] = {}
        submissions: dict[str, Any] = {}
        evaluations: dict[str, Any] = {}
        classroom: dict[str, Any] = {}
        executions: dict[str, Any] = {}
        remediation: dict[str, Any] = {}
        try:
            connection = self._engine.connect()
            transaction = connection.begin()
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            )
            connection.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            generated_at = connection.execute(
                text("SELECT transaction_timestamp()")
            ).scalar_one()
            if not isinstance(generated_at, datetime):
                raise SchoolIntelligenceReadUnavailable(_UNAVAILABLE)
            if class_refs:
                params: dict[str, Any] = {
                    "tenant_id": tenant_id,
                    "class_refs": class_refs,
                    "generated_at": generated_at,
                    "policy_id": evaluation_policy_id,
                    "policy_version": evaluation_policy_version,
                }
                assignments = self._read_assignment_facts(connection, params)
                submissions = self._read_submission_facts(connection, params)
                evaluations = self._read_evaluation_facts(connection, params)
                classroom = self._read_classroom_facts(connection, params)
                executions = self._read_execution_facts(connection, params)
                remediation = self._read_remediation_facts(connection, params)
        except SchoolIntelligenceReadUnavailable:
            raise
        except Exception as exc:
            raise SchoolIntelligenceReadUnavailable(_UNAVAILABLE) from exc
        finally:
            if transaction is not None and transaction.is_active:
                transaction.rollback()
            if connection is not None:
                connection.close()

        if generated_at is None:
            raise SchoolIntelligenceReadUnavailable(_UNAVAILABLE)
        classes = tuple(
            _merge_class(
                class_ref,
                assignments=assignments,
                submissions=submissions,
                evaluations=evaluations,
                classroom=classroom,
                executions=executions,
                remediation=remediation,
            )
            for class_ref in class_refs
        )
        return SchoolIntelligenceFactsSnapshot(
            generated_at=generated_at,
            classes=classes,
        )

    def _read_assignment_facts(
        self, connection: Connection, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._rows(_ASSIGNMENT_SQL, connection, params)

    def _read_submission_facts(
        self, connection: Connection, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._rows(_SUBMISSION_SQL, connection, params)

    def _read_evaluation_facts(
        self, connection: Connection, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._rows(_EVALUATION_SQL, connection, params)

    def _read_classroom_facts(
        self, connection: Connection, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._rows(_CLASSROOM_SQL, connection, params)

    def _read_execution_facts(
        self, connection: Connection, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._rows(_EXECUTION_SQL, connection, params)

    def _read_remediation_facts(
        self, connection: Connection, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._rows(_REMEDIATION_SQL, connection, params)

    def _rows(
        self,
        statement,
        connection: Connection,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            result = connection.execute(statement, params)
            return {str(row.class_ref): row for row in result}
        except SchoolIntelligenceReadUnavailable:
            raise
        except Exception as exc:
            raise SchoolIntelligenceReadUnavailable(_UNAVAILABLE) from exc


def _merge_class(
    class_ref: str,
    *,
    assignments: Mapping[str, Any],
    submissions: Mapping[str, Any],
    evaluations: Mapping[str, Any],
    classroom: Mapping[str, Any],
    executions: Mapping[str, Any],
    remediation: Mapping[str, Any],
) -> AuthorizedClassFacts:
    assignment_row = assignments.get(class_ref)
    submission_row = submissions.get(class_ref)
    evaluation_row = evaluations.get(class_ref)
    classroom_row = classroom.get(class_ref)
    execution_row = executions.get(class_ref)
    remediation_row = remediation.get(class_ref)
    teaching_assignment_count = (
        int(assignment_row.teaching_assignment_count) if assignment_row else 0
    )
    active = int(assignment_row.active_count) if assignment_row else 0
    closed = int(assignment_row.closed_count) if assignment_row else 0
    cancelled = int(assignment_row.cancelled_count) if assignment_row else 0
    has_recorded = bool(classroom_row.has_recorded) if classroom_row else False
    recorded_assignment_count = (
        int(classroom_row.assignments_with_recorded_count) if classroom_row else 0
    )
    return AuthorizedClassFacts(
        class_ref=class_ref,
        teaching_assignment_count=teaching_assignment_count,
        assignment_lifecycle=AssignmentLifecycleCounts(
            active=active,
            closed=closed,
            cancelled=cancelled,
        ),
        learner_submission_count=(
            int(submission_row.learner_submission_count) if submission_row else 0
        ),
        current_policy_evaluation_count=(
            int(evaluation_row.current_policy_evaluation_count)
            if evaluation_row
            else 0
        ),
        has_recorded_classroom_assessment=has_recorded,
        assignments_with_recorded_classroom_assessment_count=recorded_assignment_count,
        completed_teaching_execution_count=(
            int(execution_row.completed_teaching_execution_count)
            if execution_row
            else 0
        ),
        remediation_activity_count=(
            int(remediation_row.remediation_activity_count)
            if remediation_row
            else 0
        ),
    )
