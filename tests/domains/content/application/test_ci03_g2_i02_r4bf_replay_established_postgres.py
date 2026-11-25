"""CI03-G2-I02R4-BF — establish success receipt then replay-deny on binding change."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    AggregateRevisionConflict,
    IdempotencyKeyReused,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.platform.idempotency.models import CONTENT_WORKSHEET_HINT_ATTEST_V1
from tests.domains.content.application.helpers_ci03_g2_i01 import integrated_attest_service
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    capture_durable_snapshot,
    load_idempotency_receipt,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
    _corrupt_idempotency_fingerprint,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)

pytestmark = pytest.mark.ci03_g2_i02


def test_attest_replay_same_key_rejects_changed_outcomes_after_success(
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
    idem = f"bf-established-{uuid.uuid7()}"
    outcomes = _positive_outcomes(
        _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
    )
    attest.record(
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
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        assignment_id=assignment_id,
        reviewer_principal_id=reviewer_id,
    )
    bad_outcomes = list(outcomes)
    bad_outcomes[0] = {**bad_outcomes[0], "outcome_kind": "AMBIGUOUS"}
    with pytest.raises(
        (IdempotencyKeyReused, WorksheetLearnerHintAttestationInvalid),
        match="idempotency key already bound",
    ):
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=bad_outcomes,
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
    assert after == before
    receipt = load_idempotency_receipt(
        ci03_g2_runtime_engine,
        tenant_id,
        reviewer_id,
        operation=CONTENT_WORKSHEET_HINT_ATTEST_V1,
        idempotency_key=idem,
    )
    assert receipt is not None


def test_attest_replay_after_corrupt_persisted_fingerprint_denies_new_effect(
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
    idem = f"bf-fp-established-{uuid.uuid7()}"
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
    _corrupt_idempotency_fingerprint(
        ci03_g2_bootstrap_engine,
        tenant_id,
        reviewer_id,
        CONTENT_WORKSHEET_HINT_ATTEST_V1,
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
    with pytest.raises(
        WorksheetLearnerHintAttestationInvalid, match="idempotency key already bound"
    ):
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


def test_attest_replay_same_key_rejects_stale_aggregate_revision(
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
    idem = f"bf-rev-stale-{uuid.uuid7()}"
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
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        assignment_id=assignment_id,
        reviewer_principal_id=reviewer_id,
    )
    stale = AggregateRevision(int(revision) - 1)
    with pytest.raises(
        (AggregateRevisionConflict, WorksheetLearnerHintAttestationInvalid),
        match="idempotency key already bound|aggregate_revision",
    ):
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=stale,
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
    assert after == before