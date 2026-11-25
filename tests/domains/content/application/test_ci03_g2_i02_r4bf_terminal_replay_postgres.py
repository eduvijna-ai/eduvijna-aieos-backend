"""CI03-G2-I02R4-BF — terminal REJECT / REQUEST_CHANGES receipt replay (PG18)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_REJECT_V1,
    CONTENT_REVIEW_REQUEST_CHANGES_V1,
)
from tests.domains.content.application.helpers_ci03_g2_i01 import integrated_review_service
from tests.domains.content.application.helpers_ci03_g2_i02_r4b3r1 import (
    TERMINAL_REJECT_OUTBOX_EVENT,
    TERMINAL_REQUEST_CHANGES_OUTBOX_EVENT,
    build_terminal_rejected_scenario,
    build_terminal_request_changes_scenario,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    capture_durable_snapshot,
    load_idempotency_receipt,
    outbox_count_for_event_type,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
)

pytestmark = pytest.mark.ci03_g2_i02


@pytest.mark.parametrize(
    ("builder", "operation", "idem_attr", "outbox_event"),
    (
        (
            build_terminal_rejected_scenario,
            CONTENT_REVIEW_REJECT_V1,
            "reject_idem",
            TERMINAL_REJECT_OUTBOX_EVENT,
        ),
        (
            build_terminal_request_changes_scenario,
            CONTENT_REVIEW_REQUEST_CHANGES_V1,
            "request_changes_idem",
            TERMINAL_REQUEST_CHANGES_OUTBOX_EVENT,
        ),
    ),
)
def test_terminal_receipt_replay_after_terminal_decision_preserves_durable_state(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    builder,
    operation: str,
    idem_attr: str,
    outbox_event: str,
) -> None:
    scenario = builder(ci03_g2_bootstrap_engine, ci03_g2_runtime_engine)
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
    assert before.stewardship_state == StewardshipState.GENERATED.value
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
        outbox_event,
    )
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        scenario.tenant_id,
        clock=lambda: _NOW,
        school_context_provider=scenario.provider,
    )
    if operation == CONTENT_REVIEW_REJECT_V1:
        review.reject(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=scenario.revision,
            reason_code="governed",
            comment="reject",
            idempotency_key=idem_key,
            event_context=_event(scenario.reviewer_id),
            audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
            now=_NOW,
        )
    else:
        review.request_changes(
            scenario.tenant_id,
            scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            expected_aggregate_revision=scenario.revision,
            reason_code="governed",
            comment="changes",
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
            outbox_event,
        )
        == outbox_before
    )