"""CI03-G2-I02R4-B — #80 normative acceptance proofs (Stage B, PG18).

Maps governing issue invariants to executable tests. Stage A 48-case matrix is
foundational only; this module adds architecture acceptance not claimed complete
by Stage A alone.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import WorksheetLearnerHintAttestationInvalid
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.platform.idempotency.models import CONTENT_WORKSHEET_HINT_ATTEST_V1
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
from tests.domains.content.application.test_ci03_g2_i02_mutation_postgres import (
    _seed_reviewers,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
pytestmark = pytest.mark.ci03_g2_i02

# B1 — honest acceptance inventory (#80). Status: EXECUTED | PARTIAL | GAP
I02_B_PROOF_ENTRIES: tuple[dict[str, str], ...] = (
    {
        "id": "O64-05_fk_binding",
        "test": "test_ci03_g2_i01_assignment_schema::test_bound_attestation_fk_requires_matching_reviewer_principal",
        "status": "EXECUTED",
    },
    {
        "id": "O64-04_attest_receipt_result_id",
        "test": "test_ci03_g2_i02_r4b_acceptance_postgres::test_bound_attest_idempotency_stores_attestation_result_id",
        "status": "EXECUTED",
    },
    {
        "id": "O64-04_request_fingerprint_mismatch_same_scope",
        "test": "test_ci03_g2_i02_r4b_acceptance_postgres::test_bound_attest_replay_rejects_request_fingerprint_mismatch_same_key",
        "status": "EXECUTED",
        "notes": "client payload change, not persisted receipt corruption",
    },
    {
        "id": "O64-04_persisted_fingerprint_corruption",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_attest_replay_rejects_corrupt_persisted_fingerprint",
        "status": "EXECUTED",
        "notes": "bootstrap-only idempotency row fault injection",
    },
    {
        "id": "O64-04_persisted_result_attestation_mismatch",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_attest_replay_rejects_mismatched_persisted_result_attestation_id",
        "status": "EXECUTED",
    },
    {
        "id": "O64-04_persisted_decision_receipt_mismatch",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_approve_replay_rejects_mismatched_decision_receipt_id",
        "status": "EXECUTED",
    },
    {
        "id": "O64-04_decision_receipt_binds_row",
        "test": "test_ci03_g2_i02_r4b_acceptance_postgres::test_approve_idempotency_receipt_binds_review_decision_id",
        "status": "EXECUTED",
    },
    {
        "id": "O64-03-C_four_effects_stage_a",
        "test": "test_ci03_g2_i02_mutation_postgres::TestCi03G2I02AssignmentBoundMutations",
        "status": "PARTIAL",
        "notes": "Stage A baseline; not full #80 matrix",
    },
    {
        "id": "O64-03-C_expiry_at_effect_four_mutations",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_mutation_denied_when_assignment_expired_at_effect",
        "status": "EXECUTED",
    },
    {
        "id": "O64-03-C_expiry_crossing_under_lock",
        "test": "test_ci03_g2_i01_fa01_foundation_proof::test_guard_expiry_crossing_during_slow_authority",
        "status": "PARTIAL",
        "notes": "guard-only; not integrated mutation service lock-wait",
    },
    {
        "id": "revoke_then_attest_ordering",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_revoke_before_attest_denies_attest",
        "status": "EXECUTED",
    },
    {
        "id": "attest_then_revoke_ordering",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_attest_before_revoke_succeeds_then_revoke_idempotent",
        "status": "EXECUTED",
    },
    {
        "id": "concurrent_pg_lock_races",
        "test": "(none)",
        "status": "GAP",
        "notes": "requires dual-connection lock barriers; revoke vs all decisions both orderings",
    },
    {
        "id": "publisher_after_reviewer_expiry_kernel_auth",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_publish_kernel_authorization_after_assignment_expired",
        "status": "EXECUTED",
    },
    {
        "id": "migration_receipt_column_downgrade_guard",
        "test": "ci03g020002 downgrade() receipt count refusal",
        "status": "EXECUTED",
        "notes": "source-level; schema evidence via test_downgrade_refuses_nonempty_assignment_evidence",
    },
    {
        "id": "outbox_exact_counts",
        "test": "(none)",
        "status": "GAP",
    },
)


def _idem_attest_result_id(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    *,
    idem_key: str | None = None,
) -> uuid.UUID | None:
    from aieos.platform.idempotency.hashing import hash_idempotency_key

    with runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        params: dict[str, object] = {
            "tid": tenant_id,
            "pid": principal_id,
            "op": CONTENT_WORKSHEET_HINT_ATTEST_V1,
        }
        sql = """
            SELECT result_worksheet_hint_attestation_id
            FROM api.idempotency_records
            WHERE tenant_id = :tid
              AND actor_principal_id = :pid
              AND operation = :op
        """
        if idem_key is not None:
            sql += " AND idempotency_key_sha256 = :key"
            params["key"] = hash_idempotency_key(idem_key)
        row = conn.execute(text(sql), params).scalar_one_or_none()
        return row


class TestCi03G2I02R4BAcceptance:
    def test_bound_attest_idempotency_stores_attestation_result_id(
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
            idempotency_key=f"b-res-{uuid.uuid7()}",
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
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        idem = f"b-attest-result-{uuid.uuid7()}"
        att_id = attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=AggregateRevision(int(revision)),
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        receipt_id = _idem_attest_result_id(
            ci03_g2_runtime_engine, tenant_id, reviewer_id, idem_key=idem
        )
        assert receipt_id is not None
        assert receipt_id == att_id.value

    def test_bound_attest_replay_rejects_request_fingerprint_mismatch_same_key(
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
            idempotency_key=f"b-fp-{uuid.uuid7()}",
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
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        hinted = _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        idem = f"b-tamper-{uuid.uuid7()}"
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=AggregateRevision(int(revision)),
            per_question_outcomes=_positive_outcomes(hinted),
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        bad_outcomes = list(_positive_outcomes(hinted))
        bad_outcomes[0] = {**bad_outcomes[0], "human_review_pass": False}
        with pytest.raises(
            WorksheetLearnerHintAttestationInvalid, match="idempotency key already bound"
        ):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=AggregateRevision(int(revision)),
                per_question_outcomes=bad_outcomes,
                idempotency_key=idem,
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )
        assert (
            _denial_count(ci03_g2_runtime_engine, tenant_id, CONTENT_REVIEW_MUTATION_DENIED) >= 1
        )

    def test_approve_idempotency_receipt_binds_review_decision_id(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        from aieos.platform.idempotency.models import CONTENT_REVIEW_APPROVE_V1

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
            idempotency_key=f"b-apr-{uuid.uuid7()}",
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
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        review = integrated_review_service(
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
            expected_aggregate_revision=AggregateRevision(int(revision)),
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"b-att-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        approve_idem = f"b-approve-receipt-{uuid.uuid7()}"
        approved = review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=AggregateRevision(int(revision)),
            reason_code=None,
            comment=None,
            idempotency_key=approve_idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            decision_id = conn.execute(
                text(
                    """
                    SELECT review_decision_id FROM content.review_decisions
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
            receipt_decision = conn.execute(
                text(
                    """
                    SELECT result_review_decision_id FROM api.idempotency_records
                    WHERE tenant_id = :tid
                      AND actor_principal_id = :pid
                      AND operation = :op
                    """
                ),
                {
                    "tid": tenant_id,
                    "pid": reviewer_id,
                    "op": CONTENT_REVIEW_APPROVE_V1,
                },
            ).scalar_one()
        assert receipt_decision == decision_id
        assert str(approved.review_decision_id.value) == str(decision_id)
