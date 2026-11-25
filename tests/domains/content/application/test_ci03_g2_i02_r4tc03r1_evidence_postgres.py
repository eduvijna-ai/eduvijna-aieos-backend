"""CI03-G2-I02R4-TC03R1 — Chief evidence corrections (PG18)."""

from __future__ import annotations

import dataclasses
import json
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
from aieos.domains.content.application.worksheet_hint_append import (
    worksheet_learner_hint_version_provenance,
)
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
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    _append_v1,
    _event_context,
    _seed_worksheet_content,
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
    _ = wrong, original_get


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


def test_issuer_submit_revoked_blocks_reissue_after_bound_attest(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R1-03: submit loss on protected issuer mutation after attest exists."""
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
    issue, *_ = _services(ci03_g2_runtime_engine, tenant_id, clock=lambda: _NOW)
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
        issue.issue(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            reviewer_principal_id=reviewer_id,
            idempotency_key=f"tc03r1-reissue-{uuid.uuid7()}",
            event_context=_event(owner_id),
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
    assert after.attestations == before.attestations
    assert after.assignment_issuance_rows == before.assignment_issuance_rows


@pytest.mark.parametrize(
    "malformed",
    (
        pytest.param(lambda _tid, _pid: (123,), id="tuple_non_string"),
        pytest.param(lambda _tid, _pid: [CLASS_REF], id="list_not_tuple"),
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
    with pytest.raises((SchoolContextUnavailable, WorksheetLearnerHintAttestationInvalid)):
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
    """TC03R1-03: hint-free / legacy path not affected by assignment-bound attestation gate.

    HTTP v2 append leaves provenance null; bootstrap-only provenance attach matches T1c
  trigger gate pattern so issue can bind class_ref without mutating payload.
    """
    from tests.conftest import provision_runtime_grants
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
    provenance = worksheet_learner_hint_version_provenance(
        authoring_class_ref=CLASS_REF, principal_id=owner_id
    )
    with ci03_g2_bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE content.content_versions DISABLE TRIGGER "
                "content_versions_immutable_update"
            )
        )
        conn.execute(
            text(
                """
                UPDATE content.content_versions
                SET provenance = CAST(:prov AS jsonb)
                WHERE tenant_id = :tid AND version_id = :vid
                """
            ),
            {
                "prov": json.dumps(provenance),
                "tid": tenant_id,
                "vid": version_read.version_id.value,
            },
        )
        conn.execute(
            text(
                "ALTER TABLE content.content_versions ENABLE TRIGGER "
                "content_versions_immutable_update"
            )
        )
    from tests.domains.education.test_aieos_v1_hint_t2 import _review_service

    _review_service(ci03_g2_runtime_engine).submit(
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
    issue, _, provider, *_ = _services(ci03_g2_runtime_engine, tenant_id)
    provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
    provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
    issue.issue(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_read.version_id,
        reviewer_principal_id=reviewer_id,
        idempotency_key=f"tc03r1-issue-{uuid.uuid7()}",
        event_context=_event(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
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
        expected_aggregate_revision=revision,
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


def test_historical_publish_allowed_with_terminal_approve_binding(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R1-03: lawful historical publication after terminal approve (O64-05)."""
    scenario = build_terminal_approved_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine
    )
    publisher = uuid.uuid7()
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=scenario.tenant_id,
        principal_id=publisher,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_PUBLISH,),
    )
    kernel = AuthorizationKernel(
        ci03_g2_runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
    )
    publish = PublishContentService(
        SqlAlchemyContentUnitOfWorkFactory(ci03_g2_runtime_engine),
        KernelPublicationAuthorization(kernel),
        AllowPublicationGovernance(),
        AllowAssetCurrentGovernance(),
        build_preparation_content_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        principal_classification=CurrentPrincipalClassificationAuthority(
            ci03_g2_runtime_engine
        ),
    )
    with ci03_g2_runtime_engine.connect() as conn:
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
        idempotency_key=f"tc03r1-hist-pub-{uuid.uuid7()}",
        event_context=_event(publisher),
        audit_provenance=api_mutation_audit_provenance(publisher),
        now=_NOW,
    )
    _ = kernel


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
    ci03_g2_runtime_engine: Engine,
) -> None:
    """TC03R1-01: runtime tenant A context cannot insert tenant B issuance row."""
    tenant_a = uuid.uuid7()
    tenant_b = uuid.uuid7()
    fresh_aid = uuid.uuid7()
    with pytest.raises(
        (OperationalError, DBAPIError),
        match=r"42501|row-level security|violates row-level security",
    ):
        with ci03_g2_runtime_engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_a)},
            )
            conn.execute(
                text(
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
                ),
                {
                    "aid": fresh_aid,
                    "tid_row": tenant_b,
                    "cid": uuid.uuid7(),
                    "vid": uuid.uuid7(),
                    "rev": uuid.uuid7(),
                    "iss": uuid.uuid7(),
                    "cref": CLASS_REF,
                    "issued": _NOW,
                    "until": _NOW + timedelta(days=1),
                    "corr": uuid.uuid7(),
                },
            )
    with ci03_g2_runtime_engine.connect() as conn:
        visible = int(
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
    assert visible == 0