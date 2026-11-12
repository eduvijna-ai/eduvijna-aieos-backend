"""PK-V1-HINT-T1b test-only harness: genuine 3-HUMAN Worksheet@2 publish → Learning."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.learner_principals import CLASS_REF_5A, STUDENT_A_PRINCIPAL_ID
from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.worksheet_hint_attestation_record import (
    RecordWorksheetLearnerHintAttestationService,
)
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2
from aieos.domains.education.worksheet_v2 import parse_worksheet_v2
from aieos.domains.content.domain.version import thaw_json_value
from aieos.development.learner_principals import ensure_synthetic_student_principals
from tests.domains.education.test_aieos_v1_hint_t1 import (
    StubHumanClassification as _StubHumanClassification,
    _worksheet_v2_payload,
    build_preparation_content_schema_registry as _build_registry,
)
from aieos.domains.content.application.http_append import HttpAppendContentVersionService
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    FIXED_NOW,
    _append_v1,
    _event_context,
    _guarded_service,
    _seed_worksheet_content,
)
from tests.fakes import AllowAssetReferenceValidation
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    PreparedAssignment,
    clear_i03_side_effects,
    create_learner_assignment,
    default_membership_reader,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    IDEMPOTENCY_RETENTION,
)
from tests.platform.security.authorization.helpers import seed_principal
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.domains.education.schema import WORKSHEET_SCHEMA_VERSION

FIXED_T1B_HINT = "Consider the denominator."


def _payload_has_hints(payload: dict[str, Any]) -> bool:
    questions = payload.get("questions")
    if not isinstance(questions, list):
        return False
    return any(
        isinstance(q, dict) and q.get("learner_hint") not in (None, "")
        for q in questions
    )


def _append_v2_http(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    content_id: ContentId,
    expected_aggregate_revision: AggregateRevision,
    payload: dict[str, Any],
):
    service = HttpAppendContentVersionService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        _build_registry(),
        AllowAssetReferenceValidation(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )
    return service.append(
        tenant_id,
        principal_id,
        content_id=content_id,
        expected_aggregate_revision=expected_aggregate_revision,
        schema_id=WORKSHEET_SCHEMA_ID,
        schema_version=WORKSHEET_SCHEMA_VERSION_V2,
        payload=payload,
        idempotency_key=f"t1b-http-v2-{uuid.uuid7()}",
        event_context=_event_context(principal_id),
        audit_provenance=api_mutation_audit_provenance(principal_id),
        now=FIXED_NOW,
    )


def _positive_outcomes(question_ids: list[str]) -> list[dict[str, object]]:
    return [
        {
            "question_id": qid,
            "hard_block_clear": True,
            "human_review_pass": True,
        }
        for qid in question_ids
    ]


def _review_service(runtime_engine: Engine) -> ReviewCommandService:
    return ReviewCommandService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowReviewAuthorization(),
        AllowReviewCommentPolicy(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        principal_classification=_StubHumanClassification(),
    )


def _attest_service(runtime_engine: Engine) -> RecordWorksheetLearnerHintAttestationService:
    return RecordWorksheetLearnerHintAttestationService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowReviewAuthorization(),
        _StubHumanClassification(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _publish_service(runtime_engine: Engine) -> PublishContentService:
    return PublishContentService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowPublicationAuthorization(),
        AllowPublicationGovernance(),
        AllowAssetCurrentGovernance(),
        _build_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        principal_classification=_StubHumanClassification(),
    )


def _hinted_question_ids(
    runtime_engine: Engine, tenant_id: uuid.UUID, version_id: ContentVersionId
) -> list[str]:
    with SqlAlchemyContentUnitOfWorkFactory(runtime_engine)(tenant_id) as uow:
        version = uow.versions.get(version_id)
        assert version is not None
        worksheet = parse_worksheet_v2(dict(thaw_json_value(version.payload.body)))
        return [q.id for q in worksheet.questions if q.learner_hint is not None]


@dataclass(frozen=True, slots=True)
class GovernedWorksheetV2Publication:
    tenant_id: UUID
    author_id: UUID
    reviewer_id: UUID
    publisher_id: UUID
    content_id: UUID
    content_version_id: UUID


def publish_worksheet_v1_via_application_services(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    content_id: ContentId,
    tenant_id: uuid.UUID,
    author_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    publisher_id: uuid.UUID,
    aggregate_revision: AggregateRevision,
    version_id: ContentVersionId,
) -> AggregateRevision:
    _ = bootstrap_engine
    review = _review_service(runtime_engine)
    submitted = review.submit(
        tenant_id,
        author_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=aggregate_revision,
        idempotency_key=f"t1b-v1-submit-{uuid.uuid7()}",
        event_context=_event_context(author_id),
        audit_provenance=api_mutation_audit_provenance(author_id),
        now=FIXED_NOW,
    )
    approved = review.approve(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=submitted.aggregate_revision,
        reason_code=None,
        comment=None,
        idempotency_key=f"t1b-v1-approve-{uuid.uuid7()}",
        event_context=_event_context(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=FIXED_NOW,
    )
    assert approved.decision == "APPROVE"
    published = _publish_service(runtime_engine).publish(
        tenant_id,
        publisher_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=approved.aggregate_revision,
        idempotency_key=f"t1b-v1-publish-{uuid.uuid7()}",
        event_context=_event_context(publisher_id),
        audit_provenance=api_mutation_audit_provenance(publisher_id),
        now=FIXED_NOW,
    )
    assert published.version_id == version_id
    return published.aggregate_revision


def _current_content_revision(
    bootstrap_engine: Engine, *, tenant_id: UUID, content_id: UUID
) -> AggregateRevision:
    with bootstrap_engine.connect() as conn:
        rev = conn.execute(
            text(
                """
                SELECT aggregate_revision FROM content.contents
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {"tid": tenant_id, "cid": content_id},
        ).scalar_one()
    return AggregateRevision(int(rev))


def _complete_v2_publish(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    author_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    publisher_id: uuid.UUID,
    content_id: ContentId,
    version_read,
    revision: AggregateRevision,
    class_ref: str,
    require_attestation: bool,
) -> ContentVersionId:
    _ = class_ref
    review = _review_service(runtime_engine)
    submitted = review.submit(
        tenant_id,
        author_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=revision,
        idempotency_key=f"t1b-v2-submit-{uuid.uuid7()}",
        event_context=_event_context(author_id),
        audit_provenance=api_mutation_audit_provenance(author_id),
        now=FIXED_NOW,
    )
    if require_attestation:
        hinted_ids = _hinted_question_ids(
            runtime_engine, tenant_id, version_read.version_id
        )
        _attest_service(runtime_engine).record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=submitted.aggregate_revision,
            per_question_outcomes=_positive_outcomes(hinted_ids),
            idempotency_key=f"t1b-v2-attest-{uuid.uuid7()}",
            event_context=_event_context(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=FIXED_NOW,
        )
    approved = review.approve(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=submitted.aggregate_revision,
        reason_code=None,
        comment=None,
        idempotency_key=f"t1b-v2-approve-{uuid.uuid7()}",
        event_context=_event_context(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=FIXED_NOW,
    )
    assert approved.decision == "APPROVE"
    published = _publish_service(runtime_engine).publish(
        tenant_id,
        publisher_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=approved.aggregate_revision,
        idempotency_key=f"t1b-v2-publish-{uuid.uuid7()}",
        event_context=_event_context(publisher_id),
        audit_provenance=api_mutation_audit_provenance(publisher_id),
        now=FIXED_NOW,
    )
    assert published.version_id == version_read.version_id
    assert_publication_evidence(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_id=content_id.value,
        version_id=version_read.version_id.value,
        author_id=author_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
        expect_attestation=require_attestation,
    )
    return version_read.version_id


def publish_worksheet_v2_three_human(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    payload: dict[str, Any] | None = None,
    class_ref: str = CLASS_REF_5A,
) -> GovernedWorksheetV2Publication:
    tenant_id = uuid.uuid7()
    author_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    publisher_id = uuid.uuid7()
    for principal_id in (author_id, reviewer_id, publisher_id):
        seed_principal(bootstrap_engine, principal_id, principal_kind=PrincipalKind.HUMAN)

    content_id = _seed_worksheet_content(
        bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
    )
    _, rev_after_v1 = _append_v1(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=author_id,
        content_id=content_id,
    )

    body = payload if payload is not None else _worksheet_v2_payload(hint=FIXED_T1B_HINT)
    require_attestation = _payload_has_hints(body)
    if require_attestation:
        version_read, revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=body,
            authoring_class_ref=class_ref,
            idempotency_key=f"t1b-v2-append-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
    else:
        version_read, revision = _append_v2_http(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=body,
        )
    _complete_v2_publish(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        author_id=author_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
        content_id=content_id,
        version_read=version_read,
        revision=revision,
        class_ref=class_ref,
        require_attestation=require_attestation,
    )
    return GovernedWorksheetV2Publication(
        tenant_id=tenant_id,
        author_id=author_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
        content_id=content_id.value,
        content_version_id=version_read.version_id.value,
    )


def assert_publication_evidence(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_id: UUID,
    version_id: UUID,
    author_id: UUID,
    reviewer_id: UUID,
    publisher_id: UUID,
    expect_attestation: bool = True,
) -> None:
    with bootstrap_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT cv.schema_id, cv.schema_version, cv.created_by_principal_id,
                       rd.reviewer_principal_id, p.published_by_principal_id,
                       att.payload_digest IS NOT NULL AS has_attestation
                FROM content.content_versions cv
                JOIN content.review_decisions rd
                  ON rd.tenant_id = cv.tenant_id
                 AND rd.version_id = cv.version_id
                 AND rd.decision = 'APPROVE'
                JOIN content.publications p
                  ON p.tenant_id = cv.tenant_id
                 AND p.version_id = cv.version_id
                LEFT JOIN content.worksheet_learner_hint_version_attestations att
                  ON att.tenant_id = cv.tenant_id
                 AND att.version_id = cv.version_id
                WHERE cv.tenant_id = :tid
                  AND cv.content_id = :cid
                  AND cv.version_id = :vid
                """
            ),
            {"tid": tenant_id, "cid": content_id, "vid": version_id},
        ).one()
        assert row.schema_id == WORKSHEET_SCHEMA_ID
        assert int(row.schema_version) == WORKSHEET_SCHEMA_VERSION_V2
        assert row.created_by_principal_id == author_id
        assert row.reviewer_principal_id == reviewer_id
        assert row.published_by_principal_id == publisher_id
        assert bool(row.has_attestation) is expect_attestation


def prepare_governed_worksheet_v2_assignment(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    payload: dict[str, Any] | None = None,
    student_id: UUID = STUDENT_A_PRINCIPAL_ID,
) -> PreparedAssignment:
    ensure_synthetic_student_principals(bootstrap_engine)
    publication = publish_worksheet_v2_three_human(
        runtime_engine, bootstrap_engine, payload=payload
    )
    teacher_id = publication.author_id
    assignment = create_learner_assignment(
        runtime_engine,
        tenant_id=publication.tenant_id,
        principal_id=teacher_id,
        content_id=publication.content_id,
        content_version_id=publication.content_version_id,
        idempotency_key=f"t1b-create-{uuid.uuid7()}",
        class_ref=CLASS_REF_5A,
    )
    reader = default_membership_reader(publication.tenant_id)
    return PreparedAssignment(
        tenant_id=publication.tenant_id,
        teacher_id=teacher_id,
        student_id=student_id,
        content_id=publication.content_id,
        content_version_id=publication.content_version_id,
        assignment=assignment,
        membership=reader,
    )


@dataclass(frozen=True, slots=True)
class ExactPinAssignments:
    v1_assignment: PreparedAssignment
    v2_assignment: PreparedAssignment
    v1_content_version_id: UUID
    v2_content_version_id: UUID


def prepare_exact_pin_v1_then_v2_assignments(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    v2_payload: dict[str, Any] | None = None,
) -> ExactPinAssignments:
    ensure_synthetic_student_principals(bootstrap_engine)
    tenant_id = uuid.uuid7()
    author_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    publisher_id = uuid.uuid7()
    for principal_id in (author_id, reviewer_id, publisher_id):
        seed_principal(bootstrap_engine, principal_id, principal_kind=PrincipalKind.HUMAN)

    content_id = _seed_worksheet_content(
        bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
    )
    v1_version_id, rev_after_v1 = _append_v1(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=author_id,
        content_id=content_id,
    )
    publish_worksheet_v1_via_application_services(
        runtime_engine,
        bootstrap_engine,
        content_id=content_id,
        tenant_id=tenant_id,
        author_id=author_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
        aggregate_revision=rev_after_v1,
        version_id=v1_version_id,
    )
    reader = default_membership_reader(tenant_id)
    v1_assignment = create_learner_assignment(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=author_id,
        content_id=content_id.value,
        content_version_id=v1_version_id.value,
        idempotency_key=f"t1b-pin-v1-{uuid.uuid7()}",
        class_ref=CLASS_REF_5A,
    )
    prepared_v1 = PreparedAssignment(
        tenant_id=tenant_id,
        teacher_id=author_id,
        student_id=STUDENT_A_PRINCIPAL_ID,
        content_id=content_id.value,
        content_version_id=v1_version_id.value,
        assignment=v1_assignment,
        membership=reader,
    )

    expected_rev = _current_content_revision(
        bootstrap_engine, tenant_id=tenant_id, content_id=content_id.value
    )
    body = v2_payload or _worksheet_v2_payload(hint=FIXED_T1B_HINT)
    require_attestation = _payload_has_hints(body)
    if require_attestation:
        version_read, revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=expected_rev,
            payload=body,
            authoring_class_ref=CLASS_REF_5A,
            idempotency_key=f"t1b-pin-v2-append-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
    else:
        version_read, revision = _append_v2_http(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
            expected_aggregate_revision=expected_rev,
            payload=body,
        )
    _complete_v2_publish(
        runtime_engine,
        bootstrap_engine,
        tenant_id=tenant_id,
        author_id=author_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
        content_id=content_id,
        version_read=version_read,
        revision=revision,
        class_ref=CLASS_REF_5A,
        require_attestation=require_attestation,
    )
    v2_assignment = create_learner_assignment(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=author_id,
        content_id=content_id.value,
        content_version_id=version_read.version_id.value,
        idempotency_key=f"t1b-pin-v2-{uuid.uuid7()}",
        class_ref=CLASS_REF_5A,
    )
    prepared_v2 = PreparedAssignment(
        tenant_id=tenant_id,
        teacher_id=author_id,
        student_id=STUDENT_A_PRINCIPAL_ID,
        content_id=content_id.value,
        content_version_id=version_read.version_id.value,
        assignment=v2_assignment,
        membership=reader,
    )
    return ExactPinAssignments(
        v1_assignment=prepared_v1,
        v2_assignment=prepared_v2,
        v1_content_version_id=v1_version_id.value,
        v2_content_version_id=version_read.version_id.value,
    )


def assert_v1_publication_evidence(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    version_id: UUID,
) -> None:
    with bootstrap_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT schema_id, schema_version
                FROM content.content_versions
                WHERE tenant_id = :tid AND version_id = :vid
                """
            ),
            {"tid": tenant_id, "vid": version_id},
        ).one()
        assert row.schema_id == WORKSHEET_SCHEMA_ID
        assert int(row.schema_version) == WORKSHEET_SCHEMA_VERSION


def clear_t1b_side_effects(bootstrap_engine: Engine) -> None:
    """Learning-side cleanup only; immutable Content publication rows are left intact."""
    clear_i03_side_effects(bootstrap_engine)
