"""CI03-G2-I02R4-B3R1 — authority losses, sink failure, wrong binding (PG18)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    PublicationForbidden,
    ReviewAssignmentConflict,
    ReviewForbidden,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import CONTENT_REVIEW_DECIDE
from aieos.domains.content.application.review_assignment_denial_audit import (
    DenialAuditUnavailable,
    REASON_REVIEW_CAPABILITY,
    ReviewAssignmentDenialAuditPort,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
)
from aieos.platform.security.authorization.content_adapters import (
    AIEOS_CONTENT_CAPABILITIES,
    KernelPublicationAuthorization,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.domains.content.application.publish import PublishContentService
from tests.domains.content.application.helpers_ci03_g2_i02_r4b3r1 import (
    build_terminal_approved_scenario,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_latest_denial_matches,
    capture_durable_snapshot,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    CLASS_REF,
    _NOW,
    _event,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t1 import build_preparation_content_schema_registry
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowPublicationGovernance,
    IDEMPOTENCY_RETENTION,
)
from tests.platform.security.authorization.helpers import (
    revoke_membership,
    seed_active_authority,
)

pytestmark = pytest.mark.ci03_g2_i02


class _FailingDenialAudit(ReviewAssignmentDenialAuditPort):
    def record(self, record) -> None:  # noqa: ANN001
        raise RuntimeError("sink unavailable")


def _correlated_event(principal_id: uuid.UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


class TestCi03G2I02R4B3R1AuthoritySink:
    def test_mutation_denial_sink_unavailable_fail_closed_no_durable_effect(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        clock_holder = {"t": _NOW}
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            _revoke,
            assignment_id,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=lambda: clock_holder["t"],
        )
        before = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: clock_holder["t"],
            school_context_provider=provider,
            denial_audit=_FailingDenialAudit(),
        )
        clock_holder["t"] = _NOW + timedelta(days=8)
        event = _correlated_event(reviewer_id)
        with pytest.raises(DenialAuditUnavailable):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(
                        ci03_g2_runtime_engine, tenant_id, version_id
                    )
                ),
                idempotency_key=f"b3r1-sink-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock_holder["t"],
            )
        after = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        assert after.attestations == before.attestations
        assert after.reviewer_idempotency_success == before.reviewer_idempotency_success
        assert after.mutation_denials == before.mutation_denials

    def test_reviewer_membership_revoked_denies_attest(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            _revoke,
            assignment_id,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        revoke_membership(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
        )
        before = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        event = _correlated_event(reviewer_id)
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        with pytest.raises(ReviewForbidden):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
                ),
                idempotency_key=f"b3r1-memb-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )
        after = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        assert after.attestations == before.attestations
        assert after.mutation_denials == before.mutation_denials + 1
        assert_latest_denial_matches(
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            correlation_id=event.correlation_id,
            reason_code=REASON_REVIEW_CAPABILITY,
        )

    def test_approve_rejects_corrupt_attestation_digest(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            _revoke,
            assignment_id,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"b3r1-digest-attest-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        with ci03_g2_bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE content.worksheet_learner_hint_version_attestations
                    SET payload_digest = :bad
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {"bad": "f" * 64, "tid": tenant_id, "vid": version_id.value},
            )
        review = integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        before = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        event = _correlated_event(reviewer_id)
        with pytest.raises(WorksheetLearnerHintAttestationInvalid):
            review.approve(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code=None,
                comment=None,
                idempotency_key=f"b3r1-digest-apr-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )
        after = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        assert after.decisions == before.decisions

    def test_cross_reviewer_approve_denied(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            _revoke,
            assignment_id,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        other = uuid.uuid7()
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=other,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        provider.set_reviewer_class_eligibility(other, (CLASS_REF,))
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"b3r1-cross-attest-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        review = integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        before = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=other,
        )
        with pytest.raises((ReviewForbidden, ReviewAssignmentConflict)):
            review.approve(
                tenant_id,
                other,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code=None,
                comment=None,
                idempotency_key=f"b3r1-cross-apr-{uuid.uuid7()}",
                event_context=_correlated_event(other),
                audit_provenance=api_mutation_audit_provenance(other),
                now=_NOW,
            )
        after = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=other,
        )
        assert after.decisions == before.decisions

    def test_kernel_publisher_without_grant_denied_after_terminal_approve(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        scenario = build_terminal_approved_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
        )
        no_grant = uuid.uuid7()
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=scenario.tenant_id,
            principal_id=no_grant,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        kernel = AuthorizationKernel(
            ci03_g2_runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
        )
        publish = PublishContentService(
            SqlAlchemyContentUnitOfWorkFactory(ci03_g2_runtime_engine),
            KernelPublicationAuthorization(kernel),
            AllowPublicationGovernance(),
            AllowAssetCurrentGovernance(),
            build_preparation_content_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            principal_classification=CurrentPrincipalClassificationAuthority(
                ci03_g2_runtime_engine
            ),
        )
        with pytest.raises(PublicationForbidden):
            publish.publish(
                scenario.tenant_id,
                no_grant,
                content_id=scenario.content_id,
                version_id=scenario.version_id,
                expected_aggregate_revision=scenario.revision,
                idempotency_key=f"b3r1-pub-{uuid.uuid7()}",
                event_context=_event(no_grant),
                audit_provenance=api_mutation_audit_provenance(no_grant),
                now=_NOW,
            )