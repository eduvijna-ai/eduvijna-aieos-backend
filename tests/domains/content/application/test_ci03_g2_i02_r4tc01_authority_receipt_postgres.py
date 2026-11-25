"""CI03-G2-I02R4-TC01 — authority and receipt correctness (PG18, ADR-O64-03-C/D/E)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ContentVersionNotFound,
    ReviewAssignmentConflict,
    ReviewAssignmentForbidden,
    ReviewAssignmentNotAllowed,
    ReviewForbidden,
    WorksheetLearnerHintAttestationInvalid,
    WorksheetLearnerHintPublicationBlocked,
    PublicationForbidden,
)
from aieos.domains.content.application.ports import (
    CONTENT_PUBLISH,
    CONTENT_REVIEW_ASSIGN,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.review_assignment_bound_mutations import (
    assert_approve_attestation_assignment_match,
)
from aieos.domains.content.domain.identities import ContentId, ReviewAssignmentId
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.domains.teaching.application.errors import SchoolContextUnavailable
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_REVIEW_REJECT_V1,
    CONTENT_REVIEW_REQUEST_CHANGES_V1,
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
)
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
from tests.domains.content.application.helpers_ci03_g2_i02_r4b3r1 import (
    build_terminal_approved_scenario,
    build_terminal_rejected_scenario,
    build_terminal_request_changes_scenario,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_snapshot_unchanged_except_denial,
    assignment_valid_until,
    capture_durable_snapshot,
    load_idempotency_receipt,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
    _services,
)
from tests.domains.content.application.test_ci03_g2_i01_fa01_foundation_proof import (
    _UnavailableClassAuthority,
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
from tests.platform.security.authorization.helpers import revoke_grant, seed_active_authority

pytestmark = pytest.mark.ci03_g2_i02


@dataclass(frozen=True, slots=True)
class _TerminalReplayCase:
    label: str
    builder: Callable[[Engine, Engine], Any]
    operation: str
    idem_attr: str
    replay_revision_attr: str


_TERMINAL_REPLAY_CASES = (
    _TerminalReplayCase(
        "post_approve_attest",
        build_terminal_approved_scenario,
        CONTENT_WORKSHEET_HINT_ATTEST_V1,
        "attest_idem",
        "attest_effect_revision",
    ),
    _TerminalReplayCase(
        "post_approve_approve",
        build_terminal_approved_scenario,
        CONTENT_REVIEW_APPROVE_V1,
        "approve_idem",
        "approve_effect_revision",
    ),
    _TerminalReplayCase(
        "terminal_reject",
        build_terminal_rejected_scenario,
        CONTENT_REVIEW_REJECT_V1,
        "reject_idem",
        "effect_call_revision",
    ),
    _TerminalReplayCase(
        "terminal_request_changes",
        build_terminal_request_changes_scenario,
        CONTENT_REVIEW_REQUEST_CHANGES_V1,
        "request_changes_idem",
        "effect_call_revision",
    ),
)


@pytest.mark.parametrize("case", _TERMINAL_REPLAY_CASES, ids=lambda c: c.label)
def test_terminal_receipt_replay_returns_original_result(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    case: _TerminalReplayCase,
) -> None:
    scenario = case.builder(ci03_g2_bootstrap_engine, ci03_g2_runtime_engine)
    idem_key = getattr(scenario, case.idem_attr)
    replay_revision = getattr(scenario, case.replay_revision_attr)
    receipt_before = load_idempotency_receipt(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        scenario.reviewer_id,
        operation=case.operation,
        idempotency_key=idem_key,
    )
    assert receipt_before is not None
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )
    if case.operation == CONTENT_WORKSHEET_HINT_ATTEST_V1:
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            scenario.tenant_id,
            clock=lambda: _NOW,
            school_context_provider=scenario.provider,
        )
        replay_result = attest.record(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=replay_revision,
            per_question_outcomes=scenario.outcomes,
            idempotency_key=idem_key,
            event_context=_event(scenario.reviewer_id),
            audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
            now=_NOW,
        )
        assert (
            replay_result.value == receipt_before.result_worksheet_hint_attestation_id
        )
    else:
        review = integrated_review_service(
            ci03_g2_runtime_engine,
            scenario.tenant_id,
            clock=lambda: _NOW,
            school_context_provider=scenario.provider,
        )
        kwargs = dict(
            execution_tenant_id=scenario.tenant_id,
            principal_id=scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=replay_revision,
            idempotency_key=idem_key,
            event_context=_event(scenario.reviewer_id),
            audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
            now=_NOW,
        )
        if case.operation == CONTENT_REVIEW_APPROVE_V1:
            decision = review.approve(**kwargs, reason_code=None, comment=None)
        elif case.operation == CONTENT_REVIEW_REJECT_V1:
            decision = review.reject(**kwargs, reason_code="governed", comment="reject")
        else:
            decision = review.request_changes(
                **kwargs, reason_code="governed", comment="changes"
            )
        assert decision.review_decision_id.value == receipt_before.result_review_decision_id
        assert int(decision.aggregate_revision) == receipt_before.result_aggregate_revision
    receipt_after = load_idempotency_receipt(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        scenario.reviewer_id,
        operation=case.operation,
        idempotency_key=idem_key,
    )
    assert receipt_after == receipt_before
    after = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )
    assert after == before


@pytest.mark.parametrize(
    "receipt_kind",
    ("active_attest", "terminal_reject"),
)
def test_established_receipt_expiry_denied(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    receipt_kind: str,
) -> None:
    clock_holder = {"t": _NOW}

    def clock():
        return clock_holder["t"]

    if receipt_kind == "active_attest":
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
        idem = f"tc01-exp-active-{uuid.uuid7()}"
        outcomes = _positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        )
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
            per_question_outcomes=outcomes,
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock(),
        )
        operation = CONTENT_WORKSHEET_HINT_ATTEST_V1
        valid_until = assignment_valid_until(
            ci03_g2_runtime_engine, tenant_id, assignment_id
        )
    else:
        scenario = build_terminal_rejected_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
        )
        tenant_id = scenario.tenant_id
        reviewer_id = scenario.reviewer_id
        provider = scenario.provider
        assignment_id = scenario.assignment_id
        content_id = scenario.content_id
        version_id = scenario.version_id
        revision = scenario.effect_call_revision
        idem = scenario.reject_idem
        operation = CONTENT_REVIEW_REJECT_V1
        valid_until = assignment_valid_until(
            ci03_g2_runtime_engine, tenant_id, assignment_id
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
    clock_holder["t"] = valid_until + timedelta(seconds=1)
    attest = integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=clock,
        school_context_provider=provider,
    )
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=clock,
        school_context_provider=provider,
    )
    with pytest.raises(
        (ReviewAssignmentConflict, ReviewAssignmentNotAllowed),
        match="revoked or expired|not eligible for terminal receipt",
    ):
        if operation == CONTENT_WORKSHEET_HINT_ATTEST_V1:
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
                now=clock(),
            )
        else:
            review.reject(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code="governed",
                comment="reject",
                idempotency_key=idem,
                event_context=_event(reviewer_id),
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
    assert_snapshot_unchanged_except_denial(before, after)
    assert load_idempotency_receipt(
        ci03_g2_runtime_engine,
        tenant_id,
        reviewer_id,
        operation=operation,
        idempotency_key=idem,
    )


def test_terminal_receipt_current_authority_lost_denies_review_replay(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    scenario = build_terminal_approved_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
    )
    revoke_grant(
        ci03_g2_bootstrap_engine,
        tenant_id=scenario.tenant_id,
        principal_id=scenario.reviewer_id,
        capability=CONTENT_REVIEW_DECIDE,
    )
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        clock=lambda: _NOW,
        school_context_provider=scenario.provider,
    )
    with pytest.raises(ReviewForbidden, match="review capability denied"):
        review.approve(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=scenario.approve_effect_revision,
            reason_code=None,
            comment=None,
            idempotency_key=scenario.approve_idem,
            event_context=_event(scenario.reviewer_id),
            audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
            now=_NOW,
        )
    after = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )
    assert_snapshot_unchanged_except_denial(before, after)


def test_terminal_receipt_revoked_denied(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    scenario = build_terminal_rejected_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
    )
    _, revoke, *_ = _services(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        provider=scenario.provider,
    )
    with ci03_g2_runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(scenario.tenant_id)},
        )
        owner_id = conn.execute(
            text(
                "SELECT owner_principal_id FROM content.contents WHERE content_id = :cid"
            ),
            {"cid": scenario.content_id.value},
        ).scalar_one()
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )
    revoke.revoke(
        scenario.tenant_id,
        owner_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        idempotency_key=f"tc01-revoke-before-replay-{uuid.uuid7()}",
        event_context=_event(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
    )
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        clock=lambda: _NOW,
        school_context_provider=scenario.provider,
    )
    with pytest.raises(
        (ReviewAssignmentConflict, ReviewAssignmentNotAllowed),
        match="not eligible|revoked",
    ):
        review.reject(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=scenario.effect_call_revision,
            reason_code="governed",
            comment="reject",
            idempotency_key=scenario.reject_idem,
            event_context=_event(scenario.reviewer_id),
            audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
            now=_NOW,
        )
    after = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )
    assert after.decisions == before.decisions


def test_established_receipt_replay_wrong_version_denied(
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
    idem = f"tc01-wrong-ver-{uuid.uuid7()}"
    outcomes = _positive_outcomes(
        _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
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
    from aieos.domains.content.domain.identities import ContentVersionId

    wrong_version = ContentVersionId(uuid.uuid7())
    attest = integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    with pytest.raises(
        (WorksheetLearnerHintAttestationInvalid, ContentVersionNotFound),
        match="idempotency replay version binding mismatch|not visible",
    ):
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=wrong_version,
            expected_aggregate_revision=revision,
            per_question_outcomes=outcomes,
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


def test_established_receipt_replay_wrong_content_denied(
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
    idem = f"tc01-wrong-cid-{uuid.uuid7()}"
    outcomes = _positive_outcomes(
        _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
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
        per_question_outcomes=outcomes,
        idempotency_key=idem,
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    attest = integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    with pytest.raises(
        (WorksheetLearnerHintAttestationInvalid, ContentVersionNotFound),
        match="content binding mismatch|not visible",
    ):
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=ContentId(uuid.uuid7()),
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=outcomes,
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )


def test_same_idempotency_key_different_operation_scope_not_global_conflict(
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
    shared_key = f"tc01-shared-scope-{uuid.uuid7()}"
    outcomes = _positive_outcomes(
        _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
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
        per_question_outcomes=outcomes,
        idempotency_key=shared_key,
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
    decision = review.approve(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        reason_code=None,
        comment=None,
        idempotency_key=shared_key,
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    assert decision is not None
    assert (
        load_idempotency_receipt(
            ci03_g2_runtime_engine,
            tenant_id,
            reviewer_id,
            operation=CONTENT_WORKSHEET_HINT_ATTEST_V1,
            idempotency_key=shared_key,
        )
        is not None
    )
    assert (
        load_idempotency_receipt(
            ci03_g2_runtime_engine,
            tenant_id,
            reviewer_id,
            operation=CONTENT_REVIEW_APPROVE_V1,
            idempotency_key=shared_key,
        )
        is not None
    )
    _ = assignment_id


def test_new_effect_denied_when_reviewer_class_eligibility_empty(
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
    provider.set_reviewer_class_eligibility(reviewer_id, ())
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
    with pytest.raises(ReviewAssignmentForbidden, match="eligible"):
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"tc01-no-class-{uuid.uuid7()}",
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


def test_new_effect_denied_when_school_context_class_unavailable(
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
        issuer_class_authority=_UnavailableClassAuthority(),
    )
    with pytest.raises(SchoolContextUnavailable, match="unavailable"):
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"tc01-sc-unavail-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )


def test_issuer_submit_revoked_denies_reissue_new_effect(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    (
        tenant_id,
        owner_id,
        reviewer_id,
        provider,
        _revoke,
        assignment_id,
        content_id,
        version_id,
        _revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    issue, _, _, *_ = _services(ci03_g2_runtime_engine, tenant_id)
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
            idempotency_key=f"tc01-submit-revoke-{uuid.uuid7()}",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
    _ = provider, assignment_id


def test_approve_interlock_rejects_null_attestation_assignment() -> None:
    active = ReviewAssignmentId(uuid.uuid7())
    with pytest.raises(
        WorksheetLearnerHintAttestationInvalid,
        match="approve requires assignment-bound attestation",
    ):
        assert_approve_attestation_assignment_match(None, active)


def test_approve_interlock_rejects_mismatched_attestation_assignment() -> None:
    with pytest.raises(
        WorksheetLearnerHintAttestationInvalid,
        match="does not match active slot",
    ):
        assert_approve_attestation_assignment_match(
            ReviewAssignmentId(uuid.uuid7()),
            ReviewAssignmentId(uuid.uuid7()),
        )


def test_publish_rejects_reviewer_as_publisher(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    scenario = build_terminal_approved_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
    )
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=scenario.tenant_id,
        principal_id=scenario.reviewer_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_PUBLISH,),
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
    with pytest.raises(WorksheetLearnerHintPublicationBlocked, match="distinct"):
        publish.publish(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=scenario.revision,
            idempotency_key=f"tc01-pub-reviewer-{uuid.uuid7()}",
            event_context=_event(scenario.reviewer_id),
            audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
            now=_NOW,
        )


def test_publish_rejects_non_human_principal(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    scenario = build_terminal_approved_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
    )
    publisher_id = uuid.uuid7()
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=scenario.tenant_id,
        principal_id=publisher_id,
        principal_kind=PrincipalKind.WORKLOAD,
        capabilities=(CONTENT_PUBLISH,),
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
    from aieos.platform.security.context import UnauthorizedError

    with pytest.raises(
        (PublicationForbidden, WorksheetLearnerHintPublicationBlocked, UnauthorizedError),
        match="not authorized|HUMAN|distinct|publish",
    ):
        publish.publish(
            scenario.tenant_id,
            publisher_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=scenario.revision,
            idempotency_key=f"tc01-pub-nonhuman-{uuid.uuid7()}",
            event_context=_event(publisher_id),
            audit_provenance=api_mutation_audit_provenance(publisher_id),
            now=_NOW,
        )