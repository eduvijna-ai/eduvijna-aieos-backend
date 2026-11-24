"""CI03-G2-I01 reviewer assignment authority — real PG18 proofs."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.coherent_school_context import development_coherent_school_context_provider
from aieos.development.reviewer_eligibility import development_reviewer_eligibility_reader
from aieos.development.school_context import development_class_authority
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
    SqlAlchemyReviewAssignmentDenialAudit,
)
from aieos.domains.content.application.review_assignment_guard import (
    ReviewerUseGuardContext,
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
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID
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
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.ci03_g2_i01

CLASS_REF = "class-5a-ci03-g2"
SHA = "a" * 64
_NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def _hint_v2_payload() -> dict:
    return {
        "title": "CI03 G2 worksheet",
        "questions": [
            {
                "question_id": "q1",
                "type": "short_answer",
                "stem": "2+2?",
                "answer": "4",
                "explanation": "basic",
                "learner_hint": "count on fingers",
            }
        ],
    }


def _seed_worksheet_in_review(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
) -> tuple[ContentId, ContentVersionId]:
    content_id = uuid.uuid7()
    parent_version_id = uuid.uuid7()
    version_id = uuid.uuid7()
    prov = {
        "kind": "worksheet_learner_hint",
        "schema_version": 1,
        "authoring_class_ref": CLASS_REF,
        "authoring_principal_id": str(owner_id),
    }
    with runtime_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, 'education.worksheet', 'WS',
                    '', 'en-IN', 'IN_REVIEW', :version_id,
                    NULL, 2, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner_id,
                "version_id": version_id,
                "now": _NOW,
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
                    :version_id, :tenant_id, :content_id, 1, NULL,
                    :schema_id, 1, '{"marker":"v1"}'::jsonb, :sha, 'HUMAN',
                    NULL, :now, :owner
                )
                """
            ),
            {
                "version_id": parent_version_id,
                "tenant_id": tenant_id,
                "content_id": content_id,
                "schema_id": WORKSHEET_SCHEMA_ID,
                "sha": SHA,
                "now": _NOW,
                "owner": owner_id,
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
                    :version_id, :tenant_id, :content_id, 2, :parent,
                    :schema_id, 2, CAST(:payload AS jsonb), :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :owner
                )
                """
            ),
            {
                "version_id": version_id,
                "tenant_id": tenant_id,
                "content_id": content_id,
                "parent": parent_version_id,
                "schema_id": WORKSHEET_SCHEMA_ID,
                "payload": json.dumps(_hint_v2_payload()),
                "sha": SHA,
                "prov": json.dumps(prov),
                "now": _NOW,
                "owner": owner_id,
            },
        )
    return ContentId(content_id), ContentVersionId(version_id)


def _services(runtime_engine: Engine, tenant_id: uuid.UUID):
    kernel = AuthorizationKernel(
        runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
    )
    provider = development_coherent_school_context_provider(tenant_id=tenant_id)
    uow_factory = SqlAlchemyContentUnitOfWorkFactory(runtime_engine)
    assign_auth = KernelReviewAssignmentAuthorization(kernel)
    review_auth = KernelReviewAuthorization(kernel)
    classification = CurrentPrincipalClassificationAuthority(runtime_engine)
    class_authority = development_class_authority(provider)
    reviewer_eligibility = SchoolContextReviewerEligibilityService(
        development_reviewer_eligibility_reader(provider)
    )
    denial = SqlAlchemyReviewAssignmentDenialAudit(runtime_engine)
    retention = timedelta(hours=24)
    issue = IssueReviewAssignmentService(
        uow_factory,
        assign_auth,
        review_auth,
        classification,
        class_authority,
        reviewer_eligibility,
        idempotency_retention=retention,
        denial_audit=denial,
    )
    revoke = RevokeReviewAssignmentService(
        uow_factory,
        assign_auth,
        review_auth,
        classification,
        class_authority,
        idempotency_retention=retention,
        denial_audit=denial,
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
            now=_NOW,
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
            now=_NOW,
        )
        assert again == assignment_id
        with pytest.raises(ReviewAssignmentConflict):
            issue.issue(
                tenant_id,
                owner_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=uuid.uuid7(),
                idempotency_key="idem-issue-2",
                event_context=ctx,
                audit_provenance=audit,
                now=_NOW,
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
            now=_NOW + timedelta(minutes=1),
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
            now=_NOW + timedelta(minutes=2),
        )
        assert reassigned != assignment_id

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
                now=_NOW,
            )
        assert _denial_count(runtime_engine, tenant_id, CONTENT_REVIEW_ASSIGN_DENIED) == 1

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
            now=_NOW,
        )
        guard_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            class_ref="wrong-class",
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewAssignmentForbidden):
                enforce_reviewer_use_guard(
                    uow,
                    ctx=guard_ctx,
                    assign_authorization=assign_auth,
                    authorization=review_auth,
                    principal_classification=classification,
                    reviewer_eligibility=reviewer_eligibility,
                    issuer_class_authority=class_authority,
                    now=_NOW,
                )
        guard_ctx_ok = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            class_ref=CLASS_REF,
        )
        with uow_factory(tenant_id) as uow:
            active = enforce_reviewer_use_guard(
                uow,
                ctx=guard_ctx_ok,
                assign_authorization=assign_auth,
                authorization=review_auth,
                principal_classification=classification,
                reviewer_eligibility=reviewer_eligibility,
                issuer_class_authority=class_authority,
                now=_NOW,
            )
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
            now=_NOW + timedelta(minutes=5),
        )
        terminal_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            class_ref=CLASS_REF,
            mode=ReviewerUseMode.TERMINAL_RECEIPT,
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewAssignmentNotAllowed):
                enforce_reviewer_use_guard(
                    uow,
                    ctx=terminal_ctx,
                    assign_authorization=assign_auth,
                    authorization=review_auth,
                    principal_classification=classification,
                    reviewer_eligibility=reviewer_eligibility,
                    issuer_class_authority=class_authority,
                    now=_NOW + timedelta(minutes=6),
                )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewAssignmentConflict):
                enforce_reviewer_use_guard(
                    uow,
                    ctx=guard_ctx,
                    assign_authorization=assign_auth,
                    authorization=review_auth,
                    principal_classification=classification,
                    reviewer_eligibility=reviewer_eligibility,
                    issuer_class_authority=class_authority,
                    now=_NOW + timedelta(minutes=6),
                )