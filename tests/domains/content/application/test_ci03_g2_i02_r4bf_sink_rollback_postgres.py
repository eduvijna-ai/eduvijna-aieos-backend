"""CI03-G2-I02R4-BF — denial sink and success-path transactional rollback (PG18)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import PersistenceOperationFailed
from aieos.domains.content.application.review_assignment_denial_audit import (
    DenialAuditUnavailable,
    ReviewAssignmentDenialAuditPort,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    capture_durable_snapshot,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
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


@pytest.mark.parametrize("effect", ("attest", "reject"))
def test_denial_sink_unavailable_fail_closed_no_protected_effect(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    effect: str,
) -> None:
    clock_holder = {"t": _NOW}

    def clock():
        return clock_holder["t"]

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
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=clock
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
    clock_holder["t"] = _NOW + timedelta(days=8)
    event = _correlated_event(reviewer_id)
    denial = _FailingDenialAudit()
    with pytest.raises(DenialAuditUnavailable):
        if effect == "attest":
            integrated_attest_service(
                ci03_g2_runtime_engine,
                tenant_id,
                clock=clock,
                school_context_provider=provider,
                denial_audit=denial,
            ).record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
                ),
                idempotency_key=f"bf-sink-{effect}-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock(),
            )
        else:
            integrated_review_service(
                ci03_g2_runtime_engine,
                tenant_id,
                clock=clock,
                school_context_provider=provider,
                denial_audit=denial,
            ).reject(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code="governed",
                comment="deny",
                idempotency_key=f"bf-sink-{effect}-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock(),
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
    assert after.decisions == before.decisions
    assert after.reviewer_idempotency_success == before.reviewer_idempotency_success
    assert after.mutation_denials == before.mutation_denials


def test_approve_success_outbox_failure_rolls_back_entire_transaction(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
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
    integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    ).record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        ),
        idempotency_key=f"bf-pre-appr-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
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

    def boom(self, *args, **kwargs):  # noqa: ANN001
        raise PersistenceOperationFailed("injected outbox insert failure")

    monkeypatch.setattr(SqlAlchemyOutboxRepository, "insert", boom)
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    with pytest.raises(PersistenceOperationFailed):
        review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"bf-outbox-fail-{uuid.uuid7()}",
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
    assert after == before