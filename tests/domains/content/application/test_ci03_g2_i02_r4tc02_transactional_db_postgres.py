"""CI03-G2-I02R4-TC02 — transactional integrity, command evidence, migration, privilege (PG18)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.domains.content.application.errors import (
    PersistenceOperationFailed,
    ReviewAssignmentConflict,
)
from aieos.domains.content.application.review_assignment_denial_audit import (
    DenialAuditUnavailable,
    ReviewAssignmentDenialAuditPort,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.events.persistence.repositories import SqlAlchemyOutboxRepository
from aieos.platform.security.audit.persistence.errors import SecurityAuditPersistenceError
from aieos.platform.security.audit.persistence.repositories import (
    SqlAlchemySecurityMutationAuditRepository,
)
from tests.conftest import RUNTIME_USER, alembic_config, provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_denial_command_no_receipt,
    assert_success_mutation_command_evidence,
    capture_durable_snapshot,
    schema_fingerprint_ci03g020002,
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


@pytest.mark.parametrize("effect", ("approve", "request_changes"))
def test_denial_sink_unavailable_fail_closed_with_full_snapshot(
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
    if effect == "approve":
        integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=clock,
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
            idempotency_key=f"tc02-pre-attest-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock(),
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
    idem_key = f"tc02-sink-{effect}-{uuid.uuid7()}"
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=clock,
        school_context_provider=provider,
        denial_audit=_FailingDenialAudit(),
    )
    with pytest.raises(DenialAuditUnavailable):
        if effect == "approve":
            review.approve(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code=None,
                comment=None,
                idempotency_key=idem_key,
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock(),
            )
        else:
            review.request_changes(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code="governed",
                comment="changes",
                idempotency_key=idem_key,
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
    assert after == before
    assert_denial_command_no_receipt(
        ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        principal_id=reviewer_id,
        effect=effect,
        idempotency_key=idem_key,
    )


@pytest.mark.parametrize("effect", ("attest", "reject"))
def test_success_audit_failure_rolls_back(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    effect: str,
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
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        assignment_id=assignment_id,
        reviewer_principal_id=reviewer_id,
    )

    def boom(self, record) -> None:  # noqa: ANN001
        raise SecurityAuditPersistenceError("injected audit insert failure")

    monkeypatch.setattr(SqlAlchemySecurityMutationAuditRepository, "insert", boom)
    event = _correlated_event(reviewer_id)
    idem_key = f"tc02-audit-fail-{effect}-{uuid.uuid7()}"
    with pytest.raises(PersistenceOperationFailed):
        if effect == "attest":
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
                idempotency_key=idem_key,
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )
        else:
            integrated_review_service(
                ci03_g2_runtime_engine,
                tenant_id,
                clock=lambda: _NOW,
                school_context_provider=provider,
            ).reject(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code="governed",
                comment="deny",
                idempotency_key=idem_key,
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
    assert after == before
    assert_denial_command_no_receipt(
        ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        principal_id=reviewer_id,
        effect=effect,
        idempotency_key=idem_key,
    )


@pytest.mark.parametrize("effect", ("reject", "request_changes"))
def test_decision_success_outbox_failure_rolls_back(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    effect: str,
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
    idem_key = f"tc02-outbox-{effect}-{uuid.uuid7()}"
    with pytest.raises(PersistenceOperationFailed):
        if effect == "reject":
            review.reject(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code="governed",
                comment="deny",
                idempotency_key=idem_key,
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )
        else:
            review.request_changes(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code="governed",
                comment="rc",
                idempotency_key=idem_key,
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


@pytest.mark.parametrize(
    "effect",
    ("attest", "approve", "reject", "request_changes"),
)
def test_success_mutation_command_identity_representative(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    effect: str,
) -> None:
    provision_runtime_grants(ci03_g2_bootstrap_engine)
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
    approve_revision = revision
    if effect in ("approve",):
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
            idempotency_key=f"tc02-id-pre-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            approve_revision = AggregateRevision(
                int(
                    conn.execute(
                        text(
                            "SELECT aggregate_revision FROM content.contents WHERE content_id = :cid"
                        ),
                        {"cid": content_id.value},
                    ).scalar_one()
                )
            )
    event = _correlated_event(reviewer_id)
    idem_key = f"tc02-id-{effect}-{uuid.uuid7()}"
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    if effect == "attest":
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
            idempotency_key=idem_key,
            event_context=event,
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
    elif effect == "approve":
        review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=approve_revision,
            reason_code=None,
            comment=None,
            idempotency_key=idem_key,
            event_context=event,
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
    elif effect == "reject":
        review.reject(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code="governed",
            comment="deny",
            idempotency_key=idem_key,
            event_context=event,
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
    else:
        review.request_changes(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code="governed",
            comment="changes",
            idempotency_key=idem_key,
            event_context=event,
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
    assert_success_mutation_command_evidence(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        principal_id=reviewer_id,
        content_id=content_id,
        version_id=version_id,
        effect=effect,
        idempotency_key=idem_key,
        correlation_id=event.correlation_id,
    )


def test_attest_emits_zero_outbox_by_design(
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
    event = _correlated_event(reviewer_id)
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
        idempotency_key=f"tc02-zero-outbox-{uuid.uuid7()}",
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
    assert after.outbox_messages == before.outbox_messages


def test_downgrade_refuses_on_mutation_denial_before_receipt_guard(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    ci03_g2_postgres18: dict[str, str],
) -> None:
    """Downgrade guard #2: mutation.denied rows block before receipt-column check."""
    provision_runtime_grants(ci03_g2_bootstrap_engine)
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
    before_fp = schema_fingerprint_ci03g020002(ci03_g2_bootstrap_engine)
    clock_holder["t"] = _NOW + timedelta(days=8)
    with pytest.raises(ReviewAssignmentConflict):
        integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=clock,
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
            idempotency_key=f"tc02-deny-only-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock(),
        )
    with ci03_g2_bootstrap_engine.connect() as conn:
        bound_rows = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE tenant_id = :tid AND review_assignment_id IS NOT NULL
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )
        mutation_denials = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.authorization_denial_records
                    WHERE tenant_id = :tid
                      AND action = 'content.review.mutation.denied'
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )
    assert bound_rows == 0
    assert mutation_denials >= 1
    cfg = alembic_config(ci03_g2_postgres18["migrator_url"])
    with pytest.raises(Exception, match="refused"):
        command.downgrade(cfg, "ci03g020001")
    after_fp = schema_fingerprint_ci03g020002(ci03_g2_bootstrap_engine)
    assert after_fp == before_fp


def test_ci03g020002_fk_and_assignment_binding_catalog(
    ci03_g2_bootstrap_engine: Engine,
) -> None:
    with ci03_g2_bootstrap_engine.connect() as conn:
        fk_attest = conn.execute(
            text(
                """
                SELECT count(*) FROM information_schema.table_constraints
                WHERE constraint_schema = 'content'
                  AND constraint_name = 'fk_worksheet_hint_attestations_assignment'
                """
            )
        ).scalar_one()
        fk_decision = conn.execute(
            text(
                """
                SELECT count(*) FROM information_schema.table_constraints
                WHERE constraint_schema = 'content'
                  AND constraint_name = 'fk_review_decisions_assignment'
                """
            )
        ).scalar_one()
    assert int(fk_attest) == 1
    assert int(fk_decision) == 1


def test_runtime_role_cannot_delete_immutable_attestation(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    (
        tenant_id,
        _owner,
        reviewer_id,
        provider,
        _revoke,
        _assignment_id,
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
        idempotency_key=f"tc02-del-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    with pytest.raises(
        (OperationalError, DBAPIError),
        match="immutable|permission denied",
    ):
        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            conn.execute(
                text(
                    """
                    DELETE FROM content.worksheet_learner_hint_version_attestations
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {"tid": tenant_id, "vid": version_id.value},
            )


def test_runtime_role_cannot_create_schema_object(
    ci03_g2_runtime_engine: Engine,
) -> None:
    with pytest.raises(
        (OperationalError, DBAPIError),
        match="permission denied",
    ):
        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(text(f"CREATE TABLE public.tc02_probe_{uuid.uuid4().hex[:8]} (id int)"))


def test_cross_tenant_assignment_slot_hidden_from_runtime(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    tenant_b = uuid.uuid7()
    (
        tenant_a,
        *_rest,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine,
        ci03_g2_runtime_engine,
        clock=lambda: _NOW,
    )
    with ci03_g2_runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_b)},
        )
        visible = conn.execute(
            text(
                """
                SELECT count(*) FROM content.review_current_assignments
                WHERE tenant_id = :tid
                """
            ),
            {"tid": tenant_a},
        ).scalar_one()
    assert int(visible) == 0


def test_runtime_connection_uses_migration_created_role(
    ci03_g2_runtime_engine: Engine,
) -> None:
    with ci03_g2_runtime_engine.connect() as conn:
        current_user = conn.execute(text("SELECT current_user")).scalar_one()
    assert current_user == RUNTIME_USER