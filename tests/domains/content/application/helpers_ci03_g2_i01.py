"""Governed worksheet@2 IN_REVIEW fixtures and I02 mutation wiring for CI03-G2 PG18 tests."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine

from aieos.development.coherent_school_context import (
    DevelopmentCoherentSchoolContextProvider,
    development_coherent_school_context_provider,
)
from aieos.development.reviewer_eligibility import development_reviewer_eligibility_reader
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.review import ReviewCommandService
from aieos.domains.content.application.review_assignment_bound_mutations import (
    AssignmentBoundMutationDependencies,
)
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibilityService,
)
from aieos.domains.content.application.worksheet_hint_attestation_record import (
    RecordWorksheetLearnerHintAttestationService,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthorityService
from aieos.platform.security.audit.persistence.authorization_denial_records import (
    SqlAlchemyAuthorizationDenialRecords,
)
from aieos.platform.security.authorization import (
    CurrentPrincipalClassificationAuthority,
    KernelReviewAssignmentAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.content_adapters import AIEOS_CONTENT_CAPABILITIES
from aieos.platform.security.authorization import AuthorizationKernel
from tests.domains.education.test_aieos_v1_hint_t1 import build_preparation_content_schema_registry
from tests.fakes import (
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    AllowAssetCurrentGovernance,
    IDEMPOTENCY_RETENTION,
)
from tests.domains.education.test_aieos_v1_hint_t1 import _worksheet_v2_payload
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    _append_v1,
    _event_context,
    _guarded_service,
    _seed_worksheet_content,
)
from tests.domains.education.test_aieos_v1_hint_t2 import _review_service
from aieos.domains.content.application.audit import api_mutation_audit_provenance


def seed_worksheet_in_review_governed(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    class_ref: str,
    now: datetime,
) -> tuple[ContentId, ContentVersionId]:
    """Author v1, guarded hint@2 append, submit for review — no version SQL."""
    content_id = _seed_worksheet_content(
        bootstrap_engine, tenant_id=tenant_id, owner_principal_id=owner_id
    )
    _, rev_after_v1 = _append_v1(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        content_id=content_id,
    )
    version_read, revision = _guarded_service(runtime_engine).append(
        tenant_id,
        owner_id,
        content_id=content_id,
        expected_aggregate_revision=rev_after_v1,
        payload=_worksheet_v2_payload(hint="Think step by step."),
        authoring_class_ref=class_ref,
        idempotency_key=f"ci03-g2-hint-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=now,
    )
    _review_service(runtime_engine).submit(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=revision,
        idempotency_key=f"ci03-g2-submit-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=now,
    )
    return content_id, version_read.version_id


def assignment_bound_deps(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    *,
    clock: Callable[[], datetime] | None = None,
    school_context_provider: DevelopmentCoherentSchoolContextProvider | None = None,
) -> AssignmentBoundMutationDependencies:
    provider = school_context_provider or development_coherent_school_context_provider(
        tenant_id=tenant_id
    )
    kernel = AuthorizationKernel(runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES)
    effective_clock = clock or (lambda: datetime.now(UTC))
    return AssignmentBoundMutationDependencies(
        assign_authorization=KernelReviewAssignmentAuthorization(kernel),
        review_authorization=KernelReviewAuthorization(kernel),
        principal_classification=CurrentPrincipalClassificationAuthority(runtime_engine),
        reviewer_eligibility=SchoolContextReviewerEligibilityService(
            development_reviewer_eligibility_reader(provider)
        ),
        issuer_class_authority=SchoolContextClassAuthorityService(provider),
        denial_audit=SqlAlchemyAuthorizationDenialRecords(runtime_engine),
        clock=effective_clock,
    )


def integrated_attest_service(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    *,
    clock: Callable[[], datetime] | None = None,
    school_context_provider: DevelopmentCoherentSchoolContextProvider | None = None,
) -> RecordWorksheetLearnerHintAttestationService:
    return RecordWorksheetLearnerHintAttestationService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowReviewAuthorization(),
        CurrentPrincipalClassificationAuthority(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        assignment_bound=assignment_bound_deps(
            runtime_engine,
            tenant_id,
            clock=clock,
            school_context_provider=school_context_provider,
        ),
    )


def integrated_review_service(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    *,
    clock: Callable[[], datetime] | None = None,
    school_context_provider: DevelopmentCoherentSchoolContextProvider | None = None,
) -> ReviewCommandService:
    return ReviewCommandService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowReviewAuthorization(),
        AllowReviewCommentPolicy(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        principal_classification=CurrentPrincipalClassificationAuthority(runtime_engine),
        assignment_bound=assignment_bound_deps(
            runtime_engine,
            tenant_id,
            clock=clock,
            school_context_provider=school_context_provider,
        ),
    )


def integrated_publish_service(runtime_engine: Engine) -> PublishContentService:
    return PublishContentService(
        SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        AllowPublicationAuthorization(),
        AllowPublicationGovernance(),
        AllowAssetCurrentGovernance(),
        build_preparation_content_schema_registry(),
        idempotency_retention=timedelta(hours=24),
        principal_classification=CurrentPrincipalClassificationAuthority(runtime_engine),
    )