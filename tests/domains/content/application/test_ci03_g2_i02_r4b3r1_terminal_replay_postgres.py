"""CI03-G2-I02R4-B3R1 — terminal decision receipt replay (PG18)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ReviewAlreadyDecided,
    ReviewAssignmentNotAllowed,
    ReviewDecisionNotAllowed,
)
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.events.constants import EVENT_CONTENT_REVIEW_APPROVED_V1
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_REVIEW_REJECT_V1,
    CONTENT_REVIEW_REQUEST_CHANGES_V1,
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
)
from tests.domains.content.application.helpers_ci03_g2_i01 import integrated_review_service
from tests.domains.content.application.helpers_ci03_g2_i02_r4b3r1 import (
    TERMINAL_APPROVE_OUTBOX_EVENT,
    build_terminal_approved_scenario,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_snapshot_unchanged_except_denial,
    capture_durable_snapshot,
    load_idempotency_receipt,
    outbox_count_for_event_type,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import _NOW
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import _event
from tests.domains.content.application.helpers_ci03_g2_i01 import integrated_attest_service

pytestmark = pytest.mark.ci03_g2_i02


@pytest.mark.parametrize(
    ("operation", "idem_attr"),
    (
        (CONTENT_WORKSHEET_HINT_ATTEST_V1, "attest_idem"),
        (CONTENT_REVIEW_APPROVE_V1, "approve_idem"),
    ),
)
def test_terminal_receipt_replay_after_approve_preserves_durable_state(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    operation: str,
    idem_attr: str,
) -> None:
    scenario = build_terminal_approved_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
    )
    idem_key = getattr(scenario, idem_attr)
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )
    assert before.stewardship_state == StewardshipState.APPROVED.value
    receipt_before = load_idempotency_receipt(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        scenario.reviewer_id,
        operation=operation,
        idempotency_key=idem_key,
    )
    assert receipt_before is not None
    outbox_before = outbox_count_for_event_type(
        ci03_g2_bootstrap_engine,
        scenario.tenant_id,
        TERMINAL_APPROVE_OUTBOX_EVENT,
    )
    replay_revision = scenario.attest_effect_revision
    if operation == CONTENT_WORKSHEET_HINT_ATTEST_V1:
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            scenario.tenant_id,
            clock=lambda: _NOW,
            school_context_provider=scenario.provider,
        )
        attest.record(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=AggregateRevision(replay_revision),
            per_question_outcomes=scenario.outcomes,
            idempotency_key=idem_key,
            event_context=_event(scenario.reviewer_id),
            audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
            now=_NOW,
        )
    else:
        review = integrated_review_service(
            ci03_g2_runtime_engine,
            scenario.tenant_id,
            clock=lambda: _NOW,
            school_context_provider=scenario.provider,
        )
        review.approve(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=AggregateRevision(replay_revision),
            reason_code=None,
            comment=None,
            idempotency_key=idem_key,
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
    assert after == before
    receipt_after = load_idempotency_receipt(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        scenario.reviewer_id,
        operation=operation,
        idempotency_key=idem_key,
    )
    assert receipt_after == receipt_before
    assert (
        outbox_count_for_event_type(
            ci03_g2_bootstrap_engine,
            scenario.tenant_id,
            EVENT_CONTENT_REVIEW_APPROVED_V1,
        )
        == outbox_before
    )


@pytest.mark.parametrize(
    ("effect", "operation"),
    (
        ("reject", CONTENT_REVIEW_REJECT_V1),
        ("request_changes", CONTENT_REVIEW_REQUEST_CHANGES_V1),
    ),
)
def test_new_terminal_effect_denied_after_approve(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    effect: str,
    operation: str,
) -> None:
    scenario = build_terminal_approved_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
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
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=scenario.reviewer_id,
        effective_actor_id=scenario.reviewer_id,
    )
    kwargs = dict(
        execution_tenant_id=scenario.tenant_id,
        principal_id=scenario.reviewer_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        expected_aggregate_revision=scenario.revision,
        idempotency_key=f"b3r1-new-{effect}-{uuid.uuid7()}",
        event_context=event,
        audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
        now=_NOW,
    )
    with pytest.raises(
        (ReviewAlreadyDecided, ReviewDecisionNotAllowed, ReviewAssignmentNotAllowed)
    ):
        if effect == "reject":
            review.reject(**kwargs, reason_code="governed", comment="deny")
        else:
            review.request_changes(**kwargs, reason_code="governed", comment="chg")
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
    assert (
        load_idempotency_receipt(
            ci03_g2_runtime_engine,
            scenario.tenant_id,
            scenario.reviewer_id,
            operation=operation,
            idempotency_key=kwargs["idempotency_key"],
        )
        is None
    )