"""Tenant/resource-scoped durable effect snapshots for CI03-G2-I02 Stage B proofs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.errors import ReviewAssignmentConflict
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.application.review_assignment_denial_audit import (
    REASON_ASSIGNMENT_CONFLICT,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId, ReviewAssignmentId
from aieos.platform.idempotency.hashing import hash_idempotency_key
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import _denial_count

_WORKSHEET_HINT_ATTEST_AUDIT = "content.worksheet_hint.attest"
_REVIEW_DECISION_AUDIT_ACTIONS = (
    "content.review.approve",
    "content.review.reject",
    "content.review.request_changes",
)
_REVIEW_REVOKE_AUDIT = "content.review.revoke"


@dataclass(frozen=True, slots=True)
class DurableEffectSnapshot:
    attestations: int
    decisions: int
    mutation_denials: int
    reviewer_idempotency_success: int
    head_revision: int
    stewardship_state: str
    slot_active_assignment: uuid.UUID | None
    assignment_issuance_rows: int
    revocation_rows: int
    worksheet_hint_attest_audits: int
    review_decision_audits: int
    review_revoke_audits: int
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
                    WHERE tenant_id = :tid
                      AND actor_principal_id = :pid
                    """
                ),
                {"tid": tenant_id, "pid": reviewer_principal_id},
            ).scalar_one()
        )
        head_row = conn.execute(
            text(
                """
                SELECT aggregate_revision, stewardship_state
                FROM content.contents
                WHERE content_id = :cid
                """
            ),
            {"cid": content_id.value},
        ).one()
        head_revision = int(head_row[0])
        stewardship_state = str(head_row[1])
        slot_active = conn.execute(
            text(
                """
                SELECT active_assignment_id FROM content.review_current_assignments
                WHERE content_id = :cid AND version_id = :vid
                """
            ),
            {"cid": content_id.value, "vid": version_id.value},
        ).scalar_one_or_none()
        assignment_issuance_rows = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_assignment_issuances
                    WHERE tenant_id = :tid AND assignment_id = :aid
                    """
                ),
                {"tid": tenant_id, "aid": assignment_id.value},
            ).scalar_one()
        )
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
        review_decision_audits = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid
                      AND action IN (
                        'content.review.approve',
                        'content.review.reject',
                        'content.review.request_changes'
                      )
                    """
                ),
                {"tid": tenant_id},
            ).scalar_one()
        )
        review_revoke_audits = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid AND action = :action
                    """
                ),
                {"tid": tenant_id, "action": _REVIEW_REVOKE_AUDIT},
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
        stewardship_state=stewardship_state,
        slot_active_assignment=slot_active,
        assignment_issuance_rows=assignment_issuance_rows,
        revocation_rows=revocation_rows,
        worksheet_hint_attest_audits=worksheet_hint_attest_audits,
        review_decision_audits=review_decision_audits,
        review_revoke_audits=review_revoke_audits,
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


def idempotency_success_count_for_key(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    *,
    operation: str,
    idempotency_key: str,
) -> int:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM api.idempotency_records
                    WHERE tenant_id = :tid
                      AND actor_principal_id = :pid
                      AND operation = :op
                      AND idempotency_key_sha256 = :key
                    """
                ),
                {
                    "tid": tenant_id,
                    "pid": principal_id,
                    "op": operation,
                    "key": hash_idempotency_key(idempotency_key),
                },
            ).scalar_one()
        )


def latest_mutation_denial(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    *,
    principal_id: uuid.UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
) -> tuple[str, uuid.UUID] | None:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        row = conn.execute(
            text(
                """
                SELECT reason_code, correlation_id
                FROM security.authorization_denial_records
                WHERE tenant_id = :tid
                  AND action = :action
                  AND principal_id = :pid
                  AND content_id = :cid
                  AND version_id = :vid
                ORDER BY occurred_at DESC
                LIMIT 1
                """
            ),
            {
                "tid": tenant_id,
                "action": CONTENT_REVIEW_MUTATION_DENIED,
                "pid": principal_id,
                "cid": content_id.value,
                "vid": version_id.value,
            },
        ).first()
    if row is None:
        return None
    reason_code, correlation_id = row
    return str(reason_code), correlation_id


def assert_review_assignment_expiry_exception(exc: BaseException) -> None:
    assert isinstance(exc, ReviewAssignmentConflict)
    message = str(exc).lower()
    assert "revoked" in message or "expired" in message


def assert_review_assignment_conflict_denied(exc: BaseException) -> None:
    assert isinstance(exc, ReviewAssignmentConflict)
    message = str(exc).lower()
    assert any(
        token in message
        for token in (
            "revoked",
            "expired",
            "no active assignment",
        )
    )


def assert_latest_denial_matches(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    correlation_id: uuid.UUID,
    reason_code: str = REASON_ASSIGNMENT_CONFLICT,
) -> None:
    latest = latest_mutation_denial(
        runtime_engine,
        tenant_id,
        principal_id=principal_id,
        content_id=content_id,
        version_id=version_id,
    )
    assert latest is not None
    got_reason, got_correlation = latest
    assert got_reason == reason_code
    assert got_correlation == correlation_id


def _assert_durable_unchanged_core(
    before: DurableEffectSnapshot, after: DurableEffectSnapshot
) -> None:
    assert after.attestations == before.attestations
    assert after.decisions == before.decisions
    assert after.reviewer_idempotency_success == before.reviewer_idempotency_success
    assert after.head_revision == before.head_revision
    assert after.stewardship_state == before.stewardship_state
    assert after.assignment_issuance_rows == before.assignment_issuance_rows
    assert after.worksheet_hint_attest_audits == before.worksheet_hint_attest_audits
    assert after.review_decision_audits == before.review_decision_audits
    assert after.review_revoke_audits == before.review_revoke_audits
    assert after.outbox_messages == before.outbox_messages


def assert_protected_effect_denied_after_revoke(
    before: DurableEffectSnapshot, after: DurableEffectSnapshot
) -> None:
    _assert_durable_unchanged_core(before, after)
    assert after.slot_active_assignment is None
    assert after.revocation_rows == before.revocation_rows + 1
    assert after.mutation_denials == before.mutation_denials + 1


def assert_snapshot_unchanged_except_denial(
    before: DurableEffectSnapshot, after: DurableEffectSnapshot
) -> None:
    _assert_durable_unchanged_core(before, after)
    assert after.slot_active_assignment == before.slot_active_assignment
    assert after.revocation_rows == before.revocation_rows
    assert after.mutation_denials == before.mutation_denials + 1