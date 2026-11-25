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
        "id": "O64-03-C_expiry_same_txn_slot_hold",
        "test": "test_ci03_g2_i02_r4b2_contention_expiry_postgres::test_expiry_advances_clock_while_same_txn_holds_slot_without_lock_wait",
        "status": "EXECUTED",
        "notes": "intra-txn hold only; not independent lock-wait proof",
    },
    {
        "id": "O64-03-C_expiry_lock_wait_integrated",
        "test": "test_ci03_g2_i02_r4b2_contention_expiry_postgres::test_expiry_lock_wait_crosses_persisted_valid_until",
        "status": "EXECUTED",
        "notes": "passive blocker + pg_blocking_pids; at/after valid_until",
    },
    {
        "id": "O64-03-C_expiry_slow_authority_integrated",
        "test": "test_ci03_g2_i02_r4b2_contention_expiry_postgres::test_expiry_crosses_during_integrated_authority_lookup",
        "status": "EXECUTED",
    },
    {
        "id": "O64-03-C_expiry_crossing_guard_only",
        "test": "test_ci03_g2_i01_fa01_foundation_proof::test_guard_expiry_crossing_during_slow_authority",
        "status": "EXECUTED",
        "notes": "guard baseline; supplemented by r4b2 integrated",
    },
    {
        "id": "revoke_then_attest_ordering",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_revoke_before_attest_denies_attest",
        "status": "EXECUTED",
        "notes": "sequential; r4b2 adds lock-barrier variant for all effects",
    },
    {
        "id": "attest_then_revoke_ordering",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_attest_before_revoke_succeeds_leaves_immutable_attestation",
        "status": "EXECUTED",
        "notes": "sequential",
    },
    {
        "id": "concurrent_pg_lock_races_eight_orderings",
        "test": "test_ci03_g2_i02_r4b2_contention_expiry_postgres::TestCi03G2I02R4B2Concurrency",
        "status": "EXECUTED",
        "notes": "pg_blocking_pids; revoke-first + effect-first × four mutations",
    },
    {
        "id": "publisher_after_reviewer_expiry_kernel_auth",
        "test": "test_ci03_g2_i02_r4b1_acceptance_postgres::test_publish_kernel_authorization_after_assignment_expired",
        "status": "EXECUTED",
    },
    {
        "id": "migration_receipt_column_downgrade_guard",
        "test": "test_downgrade_refuses_when_hint_attestation_receipt_ids_exist",
        "status": "EXECUTED",
    },
    {
        "id": "outbox_exact_counts",
        "test": "helpers_ci03_g2_i02_snapshot + r4b2 effect-first attest",
        "status": "PARTIAL",
        "notes": "tenant outbox delta on attest success; exact event vocabulary still open",
    },
    {
        "id": "replay_deny_full_grid",
        "test": "test_ci03_g2_i02_r4b3r1_replay_deny_grid_postgres + r4b3 + r4b1 receipt faults",
        "status": "PARTIAL",
        "notes": "integrated expired/revoked/fingerprint/cross-actor; wrong version/op/result grid BLOCKED",
    },
    {
        "id": "issuer_reviewer_authority_losses",
        "test": "test_ci03_g2_i02_r4b3r1_authority_sink_postgres + r4b3 decide revoke",
        "status": "PARTIAL",
        "notes": "membership revoke + decide revoke; issuer class loss still BLOCKED",
    },
    {
        "id": "sink_unavailable",
        "test": "test_ci03_g2_i02_r4b3r1_authority_sink_postgres::test_mutation_denial_sink_unavailable_fail_closed_no_durable_effect",
        "status": "PARTIAL",
        "notes": "bound-mutation deny sink; success-path audit/outbox rollback still BLOCKED",
    },
    {
        "id": "stale_revoke_reissue_supersession",
        "test": "test_ci03_g2_i02_r4b3_authority_replay_postgres::test_reissue_blocked_when_attestation_immutable",
        "status": "EXECUTED",
    },
    {
        "id": "wrong_attestation_binding_approve",
        "test": "test_ci03_g2_i02_r4b3r1_authority_sink_postgres (digest corrupt + cross-reviewer)",
        "status": "EXECUTED",
        "notes": "missing attestation remains r4b3; wrong digest/reviewer covered in B3R1",
    },
    {
        "id": "b2r1_durable_snapshot_evidence",
        "test": "helpers_ci03_g2_i02_snapshot + r4b2 expiry/contention",
        "status": "EXECUTED",
        "notes": "stewardship, issuance, audit/outbox slices, denial correlation",
    },
    {
        "id": "contention_helper_cleanup",
        "test": "test_ci03_g2_i02_contention_cleanup_postgres",
        "status": "EXECUTED",
        "notes": "holder early fail before waiter start",
    },
    {
        "id": "terminal_attest_replay",
        "test": "test_ci03_g2_i02_r4b3r1_terminal_replay_postgres",
        "status": "EXECUTED",
        "notes": "post-approve terminal receipt replay; active same-key in r4b3",
    },
    {
        "id": "publisher_negatives_legacy",
        "test": "r4b1 publish after expiry + r4b3r1 kernel no-grant after terminal approve",
        "status": "PARTIAL",
        "notes": "hint-free/legacy row-level negatives still BLOCKED",
    },
)


# B3R1 — obligation matrix (Chief directive); BLOCKED items are mandatory Stage B follow-on.
I02_B3R1_PROOF_ENTRIES: tuple[dict[str, str], ...] = (
    {
        "obligation": "B3R1-01",
        "test": "r4b3::test_active_attest_same_key_replay + r4b3r1_terminal_replay",
        "status": "EXECUTED",
    },
    {
        "obligation": "B3R1-02",
        "test": "test_ci03_g2_i02_r4b3r1_replay_deny_grid_postgres",
        "status": "PARTIAL",
        "notes": "8 integrated cells + skips; stale supersession/wrong binding grid BLOCKED",
    },
    {
        "obligation": "B3R1-03",
        "test": "test_ci03_g2_i02_r4b3r1_authority_sink_postgres (membership, decide revoke via grid)",
        "status": "PARTIAL",
    },
    {
        "obligation": "B3R1-04",
        "test": "test_mutation_denial_sink_unavailable_fail_closed_no_durable_effect",
        "status": "PARTIAL",
        "notes": "success txn audit/outbox rollback BLOCKED",
    },
    {
        "obligation": "B3R1-05",
        "test": "r4b3 reissue/attest-after-revoke replay",
        "status": "EXECUTED",
    },
    {
        "obligation": "B3R1-06",
        "test": "test_attestation_row_immutable_at_database_boundary + test_cross_reviewer_approve_denied",
        "status": "EXECUTED",
    },
    {
        "obligation": "B3R1-07",
        "test": "helpers_ci03_g2_i02_snapshot + terminal replay outbox/receipt asserts",
        "status": "PARTIAL",
        "notes": "per-command audit/outbox correlation for all 24 contention cases still open",
    },
    {
        "obligation": "B3R1-08",
        "test": "test_kernel_publisher_without_grant_denied_after_terminal_approve",
        "status": "PARTIAL",
    },
    {
        "obligation": "B3R1-09",
        "test": "test_ci03_g2_i02_contention_cleanup_postgres",
        "status": "EXECUTED",
    },
    {
        "obligation": "B3R1-10",
        "test": "I02_B_PROOF_ENTRIES + I02_B3R1_PROOF_ENTRIES",
        "status": "EXECUTED",
    },
    {
        "obligation": "B4/B6",
        "test": "disposable ci03g020002 roundtrip + runtime privilege",
        "status": "PARTIAL",
        "notes": "superseded by BF-10/BF-11 file targets",
    },
)


# BF — Stage B final closure matrix (Chief review; no aggregate PASS for open cells).
I02_BF_CLOSURE_MATRIX: tuple[dict[str, str], ...] = (
    {
        "id": "BF-01",
        "obligation": "closure_matrix",
        "test": "I02_BF_CLOSURE_MATRIX",
        "status": "EXECUTED",
    },
    {
        "id": "BF-02",
        "obligation": "terminal_reject_rc_receipt_replay",
        "test": "test_ci03_g2_i02_r4bf_terminal_replay_postgres",
        "status": "EXECUTED",
        "notes": "post-APPROVE attest/approve retained in r4b3r1",
    },
    {
        "id": "BF-03",
        "obligation": "establish_then_replay_deny",
        "test": "test_ci03_g2_i02_r4bf_replay_established_postgres",
        "status": "PARTIAL",
        "notes": "changed payload, fingerprint, stale revision; supersession grid N/A/ADR",
    },
    {
        "id": "BF-04",
        "obligation": "issuer_reviewer_authority_loss",
        "test": "r4b3r1 + r4bf_authority + grid",
        "status": "PARTIAL",
        "notes": "issuer assign locks revoke+attest; membership/decide; class/unavailable gaps",
    },
    {
        "id": "BF-05",
        "obligation": "sink_and_success_rollback",
        "test": "test_ci03_g2_i02_r4bf_sink_rollback_postgres",
        "status": "PARTIAL",
        "notes": "deny sink attest+reject; approve outbox rollback; audit-only inject open",
    },
    {
        "id": "BF-06",
        "obligation": "wrong_attestation_binding",
        "test": "r4b3r1 immutable + cross-reviewer",
        "status": "EXECUTED",
    },
    {
        "id": "BF-07",
        "obligation": "per_command_audit_outbox_24",
        "test": "helpers_ci03_g2_i02_snapshot + r4b2",
        "status": "PARTIAL",
        "notes": "aggregate deltas wired; exact event identity all 24 cells open",
    },
    {
        "id": "BF-08",
        "obligation": "publisher_negatives",
        "test": "r4b1 + r4bf_publisher + r4b3r1 kernel",
        "status": "PARTIAL",
        "notes": "hint-free legacy row-level N/A until ADR fixture",
    },
    {
        "id": "BF-09",
        "obligation": "contention_cleanup",
        "test": "test_ci03_g2_i02_contention_cleanup_postgres",
        "status": "PARTIAL",
        "notes": "holder early-fail; passive blocker propagation via r4b2 pg_blocking",
    },
    {
        "id": "BF-10",
        "obligation": "migration_roundtrip_refusal",
        "test": "test_ci03g020002_single_step_downgrade_upgrade + downgrade_refuses_receipt",
        "status": "EXECUTED",
    },
    {
        "id": "BF-11",
        "obligation": "runtime_privilege",
        "test": "test_ci03_g2_substrate + test_runtime_role_cannot_update_immutable_attestation_row",
        "status": "PARTIAL",
        "notes": "RLS/runtime rolsuper/bypass + immutability; DELETE/DDL grid partial",
    },
    {
        "id": "BF-12",
        "obligation": "ci_g1_g2_inventory",
        "test": "CI run 45541196270 @ 5015338",
        "status": "EXECUTED",
        "notes": "G1 28+28 receipts; G2 124 passed/3443 deselected; quality 53 fail unchanged",
    },
)


# TC01 — authority/receipt case map (Chief TCA; credits existing nodes).
I02_TC01_CASE_MAP: tuple[dict[str, str], ...] = (
    {
        "group": "TC01-A",
        "case": "terminal_replay_original_result",
        "test": "test_ci03_g2_i02_r4tc01::test_terminal_receipt_replay_returns_original_result",
        "evidence": "NEW; extends r4b3r1/r4bf terminal replay",
    },
    {
        "group": "TC01-A",
        "case": "terminal_replay_durable",
        "test": "r4b3r1_terminal_replay + r4bf_terminal_replay",
        "evidence": "PRIOR credited",
    },
    {
        "group": "TC01-B",
        "case": "established_expiry_denied",
        "test": "test_established_receipt_expiry_denied",
        "evidence": "NEW active_attest + terminal_reject",
    },
    {
        "group": "TC01-B",
        "case": "terminal_authority_lost_replay",
        "test": "test_terminal_receipt_current_authority_lost_denies_review_replay",
        "evidence": "NEW; kernel decide gate precedes idempotent replay",
    },
    {
        "group": "TC01-B",
        "case": "terminal_revoked_replay_denied",
        "test": "test_terminal_receipt_revoked_denied",
        "evidence": "NEW",
    },
    {
        "group": "TC01-B",
        "case": "stale_revoke_supersession",
        "test": "test_ci03_g2_i01_assignment_postgres::test_stale_revoke_leaves_current_slot",
        "evidence": "PRIOR G2-I01",
    },
    {
        "group": "TC01-B",
        "case": "reissue_after_attest",
        "test": "r4b3::test_reissue_blocked_when_attestation_immutable",
        "evidence": "PRIOR lifecycle prohibition",
    },
    {
        "group": "TC01-C",
        "case": "changed_payload_fingerprint_stale",
        "test": "r4bf_replay_established + r4b3r1 grid",
        "evidence": "PRIOR credited",
    },
    {
        "group": "TC01-C",
        "case": "wrong_version_content_replay",
        "test": "test_established_receipt_replay_wrong_version_denied + wrong_content",
        "evidence": "NEW",
    },
    {
        "group": "TC01-C",
        "case": "operation_scope_not_global",
        "test": "test_same_idempotency_key_different_operation_scope_not_global_conflict",
        "evidence": "NEW positive",
    },
    {
        "group": "TC01-D",
        "case": "issuer_assign_lock",
        "test": "r4bf_authority::test_issuer_assign_revoked_denies_revoke_and_bound_attest",
        "evidence": "PRIOR",
    },
    {
        "group": "TC01-D",
        "case": "membership_decide_revoke",
        "test": "r4b3r1_authority_sink + r4b3 decide revoked",
        "evidence": "PRIOR",
    },
    {
        "group": "TC01-D",
        "case": "class_eligibility_unavailable",
        "test": "test_new_effect_denied_when_reviewer_class_eligibility_empty + school_context_unavailable",
        "evidence": "NEW",
    },
    {
        "group": "TC01-D",
        "case": "issuer_submit_revoked",
        "test": "test_issuer_submit_revoked_denies_reissue_new_effect",
        "evidence": "NEW (reissue path)",
    },
    {
        "group": "TC01-E",
        "case": "null_assignment_interlock",
        "test": "test_approve_interlock_rejects_null_attestation_assignment",
        "evidence": "NEW application interlock",
    },
    {
        "group": "TC01-E",
        "case": "assignment_mismatch_interlock",
        "test": "test_approve_interlock_rejects_mismatched_attestation_assignment",
        "evidence": "NEW + PRIOR cross-reviewer/b3r1 immutable",
    },
    {
        "group": "TC01-F",
        "case": "historical_publish_expiry",
        "test": "r4b1::test_publish_kernel_authorization_after_assignment_expired",
        "evidence": "PRIOR",
    },
    {
        "group": "TC01-F",
        "case": "publisher_distinctness_non_human",
        "test": "test_publish_rejects_reviewer_as_publisher + test_publish_rejects_non_human_principal",
        "evidence": "NEW; PRIOR no-grant kernel",
    },
)

# TC02 — transactional / database integrity (Chief TC02 scope).
I02_TC02_CASE_MAP: tuple[dict[str, str], ...] = (
    {
        "group": "TC02-A",
        "case": "denial_sink_fail_closed",
        "test": "r4bf_sink + r4tc02::test_denial_sink_unavailable_fail_closed_with_full_snapshot",
        "evidence": "PRIOR attest/reject; NEW approve/request_changes + snapshot",
    },
    {
        "group": "TC02-A",
        "case": "success_audit_rollback",
        "test": "r4tc02::test_success_audit_failure_rolls_back[attest,decision]",
        "evidence": "NEW SqlAlchemySecurityMutationAuditRepository.insert fault",
    },
    {
        "group": "TC02-A",
        "case": "success_outbox_rollback",
        "test": "r4bf approve outbox + r4tc02 reject/request_changes outbox",
        "evidence": "PRIOR/NEW shared review decide path",
    },
    {
        "group": "TC02-A",
        "case": "attest_zero_outbox",
        "test": "r4tc02::test_attest_emits_zero_outbox_by_design",
        "evidence": "NEW contract vocabulary",
    },
    {
        "group": "TC02-B",
        "case": "command_identity_representative",
        "test": "r4tc02::test_success_mutation_command_identity_representative",
        "evidence": "NEW helper assert_success_mutation_command_evidence",
    },
    {
        "group": "TC02-B",
        "case": "contention_expiry_identity",
        "test": "r4b2 effect-first + lock-wait denial receipt guard",
        "evidence": "EXTENDED existing 24-case helpers",
    },
    {
        "group": "TC02-C",
        "case": "contention_cleanup",
        "test": "contention_cleanup_postgres passive/waiter/holder paths",
        "evidence": "PRIOR holder early-fail; NEW passive+waiter",
    },
    {
        "group": "TC02-D",
        "case": "migration_roundtrip",
        "test": "g2_i01::test_ci03g020002_single_step_downgrade_upgrade",
        "evidence": "PRIOR BF-10",
    },
    {
        "group": "TC02-D",
        "case": "downgrade_refusal",
        "test": "r4bf receipt refusal + r4tc02 mutation-denial-only refusal",
        "evidence": "NEW schema_fingerprint_ci03g020002",
    },
    {
        "group": "TC02-D",
        "case": "catalog_fk_binding",
        "test": "r4tc02::test_ci03g020002_fk_and_assignment_binding_catalog",
        "evidence": "NEW",
    },
    {
        "group": "TC02-E",
        "case": "runtime_immutable_update",
        "test": "r4bf::test_runtime_role_cannot_update_immutable_attestation_row",
        "evidence": "PRIOR BF-11",
    },
    {
        "group": "TC02-E",
        "case": "runtime_delete_ddl_cross_tenant",
        "test": "r4tc02 delete + CREATE denied + cross-tenant slot hidden",
        "evidence": "NEW",
    },
)

# TC03 — final Stage B acceptance residuals (Chief TC03 scope).
I02_TC03_REQUIREMENT_MAP: tuple[dict[str, str], ...] = (
    {
        "id": "TC03-01",
        "invariant": "wrong assignment receipt replay binding",
        "test": (
            "r4tc03 corrupt fp + immutability boundary; PRIOR r4b1 fingerprint mismatch; "
            "G2-I01 stale revoke/reissue; B3 reissue-after-attest block"
        ),
        "stage": "B",
    },
    {
        "id": "TC03-02",
        "invariant": "issuer/reviewer authority losses",
        "test": (
            "r4tc03 malformed eligibility port; PRIOR TC01 issuer submit/reissue + "
            "D/BF assign/membership/decide/class/unavailable"
        ),
        "stage": "B",
    },
    {
        "id": "TC03-03",
        "invariant": "integrated APPROVE digest/assignment interlock",
        "test": (
            "r4tc03 integrated non-assignee approve; PRIOR r4tc01 interlock unit + "
            "b3r1 cross-reviewer + immutable row boundary"
        ),
        "stage": "B",
    },
    {
        "id": "TC03-04",
        "invariant": "historical + hint-free compatibility",
        "test": (
            "PRIOR r4bf/tc01 historical publish + publisher separation; "
            "hint-free legacy path N/A (no governed hint-free fixture in #80 scope)"
        ),
        "stage": "B",
    },
    {
        "id": "TC03-05",
        "invariant": "exact durable evidence",
        "test": (
            "helpers assert_outbox_command_correlation + TC02 representative; "
            "PARTIAL: full 24-case audit/outbox identity grid → TC02 residual"
        ),
        "stage": "B",
    },
    {
        "id": "TC03-06",
        "invariant": "exceptional cleanup under held lock",
        "test": "r4tc03 passive blocker while_holding fault; PRIOR contention early-holder",
        "stage": "B",
    },
    {
        "id": "TC03-07",
        "invariant": "migration refusal preservation",
        "test": (
            "r4tc03 fingerprint FK/RLS + conditional clean roundtrip; "
            "PRIOR g2_i01/BF downgrade refusal + TC02 schema fingerprint"
        ),
        "stage": "B",
    },
    {
        "id": "TC03-08",
        "invariant": "runtime privilege / cross-tenant",
        "test": "r4tc03 forced RLS catalog + cross-tenant issuance; PRIOR r4tc02/BF runtime grid",
        "stage": "B",
    },
    {
        "id": "TC03-09",
        "invariant": "normative traceability",
        "test": "I02_TC03_REQUIREMENT_MAP (this table)",
        "stage": "B",
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
