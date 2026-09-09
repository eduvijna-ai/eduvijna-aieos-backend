"""SQLAlchemy Core Assessment repositories. They never commit or rollback."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from aieos.domains.assessment.application.errors import (
    InvalidClassroomAssessmentRequest,
    PersistenceInvariantViolation,
)
from aieos.domains.assessment.domain.classroom_assessment import ClassroomAssessment
from aieos.domains.assessment.domain.evaluation import (
    LearnerAssessmentEvaluation,
    LearnerAssessmentEvaluationItem,
    LearnerAssessmentObjectiveEvidence,
)
from aieos.domains.assessment.domain.identities import (
    AggregateRevision,
    AssessmentId,
    EvaluationId,
)
from aieos.domains.assessment.domain.lifecycle import (
    AssessmentLifecycleState,
    parse_assessment_lifecycle_state,
)
from aieos.domains.assessment.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.assessment.infrastructure.persistence.models import (
    classroom_assessments_table,
    learner_assessment_evaluation_items_table,
    learner_assessment_evaluations_table,
    learner_assessment_objective_evidence_table,
)

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100


def classroom_assessment_from_row(row) -> ClassroomAssessment:
    try:
        return ClassroomAssessment(
            assessment_id=AssessmentId(row["assessment_id"]),
            tenant_id=row["tenant_id"],
            teacher_principal_id=row["teacher_principal_id"],
            class_ref=row["class_ref"],
            content_id=row["content_id"],
            content_version_id=row["content_version_id"],
            class_result_level=row["class_result_level"],
            class_result_note=row["class_result_note"],
            lifecycle_state=row["lifecycle_state"],
            work_id=row["work_id"],
            execution_id=row["execution_id"],
            assignment_id=row["assignment_id"],
            aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),
            recorded_at=row["recorded_at"],
            voided_at=row["voided_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored ClassroomAssessment row violates the aggregate contract"
        ) from exc


class SqlAlchemyClassroomAssessmentRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, assessment: ClassroomAssessment) -> None:
        try:
            self._connection.execute(
                classroom_assessments_table.insert().values(
                    assessment_id=assessment.assessment_id.value,
                    tenant_id=assessment.tenant_id,
                    teacher_principal_id=assessment.teacher_principal_id,
                    class_ref=assessment.class_ref,
                    content_id=assessment.content_id,
                    content_version_id=assessment.content_version_id,
                    class_result_level=assessment.class_result_level.value,
                    class_result_note=assessment.class_result_note,
                    lifecycle_state=assessment.lifecycle_state.value,
                    work_id=assessment.work_id,
                    execution_id=assessment.execution_id,
                    assignment_id=assessment.assignment_id,
                    aggregate_revision=int(assessment.aggregate_revision),
                    recorded_at=assessment.recorded_at,
                    voided_at=assessment.voided_at,
                    created_at=assessment.created_at,
                    updated_at=assessment.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(self, assessment_id: AssessmentId) -> ClassroomAssessment | None:
        try:
            row = (
                self._connection.execute(
                    select(classroom_assessments_table).where(
                        classroom_assessments_table.c.assessment_id
                        == assessment_id.value,
                        classroom_assessments_table.c.tenant_id
                        == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return classroom_assessment_from_row(row)

    def get_for_update(
        self, assessment_id: AssessmentId
    ) -> ClassroomAssessment | None:
        try:
            row = (
                self._connection.execute(
                    select(classroom_assessments_table)
                    .where(
                        classroom_assessments_table.c.assessment_id
                        == assessment_id.value,
                        classroom_assessments_table.c.tenant_id
                        == self._execution_tenant_id,
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
        return classroom_assessment_from_row(row)

    def update(
        self,
        assessment: ClassroomAssessment,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        """Compare-and-set mutable assessment state. False means a lost race.

        Immutable RECORD fields are never rewritten.
        """
        try:
            result = self._connection.execute(
                update(classroom_assessments_table)
                .where(
                    classroom_assessments_table.c.assessment_id
                    == assessment.assessment_id.value,
                    classroom_assessments_table.c.tenant_id
                    == self._execution_tenant_id,
                    classroom_assessments_table.c.aggregate_revision
                    == int(expected_revision),
                )
                .values(
                    class_result_level=assessment.class_result_level.value,
                    class_result_note=assessment.class_result_note,
                    lifecycle_state=assessment.lifecycle_state.value,
                    voided_at=assessment.voided_at,
                    aggregate_revision=int(assessment.aggregate_revision),
                    updated_at=assessment.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1

    def list_for_teacher(
        self,
        teacher_principal_id: UUID,
        *,
        class_ref: str | None = None,
        work_id: UUID | None = None,
        execution_id: UUID | None = None,
        assignment_id: UUID | None = None,
        lifecycle_state: AssessmentLifecycleState | str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[ClassroomAssessment]:
        if not isinstance(limit, int) or limit < 1:
            raise InvalidClassroomAssessmentRequest("limit must be a positive integer")
        if limit > MAX_LIST_LIMIT:
            raise InvalidClassroomAssessmentRequest(
                f"limit must be at most {MAX_LIST_LIMIT}"
            )
        clauses = [
            classroom_assessments_table.c.tenant_id == self._execution_tenant_id,
            classroom_assessments_table.c.teacher_principal_id
            == teacher_principal_id,
        ]
        if class_ref is not None:
            clauses.append(classroom_assessments_table.c.class_ref == class_ref.strip())
        if work_id is not None:
            clauses.append(classroom_assessments_table.c.work_id == work_id)
        if execution_id is not None:
            clauses.append(classroom_assessments_table.c.execution_id == execution_id)
        if assignment_id is not None:
            clauses.append(
                classroom_assessments_table.c.assignment_id == assignment_id
            )
        if lifecycle_state is not None:
            state = parse_assessment_lifecycle_state(lifecycle_state)
            clauses.append(
                classroom_assessments_table.c.lifecycle_state == state.value
            )
        try:
            rows = (
                self._connection.execute(
                    select(classroom_assessments_table)
                    .where(*clauses)
                    .order_by(
                        classroom_assessments_table.c.updated_at.desc(),
                        classroom_assessments_table.c.assessment_id.desc(),
                    )
                    .limit(limit)
                )
                .mappings()
                .all()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return [classroom_assessment_from_row(row) for row in rows]


def learner_assessment_evaluation_from_rows(
    parent,
    item_rows,
    evidence_rows,
) -> LearnerAssessmentEvaluation:
    try:
        items = tuple(
            LearnerAssessmentEvaluationItem(
                question_id=row["question_id"],
                question_type=row["question_type"],
                outcome=row["outcome"],
                evaluation_method=row["evaluation_method"],
                objective_ids=tuple(row["objective_ids"] or ()),
                response_kind=row["response_kind"],
            )
            for row in item_rows
        )
        evidence = tuple(
            LearnerAssessmentObjectiveEvidence(
                objective_id=row["objective_id"],
                result=row["result"],
            )
            for row in evidence_rows
        )
        return LearnerAssessmentEvaluation(
            evaluation_id=EvaluationId(parent["evaluation_id"]),
            tenant_id=parent["tenant_id"],
            learner_principal_id=parent["learner_principal_id"],
            submission_id=parent["submission_id"],
            attempt_id=parent["attempt_id"],
            teaching_assignment_id=parent["teaching_assignment_id"],
            content_id=parent["content_id"],
            content_version_id=parent["content_version_id"],
            class_ref=parent["class_ref"],
            evaluation_policy_id=parent["evaluation_policy_id"],
            evaluation_policy_version=int(parent["evaluation_policy_version"]),
            evaluated_at=parent["evaluated_at"],
            created_at=parent["created_at"],
            items=items,
            objective_evidence=evidence,
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored LearnerAssessmentEvaluation row violates the aggregate contract"
        ) from exc


class SqlAlchemyLearnerAssessmentEvaluationRepository:
    """Insert/read only. No update path for issued evaluation truth."""

    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(
        self, evaluation: LearnerAssessmentEvaluation
    ) -> LearnerAssessmentEvaluation:
        try:
            inserted = self._insert_parent(evaluation)
            if inserted is None:
                existing = self.get_by_business_identity(
                    submission_id=evaluation.submission_id,
                    evaluation_policy_id=evaluation.evaluation_policy_id,
                    evaluation_policy_version=evaluation.evaluation_policy_version,
                )
                if existing is None:
                    raise PersistenceInvariantViolation(
                        "evaluation business identity conflict without durable row"
                    )
                return existing
            self._insert_children(evaluation)
            return evaluation
        except Exception as exc:
            reraise_as_application_error(exc)

    def get(
        self, evaluation_id: EvaluationId
    ) -> LearnerAssessmentEvaluation | None:
        try:
            row = (
                self._connection.execute(
                    select(learner_assessment_evaluations_table).where(
                        learner_assessment_evaluations_table.c.evaluation_id
                        == evaluation_id.value,
                        learner_assessment_evaluations_table.c.tenant_id
                        == self._execution_tenant_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return self._hydrate(row)

    def get_by_business_identity(
        self,
        *,
        submission_id: UUID,
        evaluation_policy_id: str,
        evaluation_policy_version: int,
    ) -> LearnerAssessmentEvaluation | None:
        try:
            row = (
                self._connection.execute(
                    select(learner_assessment_evaluations_table).where(
                        learner_assessment_evaluations_table.c.tenant_id
                        == self._execution_tenant_id,
                        learner_assessment_evaluations_table.c.submission_id
                        == submission_id,
                        learner_assessment_evaluations_table.c.evaluation_policy_id
                        == evaluation_policy_id,
                        learner_assessment_evaluations_table.c.evaluation_policy_version
                        == evaluation_policy_version,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return self._hydrate(row)

    def list_for_teaching_assignment(
        self, teaching_assignment_id: UUID
    ) -> tuple[LearnerAssessmentEvaluation, ...]:
        try:
            rows = (
                self._connection.execute(
                    select(learner_assessment_evaluations_table)
                    .where(
                        learner_assessment_evaluations_table.c.tenant_id
                        == self._execution_tenant_id,
                        learner_assessment_evaluations_table.c.teaching_assignment_id
                        == teaching_assignment_id,
                    )
                    .order_by(
                        learner_assessment_evaluations_table.c.evaluated_at,
                        learner_assessment_evaluations_table.c.evaluation_id,
                    )
                )
                .mappings()
                .all()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return tuple(self._hydrate(row) for row in rows)

    def _insert_parent(
        self, evaluation: LearnerAssessmentEvaluation
    ) -> EvaluationId | None:
        stmt = (
            pg_insert(learner_assessment_evaluations_table)
            .values(
                evaluation_id=evaluation.evaluation_id.value,
                tenant_id=evaluation.tenant_id,
                learner_principal_id=evaluation.learner_principal_id,
                submission_id=evaluation.submission_id,
                attempt_id=evaluation.attempt_id,
                teaching_assignment_id=evaluation.teaching_assignment_id,
                content_id=evaluation.content_id,
                content_version_id=evaluation.content_version_id,
                class_ref=evaluation.class_ref,
                evaluation_policy_id=evaluation.evaluation_policy_id,
                evaluation_policy_version=evaluation.evaluation_policy_version,
                evaluated_at=evaluation.evaluated_at,
                created_at=evaluation.created_at,
            )
            .on_conflict_do_nothing(
                constraint=(
                    "uq_assessment_learner_assessment_evaluations_business_identity"
                )
            )
            .returning(learner_assessment_evaluations_table.c.evaluation_id)
        )
        row = self._connection.execute(stmt).one_or_none()
        if row is None:
            return None
        return EvaluationId(row[0])

    def _insert_children(self, evaluation: LearnerAssessmentEvaluation) -> None:
        if evaluation.items:
            self._connection.execute(
                learner_assessment_evaluation_items_table.insert(),
                [
                    {
                        "tenant_id": evaluation.tenant_id,
                        "evaluation_id": evaluation.evaluation_id.value,
                        "question_id": item.question_id,
                        "item_ordinal": ordinal,
                        "question_type": item.question_type,
                        "outcome": item.outcome.value,
                        "evaluation_method": item.evaluation_method.value,
                        "objective_ids": list(item.objective_ids),
                        "response_kind": item.response_kind,
                    }
                    for ordinal, item in enumerate(evaluation.items)
                ],
            )
        if evaluation.objective_evidence:
            self._connection.execute(
                learner_assessment_objective_evidence_table.insert(),
                [
                    {
                        "tenant_id": evaluation.tenant_id,
                        "evaluation_id": evaluation.evaluation_id.value,
                        "objective_id": row.objective_id,
                        "result": row.result.value,
                    }
                    for row in evaluation.objective_evidence
                ],
            )

    def _hydrate(self, parent) -> LearnerAssessmentEvaluation:
        item_rows = (
            self._connection.execute(
                select(learner_assessment_evaluation_items_table)
                .where(
                    learner_assessment_evaluation_items_table.c.tenant_id
                    == self._execution_tenant_id,
                    learner_assessment_evaluation_items_table.c.evaluation_id
                    == parent["evaluation_id"],
                )
                .order_by(learner_assessment_evaluation_items_table.c.item_ordinal)
            )
            .mappings()
            .all()
        )
        evidence_rows = (
            self._connection.execute(
                select(learner_assessment_objective_evidence_table)
                .where(
                    learner_assessment_objective_evidence_table.c.tenant_id
                    == self._execution_tenant_id,
                    learner_assessment_objective_evidence_table.c.evaluation_id
                    == parent["evaluation_id"],
                )
                .order_by(
                    learner_assessment_objective_evidence_table.c.objective_id
                )
            )
            .mappings()
            .all()
        )
        return learner_assessment_evaluation_from_rows(parent, item_rows, evidence_rows)

