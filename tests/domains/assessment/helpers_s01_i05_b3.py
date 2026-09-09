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
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.assessment.helpers_dev08_i02 import headers as classroom_headers
from tests.domains.assessment.helpers_s01_i05_b2 import (
    BATCH_PATH,
    SINGLE_PATH,
    build_client as build_b2_client,
    count_evaluations,
    headers,
    insert_submitted,
    placeholder_mc,
    seed_world as seed_b2_world,
    worksheet_payload,
    FIXED_NOW,
)
from tests.platform.security.authorization.helpers import seed_principal

INTELLIGENCE_PATH = (
    "/api/v1/assessment/assignments/{assignment_id}/intelligence"
)
INTELLIGENCE_ACTION = "assessment.assignment.intelligence.read"
OBSOLETE_POLICY_ID = "aieos.learner_assessment.obsolete-test"
OBSOLETE_POLICY_VERSION = 99


def seed_human_principal(bootstrap_engine: Engine, principal_id: UUID) -> None:
    seed_principal(
        bootstrap_engine, principal_id, principal_kind=PrincipalKind.HUMAN
    )


def seed_workload_principal(bootstrap_engine: Engine, principal_id: UUID) -> None:
    seed_principal(
        bootstrap_engine, principal_id, principal_kind=PrincipalKind.WORKLOAD
    )


def seed_unclassified_principal(bootstrap_engine: Engine, principal_id: UUID) -> None:
    seed_principal(bootstrap_engine, principal_id, principal_kind=None)


def seed_world(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    **kwargs,
):
    world = seed_b2_world(bootstrap_engine, runtime_engine, **kwargs)
    seed_human_principal(bootstrap_engine, world.teacher_id)
    return world


def build_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    school_context_reader: object | None = None,
    assessment_authorization: object | None = None,
    unauthenticated: bool = False,
    with_school_context: bool = True,
    principal_classification_authority: object | None = None,
):
    if principal_classification_authority is None:
        return build_b2_client(
            runtime_engine,
            tenant_id,
            principal_id,
            school_context_reader=school_context_reader,
            assessment_authorization=assessment_authorization,
            unauthenticated=unauthenticated,
            with_school_context=with_school_context,
        )
    # Compose with an explicit classification authority (deny / unavailable cases).
    from aieos.development.schemas import (
        build_development_schema_registry,
        development_content_type_names,
    )
    from aieos.domains.assessment.infrastructure.persistence.uow import (
        SqlAlchemyAssessmentUnitOfWorkFactory,
    )
    from aieos.domains.content.application.catalog import StaticContentTypeCatalog
    from aieos.domains.content.infrastructure.persistence.uow import (
        SqlAlchemyContentUnitOfWorkFactory,
    )
    from aieos.domains.teaching.infrastructure.persistence.uow import (
        SqlAlchemyTeachingUnitOfWorkFactory,
    )
    from aieos.platform.api.app import create_app
    from aieos.platform.runtime.remediation_assessment_source import (
        SqlAlchemyRemediationAssessmentSource,
    )
    from fastapi.testclient import TestClient
    from tests.domains.assessment.helpers_dev08_i02 import (
        CURSOR_KEY,
        IDEMPOTENCY_RETENTION,
        MutableSchoolContextClassReader,
        _AllowTeachingWorkAuthorization,
    )
    from tests.fakes import (
        AllowAssetCurrentGovernance,
        AllowAssetReferenceValidation,
        AllowClassroomAssessmentAuthorization,
        AllowPublicationAuthorization,
        AllowPublicationGovernance,
        AllowReviewAuthorization,
        AllowReviewCommentPolicy,
        FixedPrincipalAuthenticator,
        StubSecurityContextResolver,
    )

    if not with_school_context:
        reader = None
    else:
        reader = school_context_reader or MutableSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        )
    auth = assessment_authorization or AllowClassroomAssessmentAuthorization()
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(
            runtime_engine,
            remediation_assessment_source_factory=SqlAlchemyRemediationAssessmentSource,
        ),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=auth,
        request_identity_authenticator=FixedPrincipalAuthenticator(
            principal_id, unauthenticated=unauthenticated
        ),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog(development_content_type_names()),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=reader,
        teaching_authorization=_AllowTeachingWorkAuthorization(),
        principal_classification_authority=principal_classification_authority,
    )
    return TestClient(app, raise_server_exceptions=False)


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
    "seed_human_principal",
    "seed_unclassified_principal",
    "seed_workload_principal",
    "seed_world",
    "short_answer",
    "worksheet_payload",
    "uuid",
]
