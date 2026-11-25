"""CI03-G2-I02R4-BF — issuer/reviewer integrated authority losses (PG18)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ReviewAssignmentForbidden,
    ReviewForbidden,
)
from aieos.domains.content.application.ports import CONTENT_REVIEW_ASSIGN
from aieos.platform.events.models import MutationEventContext
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_latest_denial_matches,
    capture_durable_snapshot,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
    _services,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.platform.security.authorization.helpers import revoke_grant

pytestmark = pytest.mark.ci03_g2_i02


def test_issuer_assign_revoked_denies_attest_as_bound_reviewer(
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
        revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    revoke_grant(
        ci03_g2_bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        capability=CONTENT_REVIEW_ASSIGN,
    )
    issue, revoke, _, *_ = _services(ci03_g2_runtime_engine, tenant_id)
    event = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=owner_id,
        effective_actor_id=owner_id,
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
    with pytest.raises((ReviewForbidden, ReviewAssignmentForbidden)):
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            expected_assignment_id=assignment_id,
            idempotency_key=f"bf-issuer-revoke-{uuid.uuid7()}",
            event_context=event,
            audit_provenance=api_mutation_audit_provenance(owner_id),
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
    assert after.slot_active_assignment == before.slot_active_assignment
    assert after.mutation_denials == before.mutation_denials + 1
    assert_latest_denial_matches(
        ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        content_id=content_id,
        version_id=version_id,
        correlation_id=event.correlation_id,
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
        idempotency_key=f"bf-issuer-unchanged-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    _ = issue  # issuer still callable for other tenants; revoke denied only