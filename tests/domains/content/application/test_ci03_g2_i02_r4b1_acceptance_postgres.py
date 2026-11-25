"""CI03-G2-I02R4-B1 — Stage B completion proofs (receipt, expiry, ordering, publish)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import (
    PersistenceInvariantViolation,
    PublicationForbidden,
    ReviewAssignmentConflict,
    WorksheetLearnerHintAttestationInvalid,
)
from aieos.domains.content.application.ports import CONTENT_PUBLISH
from aieos.domains.content.application.publish import PublishContentService
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
from aieos.domains.content.domain.identities import AggregateRevision
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.platform.idempotency.models import (
    CONTENT_REVIEW_APPROVE_V1,
    CONTENT_WORKSHEET_HINT_ATTEST_V1,
)
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
)
from aieos.platform.security.authorization.content_adapters import (
    AIEOS_CONTENT_CAPABILITIES,
    KernelPublicationAuthorization,
)
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
from tests.domains.content.application.test_ci03_g2_i02_mutation_postgres import (
    _seed_reviewers,
)
from tests.domains.education.test_aieos_v1_hint_t1 import build_preparation_content_schema_registry
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowPublicationGovernance,
    IDEMPOTENCY_RETENTION,
)
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.ci03_g2_i02


def _corrupt_idempotency_fingerprint(
    bootstrap_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
    operation: str,
    *,
    idempotency_key: str | None = None,
) -> None:
    from aieos.platform.idempotency.hashing import hash_idempotency_key

    with bootstrap_engine.begin() as conn:
        params: dict[str, object] = {
            "bad": "f" * 64,
            "tid": tenant_id,
            "pid": principal_id,
            "op": operation,
        }
        sql = """
            UPDATE api.idempotency_records
            SET request_fingerprint_sha256 = :bad
            WHERE tenant_id = :tid
              AND actor_principal_id = :pid
              AND operation = :op
        """
        if idempotency_key is not None:
            sql += " AND idempotency_key_sha256 = :key"
            params["key"] = hash_idempotency_key(idempotency_key)
        conn.execute(text(sql), params)


def _corrupt_idempotency_attest_result_id(
    bootstrap_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE api.idempotency_records
                SET result_worksheet_hint_attestation_id = :wrong
                WHERE tenant_id = :tid
                  AND actor_principal_id = :pid
                  AND operation = :op
                """
            ),
            {
                "wrong": uuid.uuid7(),
                "tid": tenant_id,
                "pid": principal_id,
                "op": CONTENT_WORKSHEET_HINT_ATTEST_V1,
            },
        )


def _corrupt_idempotency_decision_result_id(
    bootstrap_engine: Engine,
    tenant_id: uuid.UUID,
    principal_id: uuid.UUID,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE api.idempotency_records
                SET result_review_decision_id = :wrong
                WHERE tenant_id = :tid
                  AND actor_principal_id = :pid
                  AND operation = :op
                """
            ),
            {
                "wrong": uuid.uuid7(),
                "tid": tenant_id,
                "pid": principal_id,
                "op": CONTENT_REVIEW_APPROVE_V1,
            },
        )


def _bound_hint_scenario(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    *,
    clock,
):
    provision_runtime_grants(bootstrap_engine)
    tenant_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    _seed_reviewers(bootstrap_engine, tenant_id, owner_id, reviewer_id)
    issue, revoke, provider, *_ = _services(runtime_engine, tenant_id, clock=clock)
    provider.set_teacher_class_authority(owner_id, (CLASS_REF,))
    provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
    content_id, version_id = seed_worksheet_in_review_governed(
        bootstrap_engine,
        runtime_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
        class_ref=CLASS_REF,
        now=clock(),
    )
    assignment_id = issue.issue(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_id,
        reviewer_principal_id=reviewer_id,
        idempotency_key=f"b1-issue-{uuid.uuid7()}",
        event_context=_event(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
    )
    with runtime_engine.connect() as conn:
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
    return (
        tenant_id,
        owner_id,
        reviewer_id,
        provider,
        revoke,
        assignment_id,
        content_id,
        version_id,
        AggregateRevision(int(revision)),
    )


class TestCi03G2I02R4B1ReceiptAndExpiry:
    def test_attest_replay_rejects_corrupt_persisted_fingerprint(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            *_,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        idem = f"b1-fp-corrupt-{uuid.uuid7()}"
        first = attest.record(
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
        )
        with pytest.raises(
            WorksheetLearnerHintAttestationInvalid, match="idempotency key already bound"
        ):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(
                        ci03_g2_runtime_engine, tenant_id, version_id
                    )
                ),
                idempotency_key=idem,
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )
        _ = first
        assert (
            _denial_count(ci03_g2_runtime_engine, tenant_id, CONTENT_REVIEW_MUTATION_DENIED)
            >= 1
        )

    def test_attest_replay_rejects_mismatched_persisted_result_attestation_id(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            *_,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        idem = f"b1-res-mismatch-{uuid.uuid7()}"
        attest.record(
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
        _corrupt_idempotency_attest_result_id(
            ci03_g2_bootstrap_engine, tenant_id, reviewer_id
        )
        with pytest.raises(
            WorksheetLearnerHintAttestationInvalid,
            match="identity mismatch",
        ):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(
                        ci03_g2_runtime_engine, tenant_id, version_id
                    )
                ),
                idempotency_key=idem,
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )

    def test_approve_replay_rejects_mismatched_decision_receipt_id(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            *_,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
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
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"b1-att-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        approve_idem = f"b1-apr-mismatch-{uuid.uuid7()}"
        review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=approve_idem,
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        _corrupt_idempotency_decision_result_id(
            ci03_g2_bootstrap_engine, tenant_id, reviewer_id
        )
        with pytest.raises(PersistenceInvariantViolation, match="identity mismatch"):
            review.approve(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                reason_code=None,
                comment=None,
                idempotency_key=approve_idem,
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )

    @pytest.mark.parametrize(
        ("effect",),
        (
            ("attest",),
            ("approve",),
            ("reject",),
            ("request_changes",),
        ),
    )
    def test_mutation_denied_when_assignment_expired_at_effect(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
        effect: str,
    ) -> None:
        clock_holder = {"t": _NOW}
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            *_,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=lambda: clock_holder["t"],
        )
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
        outcomes = _positive_outcomes(
            _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
        )
        if effect == "approve":
            clock_holder["t"] = _NOW
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=outcomes,
                idempotency_key=f"b1-pre-{uuid.uuid7()}",
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=clock_holder["t"],
            )
        clock_holder["t"] = _NOW + timedelta(days=8)
        with pytest.raises(ReviewAssignmentConflict, match="expired"):
            if effect == "attest":
                attest.record(
                    tenant_id,
                    reviewer_id,
                    content_id=content_id,
                    version_id=version_id,
                    expected_aggregate_revision=revision,
                    per_question_outcomes=outcomes,
                    idempotency_key=f"b1-exp-{effect}-{uuid.uuid7()}",
                    event_context=_event(reviewer_id),
                    audit_provenance=api_mutation_audit_provenance(reviewer_id),
                    now=clock_holder["t"],
                )
            elif effect == "approve":
                review.approve(
                    tenant_id,
                    reviewer_id,
                    content_id=content_id,
                    version_id=version_id,
                    expected_aggregate_revision=revision,
                    reason_code=None,
                    comment=None,
                    idempotency_key=f"b1-exp-{effect}-{uuid.uuid7()}",
                    event_context=_event(reviewer_id),
                    audit_provenance=api_mutation_audit_provenance(reviewer_id),
                    now=clock_holder["t"],
                )
            elif effect == "reject":
                review.reject(
                    tenant_id,
                    reviewer_id,
                    content_id=content_id,
                    version_id=version_id,
                    expected_aggregate_revision=revision,
                    reason_code=None,
                    comment=None,
                    idempotency_key=f"b1-exp-{effect}-{uuid.uuid7()}",
                    event_context=_event(reviewer_id),
                    audit_provenance=api_mutation_audit_provenance(reviewer_id),
                    now=clock_holder["t"],
                )
            else:
                review.request_changes(
                    tenant_id,
                    reviewer_id,
                    content_id=content_id,
                    version_id=version_id,
                    expected_aggregate_revision=revision,
                    reason_code=None,
                    comment="expiry",
                    idempotency_key=f"b1-exp-{effect}-{uuid.uuid7()}",
                    event_context=_event(reviewer_id),
                    audit_provenance=api_mutation_audit_provenance(reviewer_id),
                    now=clock_holder["t"],
                )
        assert (
            _denial_count(ci03_g2_runtime_engine, tenant_id, CONTENT_REVIEW_MUTATION_DENIED)
            >= 1
        )

    def test_publish_kernel_authorization_after_assignment_expired(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        clock_holder = {"t": _NOW}
        (
            tenant_id,
            owner_id,
            reviewer_id,
            provider,
            *_,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=lambda: clock_holder["t"],
        )
        publisher_id = uuid.uuid7()
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=publisher_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(CONTENT_PUBLISH,),
        )
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
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"b1-att-pub-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock_holder["t"],
        )
        approved = review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"b1-apr-pub-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock_holder["t"],
        )
        with ci03_g2_bootstrap_engine.connect() as conn:
            valid_until = conn.execute(
                text(
                    """
                    SELECT valid_until FROM content.review_assignment_issuances
                    WHERE tenant_id = :tid AND content_id = :cid AND version_id = :vid
                    """
                ),
                {"tid": tenant_id, "cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
        clock_holder["t"] = _NOW + timedelta(days=30)
        assert clock_holder["t"] > valid_until
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
        published = publish.publish(
            tenant_id,
            publisher_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=approved.aggregate_revision,
            idempotency_key=f"b1-publish-{uuid.uuid7()}",
            event_context=_event(publisher_id),
            audit_provenance=api_mutation_audit_provenance(publisher_id),
            now=clock_holder["t"],
        )
        assert published.version_id == version_id
        assert publisher_id != owner_id and publisher_id != reviewer_id

    def test_publish_denied_without_kernel_publish_grant(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            _owner,
            reviewer_id,
            provider,
            *_,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        no_publish_id = uuid.uuid7()
        seed_active_authority(
            ci03_g2_bootstrap_engine,
            tenant_id=tenant_id,
            principal_id=no_publish_id,
            principal_kind=PrincipalKind.HUMAN,
            capabilities=(),
        )
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
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"b1-att-nopub-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        approved = review.approve(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            reason_code=None,
            comment=None,
            idempotency_key=f"b1-apr-nopub-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
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
        with pytest.raises(PublicationForbidden):
            publish.publish(
                tenant_id,
                no_publish_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=approved.aggregate_revision,
                idempotency_key=f"b1-nopub-{uuid.uuid7()}",
                event_context=_event(no_publish_id),
                audit_provenance=api_mutation_audit_provenance(no_publish_id),
                now=_NOW,
            )


class TestCi03G2I02R4B1Ordering:
    def test_revoke_before_attest_denies_attest(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            owner_id,
            reviewer_id,
            provider,
            revoke,
            assignment_id,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        ctx = _event(owner_id)
        audit = api_mutation_audit_provenance(owner_id)
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key=f"b1-rev-first-{uuid.uuid7()}",
            event_context=ctx,
            audit_provenance=audit,
        )
        attest = integrated_attest_service(
            ci03_g2_runtime_engine,
            tenant_id,
            clock=lambda: _NOW,
            school_context_provider=provider,
        )
        with pytest.raises(ReviewAssignmentConflict):
            attest.record(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_id,
                expected_aggregate_revision=revision,
                per_question_outcomes=_positive_outcomes(
                    _hinted_ids_from_version(
                        ci03_g2_runtime_engine, tenant_id, version_id
                    )
                ),
                idempotency_key=f"b1-att-after-revoke-{uuid.uuid7()}",
                event_context=_event(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=_NOW,
            )

    def test_attest_before_revoke_succeeds_leaves_immutable_attestation(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
    ) -> None:
        (
            tenant_id,
            owner_id,
            reviewer_id,
            provider,
            revoke,
            assignment_id,
            content_id,
            version_id,
            revision,
        ) = _bound_hint_scenario(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
        )
        attest = integrated_attest_service(
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
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(ci03_g2_runtime_engine, tenant_id, version_id)
            ),
            idempotency_key=f"b1-att-before-revoke-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=_NOW,
        )
        ctx = _event(owner_id)
        audit = api_mutation_audit_provenance(owner_id)
        revoke.revoke(
            tenant_id,
            owner_id,
            content_id=content_id,
            version_id=version_id,
            assignment_id=assignment_id,
            idempotency_key=f"b1-rev-after-attest-{uuid.uuid7()}",
            event_context=ctx,
            audit_provenance=audit,
        )
        with ci03_g2_runtime_engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            attest_count = conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": content_id.value, "vid": version_id.value},
            ).scalar_one()
        assert int(attest_count) == 1