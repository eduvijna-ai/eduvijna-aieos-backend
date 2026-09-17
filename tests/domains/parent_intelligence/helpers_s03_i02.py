"""Shared fixtures for AIEOS360-S03-I02 Parent Intelligence GET."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.learning.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
    UnconfiguredSchoolContextLearnerMembershipReader,
)
from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapabilityForbidden,
    ParentLearnerAccessContractError,
    ParentLearnerAccessUnavailable,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
    CurrentParentLearnerAccessService,
    UnconfiguredSchoolContextParentLearnerAccessReader,
)
from aieos.domains.parent_intelligence.application.models import (
    ParentIntelligenceFactsSnapshot,
    ParentLearnerFacts,
)
from aieos.domains.parent_intelligence.infrastructure.read_projection import (
    SqlAlchemyParentIntelligenceFactsReader,
)
from aieos.platform.security.authorization.decisions import PrincipalKind, PrincipalStatus
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    seed_published_learner_content,
)
from tests.platform.security.authorization.helpers import (
    seed_active_authority,
    seed_grant,
    seed_membership,
    seed_principal,
    seed_tenant,
)

FIXED_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
HOME_PATH = "/api/v1/parent-os/home"
CHILD_PATH = "/api/v1/parent-os/children/{learner_principal_id}"
CLASS_REF_HOME = "class-parent-home"
CLASS_REF_OTHER = "class-parent-other"

HOME_KEYS = {"generated_at", "projection_mode", "time_window", "children"}
TIME_WINDOW_KEYS = {"mode", "start", "end"}
CHILD_KEYS = {"learner_principal_id", "assignments"}
ASSIGNMENT_KEYS = {
    "assignment_id",
    "title",
    "content_type",
    "available_from",
    "due_at",
    "attempt_status",
    "submitted_at",
}
FORBIDDEN_BODY_TOKENS = (
    "class_ref",
    "school_learner_ref",
    "content_id",
    "content_version_id",
    "assignment_lifecycle",
    "attempt_id",
    "submission_id",
    "evaluation_id",
    "teacher_principal_id",
    "presentation_label",
    "display_name",
    "legal_name",
    "relationship_type",
    "guardian_type",
    "custody_type",
    "mastery",
    "competency",
    "diagnosis",
    "predicted",
    "behind peers",
    "response_snapshot",
    "teacher_notes",
    "PRIVATE_EXECUTION_NOTE",
    "class_result_level",
    "class_result_note",
)


class MutableParentAccessReader:
    def __init__(self, items: tuple[UUID, ...] = ()) -> None:
        self.items = list(items)
        self.calls: list[tuple[UUID, UUID]] = []

    def set_current(self, learner_principal_ids: tuple[UUID, ...]) -> None:
        self.items = list(learner_principal_ids)

    def list_current_authorized_learners(self, tenant_id, adult_principal_id):
        self.calls.append((tenant_id, adult_principal_id))
        return tuple(
            AuthorizedLearnerAccess(learner_principal_id=item) for item in self.items
        )


class DenyParentIntelligenceAuthorization:
    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        del tenant_id, principal_id, capability
        raise ParentIntelligenceCapabilityForbidden(
            "parent intelligence capability denied"
        )


class UnavailableParentAccessReader:
    def list_current_authorized_learners(self, tenant_id, adult_principal_id):
        del tenant_id, adult_principal_id
        raise ParentLearnerAccessUnavailable(
            "Parent Learner Access is temporarily unavailable"
        )


class InvalidParentAccessReader:
    def list_current_authorized_learners(self, tenant_id, adult_principal_id):
        del tenant_id, adult_principal_id
        raise ParentLearnerAccessContractError(
            "Parent Learner Access provider returned an invalid response"
        )


class RecordingIntegrity:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    def validate_learner_subject(self, *, tenant_id, learner_principal_id) -> None:
        del tenant_id
        self.calls.append(learner_principal_id)


class RecordingFactsReader:
    def __init__(
        self,
        snapshot: ParentIntelligenceFactsSnapshot | None = None,
        *,
        exc: BaseException | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.exc = exc
        self.calls: list[tuple[UUID, tuple[UUID, ...], datetime]] = []

    def read_authorized_learner_facts(
        self,
        *,
        tenant_id: UUID,
        authorized_learner_principal_ids,
        observed_at: datetime,
    ) -> ParentIntelligenceFactsSnapshot:
        self.calls.append(
            (tenant_id, tuple(authorized_learner_principal_ids), observed_at)
        )
        if self.exc is not None:
            raise self.exc
        if self.snapshot is None:
            return ParentIntelligenceFactsSnapshot(
                generated_at=observed_at,
                learners=tuple(
                    ParentLearnerFacts(
                        learner_principal_id=learner_id, assignments=()
                    )
                    for learner_id in authorized_learner_principal_ids
                ),
            )
        return self.snapshot


class RecordingMembershipReader:
    def __init__(
        self,
        memberships: dict[UUID, tuple[CurrentLearnerClassMembership, ...]] | None = None,
        *,
        exc: BaseException | None = None,
    ) -> None:
        self.memberships = memberships or {}
        self.exc = exc
        self.calls: list[UUID] = []

    def list_current_memberships(self, tenant_id, learner_principal_id):
        del tenant_id
        self.calls.append(learner_principal_id)
        if self.exc is not None:
            raise self.exc
        return self.memberships.get(learner_principal_id, ())


class AlwaysHumanGate:
    def require_current_human_principal(self, principal_id):
        return principal_id


class AllowParentIntelligenceAuthorization:
    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        del tenant_id, principal_id, capability


def headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-AIEOS-Tenant-ID": str(tenant_id)}


def child_path(learner_principal_id: UUID) -> str:
    return CHILD_PATH.format(learner_principal_id=learner_principal_id)


def empty_facts(observed_at: datetime, *learner_ids: UUID) -> ParentIntelligenceFactsSnapshot:
    return ParentIntelligenceFactsSnapshot(
        generated_at=observed_at,
        learners=tuple(
            ParentLearnerFacts(learner_principal_id=learner_id, assignments=())
            for learner_id in learner_ids
        ),
    )


def seed_human_adult_with_capability(
    bootstrap_engine: Engine, *, tenant_id: UUID, principal_id: UUID
) -> None:
    seed_active_authority(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=("parent.intelligence.read",),
    )


def seed_human_learner(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    status: str = PrincipalStatus.ACTIVE,
) -> None:
    seed_tenant(bootstrap_engine, tenant_id)
    seed_principal(
        bootstrap_engine,
        learner_id,
        principal_kind=PrincipalKind.HUMAN,
        status=status,
    )
    seed_membership(bootstrap_engine, tenant_id=tenant_id, principal_id=learner_id)


def revoke_parent_intelligence_read(
    bootstrap_engine: Engine, *, tenant_id: UUID, principal_id: UUID
) -> None:
    seed_grant(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        capability="parent.intelligence.read",
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
    available_from: datetime = FIXED_NOW,
    due_at: datetime | None = None,
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
                    'MustNotLeak', NULL, :state,
                    :available_from, :available_from, :due_at, :closed_at,
                    :cancelled_at, 0, :available_from, :available_from
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
                "available_from": available_from,
                "due_at": due_at,
                "closed_at": closed_at,
                "cancelled_at": cancelled_at,
            },
        )
    return aid


def insert_in_progress_attempt(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    started_at: datetime = FIXED_NOW,
) -> UUID:
    attempt_id = uuid.uuid7()
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
                    :class_ref, 1, 'IN_PROGRESS', :started_at,
                    :started_at, NULL, NULL, 0, :started_at, :started_at
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
            },
        )
    return attempt_id


def insert_submitted_attempt(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    learner_id: UUID,
    assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    submitted_at: datetime = FIXED_NOW,
    include_submission: bool = True,
    submission_id: UUID | None = None,
) -> tuple[UUID, UUID | None]:
    attempt_id = uuid.uuid7()
    sid = submission_id or uuid.uuid7()
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
                "submission_id": sid,
            },
        )
        if include_submission:
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
                        CAST(:snapshot AS jsonb), :submitted_at, 0, NULL,
                        :submitted_at
                    )
                    """
                ),
                {
                    "submission_id": sid,
                    "tid": tenant_id,
                    "attempt_id": attempt_id,
                    "learner_id": learner_id,
                    "assignment_id": assignment_id,
                    "cid": content_id,
                    "vid": content_version_id,
                    "class_ref": class_ref,
                    "snapshot": json.dumps(
                        [{"question_id": "q1", "response_kind": "text", "value": "secret"}]
                    ),
                    "submitted_at": submitted_at,
                },
            )
            return attempt_id, sid
    return attempt_id, None


def source_row_counts(engine: Engine, tenant_id: UUID) -> dict[str, int]:
    queries = {
        "assignments": "SELECT count(*) FROM teaching.assignments WHERE tenant_id = :tid",
        "attempts": "SELECT count(*) FROM learning.attempts WHERE tenant_id = :tid",
        "submissions": "SELECT count(*) FROM learning.submissions WHERE tenant_id = :tid",
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


def assert_exact_response_keys(body: dict) -> None:
    assert set(body) == HOME_KEYS
    assert set(body["time_window"]) == TIME_WINDOW_KEYS
    for child in body["children"]:
        assert set(child) == CHILD_KEYS
        for assignment in child["assignments"]:
            assert set(assignment) == ASSIGNMENT_KEYS


def assert_concealed_404(response) -> None:
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "parent_learner_not_found"
    assert body["title"] == "Parent learner not found"
    assert body["detail"] == "Parent learner was not found"
    dumped = json.dumps(body).lower()
    assert "unauthorized" not in dumped
    assert "other tenant" not in dumped
    assert "revoked" not in dumped
    assert "unknown principal" not in dumped
    assert "not your child" not in dumped


def parent_access_service(
    *,
    reader,
    integrity=None,
    authorization=None,
    classification=None,
) -> CurrentParentLearnerAccessService:
    return CurrentParentLearnerAccessService(
        classification=classification or AlwaysHumanGate(),
        authorization=authorization or AllowParentIntelligenceAuthorization(),
        reader=reader,
        integrity=integrity or RecordingIntegrity(),
    )


def build_client(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    access_reader: object | None = None,
    parent_intelligence_authorization: object | None = None,
    facts_reader: object | None = None,
    membership_reader: object | None = None,
    integrity: object | None = None,
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
        SecurityAuthorityLearnerPrincipalIntegrity,
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

    membership = membership_reader or UnconfiguredSchoolContextLearnerMembershipReader()
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
        parent_intelligence_authorization=parent_intelligence_authorization,
        school_context_parent_learner_access_reader=(
            access_reader or UnconfiguredSchoolContextParentLearnerAccessReader()
        ),
        parent_learner_integrity_authority=(
            integrity or SecurityAuthorityLearnerPrincipalIntegrity(runtime_engine)
        ),
        parent_intelligence_facts_reader=(
            facts_reader
            or SqlAlchemyParentIntelligenceFactsReader(
                runtime_engine, membership_reader=membership
            )
        ),
    )
    return TestClient(app, raise_server_exceptions=False)
