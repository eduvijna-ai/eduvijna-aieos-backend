"""CI03-G2-I02R4-TC03 — final Stage B acceptance residuals (PG18)."""

from __future__ import annotations

import threading
import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ReviewAssignmentForbidden,
    ReviewForbidden,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import CONTENT_REVIEW_SUBMIT
from aieos.domains.teaching.application.errors import SchoolContextUnavailable
from aieos.platform.idempotency.models import CONTENT_WORKSHEET_HINT_ATTEST_V1
from tests.conftest import RUNTIME_USER, alembic_config
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from tests.domains.content.application.helpers_ci03_g2_i02_contention import (
    passive_head_slot_blocker,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_snapshot_unchanged_except_denial,
    capture_durable_snapshot,
    load_idempotency_receipt,
    schema_fingerprint_ci03g020002,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
    _services,
)
from sqlalchemy.exc import OperationalError

from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
    _corrupt_idempotency_fingerprint,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.platform.security.authorization.helpers import revoke_grant

pytestmark = pytest.mark.ci03_g2_i02


def test_attestation_row_immutable_precludes_assignment_patch_at_read_boundary(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03-01/03 boundary: assignment binding fault must go through idempotency layer."""
    (
        tenant_id,
        _owner,
        reviewer_id,
        provider,
        _revoke,
        _assignment_id,
        content_id,
        version_id,
        revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    ).record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        ),
        idempotency_key=f"tc03-immut-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    with pytest.raises(OperationalError, match="immutable"):
        with ci03_g2_bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE content.worksheet_learner_hint_version_attestations
                    SET review_assignment_id = NULL
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {"tid": tenant_id, "vid": version_id.value},
            )


def test_established_attest_replay_denies_corrupt_assignment_fingerprint(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03-01: corrupt stored fingerprint → replay fail-closed, receipt preserved."""
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
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    idem = f"tc03-fp-bind-{uuid.uuid7()}"
    integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    ).record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        ),
        idempotency_key=idem,
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    _corrupt_idempotency_fingerprint(
        ci03_g2_bootstrap_engine,
        tenant_id,
        reviewer_id,
        CONTENT_WORKSHEET_HINT_ATTEST_V1,
        idempotency_key=idem,
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
    with pytest.raises(
        WorksheetLearnerHintAttestationInvalid,
        match="idempotency key already bound",
    ):
        integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        ).record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
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
    assert_snapshot_unchanged_except_denial(before, after)
    assert (
        load_idempotency_receipt(
            ci03_g2_runtime_engine,
            tenant_id,
            reviewer_id,
            operation=CONTENT_WORKSHEET_HINT_ATTEST_V1,
            idempotency_key=idem,
        )
        is not None
    )


def test_integrated_approve_interlock_rejects_non_assignee_after_attestation(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03-03: integrated APPROVE path enforces attestation/slot binding (non-assignee)."""
    from aieos.domains.content.application.ports import CONTENT_REVIEW_DECIDE
    from aieos.platform.security.authorization.decisions import PrincipalKind
    from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
        CLASS_REF,
    )
    from tests.platform.security.authorization.helpers import seed_active_authority

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
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    other = uuid.uuid7()
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=other,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_REVIEW_DECIDE,),
    )
    provider.set_reviewer_class_eligibility(other, (CLASS_REF,))
    integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    ).record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        ),
        idempotency_key=f"tc03-int-apr-att-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    before = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        assignment_id=assignment_id,
        reviewer_principal_id=other,
    )
    with pytest.raises(ReviewAssignmentForbidden):
        integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        ).approve(
            tenant_id,
            other,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"tc03-int-apr-{uuid.uuid7()}",
            event_context=_event(other),
            audit_provenance=api_mutation_audit_provenance(other),
            now=_NOW,
        )
    after = capture_durable_snapshot(
        bootstrap_engine=ci03_g2_bootstrap_engine,
        runtime_engine=ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        content_id=content_id,
        version_id=version_id,
        assignment_id=assignment_id,
        reviewer_principal_id=other,
    )
    assert after.decisions == before.decisions


def test_issuer_submit_revoked_allows_bound_attest_denies_reissue_only(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03-02: submit loss blocks issuer assign path; bound reviewer attest unaffected."""
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
        capability=CONTENT_REVIEW_SUBMIT,
    )
    issue, *_ = _services(ci03_g2_runtime_engine, tenant_id, clock=lambda: _NOW)
    with pytest.raises(ReviewForbidden):
        issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key=f"tc03-submit-gone-{uuid.uuid7()}",
            event_context=_event(owner_id),
            audit_provenance=api_mutation_audit_provenance(owner_id),
        )
    integrated_attest_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    ).record(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_id,
        expected_aggregate_revision=revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        ),
        idempotency_key=f"tc03-attest-ok-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    _ = assignment_id


def test_malformed_reviewer_eligibility_denies_bound_attest(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC03-02: integrated guard denies protected attest when eligibility facts are malformed."""

    def _non_string_class_refs(
        _self,
        _tenant_id: uuid.UUID,
        _reviewer_principal_id: uuid.UUID,
    ) -> tuple[object, ...]:
        return (123,)

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
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    monkeypatch.setattr(
        provider,
        "list_reviewer_eligible_class_refs",
        _non_string_class_refs,
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
    with pytest.raises(SchoolContextUnavailable, match="malformed|facts"):
        integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        ).record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"tc03-malformed-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
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
    assert after.attestations == before.attestations


def test_passive_blocker_fault_during_real_lock_hold_propagates_and_releases(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03-06: failure inside passive blocker while PG locks held; UoW released."""
    (
        tenant_id,
        _owner,
        _reviewer,
        _provider,
        _revoke,
        _assignment_id,
        content_id,
        version_id,
        _revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    locks_acquired = threading.Event()
    release = threading.Event()
    blocker_pid: list[int | None] = [None]
    blocker_errors: list[BaseException] = []

    def failing_holding() -> None:
        raise RuntimeError("fault while passive blocker holds locks")

    def blocker() -> None:
        try:
            passive_head_slot_blocker(
                ci03_g2_runtime_engine,
                tenant_id,
                content_id=content_id,
                version_id=version_id,
                locks_acquired=locks_acquired,
                release=release,
                blocker_pid=blocker_pid,
                while_holding=failing_holding,
            )
        except BaseException as exc:  # noqa: BLE001
            blocker_errors.append(exc)

    thread = threading.Thread(target=blocker, name="tc03-passive-fault")
    thread.start()
    assert locks_acquired.wait(timeout=30)
    release.set()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(blocker_errors) == 1
    assert isinstance(blocker_errors[0], RuntimeError)
    with ci03_g2_runtime_engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def test_migration_schema_fingerprint_includes_fks_and_forced_rls(
    ci03_g2_bootstrap_engine: Engine,
) -> None:
    """TC03-07: catalog anchors beyond column counts."""
    fp = schema_fingerprint_ci03g020002(ci03_g2_bootstrap_engine)
    assert fp.alembic_head == "ci03g020002"
    assert fp.attest_bound_column == 1
    assert fp.decision_bound_column == 1
    assert fp.receipt_attest_result_column == 1
    assert fp.fk_attest_assignment == 1
    assert fp.fk_decision_assignment == 1
    assert fp.uq_issuance_resource_reviewer == 1
    assert fp.attest_rls_forced is True
    assert fp.decision_rls_forced is True


def test_migration_clean_roundtrip_restores_fingerprint(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_postgres18: dict[str, str],
) -> None:
    before = schema_fingerprint_ci03g020002(ci03_g2_bootstrap_engine)
    cfg = alembic_config(ci03_g2_postgres18["migrator_url"])
    with ci03_g2_bootstrap_engine.connect() as conn:
        bound_before = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE review_assignment_id IS NOT NULL
                    """
                )
            ).scalar_one()
        )
    if bound_before > 0:
        pytest.skip(
            "shared G2 substrate already has bound evidence; clean roundtrip credited in g2_i01"
        )
    command.downgrade(cfg, "ci03g020001")
    mid = schema_fingerprint_ci03g020002(ci03_g2_bootstrap_engine)
    assert mid.alembic_head == "ci03g020001"
    assert mid.attest_bound_column == 0
    command.upgrade(cfg, "ci03g020002")
    after = schema_fingerprint_ci03g020002(ci03_g2_bootstrap_engine)
    assert after == before


def test_runtime_catalog_shows_forced_rls_without_extra_grants(
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03-08: migration-created runtime role; no supplemental grant call in test."""
    with ci03_g2_runtime_engine.connect() as conn:
        current_user = conn.execute(text("SELECT current_user")).scalar_one()
        forced = conn.execute(
            text(
                """
                SELECT bool_and(c.relforcerowsecurity)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'content'
                  AND c.relname IN (
                    'review_assignment_issuances',
                    'review_current_assignments',
                    'worksheet_learner_hint_version_attestations'
                  )
                """
            )
        ).scalar_one()
        insert_attest = conn.execute(
            text(
                """
                SELECT has_table_privilege(current_user,
                    'content.worksheet_learner_hint_version_attestations', 'INSERT')
                """
            )
        ).scalar_one()
    assert current_user == RUNTIME_USER
    assert forced is True
    assert insert_attest is True


def test_cross_tenant_insert_into_assignment_issuance_denied(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    tenant_b = uuid.uuid7()
    (
        _tenant_a,
        owner_a,
        reviewer_a,
        _provider,
        _revoke,
        assignment_id,
        content_id,
        version_id,
        _revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine,
        ci03_g2_runtime_engine,
        clock=lambda: _NOW,
    )
    with pytest.raises(Exception):
        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_b)},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO content.review_assignment_issuances (
                        tenant_id, content_id, version_id, assignment_id,
                        reviewer_principal_id, issuer_principal_id, class_ref,
                        valid_from, valid_until
                    ) VALUES (
                        :tid_b, :cid, :vid, :aid, :rev, :iss, 'class-a',
                        NOW(), NOW() + interval '7 days'
                    )
                    """
                ),
                {
                    "tid_b": tenant_b,
                    "cid": content_id.value,
                    "vid": version_id.value,
                    "aid": assignment_id.value,
                    "rev": reviewer_a,
                    "iss": owner_a,
                },
            )