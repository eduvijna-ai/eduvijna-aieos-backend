"""AIEOS360-S01-I05-B1 — concurrent same-identity evaluation insert."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.assessment.domain.evaluation_input import (
    EvaluationContentQuestion,
    EvaluationSubmittedResponse,
    LearnerAssessmentEvaluationRequest,
)
from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DeterministicLearnerAssessmentEvaluatorV1,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from tests.dbutil import set_tenant

pytestmark = pytest.mark.aieos360_s01_i05_b1

FIXED_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
EVALUATOR = DeterministicLearnerAssessmentEvaluatorV1()
EVALUATION_TABLES = (
    "learner_assessment_objective_evidence",
    "learner_assessment_evaluation_items",
    "learner_assessment_evaluations",
)


@pytest.fixture(autouse=True)
def _clear_evaluation_rows_after_test(bootstrap_engine: Engine) -> None:
    yield
    with bootstrap_engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'assessment'
                      AND table_name = 'learner_assessment_evaluations'
                )
                """
            )
        ).scalar()
        if not exists:
            return
        for table, trigger in (
            (
                "learner_assessment_objective_evidence",
                "learner_assessment_objective_evidence_immutable_delete",
            ),
            (
                "learner_assessment_evaluation_items",
                "learner_assessment_evaluation_items_immutable_delete",
            ),
            (
                "learner_assessment_evaluations",
                "learner_assessment_evaluations_immutable_delete",
            ),
        ):
            conn.execute(
                text(f"ALTER TABLE assessment.{table} DISABLE TRIGGER {trigger}")
            )
            conn.execute(
                text(f"ALTER TABLE assessment.{table} DISABLE ROW LEVEL SECURITY")
            )
        conn.execute(text("DELETE FROM assessment.learner_assessment_objective_evidence"))
        conn.execute(text("DELETE FROM assessment.learner_assessment_evaluation_items"))
        conn.execute(text("DELETE FROM assessment.learner_assessment_evaluations"))
        for table, trigger in (
            (
                "learner_assessment_objective_evidence",
                "learner_assessment_objective_evidence_immutable_delete",
            ),
            (
                "learner_assessment_evaluation_items",
                "learner_assessment_evaluation_items_immutable_delete",
            ),
            (
                "learner_assessment_evaluations",
                "learner_assessment_evaluations_immutable_delete",
            ),
        ):
            conn.execute(
                text(f"ALTER TABLE assessment.{table} ENABLE TRIGGER {trigger}")
            )
            conn.execute(
                text(f"ALTER TABLE assessment.{table} ENABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text(f"ALTER TABLE assessment.{table} FORCE ROW LEVEL SECURITY")
            )


def _evaluation(*, tenant_id, submission_id):
    request = LearnerAssessmentEvaluationRequest(
        tenant_id=tenant_id,
        learner_principal_id=uuid.uuid7(),
        submission_id=submission_id,
        attempt_id=uuid.uuid7(),
        teaching_assignment_id=uuid.uuid7(),
        content_id=uuid.uuid7(),
        content_version_id=uuid.uuid7(),
        class_ref="class-5a",
        questions=(
            EvaluationContentQuestion(
                question_id="q1",
                question_type="multiple_choice",
                options=("A", "B", "C"),
                answer="A",
                objective_ids=("obj-1",),
            ),
        ),
        responses=(
            EvaluationSubmittedResponse(
                question_id="q1",
                response_kind="MULTIPLE_CHOICE",
                value="A",
            ),
        ),
        evaluated_at=FIXED_NOW,
    )
    return EVALUATOR.evaluate(request)


class TestConcurrentSameBusinessIdentity:
    def test_35_concurrent_insert_one_durable_outcome(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        submission_id = uuid.uuid7()
        first = _evaluation(tenant_id=tenant_id, submission_id=submission_id)
        second = _evaluation(tenant_id=tenant_id, submission_id=submission_id)
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)

        def _insert(evaluation):
            with factory(tenant_id) as uow:
                stored = uow.learner_assessment_evaluations.insert(evaluation)
                uow.commit()
                return str(stored.evaluation_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_insert, (first, second)))
        assert len(set(results)) == 1
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                      FROM assessment.learner_assessment_evaluations
                     WHERE tenant_id = :tenant_id
                       AND submission_id = :submission_id
                    """
                ),
                {"tenant_id": tenant_id, "submission_id": submission_id},
            ).scalar_one()
        assert count == 1
