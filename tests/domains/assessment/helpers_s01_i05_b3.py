"""Shared fixtures for AIEOS360-S01-I05-B3 Teacher Assessment Intelligence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.domains.assessment.domain.evaluation import (
    LearnerAssessmentEvaluation,
    LearnerAssessmentEvaluationItem,
    LearnerAssessmentObjectiveEvidence,
)
from aieos.domains.assessment.domain.evaluation_vocabulary import (
    EvaluationMethod,
    ItemOutcome,
    ObjectiveEvidenceResult,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from tests.domains.assessment.helpers_dev08_i02 import headers as classroom_headers
from tests.domains.assessment.helpers_s01_i05_b2 import (
    BATCH_PATH,
    SINGLE_PATH,
    build_client,
    count_evaluations,
    headers,
    insert_submitted,
    placeholder_mc,
    seed_world,
    worksheet_payload,
    FIXED_NOW,
)

INTELLIGENCE_PATH = (
    "/api/v1/assessment/assignments/{assignment_id}/intelligence"
)
INTELLIGENCE_ACTION = "assessment.assignment.intelligence.read"
OBSOLETE_POLICY_ID = "aieos.learner_assessment.obsolete-test"
OBSOLETE_POLICY_VERSION = 99


def read_headers(tenant_id: UUID) -> dict[str, str]:
    return classroom_headers(tenant_id)


def ensure_submission(
    client,
    *,
    tenant_id: UUID,
    submission_id: UUID,
    idempotency_key: str,
):
    return client.post(
        SINGLE_PATH.format(submission_id=submission_id),
        headers=headers(tenant_id, idempotency_key=idempotency_key),
    )


def ensure_assignment(
    client,
    *,
    tenant_id: UUID,
    assignment_id: UUID,
    idempotency_key: str,
):
    return client.post(
        BATCH_PATH.format(assignment_id=assignment_id),
        headers=headers(tenant_id, idempotency_key=idempotency_key),
    )


def get_intelligence(client, *, tenant_id: UUID, assignment_id: UUID):
    return client.get(
        INTELLIGENCE_PATH.format(assignment_id=assignment_id),
        headers=read_headers(tenant_id),
    )


def mc_correct(question_id: str = "q-1") -> AttemptResponseItem:
    return placeholder_mc(question_id, "1/2")


def mc_incorrect(question_id: str = "q-1") -> AttemptResponseItem:
    return placeholder_mc(question_id, "1/3")


def short_answer(question_id: str = "q-2", text: str = "one half") -> AttemptResponseItem:
    from aieos.domains.learning.domain.identities import AttemptId

    return AttemptResponseItem.short_answer(
        attempt_id=AttemptId.generate(),
        question_id=question_id,
        text_value=text,
    )


def insert_obsolete_policy_evaluation(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    submission_id: UUID,
    attempt_id: UUID,
    assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
) -> UUID:
    evaluation = LearnerAssessmentEvaluation.issue(
        tenant_id=tenant_id,
        learner_principal_id=learner_id,
        submission_id=submission_id,
        attempt_id=attempt_id,
        teaching_assignment_id=assignment_id,
        content_id=content_id,
        content_version_id=content_version_id,
        class_ref=class_ref,
        evaluation_policy_id=OBSOLETE_POLICY_ID,
        evaluation_policy_version=OBSOLETE_POLICY_VERSION,
        evaluated_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC),
        items=(
            LearnerAssessmentEvaluationItem(
                question_id="q-1",
                question_type="multiple_choice",
                outcome=ItemOutcome.CORRECT,
                evaluation_method=EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER,
                objective_ids=("obj-1",),
                response_kind="MULTIPLE_CHOICE",
            ),
        ),
        objective_evidence=(
            LearnerAssessmentObjectiveEvidence(
                objective_id="obj-1",
                result=ObjectiveEvidenceResult.DEMONSTRATED_ON_SUBMITTED_ITEMS,
            ),
        ),
    )
    factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        persisted = uow.learner_assessment_evaluations.insert(evaluation)
        uow.commit()
    return persisted.evaluation_id.value


__all__ = [
    "BATCH_PATH",
    "FIXED_NOW",
    "INTELLIGENCE_ACTION",
    "INTELLIGENCE_PATH",
    "OBSOLETE_POLICY_ID",
    "OBSOLETE_POLICY_VERSION",
    "SINGLE_PATH",
    "build_client",
    "count_evaluations",
    "ensure_assignment",
    "ensure_submission",
    "get_intelligence",
    "insert_obsolete_policy_evaluation",
    "insert_submitted",
    "mc_correct",
    "mc_incorrect",
    "read_headers",
    "seed_world",
    "short_answer",
    "worksheet_payload",
    "uuid",
]
