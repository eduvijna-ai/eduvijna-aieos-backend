"""CI03-G2-I02R4-B3 — authority losses, terminal replay, wrong attestation binding (PG18)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    PersistenceInvariantViolation,
    ReviewForbidden,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import CONTENT_REVIEW_DECIDE
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.application.review_assignment_denial_audit import (
    REASON_REVIEW_CAPABILITY,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.models import CONTENT_WORKSHEET_HINT_ATTEST_V1
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_latest_denial_matches,
    capture_durable_snapshot,
    idempotency_success_count_for_key,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    CLASS_REF,
    _NOW,
    _denial_count,
    _event,
    _services,
)
from aieos.domains.content.domain.identities import ReviewAssignmentId
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.platform.security.authorization.helpers import (
    revoke_grant,
    seed_active_authority,
)

pytestmark = pytest.mark.ci03_g2_i02


def _correlated_event(principal_id: uuid.UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


class TestCi03G2I02R4B3AuthorityReplay:
    def test_terminal_attest_replay_preserves_durable_state(
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
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        outcomes = _positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        )
        idem = f"b3-terminal-attest-{uuid.uuid7()}"
        first = attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=outcomes,
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        mid = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        assert mid.attestations == before.attestations + 1
        assert mid.worksheet_hint_attest_audits == before.worksheet_hint_attest_audits + 1
        assert idempotency_success_count_for_key(
            ci03_g2_runtime_engine,
            tenant_id,
            reviewer_id,
            operation=CONTENT_WORKSHEET_HINT_ATTEST_V1,
            idempotency_key=idem,
        ) == 1
        second = attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=outcomes,
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        assert first == second
        after = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            reviewer_principal_id=reviewer_id,
        )
        assert after == mid

    def test_attest_denied_after_reviewer_decide_revoked(
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
        revoke_grant(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            capability=CONTENT_REVIEW_DECIDE,
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
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        event = _correlated_event(reviewer_id)
        with pytest.raises(ReviewForbidden, match="review capability denied"):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
                ),
                idempotency_key=f"b3-decide-revoked-{uuid.uuid7()}",
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

    def test_approve_rejects_attestation_from_superseded_assignment(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            owner_id,
            reviewer_id,
            provider,
            revoke,
            assignment_id,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        reviewer2 = uuid.uuid7()
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer2,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        provider.set_reviewer_class_eligibility(reviewer2, (CLASS_REF,))
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
            idempotency_key=f"b3-bind-attest-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key=f"b3-bind-revoke-{uuid.uuid7()}",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        issue, _, *_ = _services(ci03_g2_runtime_engine, tenant_id)
        new_assignment_id = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer2,
            idempotency_key=f"b3-bind-reissue-{uuid.uuid7()}",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        review = integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        assert isinstance(new_assignment_id, ReviewAssignmentId)
        before = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=new_assignment_id,
            reviewer_principal_id=reviewer2,
        )
        event = _correlated_event(reviewer2)
        with pytest.raises(
            WorksheetLearnerHintAttestationInvalid,
            match="does not match active slot binding",
        ):
            review.approve(
                tenant_id,
                reviewer2,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code=None,
                comment=None,
                idempotency_key=f"b3-wrong-binding-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer2),
                now=_NOW,
            )
        after = capture_durable_snapshot(
            bootstrap_engine=ci03_g2_bootstrap_engine,
            runtime_engine=ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=new_assignment_id,
            reviewer_principal_id=reviewer2,
        )
        assert after.decisions == before.decisions
        assert (
            _denial_count(ci03_g2_runtime_engine, tenant_id, CONTENT_REVIEW_MUTATION_DENIED)
            >= before.mutation_denials + 1
        )

    def test_attest_replay_denied_after_assignment_revoked(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            owner_id,
            reviewer_id,
            provider,
            revoke,
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
        idem = f"b3-revoke-replay-{uuid.uuid7()}"
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key=f"b3-revoke-slot-{uuid.uuid7()}",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
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
        with pytest.raises(PersistenceInvariantViolation, match="revoked or expired"):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
                ),
                idempotency_key=idem,
                event_context=_event(reviewer_id),
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
        assert after.reviewer_idempotency_success == before.reviewer_idempotency_success