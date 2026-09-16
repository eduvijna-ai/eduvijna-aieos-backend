"""Shared fixtures for AIEOS360-S02-I02 Principal School Intelligence GET."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.principal_school_context import (
    CLASS_REF_6A,
    CLASS_REF_6B,
    DevelopmentSchoolIntelligencePermit,
)
from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
)
from aieos.domains.school_intelligence.application.errors import (
    SchoolIntelligenceCapabilityForbidden,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
)
from aieos.domains.school_intelligence.infrastructure.read_projection import (
    SqlAlchemySchoolIntelligenceFactsReader,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    seed_published_learner_content,
)
from tests.platform.security.authorization.helpers import (
    seed_active_authority,
    seed_grant,
    seed_principal,
)

FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
PATH = "/api/v1/principal-os/school-intelligence"
UNAUTHORIZED_CLASS_REF = "class-unauthorized"
CROSS_TENANT_CLASS_REF = "class-6a"
OBSOLETE_POLICY_ID = "aieos.learner_assessment.obsolete-test"
OBSOLETE_POLICY_VERSION = 99


class MutablePrincipalScopeReader:
    def __init__(self, items: tuple[AuthorizedSchoolClassRef, ...] = ()) -> None:
        self.items = list(items)
        self.calls: list[tuple[UUID, UUID]] = []

    def list_current_authorized_classes(self, tenant_id, principal_id):
        self.calls.append((tenant_id, principal_id))
        return tuple(self.items)


class DenySchoolIntelligenceAuthorization:
    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        del tenant_id, principal_id, capability
        raise SchoolIntelligenceCapabilityForbidden(
            "school intelligence capability denied"
        )


def default_scope() -> tuple[AuthorizedSchoolClassRef, ...]:
    return (
        AuthorizedSchoolClassRef(class_ref=CLASS_REF_6A, display_label="Grade 6A"),
        AuthorizedSchoolClassRef(class_ref=CLASS_REF_6B, display_label="Grade 6B"),
    )


def headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-AIEOS-Tenant-ID": str(tenant_id)}


def seed_human_with_capability(
    bootstrap_engine: Engine, *, tenant_id: UUID, principal_id: UUID
) -> None:
    seed_active_authority(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=("school.intelligence.read",),
    )


def revoke_school_intelligence_read(
    bootstrap_engine: Engine, *, tenant_id: UUID, principal_id: UUID
) -> None:
    seed_grant(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        capability="school.intelligence.read",
        revoked_at=FIXED_NOW,
    )


def seed_content(
    bootstrap_engine: Engine, *, tenant_id: UUID, owner_id: UUID
) -> tuple[UUID, UUID]:
    return seed_published_learner_content(
        bootstrap_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def insert_assignment(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    lifecycle_state: str = "ACTIVE",
    assigned_at: datetime = FIXED_NOW,
    assignment_id: UUID | None = None,
) -> UUID:
    aid = assignment_id or uuid.uuid7()
    closed_at = FIXED_NOW if lifecycle_state == "CLOSED" else None
    cancelled_at = FIXED_NOW if lifecycle_state == "CANCELLED" else None
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO teaching.assignments (
                    assignment_id, tenant_id, teacher_principal_id,
                    content_id, content_version_id, audience_type, class_ref,
                    audience_display_label, source_work_id, lifecycle_state,
                    assigned_at, available_from, due_at, closed_at, cancelled_at,
                    aggregate_revision, created_at, updated_at
                ) VALUES (
                    :aid, :tid, :pid, :cid, :vid, 'class', :class_ref,
                    'Label', NULL, :state,
                    :assigned_at, :assigned_at, NULL, :closed_at, :cancelled_at,
                    0, :assigned_at, :assigned_at
                )
                """
            ),
            {
                "aid": aid,
                "tid": tenant_id,
                "pid": teacher_id,
                "cid": content_id,
                "vid": content_version_id,
                "class_ref": class_ref,
                "state": lifecycle_state,
                "assigned_at": assigned_at,
                "closed_at": closed_at,
                "cancelled_at": cancelled_at,
            },
        )
    return aid


def insert_submission(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    submitted_at: datetime = FIXED_NOW,
) -> tuple[UUID, UUID]:
    attempt_id = uuid.uuid7()
    submission_id = uuid.uuid7()
    started_at = submitted_at - timedelta(minutes=5)
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO learning.attempts (
                    attempt_id, tenant_id, learner_principal_id,
                    teaching_assignment_id, content_id, content_version_id,
                    class_ref, attempt_number, lifecycle_state, started_at,
                    last_saved_at, submitted_at, submission_id, aggregate_revision,
                    created_at, updated_at
                ) VALUES (
                    :attempt_id, :tid, :learner_id, :assignment_id, :cid, :vid,
                    :class_ref, 1, 'SUBMITTED', :started_at,
                    :submitted_at, :submitted_at, :submission_id, 1,
                    :started_at, :submitted_at
                )
                """
            ),
            {
                "attempt_id": attempt_id,
                "tid": tenant_id,
                "learner_id": learner_id,
                "assignment_id": assignment_id,
                "cid": content_id,
                "vid": content_version_id,
                "class_ref": class_ref,
                "started_at": started_at,
                "submitted_at": submitted_at,
                "submission_id": submission_id,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO learning.submissions (
                    submission_id, tenant_id, attempt_id, learner_principal_id,
                    teaching_assignment_id, content_id, content_version_id,
                    class_ref, response_snapshot, submitted_at,
                    assignment_revision_at_submit, due_at_at_submit, created_at
                ) VALUES (
                    :submission_id, :tid, :attempt_id, :learner_id,
                    :assignment_id, :cid, :vid, :class_ref,
                    CAST(:snapshot AS jsonb), :submitted_at, 0, NULL, :submitted_at
                )
                """
            ),
            {
                "submission_id": submission_id,
                "tid": tenant_id,
                "attempt_id": attempt_id,
                "learner_id": learner_id,
                "assignment_id": assignment_id,
                "cid": content_id,
                "vid": content_version_id,
                "class_ref": class_ref,
                "snapshot": json.dumps([]),
                "submitted_at": submitted_at,
            },
        )
    return submission_id, attempt_id


def insert_evaluation(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    submission_id: UUID,
    attempt_id: UUID,
    assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    policy_id: str = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    policy_version: int = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
    evaluated_at: datetime = FIXED_NOW,
) -> UUID:
    evaluation_id = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO assessment.learner_assessment_evaluations (
                    evaluation_id, tenant_id, learner_principal_id, submission_id,
                    attempt_id, teaching_assignment_id, content_id,
                    content_version_id, class_ref, evaluation_policy_id,
                    evaluation_policy_version, evaluated_at, created_at
                ) VALUES (
                    :evaluation_id, :tid, :learner_id, :submission_id,
                    :attempt_id, :assignment_id, :cid, :vid, :class_ref,
                    :policy_id, :policy_version, :evaluated_at, :evaluated_at
                )
                """
            ),
            {
                "evaluation_id": evaluation_id,
                "tid": tenant_id,
                "learner_id": learner_id,
                "submission_id": submission_id,
                "attempt_id": attempt_id,
                "assignment_id": assignment_id,
                "cid": content_id,
                "vid": content_version_id,
                "class_ref": class_ref,
                "policy_id": policy_id,
                "policy_version": policy_version,
                "evaluated_at": evaluated_at,
            },
        )
    return evaluation_id


def insert_classroom_assessment(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    assignment_id: UUID | None,
    lifecycle_state: str = "RECORDED",
    recorded_at: datetime = FIXED_NOW,
) -> UUID:
    assessment_id = uuid.uuid7()
    voided_at = FIXED_NOW if lifecycle_state == "VOIDED" else None
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO assessment.classroom_assessments (
                    assessment_id, tenant_id, teacher_principal_id, class_ref,
                    content_id, content_version_id, class_result_level,
                    class_result_note, lifecycle_state, work_id, execution_id,
                    assignment_id, aggregate_revision, recorded_at, voided_at,
                    created_at, updated_at
                ) VALUES (
                    :assessment_id, :tid, :pid, :class_ref, :cid, :vid,
                    'MIXED', 'private note must not leak', :state,
                    NULL, NULL, :assignment_id, 0, :recorded_at, :voided_at,
                    :recorded_at, :recorded_at
                )
                """
            ),
            {
                "assessment_id": assessment_id,
                "tid": tenant_id,
                "pid": teacher_id,
                "class_ref": class_ref,
                "cid": content_id,
                "vid": content_version_id,
                "state": lifecycle_state,
                "assignment_id": assignment_id,
                "recorded_at": recorded_at,
                "voided_at": voided_at,
            },
        )
    return assessment_id


def insert_work(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    class_label: str | None = "Misleading Label",
    intent_type: str = "prepare_tomorrow",
) -> UUID:
    work_id = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO teaching.works (
                    work_id, tenant_id, teacher_principal_id, intent_type,
                    goal_text, class_label, subject, topic, target_date,
                    locale, aggregate_revision, created_at, updated_at, archived_at
                ) VALUES (
                    :work_id, :tid, :pid, :intent_type, 'Goal',
                    :class_label, NULL, NULL, DATE '2026-09-16',
                    'en-IN', 0, :now, :now, NULL
                )
                """
            ),
            {
                "work_id": work_id,
                "tid": tenant_id,
                "pid": teacher_id,
                "intent_type": intent_type,
                "class_label": class_label,
                "now": FIXED_NOW,
            },
        )
    return work_id


def insert_execution(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    work_id: UUID,
    class_ref: str,
    lifecycle_state: str = "COMPLETED",
    started_at: datetime = FIXED_NOW,
) -> UUID:
    execution_id = uuid.uuid7()
    completed_at = FIXED_NOW if lifecycle_state == "COMPLETED" else None
    cancelled_at = FIXED_NOW if lifecycle_state == "CANCELLED" else None
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO teaching.executions (
                    execution_id, tenant_id, teacher_principal_id, work_id,
                    class_ref, lifecycle_state, started_at, completed_at,
                    cancelled_at, aggregate_revision, created_at, updated_at
                ) VALUES (
                    :execution_id, :tid, :pid, :work_id, :class_ref, :state,
                    :started_at, :completed_at, :cancelled_at, 0, :started_at,
                    :started_at
                )
                """
            ),
            {
                "execution_id": execution_id,
                "tid": tenant_id,
                "pid": teacher_id,
                "work_id": work_id,
                "class_ref": class_ref,
                "state": lifecycle_state,
                "started_at": started_at,
                "completed_at": completed_at,
                "cancelled_at": cancelled_at,
            },
        )
    return execution_id


def insert_remediation_origin(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    source_class_ref: str,
    class_label: str | None = "Misleading Label",
    source_assessment_id: UUID | None = None,
    created_at: datetime = FIXED_NOW,
) -> UUID:
    work_id = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO teaching.works (
                    work_id, tenant_id, teacher_principal_id, intent_type,
                    goal_text, class_label, subject, topic, target_date,
                    locale, aggregate_revision, created_at, updated_at, archived_at
                ) VALUES (
                    :work_id, :tid, :pid, 'remediate_class', 'Goal',
                    :class_label, NULL, NULL, DATE '2026-09-16',
                    'en-IN', 0, :now, :now, NULL
                )
                """
            ),
            {
                "work_id": work_id,
                "tid": tenant_id,
                "pid": teacher_id,
                "class_label": class_label,
                "now": created_at,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO teaching.work_remediation_origins (
                    work_id, tenant_id, source_assessment_id,
                    source_assessment_aggregate_revision,
                    source_class_result_level_snapshot, source_class_ref,
                    source_content_id, source_content_version_id,
                    source_work_id, source_execution_id, source_assignment_id,
                    initiating_teacher_principal_id, created_at
                ) VALUES (
                    :work_id, :tid, :source_assessment_id, 0, 'MIXED',
                    :source_class_ref, :cid, :vid, NULL, NULL, NULL, :pid, :created_at
                )
                """
            ),
            {
                "work_id": work_id,
                "tid": tenant_id,
                "source_assessment_id": source_assessment_id or uuid.uuid7(),
                "source_class_ref": source_class_ref,
                "cid": uuid.uuid7(),
                "vid": uuid.uuid7(),
                "pid": teacher_id,
                "created_at": created_at,
            },
        )
    return work_id


def source_row_counts(engine: Engine, tenant_id: UUID) -> dict[str, int]:
    queries = {
        "assignments": "SELECT count(*) FROM teaching.assignments WHERE tenant_id = :tid",
        "executions": "SELECT count(*) FROM teaching.executions WHERE tenant_id = :tid",
        "remediation_origins": (
            "SELECT count(*) FROM teaching.work_remediation_origins WHERE tenant_id = :tid"
        ),
        "attempts": "SELECT count(*) FROM learning.attempts WHERE tenant_id = :tid",
        "submissions": "SELECT count(*) FROM learning.submissions WHERE tenant_id = :tid",
        "classroom_assessments": (
            "SELECT count(*) FROM assessment.classroom_assessments WHERE tenant_id = :tid"
        ),
        "evaluations": (
            "SELECT count(*) FROM assessment.learner_assessment_evaluations "
            "WHERE tenant_id = :tid"
        ),
        "outbox": "SELECT count(*) FROM integration.outbox_messages WHERE tenant_id = :tid",
        "audit": "SELECT count(*) FROM security.audit_records WHERE tenant_id = :tid",
    }
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        for name, sql in queries.items():
            counts[name] = int(conn.execute(text(sql), {"tid": tenant_id}).scalar_one())
    return counts


def build_client(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    scope_reader: object | None = None,
    school_intelligence_authorization: object | None = None,
    facts_reader: object | None = None,
    unauthenticated: bool = False,
    principal_classification_authority: object | None = None,
) -> TestClient:
    from datetime import timedelta

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
    from aieos.platform.security.authorization import (
        CurrentPrincipalClassificationAuthority,
    )
    from tests.domains.assessment.helpers_dev08_i02 import (
        CURSOR_KEY,
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

    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(
            runtime_engine,
            remediation_assessment_source_factory=SqlAlchemyRemediationAssessmentSource,
        ),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(
            principal_id, unauthenticated=unauthenticated
        ),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog(development_content_type_names()),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        teaching_authorization=_AllowTeachingWorkAuthorization(),
        principal_classification_authority=(
            principal_classification_authority
            or CurrentPrincipalClassificationAuthority(runtime_engine)
        ),
        school_intelligence_authorization=(
            school_intelligence_authorization or DevelopmentSchoolIntelligencePermit()
        ),
        school_context_principal_scope_reader=(
            scope_reader or MutablePrincipalScopeReader(default_scope())
        ),
        school_intelligence_facts_reader=(
            facts_reader or SqlAlchemySchoolIntelligenceFactsReader(runtime_engine)
        ),
    )
    return TestClient(app, raise_server_exceptions=False)
