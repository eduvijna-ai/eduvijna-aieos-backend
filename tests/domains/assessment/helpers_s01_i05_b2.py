"""Shared fixtures for AIEOS360-S01-I05-B2 evaluation application tests."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.learner_principals import CLASS_REF_5A
from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.education.schema import (
    HOMEWORK_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
)
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from aieos.domains.learning.domain.submit import (
    transition_in_progress_attempt_to_submitted,
)
from aieos.domains.learning.infrastructure.persistence.uow import (
    SqlAlchemyLearningUnitOfWorkFactory,
)
from aieos.domains.teaching.application.models import TeachingAssignmentReadModel
from tests.domains.assessment.helpers_dev08_i02 import (
    MutableSchoolContextClassReader,
    build_assessment_client,
    headers as classroom_headers,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    close_assignment,
    create_learner_assignment,
    seed_published_learner_content,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
from tests.fakes import AllowClassroomAssessmentAuthorization, FixedPrincipalAuthenticator

FIXED_NOW = datetime(2026, 9, 9, 11, 0, tzinfo=UTC)
SINGLE_PATH = "/api/v1/assessment/submissions/{submission_id}/actions/evaluate"
BATCH_PATH = "/api/v1/assessment/assignments/{assignment_id}/actions/ensure-evaluations"
EVAL_ACTION = "assessment.learner_evaluation.ensure"


@dataclass(frozen=True, slots=True)
class SeededWorld:
    tenant_id: UUID
    teacher_id: UUID
    learner_id: UUID
    content_id: UUID
    content_version_id: UUID
    assignment: TeachingAssignmentReadModel
    submission_id: UUID | None
    attempt_id: UUID | None


def headers(tenant_id: UUID, *, idempotency_key: str) -> dict[str, str]:
    return classroom_headers(tenant_id, idempotency_key=idempotency_key)


def build_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    school_context_reader: object | None = None,
    assessment_authorization: object | None = None,
    unauthenticated: bool = False,
    with_school_context: bool = True,
) -> TestClient:
    if unauthenticated:
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
        from tests.domains.assessment.helpers_dev08_i02 import (
            CURSOR_KEY,
            IDEMPOTENCY_RETENTION,
            _AllowTeachingWorkAuthorization,
        )
        from tests.fakes import (
            AllowAssetCurrentGovernance,
            AllowAssetReferenceValidation,
            AllowPublicationAuthorization,
            AllowPublicationGovernance,
            AllowReviewAuthorization,
            AllowReviewCommentPolicy,
            StubSecurityContextResolver,
        )

        app = create_app(
            uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
            teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(
                runtime_engine,
                remediation_assessment_source_factory=SqlAlchemyRemediationAssessmentSource,
            ),
            assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
            assessment_authorization=AllowClassroomAssessmentAuthorization(),
            request_identity_authenticator=FixedPrincipalAuthenticator(
                principal_id, unauthenticated=True
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
            school_context_class_reader=MutableSchoolContextClassReader(
                tenant_id=tenant_id,
                teacher_principal_id=principal_id,
            ),
            teaching_authorization=_AllowTeachingWorkAuthorization(),
        )
        return TestClient(app, raise_server_exceptions=False)
    return build_assessment_client(
        runtime_engine,
        tenant_id,
        principal_id,
        school_context_reader=school_context_reader,
        with_school_context=with_school_context,
        assessment_authorization=assessment_authorization,
    )


def seed_content(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_type: str = WORKSHEET_CONTENT_TYPE,
    payload: dict[str, object] | None = None,
    owner_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    return seed_published_learner_content(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_type=content_type,
        owner_id=owner_id,
        payload=payload,
    )


def insert_submitted(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    assignment: TeachingAssignmentReadModel,
    responses: list[AttemptResponseItem] | None = None,
    class_ref: str | None = None,
    content_id: UUID | None = None,
    content_version_id: UUID | None = None,
    submitted_at: datetime | None = None,
) -> tuple[UUID, UUID]:
    attempt = LearnerAttempt.start_in_progress(
        tenant_id=tenant_id,
        learner_principal_id=learner_id,
        teaching_assignment_id=assignment.assignment_id.value,
        content_id=content_id or assignment.content_id,
        content_version_id=content_version_id or assignment.content_version_id,
        class_ref=class_ref or assignment.class_ref,
        started_at=FIXED_NOW,
    )
    bound: list[AttemptResponseItem] = []
    for item in responses or []:
        bound.append(
            AttemptResponseItem(
                attempt_id=attempt.attempt_id,
                question_id=item.question_id,
                response_kind=item.response_kind,
                choice_value=item.choice_value,
                text_value=item.text_value,
                boolean_value=item.boolean_value,
            )
        )
    submitted, submission = transition_in_progress_attempt_to_submitted(
        attempt,
        bound,
        submitted_at=submitted_at or (FIXED_NOW + timedelta(minutes=1)),
        assignment_revision_at_submit=int(assignment.aggregate_revision),
        due_at_at_submit=assignment.due_at,
    )
    factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        uow.attempts.insert(attempt)
        uow.persist_pure_submit_transition(
            submitted, submission, expected_revision=attempt.aggregate_revision
        )
        uow.commit()
    return submission.submission_id.value, attempt.attempt_id.value


def insert_in_progress(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    assignment: TeachingAssignmentReadModel,
) -> UUID:
    attempt = LearnerAttempt.start_in_progress(
        tenant_id=tenant_id,
        learner_principal_id=learner_id,
        teaching_assignment_id=assignment.assignment_id.value,
        content_id=assignment.content_id,
        content_version_id=assignment.content_version_id,
        class_ref=assignment.class_ref,
        started_at=FIXED_NOW,
    )
    factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
    with factory(tenant_id) as uow:
        uow.attempts.insert(attempt)
        uow.commit()
    return attempt.attempt_id.value


def seed_world(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    *,
    content_type: str = WORKSHEET_CONTENT_TYPE,
    payload: dict[str, object] | None = None,
    submit: bool = True,
    responses: list[AttemptResponseItem] | None = None,
) -> SeededWorld:
    tenant_id = uuid.uuid7()
    teacher_id = uuid.uuid7()
    learner_id = uuid.uuid7()
    content_id, version_id = seed_content(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_type=content_type,
        payload=payload,
        owner_id=teacher_id,
    )
    assignment = create_learner_assignment(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
        content_id=content_id,
        content_version_id=version_id,
        idempotency_key=str(uuid.uuid4()),
        class_ref=CLASS_REF_5A,
    )
    submission_id = None
    attempt_id = None
    if submit:
        submission_id, attempt_id = insert_submitted(
            runtime_engine,
            tenant_id=tenant_id,
            learner_id=learner_id,
            assignment=assignment,
            responses=responses,
        )
    return SeededWorld(
        tenant_id=tenant_id,
        teacher_id=teacher_id,
        learner_id=learner_id,
        content_id=content_id,
        content_version_id=version_id,
        assignment=assignment,
        submission_id=submission_id,
        attempt_id=attempt_id,
    )


def republish_with_payload(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_id: UUID,
    parent_version_id: UUID,
    owner_id: UUID,
    payload: dict[str, object],
    content_type: str = WORKSHEET_CONTENT_TYPE,
) -> UUID:
    version_v2 = uuid.uuid7()
    schema = {
        WORKSHEET_CONTENT_TYPE: ("education.worksheet", 1),
        QUIZ_CONTENT_TYPE: ("education.quiz", 1),
        HOMEWORK_CONTENT_TYPE: ("education.homework", 1),
    }[content_type]
    typed = ContentPayload.from_mapping(payload)
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 2, :parent,
                    :schema_id, :schema_version, CAST(:payload AS jsonb),
                    :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": version_v2,
                "tid": tenant_id,
                "cid": content_id,
                "parent": parent_version_id,
                "schema_id": schema[0],
                "schema_version": schema[1],
                "payload": canonical_payload_json(typed.body),
                "sha": typed.sha256.value,
                "prov": json.dumps({}),
                "now": FIXED_NOW,
                "actor": owner_id,
            },
        )
        conn.execute(
            text(
                """
                UPDATE content.contents
                SET published_version_id = :v2,
                    current_version_id = :v2,
                    aggregate_revision = aggregate_revision + 1,
                    updated_at = :now
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {
                "v2": version_v2,
                "tid": tenant_id,
                "cid": content_id,
                "now": FIXED_NOW,
            },
        )
    return version_v2


def count_evaluations(bootstrap_engine: Engine, *, tenant_id: UUID) -> int:
    from tests.dbutil import set_tenant

    with bootstrap_engine.begin() as conn:
        set_tenant(conn, tenant_id)
        return int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM assessment.learner_assessment_evaluations
                    WHERE tenant_id = :tid
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )


def count_eval_audits(bootstrap_engine: Engine, *, tenant_id: UUID) -> int:
    from tests.dbutil import set_tenant

    with bootstrap_engine.begin() as conn:
        set_tenant(conn, tenant_id)
        return int(
            conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM security.audit_records
                    WHERE tenant_id = :tid AND action = :action
                    """
                ),
                {"tid": tenant_id, "action": EVAL_ACTION},
            ).scalar_one()
        )


def fetch_eval_audit(bootstrap_engine: Engine, *, tenant_id: UUID) -> dict:
    from tests.dbutil import set_tenant

    with bootstrap_engine.begin() as conn:
        set_tenant(conn, tenant_id)
        row = (
            conn.execute(
                text(
                    """
                    SELECT action, primary_resource_type, primary_resource_id,
                           primary_resource_revision, resource_revision_before,
                           resource_revision_after, related_resource_refs
                    FROM security.audit_records
                    WHERE tenant_id = :tid AND action = :action
                    """
                ),
                {"tid": tenant_id, "action": EVAL_ACTION},
            )
            .mappings()
            .one()
        )
    return dict(row)


def worksheet_payload() -> dict[str, object]:
    return valid_worksheet_payload()


def placeholder_mc(question_id: str, choice_value: str) -> AttemptResponseItem:
    from aieos.domains.learning.domain.identities import AttemptId

    return AttemptResponseItem.multiple_choice(
        attempt_id=AttemptId.generate(),
        question_id=question_id,
        choice_value=choice_value,
    )


def force_lineage_content_version(
    bootstrap_engine: Engine,
    *,
    assignment_id: UUID,
    submission_id: UUID,
    content_version_id: UUID,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE teaching.assignments
                SET content_version_id = :vid
                WHERE assignment_id = :aid
                """
            ),
            {"vid": content_version_id, "aid": assignment_id},
        )
        conn.execute(
            text(
                "ALTER TABLE learning.submissions DISABLE TRIGGER "
                "learning_submissions_immutable_update"
            )
        )
        conn.execute(
            text(
                """
                UPDATE learning.submissions
                SET content_version_id = :vid
                WHERE submission_id = :sid
                """
            ),
            {"vid": content_version_id, "sid": submission_id},
        )
        conn.execute(
            text(
                "ALTER TABLE learning.submissions ENABLE TRIGGER "
                "learning_submissions_immutable_update"
            )
        )

