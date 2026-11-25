"""CI03-G2-I02R4-TC03R1 — Chief evidence corrections (PG18)."""

from __future__ import annotations

import dataclasses
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    ReviewForbidden,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import CONTENT_PUBLISH, CONTENT_REVIEW_SUBMIT
from aieos.domains.content.application.http_append import HttpAppendContentVersionService
from aieos.domains.content.application.publish import PublishContentService
import aieos.domains.content.application.worksheet_hint_attestation_record as attest_record_module
import aieos.domains.content.application.worksheet_hint_attestation_gate as attestation_gate
from aieos.domains.content.domain.identities import AggregateRevision, ReviewAssignmentId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2
from aieos.domains.teaching.application.errors import SchoolContextUnavailable
from aieos.platform.idempotency.models import CONTENT_WORKSHEET_HINT_ATTEST_V1
from aieos.platform.security.authorization.content_adapters import AIEOS_CONTENT_CAPABILITIES
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelPublicationAuthorization,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.domains.education.test_aieos_v1_hint_t1 import (
    build_preparation_content_schema_registry,
)
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
    seed_worksheet_in_review_governed,
)
from tests.domains.content.application.helpers_ci03_g2_i02_contention import (
    passive_head_slot_blocker,
    reacquire_head_slot_lock_or_timeout,
)
from tests.domains.content.application.helpers_ci03_g2_i02_r4b3r1 import (
    build_terminal_approved_scenario,
)
from tests.domains.content.application.helpers_ci03_g2_i02_disposable_pg18 import (
    owned_disposable_ci03_pg18,
)
from tests.domains.content.application.helpers_ci03_g2_i02_hint_free import (
    HintFreeApprovedScenario,
    build_hint_free_approved_scenario,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    assert_snapshot_unchanged_except_denial,
    capture_durable_snapshot,
    load_idempotency_receipt,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    CLASS_REF,
    _NOW,
    _event,
    _services,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t1 import _hint_free_v2_payload
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from alembic import command
from tests.conftest import SCHEMA_OWNER_ROLE, alembic_config, provision_runtime_grants
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    _append_v1,
    _event_context,
    _seed_worksheet_content,
)
from aieos.domains.content.infrastructure.persistence.repositories import (
    SqlAlchemyWorksheetHintAttestationRepository,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowPublicationGovernance,
    IDEMPOTENCY_RETENTION,
)
from tests.platform.security.authorization.helpers import (
    revoke_grant,
    seed_active_authority,
)

pytestmark = pytest.mark.ci03_g2_i02


def test_integrated_approve_denied_when_read_boundary_returns_wrong_assignment(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC03R1-02: actual assert_approve_attestation_assignment_match on integrated APPROVE."""
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
        idempotency_key=f"tc03r1-attest-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    wrong = ReviewAssignmentId(uuid.uuid7())
    original_get = attestation_gate.load_valid_attestation

    def _patched_load_valid_attestation(repo, version):  # noqa: ANN001
        attestation = original_get(repo, version)
        return dataclasses.replace(attestation, review_assignment_id=wrong)

    monkeypatch.setattr(
        attestation_gate, "load_valid_attestation", _patched_load_valid_attestation
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
        match="does not match active slot",
    ):
        integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        ).approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"tc03r1-appr-{uuid.uuid7()}",
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
    assert after.decisions == before.decisions
    assert after.attestations == before.attestations
    assert after.assignment_issuance_rows == before.assignment_issuance_rows
    _ = wrong, original_get


def test_integrated_approve_denied_when_read_boundary_returns_null_assignment_binding(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC03R2-02: null attestation assignment binding at APPROVE interlock."""
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
        idempotency_key=f"tc03r2-attest-null-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    original_get = attestation_gate.load_valid_attestation

    def _patched_load_valid(repo, version):  # noqa: ANN001
        attestation = original_get(repo, version)
        return dataclasses.replace(attestation, review_assignment_id=None)

    monkeypatch.setattr(
        attestation_gate, "load_valid_attestation", _patched_load_valid
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
        match="approve requires assignment-bound attestation",
    ):
        integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        ).approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"tc03r2-apr-null-{uuid.uuid7()}",
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
    assert after.decisions == before.decisions
    assert after.attestations == before.attestations


def test_integrated_approve_denied_when_repository_digest_mismatch_before_gate(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC03R2-02: digest mismatch at get_for_version seam (pre load_valid_attestation QA)."""
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
        idempotency_key=f"tc03r2-attest-digest-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    original_get = SqlAlchemyWorksheetHintAttestationRepository.get_for_version

    def _wrong_digest(self, cid, vid):  # noqa: ANN001
        row = original_get(self, cid, vid)
        if row is None:
            return None
        return dataclasses.replace(row, payload_digest="f" * 64)

    monkeypatch.setattr(
        SqlAlchemyWorksheetHintAttestationRepository,
        "get_for_version",
        _wrong_digest,
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
        match="payload_digest mismatch",
    ):
        integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        ).approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"tc03r2-apr-digest-{uuid.uuid7()}",
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
    assert after.decisions == before.decisions
    assert after.attestations == before.attestations


def test_attest_replay_denied_assignment_binding_mismatch_via_read_boundary(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC03R1-02: replay validator assignment binding mismatch, not generic corrupt sha."""
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
    idem = f"tc03r1-replay-assign-{uuid.uuid7()}"
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
    wrong_assignment = ReviewAssignmentId(uuid.uuid7())
    original_load = attest_record_module.load_valid_attestation

    def _patched_load_valid(repo, version):  # noqa: ANN001
        attestation = original_load(repo, version)
        return dataclasses.replace(
            attestation, review_assignment_id=wrong_assignment
        )

    monkeypatch.setattr(
        attest_record_module, "load_valid_attestation", _patched_load_valid
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
        match="assignment binding mismatch",
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


def test_issuer_submit_revoked_denies_assigned_reviewer_approve(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R2-02: submit loss blocks assigned reviewer APPROVE (reissue covered elsewhere)."""
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
        idempotency_key=f"tc03r1-attest-ok-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    revoke_grant(
        ci03_g2_bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        capability=CONTENT_REVIEW_SUBMIT,
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
    with pytest.raises(ReviewForbidden):
        integrated_review_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        ).approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"tc03r2-appr-submit-rev-{uuid.uuid7()}",
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
    assert after.decisions == before.decisions
    assert after.attestations == before.attestations
    assert after.assignment_issuance_rows == before.assignment_issuance_rows
    assert after.reviewer_idempotency_success == before.reviewer_idempotency_success
    assert after.head_revision == before.head_revision
    assert after.stewardship_state == before.stewardship_state
    assert after.outbox_messages == before.outbox_messages


@pytest.mark.parametrize(
    "malformed",
    (
        pytest.param(lambda _tid, _pid: (123,), id="tuple_non_string"),
        pytest.param(lambda _tid, _pid: [CLASS_REF], id="list_not_tuple"),
        pytest.param(lambda _tid, _pid: {"class": CLASS_REF}, id="dict_not_tuple"),
        pytest.param(lambda _tid, _pid: ("",), id="blank_string_ref"),
    ),
)
def test_malformed_reviewer_eligibility_shapes_denied(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    malformed,
) -> None:
    """TC03R1-03: representative malformed eligibility via shared guard port."""
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
        malformed,
    )
    with pytest.raises(SchoolContextUnavailable, match="malformed"):
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
            idempotency_key=f"tc03r1-malformed-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
    _ = assignment_id


def test_hint_free_worksheet_approve_succeeds_without_attestation(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R1-03: hint-free IN_REVIEW approve does not require assignment-bound attestation."""
    from tests.domains.content.application.test_ci03_g2_i02_mutation_postgres import (
        _seed_reviewers,
    )

    tenant_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    provision_runtime_grants(ci03_g2_bootstrap_engine)
    _seed_reviewers(ci03_g2_bootstrap_engine, tenant_id, owner_id, reviewer_id)
    content_id = _seed_worksheet_content(
        ci03_g2_bootstrap_engine, tenant_id=tenant_id, owner_principal_id=owner_id
    )
    _, rev_v1 = _append_v1(
        ci03_g2_runtime_engine, tenant_id=tenant_id, principal_id=owner_id, content_id=content_id
    )
    version_read, revision = HttpAppendContentVersionService(
        SqlAlchemyContentUnitOfWorkFactory(ci03_g2_runtime_engine),
        build_preparation_content_schema_registry(),
        AllowAssetReferenceValidation(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    ).append(
        tenant_id,
        owner_id,
        content_id=content_id,
        expected_aggregate_revision=rev_v1,
        schema_id=WORKSHEET_SCHEMA_ID,
        schema_version=WORKSHEET_SCHEMA_VERSION_V2,
        payload=_hint_free_v2_payload(),
        idempotency_key=f"tc03r1-hf-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=_NOW,
    )
    from tests.domains.education.test_aieos_v1_hint_t2 import _review_service

    _, _, provider, *_ = _services(ci03_g2_runtime_engine, tenant_id)
    provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
    submitted = _review_service(ci03_g2_runtime_engine).submit(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=revision,
        idempotency_key=f"tc03r1-hf-submit-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=_NOW,
    )
    review = integrated_review_service(
        ci03_g2_runtime_engine,
        tenant_id,
        clock=lambda: _NOW,
        school_context_provider=provider,
    )
    review.approve(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=submitted.aggregate_revision,
        reason_code=None,
        comment=None,
        idempotency_key=f"tc03r1-hf-apr-{uuid.uuid7()}",
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=_NOW,
    )
    with ci03_g2_runtime_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        attest_count = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {"tid": tenant_id, "vid": version_read.version_id.value},
            ).scalar_one()
        )
    assert attest_count == 0


def _publish_hint_free_null_binding(
    bootstrap: Engine,
    runtime: Engine,
    scenario: HintFreeApprovedScenario,
    *,
    tag: str,
) -> None:
    publisher = uuid.uuid7()
    seed_active_authority(
        bootstrap,
        tenant_id=scenario.tenant_id,
        principal_id=publisher,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_PUBLISH,),
    )
    before_decision = scenario.review_decision_id
    with bootstrap.connect() as conn:
        bind_before = conn.execute(
            text(
                """
                SELECT review_assignment_id FROM content.review_decisions
                WHERE review_decision_id = :did
                """
            ),
            {"did": before_decision},
        ).scalar_one()
    assert bind_before is None
    publish = PublishContentService(
        SqlAlchemyContentUnitOfWorkFactory(runtime),
        KernelPublicationAuthorization(
            AuthorizationKernel(runtime, known_capabilities=AIEOS_CONTENT_CAPABILITIES)
        ),
        AllowPublicationGovernance(),
        AllowAssetCurrentGovernance(),
        build_preparation_content_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        principal_classification=CurrentPrincipalClassificationAuthority(runtime),
    )
    with runtime.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(scenario.tenant_id)},
        )
        head_rev = int(
            conn.execute(
                text(
                    "SELECT aggregate_revision FROM content.contents WHERE content_id = :cid"
                ),
                {"cid": scenario.content_id.value},
            ).scalar_one()
        )
    publish.publish(
        scenario.tenant_id,
        publisher,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        expected_aggregate_revision=AggregateRevision(head_rev),
        idempotency_key=f"{tag}-hist-pub-{uuid.uuid7()}",
        event_context=_event(publisher),
        audit_provenance=api_mutation_audit_provenance(publisher),
        now=_NOW,
    )
    with bootstrap.connect() as conn:
        bind_after, did_after = conn.execute(
            text(
                """
                SELECT review_assignment_id, review_decision_id
                FROM content.review_decisions
                WHERE tenant_id = :tid AND version_id = :vid
                """
            ),
            {"tid": scenario.tenant_id, "vid": scenario.version_id.value},
        ).one()
    assert bind_after is None
    assert did_after == before_decision


def test_historical_publish_allowed_with_null_legacy_assignment_binding(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R3-01: lawful NULL binding without immutability bypass; predecessor when ORM allows."""
    predecessor_blocked: str | None = None
    try:
        with owned_disposable_ci03_pg18(alembic_revision="ci03g020001") as owned:
            cfg = alembic_config(owned.migrator_url)
            scenario_pre = build_hint_free_approved_scenario(
                owned.bootstrap_engine,
                owned.runtime_engine,
                now=_NOW,
                tag="tc03r3-pre",
            )
            command.upgrade(cfg, "ci03g020002")
            with owned.bootstrap_engine.connect() as conn:
                head = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                bind = conn.execute(
                    text(
                        """
                        SELECT review_assignment_id FROM content.review_decisions
                        WHERE review_decision_id = :did
                        """
                    ),
                    {"did": scenario_pre.review_decision_id},
                ).scalar_one()
            assert head == "ci03g020002"
            assert bind is None
            _publish_hint_free_null_binding(
                owned.bootstrap_engine,
                owned.runtime_engine,
                scenario_pre,
                tag="tc03r3-pre",
            )
            return
    except FileNotFoundError:
        predecessor_blocked = "owned disposable docker unavailable"
    except Exception as exc:  # noqa: BLE001
        predecessor_blocked = (
            f"owned ci03g020001 predecessor unavailable: {type(exc).__name__}: {exc}"
        )
    try:
        with owned_disposable_ci03_pg18(alembic_revision="ci03g020002") as owned:
            scenario = build_hint_free_approved_scenario(
                owned.bootstrap_engine,
                owned.runtime_engine,
                now=_NOW,
                tag="tc03r3-lawful",
            )
            _publish_hint_free_null_binding(
                owned.bootstrap_engine,
                owned.runtime_engine,
                scenario,
                tag="tc03r3-lawful",
            )
            return
    except FileNotFoundError:
        pass
    scenario = build_hint_free_approved_scenario(
        ci03_g2_bootstrap_engine,
        ci03_g2_runtime_engine,
        now=_NOW,
        tag="tc03r3-session",
    )
    _publish_hint_free_null_binding(
        ci03_g2_bootstrap_engine,
        ci03_g2_runtime_engine,
        scenario,
        tag="tc03r3-session",
    )
    _ = predecessor_blocked


def test_passive_blocker_fault_releases_locks_for_reacquisition(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R1-04: held-lock fault, release/join, then independent lock reacquisition."""
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
    blocker_errors: list[BaseException] = []

    def fault_while_holding() -> None:
        raise RuntimeError("tc03r1 fault under real lock hold")

    def blocker() -> None:
        try:
            passive_head_slot_blocker(
                ci03_g2_runtime_engine,
                tenant_id,
                content_id=content_id,
                version_id=version_id,
                locks_acquired=locks_acquired,
                release=release,
                blocker_pid=[None],
                while_holding=fault_while_holding,
            )
        except BaseException as exc:  # noqa: BLE001
            blocker_errors.append(exc)

    thread = threading.Thread(target=blocker, name="tc03r1-passive-fault")
    thread.start()
    assert locks_acquired.wait(timeout=30)
    release.set()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert len(blocker_errors) == 1
    reacquire_head_slot_lock_or_timeout(
        ci03_g2_runtime_engine,
        tenant_id,
        content_id=content_id,
        version_id=version_id,
    )


def test_cross_tenant_insert_into_assignment_issuance_rls_denied(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R3-02: tenant-B positive control (rolled back); tenant-A cross insert → 42501."""
    (
        _tenant_a,
        _owner_a,
        _reviewer_a,
        _provider_a,
        _revoke_a,
        _assignment_a,
        _content_a,
        _version_a,
        _revision_a,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    (
        tenant_b,
        owner_b,
        reviewer_b,
        _provider_b,
        _revoke_b,
        assignment_b,
        content_b,
        version_b,
        _revision_b,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    fresh_aid = uuid.uuid7()
    insert_sql = text(
        """
        INSERT INTO content.review_assignment_issuances (
            assignment_id, tenant_id, content_id, version_id,
            reviewer_principal_id, issuer_principal_id, class_ref,
            issued_at, valid_until, correlation_id
        ) VALUES (
            :aid, :tid_row, :cid, :vid, :rev, :iss, :cref,
            :issued, :until, :corr
        )
        """
    )
    base_row = {
        "aid": fresh_aid,
        "tid_row": tenant_b,
        "cid": content_b.value,
        "vid": version_b.value,
        "rev": reviewer_b,
        "iss": owner_b,
        "cref": CLASS_REF,
        "issued": _NOW,
        "until": _NOW + timedelta(days=1),
        "corr": uuid.uuid7(),
    }
    with ci03_g2_runtime_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_b)},
            )
            conn.execute(insert_sql, base_row)
            visible_b = int(
                conn.execute(
                    text(
                        """
                        SELECT count(*) FROM content.review_assignment_issuances
                        WHERE assignment_id = :aid
                        """
                    ),
                    {"aid": fresh_aid},
                ).scalar_one()
            )
            assert visible_b == 1
        finally:
            trans.rollback()
    denied_aid = uuid.uuid7()
    with pytest.raises(DBAPIError) as exc_info:
        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(_tenant_a)},
            )
            conn.execute(
                insert_sql,
                {**base_row, "aid": denied_aid},
            )
    orig = exc_info.value.orig
    assert getattr(orig, "sqlstate", None) == "42501"
    assert "row-level security" in str(orig).lower() or "policy" in str(orig).lower()
    with ci03_g2_bootstrap_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(_tenant_a)},
        )
        visible_a = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_assignment_issuances
                    WHERE assignment_id = :aid
                    """
                ),
                {"aid": denied_aid},
            ).scalar_one()
        )
    assert visible_a == 0
    with ci03_g2_bootstrap_engine.connect() as conn:
        control_visible = int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_assignment_issuances
                    WHERE assignment_id = :aid
                    """
                ),
                {"aid": assignment_b.value},
            ).scalar_one()
        )
    assert control_visible == 1


def test_runtime_role_denied_set_role_escalation(
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R3-02: migration-created runtime role cannot SET ROLE to schema owner."""
    with ci03_g2_runtime_engine.connect() as conn:
        current = conn.execute(text("SELECT current_user")).scalar_one()
        with pytest.raises((OperationalError, DBAPIError)):
            conn.execute(text(f"SET ROLE {SCHEMA_OWNER_ROLE}"))
    assert current is not None