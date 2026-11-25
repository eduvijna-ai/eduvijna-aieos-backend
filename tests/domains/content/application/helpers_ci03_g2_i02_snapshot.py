"""Tenant/resource-scoped durable effect snapshots for CI03-G2-I02 Stage B proofs."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.review_assignment_bound_mutations import (
    fingerprint_assignment_id,
)
from aieos.domains.content.domain.review import (
    normalize_reason_code,
    normalize_review_comment,
)
from aieos.domains.education.worksheet_hint_qa import WORKSHEET_HINT_QA_POLICY_VERSION
from aieos.platform.idempotency.hashing import fingerprint_material

from aieos.domains.content.application.errors import ReviewAssignmentConflict
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.application.review_assignment_denial_audit import (
    REASON_ASSIGNMENT_CONFLICT,
)
from aieos.domains.content.domain.identities import (
    AggregateRevision,
    ContentId,
    ContentVersionId,
    ReviewAssignmentId,
)
from aieos.platform.events.constants import (
    EVENT_CONTENT_REVIEW_APPROVED_V1,
    EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
    EVENT_CONTENT_REVIEW_REJECTED_V1,
)
from aieos.platform.idempotency.hashing import hash_idempotency_key
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_REVIEW_REJECT_V1,
    CONTENT_REVIEW_REQUEST_CHANGES_V1,
    CONTENT_REVIEW_REVOKE_V1,
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
)
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
    assert after.attestations == before.attestations
    assert after.decisions == before.decisions
    assert after.reviewer_idempotency_success == before.reviewer_idempotency_success
    assert after.head_revision == before.head_revision
    assert after.stewardship_state == before.stewardship_state
    assert after.assignment_issuance_rows == before.assignment_issuance_rows
    assert after.worksheet_hint_attest_audits == before.worksheet_hint_attest_audits
    assert after.review_decision_audits == before.review_decision_audits
    assert after.outbox_messages == before.outbox_messages
    assert after.review_revoke_audits == before.review_revoke_audits + 1
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


def assert_expiry_denial_without_revoke(
    before: DurableEffectSnapshot, after: DurableEffectSnapshot
) -> None:
    """Expiry path: slot still active, no revocation row added."""
    assert_snapshot_unchanged_except_denial(before, after)
    assert after.slot_active_assignment == before.slot_active_assignment
    assert after.slot_active_assignment is not None
    assert after.revocation_rows == before.revocation_rows
    assert after.review_revoke_audits == before.review_revoke_audits


@dataclass(frozen=True, slots=True)
class IdempotencyReceiptView:
    idempotency_record_id: uuid.UUID
    request_fingerprint_sha256: str
    result_content_id: uuid.UUID
    result_version_id: uuid.UUID | None
    result_worksheet_hint_attestation_id: uuid.UUID | None
    result_review_decision_id: uuid.UUID | None
    result_aggregate_revision: int


def load_idempotency_receipt(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    *,
    operation: str,
    idempotency_key: str,
) -> IdempotencyReceiptView | None:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        row = conn.execute(
            text(
                """
                SELECT idempotency_record_id, request_fingerprint_sha256,
                       result_content_id, result_version_id,
                       result_worksheet_hint_attestation_id, result_review_decision_id,
                       result_aggregate_revision
                FROM api.idempotency_records
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
        ).first()
    if row is None:
        return None
    return IdempotencyReceiptView(
        idempotency_record_id=row[0],
        request_fingerprint_sha256=str(row[1]),
        result_content_id=row[2],
        result_version_id=row[3],
        result_worksheet_hint_attestation_id=row[4],
        result_review_decision_id=row[5],
        result_aggregate_revision=int(row[6]),
    )


def outbox_count_for_event_type(
    bootstrap_engine: Engine, tenant_id: uuid.UUID, event_type: str
) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM integration.outbox_messages
                    WHERE tenant_id = :tid AND event_type = :etype
                    """
                ),
                {"tid": tenant_id, "etype": event_type},
            ).scalar_one()
        )


def mutation_denial_count_for_correlation(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    correlation_id: uuid.UUID,
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
                    SELECT count(*) FROM security.authorization_denial_records
                    WHERE tenant_id = :tid
                      AND correlation_id = :cid
                      AND action = :action
                    """
                ),
                {
                    "tid": tenant_id,
                    "cid": correlation_id,
                    "action": CONTENT_REVIEW_MUTATION_DENIED,
                },
            ).scalar_one()
        )


def audit_count_for_correlation(
    bootstrap_engine: Engine, tenant_id: uuid.UUID, correlation_id: uuid.UUID
) -> int:
    with bootstrap_engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.audit_records
                    WHERE tenant_id = :tid AND correlation_id = :cid
                    """
                ),
                {"tid": tenant_id, "cid": correlation_id},
            ).scalar_one()
        )


@dataclass(frozen=True, slots=True)
class MutationCommandSpec:
    effect: str
    operation: str
    audit_action: str
    outbox_event_type: str | None


SUCCESS_MUTATION_COMMAND_SPECS: dict[str, MutationCommandSpec] = {
    "attest": MutationCommandSpec(
        "attest",
        CONTENT_WORKSHEET_HINT_ATTEST_V1,
        _WORKSHEET_HINT_ATTEST_AUDIT,
        None,
    ),
    "approve": MutationCommandSpec(
        "approve",
        CONTENT_REVIEW_APPROVE_V1,
        "content.review.approve",
        EVENT_CONTENT_REVIEW_APPROVED_V1,
    ),
    "reject": MutationCommandSpec(
        "reject",
        CONTENT_REVIEW_REJECT_V1,
        "content.review.reject",
        EVENT_CONTENT_REVIEW_REJECTED_V1,
    ),
    "request_changes": MutationCommandSpec(
        "request_changes",
        CONTENT_REVIEW_REQUEST_CHANGES_V1,
        "content.review.request_changes",
        EVENT_CONTENT_REVIEW_CHANGES_REQUESTED_V1,
    ),
    "revoke": MutationCommandSpec(
        "revoke",
        CONTENT_REVIEW_REVOKE_V1,
        _REVIEW_REVOKE_AUDIT,
        None,
    ),
}


def load_success_mutation_audit(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    correlation_id: uuid.UUID,
    audit_action: str,
) -> tuple[str, uuid.UUID, uuid.UUID, int] | None:
    with bootstrap_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT action, primary_resource_id, executing_principal_id,
                       resource_revision_after
                FROM security.audit_records
                WHERE tenant_id = :tid
                  AND correlation_id = :cid
                  AND action = :action
                ORDER BY occurred_at DESC
                LIMIT 1
                """
            ),
            {"tid": tenant_id, "cid": correlation_id, "action": audit_action},
        ).first()
    if row is None:
        return None
    return str(row[0]), row[1], row[2], int(row[3])


def outbox_correlation_match_count(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    correlation_id: uuid.UUID,
    event_type: str | None = None,
    content_id: uuid.UUID | None = None,
    aggregate_revision: int | None = None,
) -> int:
    with bootstrap_engine.connect() as conn:
        clauses = [
            "tenant_id = :tid",
            "envelope->>'correlationid' = :cid",
        ]
        params: dict[str, object] = {"tid": tenant_id, "cid": str(correlation_id)}
        if event_type is not None:
            clauses.append("event_type = :etype")
            params["etype"] = event_type
        if content_id is not None:
            clauses.append("aggregate_id = :cid2")
            params["cid2"] = content_id
        if aggregate_revision is not None:
            clauses.append("aggregate_revision = :rev")
            params["rev"] = aggregate_revision
        where = " AND ".join(clauses)
        return int(
            conn.execute(
                text(f"SELECT count(*) FROM integration.outbox_messages WHERE {where}"),
                params,
            ).scalar_one()
        )


def load_outbox_for_correlation(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    content_id: uuid.UUID,
    aggregate_revision: int,
    correlation_id: uuid.UUID,
) -> bool:
    return (
        outbox_correlation_match_count(
            bootstrap_engine,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            event_type=event_type,
            content_id=content_id,
            aggregate_revision=aggregate_revision,
        )
        == 1
    )


def assert_outbox_command_correlation(
    bootstrap_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    correlation_id: uuid.UUID,
    event_type: str | None,
    content_id: uuid.UUID | None = None,
    aggregate_revision: int | None = None,
) -> None:
    """Exactly one outbox row for this command correlation, or zero when contract omits outbox."""
    count = outbox_correlation_match_count(
        bootstrap_engine,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        event_type=event_type,
        content_id=content_id,
        aggregate_revision=aggregate_revision,
    )
    if event_type is None:
        assert count == 0
    else:
        assert count == 1
    total_for_correlation = outbox_correlation_match_count(
        bootstrap_engine,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
    )
    assert total_for_correlation == count


def expected_attest_request_fingerprint(
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    expected_aggregate_revision: int,
    per_question_outcomes: Sequence[Mapping[str, object]],
    assignment_id: ReviewAssignmentId | None = None,
) -> str:
    assignment_fp = fingerprint_assignment_id(
        assignment_id_for_fp=assignment_id,
        replay_assignment_id=None,
    )
    material: dict[str, object] = {
        "content_id": str(content_id),
        "version_id": str(version_id),
        "expected_aggregate_revision": int(expected_aggregate_revision),
        "qa_policy_version": WORKSHEET_HINT_QA_POLICY_VERSION,
        "per_question_outcomes": list(per_question_outcomes),
    }
    if assignment_fp is not None:
        material["review_assignment_id"] = assignment_fp
    return fingerprint_material(material)


def expected_review_request_fingerprint(
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    request_aggregate_revision: int,
    assignment_id: ReviewAssignmentId | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
) -> str:
    from aieos.domains.content.application.review import _review_fingerprint

    code = normalize_reason_code(reason_code)
    text = normalize_review_comment(comment)
    return _review_fingerprint(
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=AggregateRevision(request_aggregate_revision),
        reason_code=code,
        comment=text,
        include_comment=True,
        review_assignment_id=assignment_id,
    )


def expected_revoke_request_fingerprint(
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    assignment_id: ReviewAssignmentId,
) -> str:
    return fingerprint_material(
        {
            "content_id": str(content_id),
            "version_id": str(version_id),
            "assignment_id": str(assignment_id),
        }
    )


def expected_result_aggregate_revision(*, effect: str, request_aggregate_revision: int) -> int:
    if effect in ("approve", "reject", "request_changes"):
        return request_aggregate_revision + 1
    return request_aggregate_revision


def _assert_canonical_fingerprint(stored: str) -> None:
    assert len(stored) == 64
    assert stored == stored.lower()
    assert all(ch in "0123456789abcdef" for ch in stored)


def assert_persisted_mutation_effect_identity(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    effect: str,
    receipt: IdempotencyReceiptView,
    assignment_id: ReviewAssignmentId | None,
    effect_clock: datetime | None,
) -> None:
    _assert_canonical_fingerprint(receipt.request_fingerprint_sha256)
    if effect == "attest":
        assert receipt.result_worksheet_hint_attestation_id is not None
        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            row = conn.execute(
                text(
                    """
                    SELECT attested_at, review_assignment_id
                    FROM content.worksheet_learner_hint_version_attestations
                    WHERE attestation_id = :aid
                    """
                ),
                {"aid": receipt.result_worksheet_hint_attestation_id},
            ).one()
        if assignment_id is not None:
            assert row[1] == assignment_id.value
        if effect_clock is not None:
            assert row[0] == effect_clock
    elif effect in ("approve", "reject", "request_changes"):
        assert receipt.result_review_decision_id is not None
        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            row = conn.execute(
                text(
                    """
                    SELECT decided_at, review_assignment_id
                    FROM content.review_decisions
                    WHERE review_decision_id = :did
                    """
                ),
                {"did": receipt.result_review_decision_id},
            ).one()
        if assignment_id is not None:
            assert row[1] == assignment_id.value
        if effect_clock is not None:
            assert row[0] == effect_clock
    elif effect == "revoke":
        assert assignment_id is not None
        with runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            row = conn.execute(
                text(
                    """
                    SELECT revoked_at FROM content.review_assignment_revocations
                    WHERE assignment_id = :aid AND tenant_id = :tid
                    ORDER BY revoked_at DESC
                    LIMIT 1
                    """
                ),
                {"aid": assignment_id.value, "tid": tenant_id},
            ).one()
        if effect_clock is not None:
            assert row[0] == effect_clock


def assert_success_mutation_command_evidence(
    *,
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    effect: str,
    idempotency_key: str,
    correlation_id: uuid.UUID,
    assignment_id: ReviewAssignmentId | None = None,
    request_aggregate_revision: int | None = None,
    expected_result_aggregate_revision: int | None = None,
    effect_clock: datetime | None = None,
    per_question_outcomes: Sequence[Mapping[str, object]] | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
) -> None:
    spec = SUCCESS_MUTATION_COMMAND_SPECS[effect]
    receipt = load_idempotency_receipt(
        runtime_engine,
        tenant_id,
        principal_id,
        operation=spec.operation,
        idempotency_key=idempotency_key,
    )
    assert receipt is not None, f"missing idempotency receipt for {effect}"
    if effect == "attest":
        assert request_aggregate_revision is not None
        assert per_question_outcomes is not None
        expected_fp = expected_attest_request_fingerprint(
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=request_aggregate_revision,
            per_question_outcomes=per_question_outcomes,
            assignment_id=assignment_id,
        )
        assert receipt.request_fingerprint_sha256 == expected_fp
    elif effect in ("approve", "reject", "request_changes"):
        assert request_aggregate_revision is not None
        expected_fp = expected_review_request_fingerprint(
            content_id=content_id,
            version_id=version_id,
            request_aggregate_revision=request_aggregate_revision,
            assignment_id=assignment_id,
            reason_code=reason_code,
            comment=comment,
        )
        assert receipt.request_fingerprint_sha256 == expected_fp
    elif effect == "revoke":
        assert assignment_id is not None
        expected_fp = expected_revoke_request_fingerprint(
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
        )
        assert receipt.request_fingerprint_sha256 == expected_fp
    if effect == "revoke":
        assert assignment_id is not None
        assert receipt.result_content_id == assignment_id.value
    else:
        assert receipt.result_content_id == content_id.value
    assert receipt.result_version_id == version_id.value
    if expected_result_aggregate_revision is not None:
        assert receipt.result_aggregate_revision == expected_result_aggregate_revision

    audit = load_success_mutation_audit(
        bootstrap_engine,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        audit_action=spec.audit_action,
    )
    assert audit is not None, f"missing audit for {effect}"
    audit_action, primary_resource_id, executing_principal_id, revision_after = audit
    assert audit_action == spec.audit_action
    assert primary_resource_id == content_id.value
    assert executing_principal_id == principal_id
    if expected_result_aggregate_revision is not None:
        assert revision_after == expected_result_aggregate_revision
    else:
        assert revision_after == receipt.result_aggregate_revision

    assert audit_count_for_correlation(bootstrap_engine, tenant_id, correlation_id) == 1

    assert_outbox_command_correlation(
        bootstrap_engine,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        event_type=spec.outbox_event_type,
        content_id=content_id.value if spec.outbox_event_type else None,
        aggregate_revision=(
            receipt.result_aggregate_revision if spec.outbox_event_type else None
        ),
    )
    assert_persisted_mutation_effect_identity(
        runtime_engine,
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        effect=effect,
        receipt=receipt,
        assignment_id=assignment_id,
        effect_clock=effect_clock,
    )


def assert_denial_command_no_receipt(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    effect: str,
    idempotency_key: str,
) -> None:
    spec = SUCCESS_MUTATION_COMMAND_SPECS[effect]
    assert (
        idempotency_success_count_for_key(
            runtime_engine,
            tenant_id,
            principal_id,
            operation=spec.operation,
            idempotency_key=idempotency_key,
        )
        == 0
    )


def assert_denied_protected_command_evidence(
    *,
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    content_id: ContentId,
    version_id: ContentVersionId,
    effect: str,
    idempotency_key: str,
    correlation_id: uuid.UUID,
    reason_code: str = REASON_ASSIGNMENT_CONFLICT,
) -> None:
    """Denied bound mutation: no success receipt, no business audit/outbox, exact denial row."""
    spec = SUCCESS_MUTATION_COMMAND_SPECS[effect]
    assert_denial_command_no_receipt(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        effect=effect,
        idempotency_key=idempotency_key,
    )
    assert_latest_denial_matches(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        content_id=content_id,
        version_id=version_id,
        correlation_id=correlation_id,
        reason_code=reason_code,
    )
    assert (
        load_success_mutation_audit(
            bootstrap_engine,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            audit_action=spec.audit_action,
        )
        is None
    )
    if spec.outbox_event_type is not None:
        assert (
            outbox_correlation_match_count(
                bootstrap_engine,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                event_type=spec.outbox_event_type,
                content_id=content_id.value,
            )
            == 0
        )
    assert (
        outbox_correlation_match_count(
            bootstrap_engine,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
        == 0
    )
    assert audit_count_for_correlation(bootstrap_engine, tenant_id, correlation_id) == 0
    assert (
        mutation_denial_count_for_correlation(
            runtime_engine,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
        == 1
    )


def assert_attestation_bound_assignment(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    version_id: ContentVersionId,
    assignment_id: ReviewAssignmentId,
) -> None:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        bound = conn.execute(
            text(
                """
                SELECT review_assignment_id
                FROM content.worksheet_learner_hint_version_attestations
                WHERE tenant_id = :tid AND version_id = :vid
                ORDER BY attested_at DESC
                LIMIT 1
                """
            ),
            {"tid": tenant_id, "vid": version_id.value},
        ).scalar_one_or_none()
    assert bound == assignment_id.value


def assert_decision_bound_assignment(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    version_id: ContentVersionId,
    assignment_id: ReviewAssignmentId,
) -> None:
    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        bound = conn.execute(
            text(
                """
                SELECT review_assignment_id
                FROM content.review_decisions
                WHERE tenant_id = :tid AND version_id = :vid
                ORDER BY decided_at DESC
                LIMIT 1
                """
            ),
            {"tid": tenant_id, "vid": version_id.value},
        ).scalar_one_or_none()
    assert bound == assignment_id.value


def assert_receipt_fingerprint_for_key(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    effect: str,
    idempotency_key: str,
) -> str:
    spec = SUCCESS_MUTATION_COMMAND_SPECS[effect]
    receipt = load_idempotency_receipt(
        runtime_engine,
        tenant_id,
        principal_id,
        operation=spec.operation,
        idempotency_key=idempotency_key,
    )
    assert receipt is not None
    _assert_canonical_fingerprint(receipt.request_fingerprint_sha256)
    return receipt.request_fingerprint_sha256


@dataclass(frozen=True, slots=True)
class MigrationSchemaFingerprint:
    alembic_head: str
    attest_bound_column: int
    decision_bound_column: int
    receipt_attest_result_column: int
    fk_attest_assignment: int
    fk_decision_assignment: int
    uq_issuance_resource_reviewer: int
    attest_rls_forced: bool | None
    decision_rls_forced: bool | None
    attest_fk_def: str | None
    decision_fk_def: str | None
    attest_table_owner: str | None
    decision_table_owner: str | None


def schema_fingerprint_ci03g020002(
    bootstrap_engine: Engine,
) -> MigrationSchemaFingerprint:
    """Alembic head, bound columns, FK anchors, and forced RLS for migration proofs."""
    with bootstrap_engine.connect() as conn:
        head = str(
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        )
        bound_attest = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_schema = 'content'
                      AND table_name = 'worksheet_learner_hint_version_attestations'
                      AND column_name = 'review_assignment_id'
                    """
                )
            ).scalar_one()
        )
        bound_decision = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_schema = 'content'
                      AND table_name = 'review_decisions'
                      AND column_name = 'review_assignment_id'
                    """
                )
            ).scalar_one()
        )
        receipt_col = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.columns
                    WHERE table_schema = 'api'
                      AND table_name = 'idempotency_records'
                      AND column_name = 'result_worksheet_hint_attestation_id'
                    """
                )
            ).scalar_one()
        )
        fk_attest = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.table_constraints
                    WHERE constraint_schema = 'content'
                      AND constraint_name = 'fk_worksheet_hint_attestations_assignment'
                    """
                )
            ).scalar_one()
        )
        fk_decision = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.table_constraints
                    WHERE constraint_schema = 'content'
                      AND constraint_name = 'fk_review_decisions_assignment'
                    """
                )
            ).scalar_one()
        )
        uq_issue = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM information_schema.table_constraints
                    WHERE constraint_schema = 'content'
                      AND constraint_name = 'uq_review_assignment_issuances_resource_reviewer'
                    """
                )
            ).scalar_one()
        )
        rls_attest = conn.execute(
            text(
                """
                SELECT c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'content'
                  AND c.relname = 'worksheet_learner_hint_version_attestations'
                """
            )
        ).scalar_one_or_none()
        rls_decision = conn.execute(
            text(
                """
                SELECT c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'content'
                  AND c.relname = 'review_decisions'
                """
            )
        ).scalar_one_or_none()
        attest_fk_def = (
            conn.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conname = 'fk_worksheet_hint_attestations_assignment'
                    """
                )
            ).scalar_one_or_none()
            if fk_attest
            else None
        )
        decision_fk_def = (
            conn.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE conname = 'fk_review_decisions_assignment'
                    """
                )
            ).scalar_one_or_none()
            if fk_decision
            else None
        )
        attest_owner = conn.execute(
            text(
                """
                SELECT pg_get_userbyid(c.relowner)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'content'
                  AND c.relname = 'worksheet_learner_hint_version_attestations'
                """
            )
        ).scalar_one_or_none()
        decision_owner = conn.execute(
            text(
                """
                SELECT pg_get_userbyid(c.relowner)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'content'
                  AND c.relname = 'review_decisions'
                """
            )
        ).scalar_one_or_none()
    return MigrationSchemaFingerprint(
        alembic_head=head,
        attest_bound_column=bound_attest,
        decision_bound_column=bound_decision,
        receipt_attest_result_column=receipt_col,
        fk_attest_assignment=fk_attest,
        fk_decision_assignment=fk_decision,
        uq_issuance_resource_reviewer=uq_issue,
        attest_rls_forced=bool(rls_attest) if rls_attest is not None else None,
        decision_rls_forced=bool(rls_decision) if rls_decision is not None else None,
        attest_fk_def=str(attest_fk_def) if attest_fk_def else None,
        decision_fk_def=str(decision_fk_def) if decision_fk_def else None,
        attest_table_owner=str(attest_owner) if attest_owner else None,
        decision_table_owner=str(decision_owner) if decision_owner else None,
    )


def _canonical_tuple_digest(row: tuple[object, ...]) -> str:
    payload = json.dumps(row, default=str, sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sorted_immutable_attestation_digests(conn) -> tuple[str, ...]:
    rows = conn.execute(
        text(
            """
            SELECT
                attestation_id::text,
                tenant_id::text,
                content_id::text,
                version_id::text,
                payload_digest,
                qa_policy_version,
                review_assignment_id::text,
                attested_at::text,
                correlation_id::text,
                md5(per_question_outcomes::text)
            FROM content.worksheet_learner_hint_version_attestations
            ORDER BY attestation_id
            """
        )
    ).fetchall()
    return tuple(_canonical_tuple_digest(tuple(r)) for r in rows)


def _sorted_immutable_decision_digests(conn) -> tuple[str, ...]:
    rows = conn.execute(
        text(
            """
            SELECT
                review_decision_id::text,
                tenant_id::text,
                content_id::text,
                version_id::text,
                decision,
                reason_code,
                comment,
                review_assignment_id::text,
                decided_at::text,
                correlation_id::text
            FROM content.review_decisions
            ORDER BY review_decision_id
            """
        )
    ).fetchall()
    return tuple(_canonical_tuple_digest(tuple(r)) for r in rows)


def _sorted_immutability_triggers(conn) -> tuple[tuple[str, str, str, str], ...]:
    rows = conn.execute(
        text(
            """
            SELECT n.nspname, c.relname, t.tgname, t.tgenabled::text
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'content'
              AND c.relname IN (
                'worksheet_learner_hint_version_attestations',
                'review_decisions'
              )
              AND NOT t.tgisinternal
            ORDER BY n.nspname, c.relname, t.tgname
            """
        )
    ).fetchall()
    return tuple((str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows)


def _sorted_rls_policies(conn) -> tuple[tuple[str, str, str, str, str], ...]:
    rows = conn.execute(
        text(
            """
            SELECT schemaname, tablename, policyname, permissive, cmd
            FROM pg_policies
            WHERE schemaname = 'content'
              AND tablename IN (
                'worksheet_learner_hint_version_attestations',
                'review_decisions'
              )
            ORDER BY tablename, policyname
            """
        )
    ).fetchall()
    return tuple((str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows)


def _sorted_idempotency_receipt_digests(conn) -> tuple[str, ...]:
    rows = conn.execute(
        text(
            """
            SELECT
                tenant_id::text,
                actor_principal_id::text,
                operation,
                request_fingerprint_sha256,
                result_content_id::text,
                result_version_id::text,
                result_aggregate_revision::text,
                result_worksheet_hint_attestation_id::text,
                result_review_decision_id::text
            FROM api.idempotency_records
            WHERE result_worksheet_hint_attestation_id IS NOT NULL
               OR result_review_decision_id IS NOT NULL
            ORDER BY tenant_id, actor_principal_id, operation, idempotency_key_sha256
            """
        )
    ).fetchall()
    return tuple(_canonical_tuple_digest(tuple(r)) for r in rows)


def _sorted_mutation_denial_digests(conn) -> tuple[str, ...]:
    rows = conn.execute(
        text(
            """
            SELECT
                tenant_id::text,
                principal_id::text,
                action,
                correlation_id::text,
                reason_code,
                content_id::text,
                version_id::text
            FROM security.authorization_denial_records
            WHERE action = 'content.review.mutation.denied'
            ORDER BY tenant_id, principal_id, correlation_id
            """
        )
    ).fetchall()
    return tuple(_canonical_tuple_digest(tuple(r)) for r in rows)


def _table_acl_fingerprints(conn) -> tuple[tuple[str, str], ...]:
    rows = conn.execute(
        text(
            """
            SELECT n.nspname, c.relname, pg_catalog.array_to_string(c.relacl, ',')
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'content'
              AND c.relname IN (
                'worksheet_learner_hint_version_attestations',
                'review_decisions'
              )
            ORDER BY n.nspname, c.relname
            """
        )
    ).fetchall()
    return tuple((f"{r[0]}.{r[1]}", str(r[2] or "")) for r in rows)


@dataclass(frozen=True, slots=True)
class MigrationRefusalSnapshot:
    schema_fingerprint: MigrationSchemaFingerprint
    bound_attestation_rows: int
    bound_decision_rows: int
    receipt_attest_result_rows: int
    mutation_denial_rows: int
    immutable_attestation_digests: tuple[str, ...]
    immutable_decision_digests: tuple[str, ...]
    immutability_triggers: tuple[tuple[str, str, str, str], ...]
    rls_policies: tuple[tuple[str, str, str, str, str], ...]
    table_acl_fingerprints: tuple[tuple[str, str], ...]
    idempotency_receipt_digests: tuple[str, ...]
    mutation_denial_digests: tuple[str, ...]


def capture_migration_refusal_snapshot(bootstrap_engine: Engine) -> MigrationRefusalSnapshot:
    fp = schema_fingerprint_ci03g020002(bootstrap_engine)
    with bootstrap_engine.connect() as conn:
        bound_attest = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE review_assignment_id IS NOT NULL
                    """
                )
            ).scalar_one()
        )
        bound_decisions = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_decisions
                    WHERE review_assignment_id IS NOT NULL
                    """
                )
            ).scalar_one()
        )
        receipt_attest = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM api.idempotency_records
                    WHERE result_worksheet_hint_attestation_id IS NOT NULL
                    """
                )
            ).scalar_one()
        )
        mutation_denials = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM security.authorization_denial_records
                    WHERE action = 'content.review.mutation.denied'
                    """
                )
            ).scalar_one()
        )
        attest_digests = _sorted_immutable_attestation_digests(conn)
        decision_digests = _sorted_immutable_decision_digests(conn)
        triggers = _sorted_immutability_triggers(conn)
        rls = _sorted_rls_policies(conn)
        acls = _table_acl_fingerprints(conn)
        receipt_digests = _sorted_idempotency_receipt_digests(conn)
        denial_digests = _sorted_mutation_denial_digests(conn)
    return MigrationRefusalSnapshot(
        schema_fingerprint=fp,
        bound_attestation_rows=bound_attest,
        bound_decision_rows=bound_decisions,
        receipt_attest_result_rows=receipt_attest,
        mutation_denial_rows=mutation_denials,
        immutable_attestation_digests=attest_digests,
        immutable_decision_digests=decision_digests,
        immutability_triggers=triggers,
        rls_policies=rls,
        table_acl_fingerprints=acls,
        idempotency_receipt_digests=receipt_digests,
        mutation_denial_digests=denial_digests,
    )