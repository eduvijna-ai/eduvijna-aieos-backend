"""Tenant/resource-scoped durable effect snapshots for CI03-G2-I02 Stage B proofs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, ReviewAssignmentId
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import _denial_count

_WORKSHEET_HINT_ATTEST_AUDIT = "content.worksheet_hint.attest"
_REVIEW_DECISION_AUDIT_PREFIX = "content.review."


@dataclass(frozen=True, slots=True)
class DurableEffectSnapshot:
    attestations: int
    decisions: int
    mutation_denials: int
    reviewer_idempotency_success: int
    head_revision: int
    slot_active_assignment: uuid.UUID | None
    revocation_rows: int
    worksheet_hint_attest_audits: int
    outbox_messages: int


def capture_durable_snapshot(
    *,
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    assignment_id: ReviewAssignmentId,
    reviewer_principal_id: uuid.UUID,
) -> DurableEffectSnapshot:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        attestations = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
        )
        decisions = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_decisions
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
        )
        reviewer_idempotency_success = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM api.idempotency_records
                    WHERE tenant_id = :tid AND actor_principal_id = :pid
                    """
                ),
                {"tid": tenant_id, "pid": reviewer_principal_id},
            ).scalar_one()
        )
        head_revision = int(
            conn.execute(
                text(
                    "SELECT aggregate_revision FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": content_id.value},
            ).scalar_one()
        )
        slot_active = conn.execute(
            text(
                """
                SELECT active_assignment_id FROM content.review_current_assignments
                WHERE content_id = :cid AND version_id = :vid
                """
            ),
            {"cid": content_id.value, "vid": version_id.value},
        ).scalar_one_or_none()
        revocation_rows = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_assignment_revocations
                    WHERE assignment_id = :aid
                    """
                ),
                {"aid": assignment_id.value},
            ).scalar_one()
        )
    with bootstrap_engine.connect() as conn:
        worksheet_hint_attest_audits = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid AND action = :action
                    """
                ),
                {"tid": tenant_id, "action": _WORKSHEET_HINT_ATTEST_AUDIT},
            ).scalar_one()
        )
        outbox_messages = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE tenant_id = :tid
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )

    return DurableEffectSnapshot(
        attestations=attestations,
        decisions=decisions,
        mutation_denials=_denial_count(
            runtime_engine, tenant_id, CONTENT_REVIEW_MUTATION_DENIED
        ),
        reviewer_idempotency_success=reviewer_idempotency_success,
        head_revision=head_revision,
        slot_active_assignment=slot_active,
        revocation_rows=revocation_rows,
        worksheet_hint_attest_audits=worksheet_hint_attest_audits,
        outbox_messages=outbox_messages,
    )


def assignment_valid_until(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    assignment_id: ReviewAssignmentId,
):
    from datetime import datetime

    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        row = conn.execute(
            text(
                """
                SELECT valid_until FROM content.review_assignment_issuances
                WHERE assignment_id = :aid AND tenant_id = :tid
                """
            ),
            {"aid": assignment_id.value, "tid": tenant_id},
        ).scalar_one()
        assert isinstance(row, datetime)
        return row


def assert_snapshot_unchanged_except_denial(
    before: DurableEffectSnapshot, after: DurableEffectSnapshot
) -> None:
    assert after.attestations == before.attestations
    assert after.decisions == before.decisions
    assert after.reviewer_idempotency_success == before.reviewer_idempotency_success
    assert after.head_revision == before.head_revision
    assert after.slot_active_assignment == before.slot_active_assignment
    assert after.revocation_rows == before.revocation_rows
    assert after.worksheet_hint_attest_audits == before.worksheet_hint_attest_audits
    assert after.outbox_messages == before.outbox_messages
    assert after.mutation_denials == before.mutation_denials + 1