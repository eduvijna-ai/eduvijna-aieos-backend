"""CI03-G2-I01 reviewer assignment authority — real PG18 proofs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.coherent_school_context import development_coherent_school_context_provider
from aieos.development.reviewer_eligibility import development_reviewer_eligibility_reader
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthorityService,
)
from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ReviewAssignmentConflict,
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
)
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_ASSIGN,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.domains.content.application.review_assignment_denial_audit import (
    CONTENT_REVIEW_ASSIGN_DENIED,
)
from aieos.platform.security.audit.persistence.authorization_denial_records import (
    SqlAlchemyAuthorizationDenialRecords,
)
from aieos.domains.content.application.review_assignment_guard import (
    ReviewerUseGuardContext,
    ReviewerUseGuardDependencies,
    ReviewerUseMode,
    enforce_reviewer_use_guard,
)
from aieos.domains.content.application.review_assignment_issue import (
    IssueReviewAssignmentService,
)
from aieos.domains.content.application.review_assignment_revoke import (
    RevokeReviewAssignmentService,
)
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibilityService,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelReviewAssignmentAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.content_adapters import AIEOS_CONTENT_CAPABILITIES
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    seed_worksheet_in_review_governed,
)
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.ci03_g2_i01

CLASS_REF = "class-5a-ci03-g2"
_NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _seed_worksheet_in_review(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> tuple[ContentId, ContentVersionId]:
    content_id, version_id = seed_worksheet_in_review_governed(
        runtime_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
        class_ref=CLASS_REF,
        now=_NOW,
    )
    return ContentId(content_id), ContentVersionId(version_id)


def _services(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    *,
    clock=None,
):
    kernel = AuthorizationKernel(
        runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
    )
    provider = development_coherent_school_context_provider(tenant_id=tenant_id)
    uow_factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
    assign_auth = KernelReviewAssignmentAuthorization(kernel)
    review_auth = KernelReviewAuthorization(kernel)
    classification = CurrentPrincipalClassificationAuthority(runtime_engine)
    class_authority = SchoolContextClassAuthorityService(provider)
    reviewer_eligibility = SchoolContextReviewerEligibilityService(
        development_reviewer_eligibility_reader(provider)
    )
    denial = SqlAlchemyAuthorizationDenialRecords(runtime_engine)
    retention = timedelta(hours=24)
    effective_clock = clock or (lambda: _NOW)  # noqa: E731
    issue = IssueReviewAssignmentService(
        uow_factory,
        assign_auth,
        review_auth,
        classification,
        class_authority,
        reviewer_eligibility,
        denial,
        idempotency_retention=retention,
        clock=effective_clock,
    )
    revoke = RevokeReviewAssignmentService(
        uow_factory,
        assign_auth,
        review_auth,
        classification,
        class_authority,
        denial,
        idempotency_retention=retention,
        clock=effective_clock,
    )
    return issue, revoke, provider, uow_factory, assign_auth, review_auth, class_authority, reviewer_eligibility


def _event(owner_id: uuid.UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        effective_actor_id=owner_id,
    )


def _denial_count(engine: Engine, tenant_id: uuid.UUID, action: str) -> int:
    with engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.authorization_denial_records
                    WHERE tenant_id = :tid AND action = :action
                    """
                ),
                {"tid": tenant_id, "action": action},
            ).scalar_one()
        )


class TestCi03G2I01AssignmentPostgres:
    def test_issue_revoke_idempotent_and_slot_rules(
        self, bootstrap_engine: Engine, runtime_engine: Engine, postgres18: str
    ) -> None:
        _ = postgres18
        provision_runtime_grants(bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_ASSIGN, CONTENT_REVIEW_SUBMIT),
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        issue, revoke, provider, *_ = _services(runtime_engine, tenant_id)
        provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
        provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
        content_id, version_id = _seed_worksheet_in_review(
            runtime_engine, tenant_id=tenant_id, owner_id=owner_id
        )
        ctx = _event(owner_id)
        audit = api_mutation_audit_provenance(owner_id)
        assignment_id = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="idem-issue-1",
            event_context=ctx,
            audit_provenance=audit,
        )
        again = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="idem-issue-1",
            event_context=ctx,
            audit_provenance=audit,
        )
        assert again == assignment_id
        reviewer2 = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer2,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        provider.set_reviewer_class_eligibility(reviewer2, (CLASS_REF,))
        with pytest.raises(ReviewAssignmentConflict):
            issue.issue(
                tenant_id,
                owner_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=reviewer2,
                idempotency_key="idem-issue-2",
                event_context=ctx,
                audit_provenance=audit,
            )
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key="idem-revoke-1",
            event_context=ctx,
            audit_provenance=audit,
        )
        reassigned = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="idem-issue-3",
            event_context=ctx,
            audit_provenance=audit,
        )
        assert reassigned != assignment_id

    def test_revoke_after_expiry_allows_reissue(
        self, bootstrap_engine: Engine, runtime_engine: Engine, postgres18: str
    ) -> None:
        _ = postgres18
        provision_runtime_grants(bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_ASSIGN, CONTENT_REVIEW_SUBMIT),
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        expiry = _NOW + timedelta(days=8)
        issue, _revoke, provider, *_ = _services(runtime_engine, tenant_id)
        provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
        provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
        content_id, version_id = _seed_worksheet_in_review(
            runtime_engine, tenant_id=tenant_id, owner_id=owner_id
        )
        ctx = _event(owner_id)
        audit = api_mutation_audit_provenance(owner_id)
        assignment_id = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="expiry-issue",
            event_context=ctx,
            audit_provenance=audit,
        )
        issue_expired, revoke, *_ = _services(
            runtime_engine, tenant_id, clock=lambda: expiry
        )
        reassigned = issue_expired.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="expiry-reissue",
            event_context=ctx,
            audit_provenance=audit,
        )
        assert reassigned != assignment_id
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key="stale-revoke",
            event_context=ctx,
            audit_provenance=audit,
        )

    def test_already_revoked_records_idempotency_without_duplicate_audit(
        self, bootstrap_engine: Engine, runtime_engine: Engine, postgres18: str
    ) -> None:
        _ = postgres18
        provision_runtime_grants(bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_ASSIGN, CONTENT_REVIEW_SUBMIT),
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        issue, revoke, provider, *_ = _services(runtime_engine, tenant_id)
        provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
        provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
        content_id, version_id = _seed_worksheet_in_review(
            runtime_engine, tenant_id=tenant_id, owner_id=owner_id
        )
        ctx = _event(owner_id)
        audit = api_mutation_audit_provenance(owner_id)
        assignment_id = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="idem-audit",
            event_context=ctx,
            audit_provenance=audit,
        )
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key="idem-revoke-main",
            event_context=ctx,
            audit_provenance=audit,
        )
        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            before = conn.execute(
                text(
                    "SELECT count(*) FROM security.audit_records "
                    "WHERE action = 'content.review.revoke'"
                )
            ).scalar_one()
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key="idem-revoke-repeat",
            event_context=ctx,
            audit_provenance=audit,
        )
        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            after = conn.execute(
                text(
                    "SELECT count(*) FROM security.audit_records "
                    "WHERE action = 'content.review.revoke'"
                )
            ).scalar_one()
            idem = conn.execute(
                text(
                    "SELECT count(*) FROM api.idempotency_records "
                    "WHERE operation = 'content_review_revoke.v1'"
                )
            ).scalar_one()
        assert after == before
        assert int(idem) == 2

    def test_fail_closed_without_assign_grant_and_denial_audit(
        self, bootstrap_engine: Engine, runtime_engine: Engine, postgres18: str
    ) -> None:
        _ = postgres18
        provision_runtime_grants(bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_SUBMIT,),
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        issue, _revoke, provider, *_ = _services(runtime_engine, tenant_id)
        provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
        provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
        content_id, version_id = _seed_worksheet_in_review(
            runtime_engine, tenant_id=tenant_id, owner_id=owner_id
        )
        ctx = _event(owner_id)
        with pytest.raises(ReviewAssignmentForbidden):
            issue.issue(
                tenant_id,
                owner_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=reviewer_id,
                idempotency_key="deny",
                event_context=ctx,
                audit_provenance=api_mutation_audit_provenance(owner_id),
            )
        assert _denial_count(runtime_engine, tenant_id, CONTENT_REVIEW_ASSIGN_DENIED) == 1
        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            reason = conn.execute(
                text(
                    "SELECT reason_code FROM security.authorization_denial_records "
                    "WHERE tenant_id = :tid LIMIT 1"
                ),
                {"tid": tenant_id},
            ).scalar_one()
        assert reason == "assign.capability.denied"

    def test_guard_fail_closed_terminal_and_protected(
        self, bootstrap_engine: Engine, runtime_engine: Engine, postgres18: str
    ) -> None:
        _ = postgres18
        provision_runtime_grants(bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_ASSIGN, CONTENT_REVIEW_SUBMIT),
        )
        seed_active_authority(
            bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        issue, revoke, provider, uow_factory, assign_auth, review_auth, class_authority, reviewer_eligibility = _services(
            runtime_engine, tenant_id
        )
        classification = CurrentPrincipalClassificationAuthority(runtime_engine)
        provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
        provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
        content_id, version_id = _seed_worksheet_in_review(
            runtime_engine, tenant_id=tenant_id, owner_id=owner_id
        )
        ctx = _event(owner_id)
        assignment_id = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="guard-issue",
            event_context=ctx,
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        deps = ReviewerUseGuardDependencies(
            assign_authorization=assign_auth,
            authorization=review_auth,
            principal_classification=classification,
            reviewer_eligibility=reviewer_eligibility,
            issuer_class_authority=class_authority,
            clock=lambda: _NOW,
        )
        wrong_actor_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=uuid.uuid7(),
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewAssignmentForbidden):
                enforce_reviewer_use_guard(uow, ctx=wrong_actor_ctx, deps=deps)
        guard_ctx_ok = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
        )
        with uow_factory(tenant_id) as uow:
            active = enforce_reviewer_use_guard(uow, ctx=guard_ctx_ok, deps=deps)
            assert active.assignment_id == assignment_id
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key="guard-revoke",
            event_context=ctx,
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        terminal_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            mode=ReviewerUseMode.TERMINAL_RECEIPT,
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewAssignmentNotAllowed):
                enforce_reviewer_use_guard(uow, ctx=terminal_ctx, deps=deps)
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewAssignmentConflict):
                enforce_reviewer_use_guard(uow, ctx=guard_ctx_ok, deps=deps)