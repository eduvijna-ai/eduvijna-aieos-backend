"""Shared fixtures for AIEOS360-S01-I03 Student assignment application tests."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.learner_principals import (
    CLASS_REF_5A,
    CLASS_REF_5B,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_B_PRINCIPAL_ID,
    ensure_synthetic_student_principals,
)
from aieos.development.learner_school_context import (
    DevelopmentSchoolContextLearnerMembershipReader,
)
from aieos.development.schemas import (
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import (
    HOMEWORK_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
    WORKSHEET_CONTENT_TYPE,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
)
from aieos.domains.teaching.application.assignment_create import (
    CreateTeachingAssignmentService,
)
from aieos.domains.teaching.application.assignment_mutations import (
    CancelTeachingAssignmentService,
    CloseTeachingAssignmentService,
    UpdateTeachingAssignmentDueService,
)
from aieos.domains.teaching.application.audit import api_mutation_audit_provenance
from aieos.domains.teaching.application.models import (
    CreateTeachingAssignmentCommand,
    TeachingAssignmentReadModel,
    UpdateTeachingAssignmentDueCommand,
)
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthorityService,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.events.models import MutationEventContext
from aieos.platform.runtime.student_learning_command import (
    SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.education.test_tos_dev04_i03_content_payloads import (
    valid_homework_payload,
    valid_quiz_payload,
)
from tests.domains.teaching.helpers_dev06_i03 import FIXED_NOW as TEACHING_FIXED_NOW
from tests.domains.teaching.helpers_dev06_i03 import create_service, event_context
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
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
from tests.platform.security.authorization.helpers import seed_principal

FIXED_NOW = datetime(2026, 8, 31, 14, 0, tzinfo=UTC)
PAST_DUE = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
FUTURE_AVAILABLE = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
IDEMPOTENCY_RETENTION = timedelta(hours=24)
CURSOR_KEY = b"aieos360-s01-i03-test-cursor-key"

HOME_PATH = "/api/v1/student-os/home"
ASSIGNMENTS_PATH = "/api/v1/student-os/assignments"
START_PATH = "/api/v1/learning/assignments/{assignment_id}/attempts"
ATTEMPT_PATH = "/api/v1/learning/attempts/{attempt_id}"
SAVE_PATH = "/api/v1/learning/attempts/{attempt_id}/responses"
SUBMIT_PATH = "/api/v1/learning/attempts/{attempt_id}/actions/submit"

_LEARNING_TABLES = ("attempt_response_items", "submissions", "attempts")
_LEARNING_AUDIT_ACTIONS = (
    "learning.attempt.start",
    "learning.attempt.save_responses",
    "learning.attempt.submit",
)
_TEACHING_ASSIGNMENT_AUDIT_ACTIONS = (
    "teaching.assignment.create",
    "teaching.assignment.due_update",
    "teaching.assignment.close",
    "teaching.assignment.cancel",
)


def _table_exists(conn, schema: str, table: str) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = :schema AND table_name = :table
                )
                """
            ),
            {"schema": schema, "table": table},
        ).scalar()
    )


def clear_i03_side_effects(bootstrap_engine: Engine) -> None:
    """Remove I03 local rows so later Alembic-cycle tests see an empty head."""

    with bootstrap_engine.begin() as conn:
        if _table_exists(conn, "learning", "attempts"):
            conn.execute(
                text(
                    "ALTER TABLE learning.attempt_response_items "
                    "DISABLE TRIGGER learning_attempt_response_items_in_progress_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE learning.submissions "
                    "DISABLE TRIGGER learning_submissions_immutable_delete"
                )
            )
            for table in _LEARNING_TABLES:
                conn.execute(
                    text(f"ALTER TABLE learning.{table} DISABLE ROW LEVEL SECURITY")
                )
            conn.execute(text("DELETE FROM learning.attempt_response_items"))
            conn.execute(text("DELETE FROM learning.submissions"))
            conn.execute(text("DELETE FROM learning.attempts"))
            conn.execute(
                text(
                    "ALTER TABLE learning.attempt_response_items "
                    "ENABLE TRIGGER learning_attempt_response_items_in_progress_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE learning.submissions "
                    "ENABLE TRIGGER learning_submissions_immutable_delete"
                )
            )
            for table in _LEARNING_TABLES:
                conn.execute(
                    text(f"ALTER TABLE learning.{table} ENABLE ROW LEVEL SECURITY")
                )
                conn.execute(
                    text(f"ALTER TABLE learning.{table} FORCE ROW LEVEL SECURITY")
                )

        if _table_exists(conn, "security", "audit_records"):
            conn.execute(
                text("ALTER TABLE security.audit_records DISABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text(
                    "ALTER TABLE security.audit_records "
                    "DISABLE TRIGGER audit_records_immutable_delete"
                )
            )
            conn.execute(
                text(
                    """
                    DELETE FROM security.audit_records
                    WHERE action IN (
                        'learning.attempt.start',
                        'learning.attempt.save_responses',
                        'learning.attempt.submit',
                        'teaching.assignment.create',
                        'teaching.assignment.due_update',
                        'teaching.assignment.close',
                        'teaching.assignment.cancel'
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE security.audit_records "
                    "ENABLE TRIGGER audit_records_immutable_delete"
                )
            )
            conn.execute(
                text("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text("ALTER TABLE security.audit_records FORCE ROW LEVEL SECURITY")
            )

        if _table_exists(conn, "api", "idempotency_records"):
            conn.execute(
                text("ALTER TABLE api.idempotency_records DISABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text(
                    """
                    DELETE FROM api.idempotency_records
                    WHERE operation LIKE 'learning_attempt_%'
                       OR operation LIKE 'teaching_assignment_%'
                    """
                )
            )
            conn.execute(
                text("ALTER TABLE api.idempotency_records ENABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text("ALTER TABLE api.idempotency_records FORCE ROW LEVEL SECURITY")
            )

        if _table_exists(conn, "teaching", "assignments"):
            conn.execute(
                text("ALTER TABLE teaching.assignments DISABLE ROW LEVEL SECURITY")
            )
            conn.execute(text("DELETE FROM teaching.assignments"))
            conn.execute(
                text("ALTER TABLE teaching.assignments ENABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text("ALTER TABLE teaching.assignments FORCE ROW LEVEL SECURITY")
            )


def _clear_i03_seeded_content(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.begin() as conn:
        if not _table_exists(conn, "content", "publications"):
            return
        for table in (
            "publications",
            "review_decisions",
            "content_versions",
            "contents",
        ):
            conn.execute(text(f"ALTER TABLE content.{table} DISABLE ROW LEVEL SECURITY"))
        conn.execute(text("DELETE FROM content.publications"))
        conn.execute(text("DELETE FROM content.review_decisions"))
        conn.execute(text("DELETE FROM content.content_versions"))
        conn.execute(text("DELETE FROM content.contents"))
        for table in (
            "publications",
            "review_decisions",
            "content_versions",
            "contents",
        ):
            conn.execute(text(f"ALTER TABLE content.{table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(
                text(f"ALTER TABLE content.{table} FORCE ROW LEVEL SECURITY")
            )


@pytest.fixture(autouse=True)
def clear_i03_side_effects_after_test(bootstrap_engine: Engine) -> Iterator[None]:
    yield
    clear_i03_side_effects(bootstrap_engine)
    try:
        _clear_i03_seeded_content(bootstrap_engine)
    except Exception:
        # Content FKs from other slices must not roll back Learning cleanup.
        return


_SCHEMA_BY_TYPE: dict[str, tuple[str, int]] = {
    WORKSHEET_CONTENT_TYPE: ("education.worksheet", 1),
    QUIZ_CONTENT_TYPE: ("education.quiz", 1),
    HOMEWORK_CONTENT_TYPE: ("education.homework", 1),
}


@dataclass(frozen=True, slots=True)
class PreparedAssignment:
    tenant_id: UUID
    teacher_id: UUID
    student_id: UUID
    content_id: UUID
    content_version_id: UUID
    assignment: TeachingAssignmentReadModel
    membership: DevelopmentSchoolContextLearnerMembershipReader

    @property
    def assignment_id(self) -> UUID:
        return self.assignment.assignment_id.value


def headers(
    tenant_id: UUID,
    *,
    idempotency_key: str | None = None,
    if_match: str | None = None,
) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id)}
    if idempotency_key is not None:
        out["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        out["If-Match"] = if_match
    return out


def etag(revision: int) -> str:
    return f'"r{revision}"'


def learner_payload(content_type: str) -> dict[str, object]:
    if content_type == QUIZ_CONTENT_TYPE:
        return valid_quiz_payload()
    if content_type == HOMEWORK_CONTENT_TYPE:
        return valid_homework_payload()
    return valid_worksheet_payload()


def seed_published_learner_content(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_type: str = WORKSHEET_CONTENT_TYPE,
    owner_id: UUID | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[UUID, UUID]:
    content_id = uuid.uuid7()
    version_id = uuid.uuid7()
    owner = owner_id or uuid.uuid7()
    schema_id, schema_version = _SCHEMA_BY_TYPE[content_type]
    body = payload if payload is not None else learner_payload(content_type)
    typed = ContentPayload.from_mapping(body)
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, :content_type, 'Title',
                    'Description', 'en-IN', 'APPROVED', :version_id,
                    :version_id, 1, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner,
                "content_type": content_type,
                "version_id": version_id,
                "now": TEACHING_FIXED_NOW,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :vid, :tid, :cid, 1, NULL,
                    :schema_id, :schema_version, CAST(:payload AS jsonb),
                    :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :actor
                )
                """
            ),
            {
                "vid": version_id,
                "tid": tenant_id,
                "cid": content_id,
                "schema_id": schema_id,
                "schema_version": schema_version,
                "payload": canonical_payload_json(typed.body),
                "sha": typed.sha256.value,
                "prov": json.dumps({}),
                "now": TEACHING_FIXED_NOW,
                "actor": owner,
            },
        )
        decision_id = uuid.uuid7()
        conn.execute(
            text(
                """
                INSERT INTO content.review_decisions (
                    review_decision_id, tenant_id, content_id, version_id,
                    decision, comment, decided_at, reviewer_principal_id,
                    effective_actor_id, correlation_id
                ) VALUES (
                    :did, :tid, :cid, :vid, 'APPROVE', NULL, :now, :actor, :actor, :corr
                )
                """
            ),
            {
                "did": decision_id,
                "tid": tenant_id,
                "cid": content_id,
                "vid": version_id,
                "now": TEACHING_FIXED_NOW,
                "actor": owner,
                "corr": uuid.uuid7(),
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO content.publications (
                    publication_id, tenant_id, content_id, version_id,
                    approval_decision_id, published_by_principal_id,
                    effective_actor_id, published_at, correlation_id
                ) VALUES (
                    :pid, :tid, :cid, :vid, :did, :actor, :actor, :now, :corr
                )
                """
            ),
            {
                "pid": uuid.uuid7(),
                "tid": tenant_id,
                "cid": content_id,
                "vid": version_id,
                "did": decision_id,
                "now": TEACHING_FIXED_NOW,
                "actor": owner,
                "corr": uuid.uuid7(),
            },
        )
    return content_id, version_id


def create_learner_assignment(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    idempotency_key: str,
    class_ref: str = CLASS_REF_5A,
    available_from: datetime | None = None,
    due_at: datetime | None = None,
) -> TeachingAssignmentReadModel:
    service: CreateTeachingAssignmentService = create_service(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        class_authority=SchoolContextClassAuthorityService(
            DevelopmentSchoolContextClassReader(
                tenant_id=tenant_id,
                teacher_principal_id=principal_id,
            )
        ),
    )
    return service.create(
        tenant_id,
        principal_id,
        CreateTeachingAssignmentCommand(
            content_id=content_id,
            content_version_id=content_version_id,
            class_ref=class_ref,
            available_from=available_from,
            due_at=due_at,
        ),
        idempotency_key=idempotency_key,
        event_context=event_context(principal_id),
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=FIXED_NOW,
    )


def close_assignment(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    assignment: TeachingAssignmentReadModel,
    idempotency_key: str,
) -> TeachingAssignmentReadModel:
    service = CloseTeachingAssignmentService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    return service.close(
        tenant_id,
        teacher_id,
        assignment_id=assignment.assignment_id,
        expected_aggregate_revision=assignment.aggregate_revision,
        idempotency_key=idempotency_key,
        event_context=event_context(teacher_id),
        audit_provenance=api_mutation_audit_provenance(teacher_id),
    )


def cancel_assignment(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    assignment: TeachingAssignmentReadModel,
    idempotency_key: str,
) -> TeachingAssignmentReadModel:
    service = CancelTeachingAssignmentService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    return service.cancel(
        tenant_id,
        teacher_id,
        assignment_id=assignment.assignment_id,
        expected_aggregate_revision=assignment.aggregate_revision,
        idempotency_key=idempotency_key,
        event_context=event_context(teacher_id),
        audit_provenance=api_mutation_audit_provenance(teacher_id),
    )


def update_assignment_due(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    teacher_id: UUID,
    assignment: TeachingAssignmentReadModel,
    due_at: datetime | None,
    idempotency_key: str,
) -> TeachingAssignmentReadModel:
    service = UpdateTeachingAssignmentDueService(
        SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    return service.update_due(
        tenant_id,
        teacher_id,
        assignment_id=assignment.assignment_id,
        expected_aggregate_revision=assignment.aggregate_revision,
        command=UpdateTeachingAssignmentDueCommand(due_at=due_at),
        idempotency_key=idempotency_key,
        event_context=event_context(teacher_id),
        audit_provenance=api_mutation_audit_provenance(teacher_id),
    )


def default_membership_reader(
    tenant_id: UUID,
) -> DevelopmentSchoolContextLearnerMembershipReader:
    return DevelopmentSchoolContextLearnerMembershipReader(tenant_id=tenant_id)


def build_student_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    learner_membership_reader: object | None = None,
) -> TestClient:
    reader = learner_membership_reader or default_membership_reader(tenant_id)
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
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
        school_context_class_reader=DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        ),
        learner_membership_reader=reader,  # type: ignore[arg-type]
        student_learning_uow_factory=SqlAlchemyStudentLearningCommandUnitOfWorkFactory(
            runtime_engine
        ),
    )
    return TestClient(app, raise_server_exceptions=False)


def prepare_class_5a_assignment(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    class_ref: str = CLASS_REF_5A,
    student_id: UUID = STUDENT_A_PRINCIPAL_ID,
    available_from: datetime | None = None,
    due_at: datetime | None = None,
    content_type: str = WORKSHEET_CONTENT_TYPE,
    membership: DevelopmentSchoolContextLearnerMembershipReader | None = None,
) -> PreparedAssignment:
    tenant_id = uuid.uuid7()
    teacher_id = uuid.uuid7()
    ensure_synthetic_student_principals(bootstrap_engine)
    seed_principal(bootstrap_engine, teacher_id, principal_kind=PrincipalKind.HUMAN)
    content_id, version_id = seed_published_learner_content(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_type=content_type,
        owner_id=teacher_id,
    )
    assignment = create_learner_assignment(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=teacher_id,
        content_id=content_id,
        content_version_id=version_id,
        idempotency_key=f"create-{uuid.uuid7()}",
        class_ref=class_ref,
        available_from=available_from,
        due_at=due_at,
    )
    reader = membership or default_membership_reader(tenant_id)
    return PreparedAssignment(
        tenant_id=tenant_id,
        teacher_id=teacher_id,
        student_id=student_id,
        content_id=content_id,
        content_version_id=version_id,
        assignment=assignment,
        membership=reader,
    )


def student_client(runtime_engine: Engine, prepared: PreparedAssignment) -> TestClient:
    return build_student_client(
        runtime_engine,
        prepared.tenant_id,
        prepared.student_id,
        learner_membership_reader=prepared.membership,
    )


def start_attempt(
    client: TestClient,
    prepared: PreparedAssignment,
    *,
    idempotency_key: str | None = None,
    assignment_id: UUID | None = None,
):
    aid = assignment_id or prepared.assignment_id
    return client.post(
        START_PATH.format(assignment_id=aid),
        headers=headers(
            prepared.tenant_id,
            idempotency_key=idempotency_key or f"start-{uuid.uuid7()}",
        ),
    )


def valid_mc_write() -> dict[str, Any]:
    return {
        "question_id": "q-1",
        "response_kind": "MULTIPLE_CHOICE",
        "choice_value": "1/2",
        "text_value": None,
        "boolean_value": None,
    }


def valid_short_write() -> dict[str, Any]:
    return {
        "question_id": "q-2",
        "response_kind": "SHORT_ANSWER",
        "choice_value": None,
        "text_value": "1/2",
        "boolean_value": None,
    }


def valid_tf_write() -> dict[str, Any]:
    return {
        "question_id": "q-3",
        "response_kind": "TRUE_FALSE",
        "choice_value": None,
        "text_value": None,
        "boolean_value": True,
    }


def save_responses(
    client: TestClient,
    prepared: PreparedAssignment,
    attempt_id: UUID,
    responses: list[dict[str, Any]],
    *,
    if_match: str,
    idempotency_key: str | None = None,
):
    return client.put(
        SAVE_PATH.format(attempt_id=attempt_id),
        headers=headers(
            prepared.tenant_id,
            idempotency_key=idempotency_key or f"save-{uuid.uuid7()}",
            if_match=if_match,
        ),
        json={"responses": responses},
    )


def submit_attempt(
    client: TestClient,
    prepared: PreparedAssignment,
    attempt_id: UUID,
    *,
    if_match: str,
    idempotency_key: str | None = None,
):
    return client.post(
        SUBMIT_PATH.format(attempt_id=attempt_id),
        headers=headers(
            prepared.tenant_id,
            idempotency_key=idempotency_key or f"submit-{uuid.uuid7()}",
            if_match=if_match,
        ),
    )


def fetch_outbox(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    event_type: str | None = None,
    aggregate_id: UUID | None = None,
) -> list[dict]:
    sql = """
        SELECT event_type, envelope, aggregate_revision, aggregate_id
        FROM integration.outbox_messages
        WHERE tenant_id = :tid
    """
    params: dict = {"tid": tenant_id}
    if event_type is not None:
        sql += " AND event_type = :etype"
        params["etype"] = event_type
    if aggregate_id is not None:
        sql += " AND aggregate_id = :aid"
        params["aid"] = aggregate_id
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_audit(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    action: str | None = None,
    resource_id: UUID | None = None,
) -> list[dict]:
    sql = """
        SELECT action, primary_resource_type, primary_resource_id,
               primary_resource_revision, resource_revision_before,
               resource_revision_after, related_resource_refs,
               executing_principal_id, effective_actor_id, execution_channel
        FROM security.audit_records
        WHERE tenant_id = :tid
    """
    params: dict = {"tid": tenant_id}
    if action is not None:
        sql += " AND action = :action"
        params["action"] = action
    if resource_id is not None:
        sql += " AND primary_resource_id = :rid"
        params["rid"] = resource_id
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_submissions(
    bootstrap_engine: Engine, *, tenant_id: UUID, attempt_id: UUID | None = None
) -> list[dict]:
    sql = """
        SELECT submission_id, attempt_id, teaching_assignment_id, learner_principal_id,
               content_id, content_version_id, class_ref, response_snapshot,
               assignment_revision_at_submit, due_at_at_submit, submitted_at
        FROM learning.submissions
        WHERE tenant_id = :tid
    """
    params: dict = {"tid": tenant_id}
    if attempt_id is not None:
        sql += " AND attempt_id = :aid"
        params["aid"] = attempt_id
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_attempts(
    bootstrap_engine: Engine, *, tenant_id: UUID, assignment_id: UUID | None = None
) -> list[dict]:
    sql = """
        SELECT attempt_id, learner_principal_id, teaching_assignment_id,
               lifecycle_state, aggregate_revision
        FROM learning.attempts
        WHERE tenant_id = :tid
    """
    params: dict = {"tid": tenant_id}
    if assignment_id is not None:
        sql += " AND teaching_assignment_id = :aid"
        params["aid"] = assignment_id
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(row) for row in rows]


def fetch_responses(
    bootstrap_engine: Engine, *, tenant_id: UUID, attempt_id: UUID
) -> list[dict]:
    sql = """
        SELECT question_id, response_kind, choice_value, text_value, boolean_value
        FROM learning.attempt_response_items
        WHERE tenant_id = :tid AND attempt_id = :aid
        ORDER BY question_id
    """
    with bootstrap_engine.connect() as conn:
        rows = conn.execute(
            text(sql), {"tid": tenant_id, "aid": attempt_id}
        ).mappings().all()
    return [dict(row) for row in rows]


class OnceThenDeniedMembershipReader:
    """I03-77: first current-membership check succeeds; later checks deny.

    Documents that AIEOS does not claim atomic ERP↔AIEOS ordering or 2PC.
    An already-authorized local command may still commit.
    """

    def __init__(self, tenant_id: UUID, class_ref: str = CLASS_REF_5A) -> None:
        self._tenant_id = tenant_id
        self._class_ref = class_ref
        self.calls = 0

    def list_current_memberships(
        self, tenant_id: UUID, learner_principal_id: UUID
    ) -> tuple[CurrentLearnerClassMembership, ...]:
        self.calls += 1
        if tenant_id != self._tenant_id:
            return ()
        if self.calls == 1:
            return (CurrentLearnerClassMembership(class_ref=self._class_ref),)
        return ()


class RaisingMembershipReader:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def list_current_memberships(self, tenant_id: UUID, learner_principal_id: UUID):
        raise self.exc
