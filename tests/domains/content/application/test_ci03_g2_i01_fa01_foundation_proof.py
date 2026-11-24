"""CI03-G2-I01 FA01 — remaining #78 foundation proof obligations (isolated PG18)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    PersistenceInvariantViolation,
    ReviewAssignmentConflict,
    ReviewAssignmentForbidden,
    ReviewForbidden,
)
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_ASSIGN,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.domains.content.application.review_assignment_guard import (
    ReviewerUseGuardContext,
    ReviewerUseGuardDependencies,
    enforce_reviewer_use_guard,
)
from aieos.domains.content.application.school_context_reviewer_eligibility import (
    SchoolContextReviewerEligibilityService,
)
from aieos.domains.teaching.application.errors import SchoolContextUnavailable
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthority
from aieos.platform.security import UnauthorizedError
from aieos.platform.security.authorization import CurrentPrincipalClassificationAuthority
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    CLASS_REF,
    _NOW,
    _event,
    _seed_worksheet_in_review,
    _services,
)
from tests.platform.security.authorization.helpers import (
    revoke_grant,
    revoke_membership,
    seed_active_authority,
)

pytestmark = pytest.mark.ci03_g2_i01

# #78 acceptance mapping (FA01 additions — not a claim of full matrix completion).
FA01_PROOF_MATRIX: dict[str, str] = {
    "guard_after_membership_revoked": "test_guard_denies_after_reviewer_membership_revoked",
    "guard_after_decide_grant_revoked": "test_guard_denies_after_reviewer_decide_revoked",
    "reissue_after_issuer_submit_revoked": "test_reissue_denied_after_issuer_submit_revoked",
    "guard_expiry_crossing_slow_authority": "test_guard_expiry_crossing_during_slow_authority",
    "self_reviewer_same_as_author": "test_issue_rejects_owner_as_reviewer",
    "wrong_execution_tenant": "test_issue_wrong_execution_tenant",
    "malformed_reviewer_eligibility": "test_guard_malformed_reviewer_eligibility",
    "stale_revoke_target_ineligible": "test_stale_revoke_allowed_after_reviewer_class_removed",
    "db_cross_tenant_issuance_fk": "test_runtime_rejects_cross_tenant_issuance_fk",
    "db_revoker_composite_fk": "test_runtime_rejects_wrong_revoker_principal_fk",
    "db_issuance_immutability": "test_runtime_rejects_issuance_update",
    "db_corrupt_multiple_live_history": "test_issue_fails_on_corrupt_multiple_live_history",
}


def _seed_owner_reviewer(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    reviewer_id: uuid.UUID,
) -> None:
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


def _issue_baseline(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    idem_key: str,
):
    provision_runtime_grants(bootstrap_engine)
    _seed_owner_reviewer(
        bootstrap_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
        reviewer_id=reviewer_id,
    )
    issue, revoke, provider, uow_factory, assign_auth, review_auth, class_auth, elig = (
        _services(runtime_engine, tenant_id)
    )
    provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
    provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
    content_id, version_id = _seed_worksheet_in_review(
        bootstrap_engine,
        runtime_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
    )
    ctx = _event(owner_id)
    assignment_id = issue.issue(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_id,
        reviewer_principal_id=reviewer_id,
        idempotency_key=idem_key,
        event_context=ctx,
        audit_provenance=api_mutation_audit_provenance(owner_id),
    )
    return (
        issue,
        revoke,
        provider,
        uow_factory,
        assign_auth,
        review_auth,
        class_auth,
        elig,
        content_id,
        version_id,
        assignment_id,
        reviewer_id,
        owner_id,
        tenant_id,
    )


@dataclass
class _ExpiryAdvancingClassAuthority:
    """Simulates slow provider completing after assignment TTL crossed."""

    _inner: SchoolContextClassAuthority
    _clock_holder: dict[str, datetime]

    def require_assignable_class_ref(self, *args: Any, **kwargs: Any) -> str:
        self._clock_holder["t"] = _NOW + timedelta(days=8)
        return self._inner.require_assignable_class_ref(*args, **kwargs)


class _MalformedReviewerReader:
    def list_reviewer_eligible_class_refs(
        self, tenant_id: UUID, reviewer_principal_id: UUID
    ) -> tuple[object, ...]:
        return ({"class_ref": CLASS_REF},)


class TestCi03G2I01Fa01FoundationProof:
    def test_guard_denies_after_reviewer_membership_revoked(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-membership",
        )
        (
            _issue,
            _revoke,
            _provider,
            uow_factory,
            assign_auth,
            review_auth,
            class_auth,
            elig,
            content_id,
            version_id,
            assignment_id,
            _reviewer_id,
            _owner_id,
            _tenant_id,
        ) = bundle
        revoke_membership(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
        )
        classification = CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine)
        deps = ReviewerUseGuardDependencies(
            assign_authorization=assign_auth,
            authorization=review_auth,
            principal_classification=classification,
            reviewer_eligibility=elig,
            issuer_class_authority=class_auth,
            clock=lambda: _NOW,
        )
        guard_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(UnauthorizedError):
                enforce_reviewer_use_guard(uow, ctx=guard_ctx, deps=deps)

    def test_guard_denies_after_reviewer_decide_revoked(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-decide-revoke",
        )
        (
            _issue,
            _revoke,
            _provider,
            uow_factory,
            assign_auth,
            review_auth,
            class_auth,
            elig,
            content_id,
            version_id,
            assignment_id,
            _r,
            _o,
            _t,
        ) = bundle
        revoke_grant(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            capability=CONTENT_REVIEW_DECIDE,
        )
        classification = CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine)
        deps = ReviewerUseGuardDependencies(
            assign_authorization=assign_auth,
            authorization=review_auth,
            principal_classification=classification,
            reviewer_eligibility=elig,
            issuer_class_authority=class_auth,
            clock=lambda: _NOW,
        )
        guard_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewForbidden):
                enforce_reviewer_use_guard(uow, ctx=guard_ctx, deps=deps)

    def test_reissue_denied_after_issuer_submit_revoked(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-submit-revoke",
        )
        issue = bundle[0]
        revoke = bundle[1]
        content_id, version_id, reviewer_id = bundle[8], bundle[9], bundle[11]
        revoke_grant(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            capability=CONTENT_REVIEW_SUBMIT,
        )
        with pytest.raises(ReviewForbidden):
            issue.issue(
                tenant_id,
                owner_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=reviewer_id,
                idempotency_key="fa01-reissue-submit",
                event_context=_event(owner_id),
                audit_provenance=api_mutation_audit_provenance(owner_id),
            )

    def test_guard_expiry_crossing_during_slow_authority(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-slow-auth",
        )
        (
            _issue,
            _revoke,
            _provider,
            uow_factory,
            assign_auth,
            review_auth,
            class_auth,
            elig,
            content_id,
            version_id,
            assignment_id,
            _r,
            _o,
            _t,
        ) = bundle
        clock_holder: dict[str, datetime] = {"t": _NOW}
        slow_class = _ExpiryAdvancingClassAuthority(class_auth, clock_holder)
        classification = CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine)
        deps = ReviewerUseGuardDependencies(
            assign_authorization=assign_auth,
            authorization=review_auth,
            principal_classification=classification,
            reviewer_eligibility=elig,
            issuer_class_authority=slow_class,
            clock=lambda: clock_holder["t"],
        )
        guard_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(ReviewAssignmentConflict):
                enforce_reviewer_use_guard(uow, ctx=guard_ctx, deps=deps)

    def test_issue_rejects_owner_as_reviewer(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=owner_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(
                CONTENT_REVIEW_ASSIGN,
                CONTENT_REVIEW_SUBMIT,
                CONTENT_REVIEW_DECIDE,
            ),
        )
        issue, _, provider, *_ = _services(ci03_g2_runtime_engine, tenant_id)
        provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
        provider.set_reviewer_class_eligibility(owner_id, (CLASS_REF,))
        content_id, version_id = _seed_worksheet_in_review(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
        )
        with pytest.raises(ReviewAssignmentForbidden, match="distinct"):
            issue.issue(
                tenant_id,
                owner_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=owner_id,
                idempotency_key="fa01-self-reviewer",
                event_context=_event(owner_id),
                audit_provenance=api_mutation_audit_provenance(owner_id),
            )

    def test_issue_wrong_execution_tenant(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-wrong-tenant-base",
        )
        issue = bundle[0]
        content_id, version_id = bundle[8], bundle[9]
        with pytest.raises((ReviewAssignmentForbidden, ReviewAssignmentConflict, UnauthorizedError)):
            issue.issue(
                other_tenant,
                owner_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=reviewer_id,
                idempotency_key="fa01-wrong-tenant",
                event_context=_event(owner_id),
                audit_provenance=api_mutation_audit_provenance(owner_id),
            )

    def test_guard_malformed_reviewer_eligibility(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-malformed-elig",
        )
        (
            _issue,
            _revoke,
            _provider,
            uow_factory,
            assign_auth,
            review_auth,
            class_auth,
            _elig,
            content_id,
            version_id,
            assignment_id,
            _r,
            _o,
            _t,
        ) = bundle
        bad_elig = SchoolContextReviewerEligibilityService(_MalformedReviewerReader())
        classification = CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine)
        deps = ReviewerUseGuardDependencies(
            assign_authorization=assign_auth,
            authorization=review_auth,
            principal_classification=classification,
            reviewer_eligibility=bad_elig,
            issuer_class_authority=class_auth,
            clock=lambda: _NOW,
        )
        guard_ctx = ReviewerUseGuardContext(
            execution_tenant_id=tenant_id,
            principal_id=reviewer_id,
            effective_actor_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
        )
        with uow_factory(tenant_id) as uow:
            with pytest.raises(SchoolContextUnavailable, match="malformed"):
                enforce_reviewer_use_guard(uow, ctx=guard_ctx, deps=deps)

    def test_stale_revoke_allowed_after_reviewer_class_removed(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-stale-ineligible",
        )
        issue = bundle[0]
        revoke = bundle[1]
        provider = bundle[2]
        content_id, version_id, assignment_id = bundle[8], bundle[9], bundle[10]
        first = assignment_id
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=first,
            idempotency_key="fa01-stale-revoke-1",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        second = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key="fa01-stale-reissue",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        provider.set_reviewer_class_eligibility(reviewer_id, ())
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=first,
            idempotency_key="fa01-stale-revoke-old",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            active = conn.execute(
                text(
                    """
                    SELECT active_assignment_id FROM content.review_current_assignments
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
        assert active == second.value


class TestCi03G2I01Fa01SchemaProof:
    def test_issue_fails_on_corrupt_multiple_live_history(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_a = uuid.uuid7()
        reviewer_b = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_a,
            idem_key="fa01-corrupt-live",
        )
        issue = bundle[0]
        content_id, version_id = bundle[8], bundle[9]
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_b,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        bundle[2].set_reviewer_class_eligibility(reviewer_b, (CLASS_REF,))
        with ci03_g2_bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO content.review_assignment_issuances (
                        assignment_id, tenant_id, content_id, version_id,
                        reviewer_principal_id, issuer_principal_id, class_ref,
                        issued_at, valid_until, correlation_id
                    ) VALUES (
                        :aid, :tid, :cid, :vid, :reviewer, :issuer, :class_ref,
                        :issued, :valid, :corr
                    )
                    """
                ),
                {
                    "aid": uuid.uuid7(),
                    "tid": tenant_id,
                    "cid": content_id.value,
                    "vid": version_id.value,
                    "reviewer": reviewer_b,
                    "issuer": owner_id,
                    "class_ref": CLASS_REF,
                    "issued": _NOW,
                    "valid": _NOW + timedelta(days=1),
                    "corr": uuid.uuid7(),
                },
            )
        with pytest.raises(PersistenceInvariantViolation):
            issue.issue(
                tenant_id,
                owner_id,
                content_id=content_id,
                version_id=version_id,
                reviewer_principal_id=reviewer_b,
                idempotency_key="fa01-corrupt-second",
                event_context=_event(owner_id),
                audit_provenance=api_mutation_audit_provenance(owner_id),
            )

    def test_runtime_rejects_cross_tenant_issuance_fk(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        owner = uuid.uuid7()
        reviewer = uuid.uuid7()
        content_id = uuid.uuid7()
        version_id = uuid.uuid7()
        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_a)},
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
                        NULL, 1, now(), :owner, now(), NULL
                    )
                    """
                ),
                {
                    "content_id": content_id,
                    "tenant_id": tenant_a,
                    "owner": owner,
                    "version_id": version_id,
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO content.content_versions (
                        version_id, tenant_id, content_id, version_number,
                        parent_version_id, schema_id, schema_version, payload,
                        payload_sha256, origin, provenance, created_at,
                        created_by_principal_id
                    ) VALUES (
                        :vid, :tid, :cid, 1, NULL,
                        'education.worksheet', 2, '{}'::jsonb,
                        :sha, 'HUMAN', '{}'::jsonb, now(), :owner
                    )
                    """
                ),
                {
                    "vid": version_id,
                    "tid": tenant_a,
                    "cid": content_id,
                    "sha": "b" * 64,
                    "owner": owner,
                },
            )
            from sqlalchemy.exc import IntegrityError

            with pytest.raises(IntegrityError) as exc_info:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            """
                            INSERT INTO content.review_assignment_issuances (
                                assignment_id, tenant_id, content_id, version_id,
                                reviewer_principal_id, issuer_principal_id, class_ref,
                                issued_at, valid_until, correlation_id
                            ) VALUES (
                                :aid, :tid, :cid, :vid, :reviewer, :issuer, 'class-x',
                                now(), now() + interval '1 day', :corr
                            )
                            """
                        ),
                        {
                            "aid": uuid.uuid7(),
                            "tid": tenant_b,
                            "cid": content_id,
                            "vid": version_id,
                            "reviewer": reviewer,
                            "issuer": owner,
                            "corr": uuid.uuid7(),
                        },
                    )
            assert exc_info.value.orig.sqlstate == "23503"  # type: ignore[union-attr]

    def test_runtime_rejects_wrong_revoker_principal_fk(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-wrong-revoker",
        )
        content_id, version_id, assignment_id = bundle[8], bundle[9], bundle[10]
        intruder = uuid.uuid7()
        from sqlalchemy.exc import IntegrityError

        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(IntegrityError) as exc_info:
                with conn.begin_nested():
                    conn.execute(
                        text(
                            """
                            INSERT INTO content.review_assignment_revocations (
                                assignment_id, tenant_id, content_id, version_id,
                                revoked_by_principal_id, revoked_at, correlation_id
                            ) VALUES (
                                :aid, :tid, :cid, :vid, :revoker, now(), :corr
                            )
                            """
                        ),
                        {
                            "aid": assignment_id.value,
                            "tid": tenant_id,
                            "cid": content_id.value,
                            "vid": version_id.value,
                            "revoker": intruder,
                            "corr": uuid.uuid7(),
                        },
                    )
            assert exc_info.value.orig.sqlstate == "23503"  # type: ignore[union-attr]

    def test_runtime_rejects_issuance_update(
        self, ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        bundle = _issue_baseline(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            reviewer_id=reviewer_id,
            idem_key="fa01-immutable",
        )
        assignment_id = bundle[10]
        from sqlalchemy.exc import ProgrammingError

        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            with pytest.raises(ProgrammingError, match="immutable"):
                conn.execute(
                    text(
                        """
                        UPDATE content.review_assignment_issuances
                        SET class_ref = 'mutated'
                        WHERE assignment_id = :aid
                        """
                    ),
                    {"aid": assignment_id.value},
                )


def test_fa01_proof_matrix_documents_nodes() -> None:
    """Sanity: every FA01 mapping entry names an existing test function."""
    import tests.domains.content.application.test_ci03_g2_i01_fa01_foundation_proof as mod

    for _req, node in FA01_PROOF_MATRIX.items():
        assert hasattr(mod, node), f"missing FA01 proof node {node}"