"""CI03-G2-I02R4-B3R1 — parameterized replay-deny grid (PG18)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ReviewAssignmentConflict,
    ReviewForbidden,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import CONTENT_REVIEW_DECIDE
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
)
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_expiry_denial_without_revoke,
    assert_latest_denial_matches,
    capture_durable_snapshot,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    CLASS_REF,
    _NOW,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
    _corrupt_idempotency_fingerprint,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.platform.security.authorization.helpers import revoke_grant, seed_active_authority

pytestmark = pytest.mark.ci03_g2_i02

_OPERATIONS = ("attest", "approve", "reject", "request_changes")


def _correlated_event(principal_id: uuid.UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def _invoke(
    *,
    effect: str,
    runtime: Engine,
    tenant_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    provider,
    content_id,
    version_id,
    revision,
    idem_key: str,
    event: MutationEventContext,
    clock,
) -> None:
    outcomes = _positive_outcomes(_hinted_ids_from_version(runtime, tenant_id, version_id))
    if effect == "attest":
        integrated_attest_service(
            runtime, tenant_id, clock=clock, school_context_provider=provider
        ).record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=outcomes,
            idempotency_key=idem_key,
            event_context=event,
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock(),
        )
        return
    review = integrated_review_service(
        runtime, tenant_id, clock=clock, school_context_provider=provider
    )
    kwargs = dict(
        execution_tenant_id=tenant_id,
        principal_id=reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        idempotency_key=idem_key,
        event_context=event,
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=clock(),
    )
    if effect == "approve":
        review.approve(**kwargs, reason_code=None, comment=None)
    elif effect == "reject":
        review.reject(**kwargs, reason_code="governed", comment="deny")
    else:
        review.request_changes(**kwargs, reason_code="governed", comment="chg")


# ADR: corrupt persisted fingerprint applies only to operations with stored receipts;
# approve/reject/request_changes share review idempotency but corruption cases covered in r4b1.
_REPLAY_DENY_GRID: tuple[tuple[str, str], ...] = (
    *(
        (case, op)
        for op in _OPERATIONS
        for case in ("expired", "reviewer_decide_revoked")
    ),
    ("corrupt_fingerprint", "attest"),
    ("cross_actor_new_key", "attest"),
)


@pytest.mark.parametrize(("deny_case", "effect"), _REPLAY_DENY_GRID)
def test_replay_deny_grid_integrated(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    deny_case: str,
    effect: str,
) -> None:
    if deny_case == "corrupt_fingerprint" and effect != "attest":
        pytest.skip("corrupt fingerprint proof is attest receipt-specific (r4b1 approve)")
    if deny_case == "cross_actor_new_key" and effect != "attest":
        pytest.skip("cross-actor idempotency scope is per principal")
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
    idem_key = f"b3r1-grid-{deny_case}-{effect}-{uuid.uuid7()}"

    if deny_case == "expired":
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
                    _hinted_ids_from_version(
                        ci03_g2_runtime_engine, tenant_id, version_id
                    )
                ),
                idempotency_key=f"b3r1-grid-exp-pre-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock(),
            )
        clock_holder["t"] = _NOW + timedelta(days=8)
        with pytest.raises(ReviewAssignmentConflict, match="revoked or expired"):
            _invoke(
                effect=effect,
                runtime=ci03_g2_runtime_engine,
                tenant_id=tenant_id,
                reviewer_id=reviewer_id,
                provider=provider,
                content_id=content_id,
                version_id=version_id,
                revision=revision,
                idem_key=idem_key,
                event=event,
                clock=clock,
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
        assert_expiry_denial_without_revoke(before, after)
        assert_latest_denial_matches(
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            content_id=content_id,
            version_id=version_id,
            correlation_id=event.correlation_id,
        )
        return

    if deny_case == "reviewer_decide_revoked":
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
                    _hinted_ids_from_version(
                        ci03_g2_runtime_engine, tenant_id, version_id
                    )
                ),
                idempotency_key=f"b3r1-grid-cap-pre-{uuid.uuid7()}",
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock(),
            )
        revoke_grant(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=reviewer_id,
            capability=CONTENT_REVIEW_DECIDE,
        )
        with pytest.raises(ReviewForbidden):
            _invoke(
                effect=effect,
                runtime=ci03_g2_runtime_engine,
                tenant_id=tenant_id,
                reviewer_id=reviewer_id,
                provider=provider,
                content_id=content_id,
                version_id=version_id,
                revision=revision,
                idem_key=idem_key,
                event=event,
                clock=clock,
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
        assert after.mutation_denials >= before.mutation_denials + 1
        return

    if deny_case == "corrupt_fingerprint":
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
            idempotency_key=idem_key,
            event_context=event,
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock(),
        )
        _corrupt_idempotency_fingerprint(
            ci03_g2_bootstrap_engine,
            tenant_id,
            reviewer_id,
            CONTENT_WORKSHEET_HINT_ATTEST_V1,
        )
        with pytest.raises(WorksheetLearnerHintAttestationInvalid):
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
                idempotency_key=idem_key,
                event_context=event,
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock(),
            )
        return

    if deny_case == "cross_actor_new_key":
        other = uuid.uuid7()
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=other,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_REVIEW_DECIDE,),
        )
        provider.set_reviewer_class_eligibility(other, (CLASS_REF,))
        with pytest.raises((ReviewForbidden, ReviewAssignmentConflict)):
            integrated_attest_service(
                ci03_g2_runtime_engine,
                tenant_id,
                clock=clock,
                school_context_provider=provider,
            ).record(
                tenant_id,
                other,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
                ),
                idempotency_key=idem_key,
                event_context=_correlated_event(other),
                audit_provenance=api_mutation_audit_provenance(other),
                now=clock(),
            )
        return

    pytest.fail(f"unhandled grid cell {deny_case}/{effect}")