"""CI03-G2-I02R4-B — #80 normative acceptance proofs (Stage B, PG18).

Maps governing issue invariants to executable tests. Stage A 48-case matrix is
foundational only; this module adds architecture acceptance not claimed complete
by Stage A alone.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ReviewAssignmentConflict,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import CONTENT_PUBLISH
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.platform.idempotency.models import CONTENT_WORKSHEET_HINT_ATTEST_V1
from tests.domains.education.test_aieos_v1_hint_t1 import StubHumanClassification
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
    seed_worksheet_in_review_governed,
)
from tests.domains.education.test_aieos_v1_hint_t1 import build_preparation_content_schema_registry
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    IDEMPOTENCY_RETENTION,
)
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
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
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.ci03_g2_i02

# B1 — source-backed inventory (#80 → proof location).
I02_B_PROOF_MATRIX: dict[str, str] = {
    "O64-05_assignment_fk_binding": (
        "test_ci03_g2_i01_assignment_schema::test_bound_attestation_fk_requires_matching_reviewer_principal"
    ),
    "O64-04_receipt_attestation_result_id": (
        "test_ci03_g2_i02_r4b_acceptance_postgres::test_bound_attest_idempotency_stores_attestation_result_id"
    ),
    "O64-04_receipt_fingerprint_tamper_denied": (
        "test_ci03_g2_i02_r4b_acceptance_postgres::test_bound_attest_replay_rejects_corrupted_fingerprint_same_scope"
    ),
    "O64-04_decision_receipt_decision_id": (
        "test_ci03_g2_i02_r4b_acceptance_postgres::test_approve_idempotency_receipt_binds_review_decision_id"
    ),
    "O64-03-C_four_effects_assignment_guarded": (
        "test_ci03_g2_i02_mutation_postgres::TestCi03G2I02AssignmentBoundMutations"
    ),
    "O64-03-C_expiry_crossing_guard": (
        "test_ci03_g2_i01_fa01_foundation_proof::test_guard_expiry_crossing_during_slow_authority"
    ),
    "revoke_vs_attest_race": (
        "test_ci03_g2_i02_r4b_acceptance_postgres::test_concurrent_revoke_before_attest_exactly_one_effect"
    ),
    "immutable_attestation_blocks_reassign": (
        "test_ci03_g2_i01_assignment_postgres::test_attestation_blocks_reassignment"
    ),
    "publisher_after_reviewer_expiry": (
        "test_ci03_g2_i02_r4b_acceptance_postgres::test_publish_after_reviewer_assignment_expired"
    ),
    "migration_downgrade_evidence_refusal": (
        "test_ci03_g2_i01_assignment_schema::test_downgrade_refuses_nonempty_assignment_evidence"
    ),
    "terminal_replay_metadata_only": (
        "test_ci03_g2_i02_mutation_postgres::test_terminal_replay_after_approve"
    ),
    "cross_actor_same_key_fail_closed": (
        "test_ci03_g2_i02_mutation_postgres::test_attest_idempotent_replay_requires_assignment_in_fingerprint"
    ),
}


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

    def test_bound_attest_replay_rejects_corrupted_fingerprint_same_scope(
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

    def test_publish_after_reviewer_assignment_expired(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        publisher_id = uuid.uuid7()
        _seed_reviewers(ci03_g2_bootstrap_engine, tenant_id, owner_id, reviewer_id)
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=publisher_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_PUBLISH,),
        )
        clock_holder = {"t": _NOW}
        issue, _, provider, *_ = _services(
            ci03_g2_runtime_engine, tenant_id, clock=lambda: clock_holder["t"]
        )
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
            idempotency_key=f"b-pub-{uuid.uuid7()}",
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
            clock=lambda: clock_holder["t"],
            school_context_provider=provider,
        )
        review = integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: clock_holder["t"],
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
            idempotency_key=f"b-att-pub-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock_holder["t"],
        )
        approved = review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=AggregateRevision(int(revision)),
            reason_code=None,
            comment=None,
            idempotency_key=f"b-appr-pub-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock_holder["t"],
        )
        clock_holder["t"] = _NOW + timedelta(days=30)
        publish = PublishContentService(
            SqlAlchemyContentUnitOfWorkFactory(ci03_g2_runtime_engine),
            AllowPublicationAuthorization(),
            AllowPublicationGovernance(),
            AllowAssetCurrentGovernance(),
            build_preparation_content_schema_registry(),
            idempotency_retention=IDEMPOTENCY_RETENTION,
            principal_classification=StubHumanClassification(),
        )
        published = publish.publish(
            tenant_id,
            publisher_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=approved.aggregate_revision,
            idempotency_key=f"b-publish-{uuid.uuid7()}",
            event_context=_event(publisher_id),
            audit_provenance=api_mutation_audit_provenance(publisher_id),
            now=clock_holder["t"],
        )
        assert published.version_id == version_id

    def test_concurrent_revoke_before_attest_exactly_one_effect(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        tenant_id = uuid.uuid7()
        owner_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        _seed_reviewers(ci03_g2_bootstrap_engine, tenant_id, owner_id, reviewer_id)
        issue, revoke, provider, *_ = _services(ci03_g2_runtime_engine, tenant_id)
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
        ctx = _event(owner_id)
        audit = api_mutation_audit_provenance(owner_id)
        assignment_id = issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key=f"b-race-issue-{uuid.uuid7()}",
            event_context=ctx,
            audit_provenance=audit,
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
        barrier = threading.Barrier(2)
        attest_ok: list[uuid.UUID] = []
        revoke_ok: list[str] = []
        errors: list[BaseException] = []

        def do_attest() -> None:
            barrier.wait()
            try:
                att_id = attest.record(
                    tenant_id,
                    reviewer_id,
                    content_id=content_id,
                    version_id=version_id,
                    expected_aggregate_revision=AggregateRevision(int(revision)),
                    per_question_outcomes=_positive_outcomes(
                        _hinted_ids_from_version(
                            ci03_g2_runtime_engine, tenant_id, version_id
                        )
                    ),
                    idempotency_key=f"b-race-attest-{uuid.uuid7()}",
                    event_context=_event(reviewer_id),
                    audit_provenance=api_mutation_audit_provenance(reviewer_id),
                    now=_NOW,
                )
                attest_ok.append(att_id.value)
            except BaseException as exc:
                errors.append(exc)

        def do_revoke() -> None:
            barrier.wait()
            try:
                revoke.revoke(
                    tenant_id,
                    owner_id,
                    content_id=content_id,
                    version_id=version_id,
                    assignment_id=assignment_id,
                    idempotency_key=f"b-race-revoke-{uuid.uuid7()}",
                    event_context=ctx,
                    audit_provenance=audit,
                )
                revoke_ok.append("ok")
            except BaseException as exc:
                errors.append(exc)

        t1 = threading.Thread(target=do_attest)
        t2 = threading.Thread(target=do_revoke)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        success_count = len(attest_ok) + len(revoke_ok)
        assert success_count == 1
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            attest_rows = conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
        assert int(attest_rows) <= 1
        if len(attest_ok) == 1 and len(revoke_ok) == 0:
            with pytest.raises(ReviewAssignmentConflict):
                revoke.revoke(
                    tenant_id,
                    owner_id,
                    content_id=content_id,
                    version_id=version_id,
                    assignment_id=assignment_id,
                    idempotency_key=f"b-race-revoke-after-{uuid.uuid7()}",
                    event_context=ctx,
                    audit_provenance=audit,
                )