"""CI03-G2-I02 assignment-bound attestation and review decisions — PG18 matrix."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import ReviewAssignmentConflict
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_ASSIGN,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.platform.events.models import MutationEventContext
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
    seed_worksheet_in_review_governed,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    CLASS_REF,
    _NOW,
    _denial_count,
    _event,
    _services,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.ci03_g2_i02


def _seed_reviewers(
    bootstrap_engine: Engine,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    reviewer_id: uuid.UUID,
) -> None:
    seed_active_authority(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_REVIEW_ASSIGN, CONTENT_REVIEW_SUBMIT),
    )
    seed_active_authority(
        bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=reviewer_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_REVIEW_DECIDE,),
    )


class TestCi03G2I02AssignmentBoundMutations:
    def test_attest_without_assignment_fails_closed(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        _seed_reviewers(ci03_g2_bootstrap_engine, tenant_id, owner_id, reviewer_id)
        content_id, version_id = seed_worksheet_in_review_governed(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            class_ref=CLASS_REF,
            now=_NOW,
        )
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            revision = conn.execute(
                text(
                    "SELECT aggregate_revision FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        attest = integrated_attest_service(ci03_g2_runtime_engine, tenant_id, clock=lambda: _NOW)
        with pytest.raises(ReviewAssignmentConflict, match="no active assignment"):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=AggregateRevision(int(revision)),
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
                ),
                idempotency_key=f"no-assign-{uuid.uuid7()}",
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )
        assert _denial_count(ci03_g2_runtime_engine, tenant_id, CONTENT_REVIEW_MUTATION_DENIED) >= 1

    def test_attest_and_approve_with_valid_assignment(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        _seed_reviewers(ci03_g2_bootstrap_engine, tenant_id, owner_id, reviewer_id)
        issue, _, provider, *_ = _services(ci03_g2_runtime_engine, tenant_id)
        provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
        provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
        content_id, version_id = seed_worksheet_in_review_governed(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            class_ref=CLASS_REF,
            now=_NOW,
        )
        issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key=f"bind-{uuid.uuid7()}",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            revision = conn.execute(
                text(
                    "SELECT aggregate_revision FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        attest = integrated_attest_service(ci03_g2_runtime_engine, tenant_id, clock=lambda: _NOW)
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=AggregateRevision(int(revision)),
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"bind-attest-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        review = integrated_review_service(ci03_g2_runtime_engine, tenant_id, clock=lambda: _NOW)
        review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=AggregateRevision(int(revision)),
            idempotency_key=f"bind-approve-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            row = conn.execute(
                text(
                    """
                    SELECT review_assignment_id FROM content.worksheet_learner_hint_version_attestations
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).one()
            decision_row = conn.execute(
                text(
                    """
                    SELECT review_assignment_id FROM content.review_decisions
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).one()
        assert row.review_assignment_id is not None
        assert decision_row.review_assignment_id == row.review_assignment_id

    def test_reject_requires_assignment(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        _seed_reviewers(ci03_g2_bootstrap_engine, tenant_id, owner_id, reviewer_id)
        content_id, version_id = seed_worksheet_in_review_governed(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            tenant_id=tenant_id,
            owner_id=owner_id,
            class_ref=CLASS_REF,
            now=_NOW,
        )
        with ci03_g2_runtime_engine.connect() as conn:
            revision = conn.execute(
                text(
                    "SELECT aggregate_revision FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        review = integrated_review_service(ci03_g2_runtime_engine, tenant_id, clock=lambda: _NOW)
        with pytest.raises(ReviewAssignmentConflict):
            review.reject(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=AggregateRevision(int(revision)),
                reason_code=None,
                comment=None,
                idempotency_key=f"rej-{uuid.uuid7()}",
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )