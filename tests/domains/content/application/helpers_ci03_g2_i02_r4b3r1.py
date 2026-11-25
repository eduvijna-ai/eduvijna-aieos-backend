"""Shared governed scenarios for CI03-G2-I02R4-B3R1 proofs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.domain.states import StewardshipState
from aieos.platform.events.constants import (
    EVENT_CONTENT_REVIEW_APPROVED_V1,
    EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
    EVENT_CONTENT_REVIEW_REJECTED_V1,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_REVIEW_REJECT_V1,
    CONTENT_REVIEW_REQUEST_CHANGES_V1,
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
)
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import _event
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import _NOW


@dataclass(frozen=True, slots=True)
class TerminalApprovedScenario:
    tenant_id: uuid.UUID
    reviewer_id: uuid.UUID
    assignment_id: object
    content_id: ContentId
    version_id: ContentVersionId
    revision: AggregateRevision
    attest_effect_revision: AggregateRevision
    provider: object
    attest_idem: str
    approve_idem: str
    approve_correlation: uuid.UUID
    outcomes: list[dict[str, object]]


def build_terminal_approved_scenario(
    bootstrap: Engine,
    runtime: Engine,
) -> TerminalApprovedScenario:
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
    ) = _bound_hint_scenario(bootstrap, runtime, clock=lambda: _NOW)
    outcomes = _positive_outcomes(_hinted_ids_from_version(runtime, tenant_id, version_id))
    attest = integrated_attest_service(
        runtime,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    attest_idem = f"b3r1-term-attest-{uuid.uuid7()}"
    attest_effect_revision = revision
    attest.record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=outcomes,
        idempotency_key=attest_idem,
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    approve_evt = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=reviewer_id,
        effective_actor_id=reviewer_id,
    )
    review = integrated_review_service(
        runtime,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    approve_idem = f"b3r1-term-approve-{uuid.uuid7()}"
    approved = review.approve(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        reason_code=None,
        comment=None,
        idempotency_key=approve_idem,
        event_context=approve_evt,
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    revision = approved.aggregate_revision
    with runtime.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        state = conn.execute(
            text(
                "SELECT stewardship_state FROM content.contents WHERE content_id = :cid"
            ),
            {"cid": content_id.value},
        ).scalar_one()
        assert state == StewardshipState.APPROVED.value
    return TerminalApprovedScenario(
        tenant_id=tenant_id,
        reviewer_id=reviewer_id,
        assignment_id=assignment_id,
        content_id=content_id,
        version_id=version_id,
        revision=revision,
        attest_effect_revision=attest_effect_revision,
        provider=provider,
        attest_idem=attest_idem,
        approve_idem=approve_idem,
        approve_correlation=approve_evt.correlation_id,
        outcomes=outcomes,
    )


TERMINAL_REPLAY_OPERATIONS = (
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
    CONTENT_REVIEW_APPROVE_V1,
)

TERMINAL_APPROVE_OUTBOX_EVENT = EVENT_CONTENT_REVIEW_APPROVED_V1
TERMINAL_REJECT_OUTBOX_EVENT = EVENT_CONTENT_REVIEW_REJECTED_V1
TERMINAL_REQUEST_CHANGES_OUTBOX_EVENT = EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1


@dataclass(frozen=True, slots=True)
class TerminalRejectedScenario:
    tenant_id: uuid.UUID
    reviewer_id: uuid.UUID
    assignment_id: object
    content_id: ContentId
    version_id: ContentVersionId
    revision: AggregateRevision
    provider: object
    reject_idem: str
    outcomes: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class TerminalRequestChangesScenario:
    tenant_id: uuid.UUID
    reviewer_id: uuid.UUID
    assignment_id: object
    content_id: ContentId
    version_id: ContentVersionId
    revision: AggregateRevision
    provider: object
    request_changes_idem: str
    outcomes: list[dict[str, object]]


def build_terminal_rejected_scenario(
    bootstrap: Engine,
    runtime: Engine,
) -> TerminalRejectedScenario:
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
    ) = _bound_hint_scenario(bootstrap, runtime, clock=lambda: _NOW)
    outcomes = _positive_outcomes(_hinted_ids_from_version(runtime, tenant_id, version_id))
    review = integrated_review_service(
        runtime,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    reject_idem = f"bf-term-reject-{uuid.uuid7()}"
    decision = review.reject(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        reason_code="governed",
        comment="reject",
        idempotency_key=reject_idem,
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    revision = decision.aggregate_revision
    with runtime.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        state = conn.execute(
            text(
                "SELECT stewardship_state FROM content.contents WHERE content_id = :cid"
            ),
            {"cid": content_id.value},
        ).scalar_one()
        assert state == StewardshipState.GENERATED.value
    return TerminalRejectedScenario(
        tenant_id=tenant_id,
        reviewer_id=reviewer_id,
        assignment_id=assignment_id,
        content_id=content_id,
        version_id=version_id,
        revision=revision,
        provider=provider,
        reject_idem=reject_idem,
        outcomes=outcomes,
    )


def build_terminal_request_changes_scenario(
    bootstrap: Engine,
    runtime: Engine,
) -> TerminalRequestChangesScenario:
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
    ) = _bound_hint_scenario(bootstrap, runtime, clock=lambda: _NOW)
    outcomes = _positive_outcomes(_hinted_ids_from_version(runtime, tenant_id, version_id))
    review = integrated_review_service(
        runtime,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    rc_idem = f"bf-term-rc-{uuid.uuid7()}"
    decision = review.request_changes(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        reason_code="governed",
        comment="changes",
        idempotency_key=rc_idem,
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    revision = decision.aggregate_revision
    with runtime.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        state = conn.execute(
            text(
                "SELECT stewardship_state FROM content.contents WHERE content_id = :cid"
            ),
            {"cid": content_id.value},
        ).scalar_one()
        assert state == StewardshipState.GENERATED.value
    return TerminalRequestChangesScenario(
        tenant_id=tenant_id,
        reviewer_id=reviewer_id,
        assignment_id=assignment_id,
        content_id=content_id,
        version_id=version_id,
        revision=revision,
        provider=provider,
        request_changes_idem=rc_idem,
        outcomes=outcomes,
    )