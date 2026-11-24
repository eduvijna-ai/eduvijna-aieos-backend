"""CI03-G2-I01 assignment schema, RLS, immutability."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from aieos.development.coherent_school_context import CLASS_REF_5A
from aieos.domains.content.application.ports import (
    CONTENT_REVIEW_ASSIGN,
    CONTENT_REVIEW_DECIDE,
    CONTENT_REVIEW_SUBMIT,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.conftest import alembic_config, provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    seed_worksheet_in_review_governed,
)
from tests.platform.security.authorization.helpers import seed_active_authority

pytestmark = pytest.mark.ci03_g2_i01

_CLASS_REF = CLASS_REF_5A
_NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def test_head_and_tables(ci03_g2_bootstrap_engine: Engine) -> None:
    with ci03_g2_bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "ci03g020002"
        )
    insp = inspect(ci03_g2_bootstrap_engine)
    names = set(insp.get_table_names(schema="content"))
    assert "review_assignment_issuances" in names
    assert "review_assignment_revocations" in names
    assert "review_current_assignments" in names
    security_names = set(insp.get_table_names(schema="security"))
    assert "authorization_denial_records" in security_names


def test_runtime_rejects_cross_version_slot_binding(
    ci03_g2_bootstrap_engine: Engine, ci03_g2_runtime_engine: Engine,
) -> None:
    from tests.conftest import provision_runtime_grants

    provision_runtime_grants(ci03_g2_bootstrap_engine)
    tenant_id = uuid.uuid7()
    content_id = uuid.uuid7()
    version_a = uuid.uuid7()
    version_b = uuid.uuid7()
    assignment_a = uuid.uuid7()
    owner = uuid.uuid7()
    reviewer = uuid.uuid7()
    prov = json.dumps(
        {
            "kind": "worksheet_learner_hint",
            "schema_version": 1,
            "authoring_class_ref": "class-x",
            "authoring_principal_id": str(owner),
        }
    )
    with ci03_g2_runtime_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        conn.execute(
            text(
                """
                INSERT INTO content.contents (
                    content_id, tenant_id, owner_principal_id, content_type, title,
                    description, locale, stewardship_state, current_version_id,
                    published_version_id, aggregate_revision, created_at,
                    created_by_principal_id, updated_at, archived_at
                ) VALUES (
                    :content_id, :tenant_id, :owner, 'education.worksheet', 'WS',
                    '', 'en-IN', 'IN_REVIEW', :version_b,
                    NULL, 2, now(), :owner, now(), NULL
                )
                """
            ),
            {
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner,
                "version_b": version_b,
            },
        )
        for vn, vid, parent in ((1, version_a, None), (2, version_b, version_a)):
            conn.execute(
                text(
                    """
                    INSERT INTO content.content_versions (
                        version_id, tenant_id, content_id, version_number,
                        parent_version_id, schema_id, schema_version, payload,
                        payload_sha256, origin, provenance, created_at,
                        created_by_principal_id
                    ) VALUES (
                        :vid, :tid, :cid, :vn, :parent,
                        'education.worksheet', 2, '{}'::jsonb,
                        :sha, 'HUMAN', CAST(:prov AS jsonb), now(), :owner
                    )
                    """
                ),
                {
                    "vid": vid,
                    "tid": tenant_id,
                    "cid": content_id,
                    "vn": vn,
                    "parent": parent,
                    "sha": "a" * 64,
                    "prov": prov,
                    "owner": owner,
                },
            )
        conn.execute(
            text(
                """
                INSERT INTO content.review_assignment_issuances (
                    assignment_id, tenant_id, content_id, version_id,
                    reviewer_principal_id, issuer_principal_id, class_ref,
                    issued_at, valid_until, correlation_id
                ) VALUES (
                    :aid, :tid, :cid, :vid, :reviewer, :owner, 'class-x',
                    now(), now() + interval '1 day', :corr
                )
                """
            ),
            {
                "aid": assignment_a,
                "tid": tenant_id,
                "cid": content_id,
                "vid": version_a,
                "reviewer": reviewer,
                "owner": owner,
                "corr": uuid.uuid7(),
            },
        )
        with pytest.raises(IntegrityError) as exc_info:
            with conn.begin_nested():
                conn.execute(
                    text(
                        """
                        INSERT INTO content.review_current_assignments (
                            tenant_id, content_id, version_id, active_assignment_id, updated_at
                        ) VALUES (
                            :tid, :cid, :vid, :aid, now()
                        )
                        """
                    ),
                    {
                        "tid": tenant_id,
                        "cid": content_id,
                        "vid": version_b,
                        "aid": assignment_a,
                    },
                )
        assert exc_info.value.orig.sqlstate == "23503"  # type: ignore[union-attr]
        assert "fk_review_current_assignments_active" in str(
            exc_info.value.orig
        ).lower()
        slot_count = conn.execute(
            text(
                """
                SELECT count(*) FROM content.review_current_assignments
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {"tid": tenant_id, "cid": content_id},
        ).scalar_one()
        assert int(slot_count) == 0


def test_downgrade_refuses_nonempty_assignment_evidence(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
    ci03_g2_postgres18: dict,
) -> None:
    from aieos.development.coherent_school_context import (
        development_coherent_school_context_provider,
    )
    from aieos.development.reviewer_eligibility import development_reviewer_eligibility_reader
    from aieos.domains.content.application.review_assignment_issue import (
        IssueReviewAssignmentService,
    )
    from aieos.domains.content.application.school_context_reviewer_eligibility import (
        SchoolContextReviewerEligibilityService,
    )
    from aieos.domains.teaching.application.school_context import (
        SchoolContextClassAuthorityService,
    )
    from aieos.domains.content.infrastructure.persistence.uow import (
        SqlAlchemyContentUnitOfWorkFactory,
    )
    from aieos.platform.events.models import MutationEventContext
    from aieos.platform.security.authorization import (
        AuthorizationKernel,
        CurrentPrincipalClassificationAuthority,
        KernelReviewAssignmentAuthorization,
        KernelReviewAuthorization,
    )
    from aieos.platform.security.authorization.content_adapters import (
        AIEOS_CONTENT_CAPABILITIES,
    )
    from aieos.platform.security.audit.persistence.authorization_denial_records import (
        SqlAlchemyAuthorizationDenialRecords,
    )
    from aieos.domains.content.application.audit import api_mutation_audit_provenance
    from datetime import timedelta

    provision_runtime_grants(ci03_g2_bootstrap_engine)
    tenant_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_REVIEW_ASSIGN, CONTENT_REVIEW_SUBMIT),
    )
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=reviewer_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_REVIEW_DECIDE,),
    )
    provider = development_coherent_school_context_provider(tenant_id=tenant_id)
    provider.set_teacher_class_authority(owner_id, (_CLASS_REF,))
    provider.set_reviewer_class_eligibility(reviewer_id, (_CLASS_REF,))
    content_id, version_id = seed_worksheet_in_review_governed(
        ci03_g2_bootstrap_engine,
        ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
        class_ref=_CLASS_REF,
        now=_NOW,
    )
    kernel = AuthorizationKernel(
        ci03_g2_runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES
    )
    uow_factory = SqlAlchemyContentUnitOfWorkFactory(ci03_g2_runtime_engine)
    issue = IssueReviewAssignmentService(
        uow_factory,
        KernelReviewAssignmentAuthorization(kernel),
        KernelReviewAuthorization(kernel),
        CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine),
        SchoolContextClassAuthorityService(provider),
        SchoolContextReviewerEligibilityService(
            development_reviewer_eligibility_reader(provider)
        ),
        SqlAlchemyAuthorizationDenialRecords(ci03_g2_runtime_engine),
        idempotency_retention=timedelta(hours=24),
        clock=lambda: _NOW,
    )
    ctx = MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=owner_id,
        effective_actor_id=owner_id,
    )
    issue.issue(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_id,
        reviewer_principal_id=reviewer_id,
        idempotency_key="downgrade-proof",
        event_context=ctx,
        audit_provenance=api_mutation_audit_provenance(owner_id),
    )
    cfg = alembic_config(ci03_g2_postgres18["migrator_url"])
    with pytest.raises(Exception, match="refused"):
        command.downgrade(cfg, "a360s010005")
    with ci03_g2_bootstrap_engine.connect() as conn:
        head = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert head == "ci03g020002"
        rows = conn.execute(
            text(
                "SELECT count(*) FROM content.review_assignment_issuances "
                "WHERE tenant_id = :tid AND content_id = :cid"
            ),
            {"tid": tenant_id, "cid": content_id.value},
        ).scalar_one()
        assert int(rows) == 1


def test_bound_attestation_fk_requires_matching_reviewer_principal(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    """Five-column assignment FK must match issuance reviewer, not only assignment id."""
    from sqlalchemy.exc import IntegrityError

    provision_runtime_grants(ci03_g2_bootstrap_engine)
    tenant_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    wrong_reviewer = uuid.uuid7()
    seed_active_authority(
        ci03_g2_bootstrap_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        principal_kind=PrincipalKind.HUMAN,
        capabilities=(CONTENT_REVIEW_ASSIGN, CONTENT_REVIEW_SUBMIT),
    )
    content_id, version_id = seed_worksheet_in_review_governed(
        ci03_g2_bootstrap_engine,
        ci03_g2_runtime_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
        class_ref=_CLASS_REF,
        now=_NOW,
    )
    assignment_id = uuid.uuid7()
    with ci03_g2_runtime_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        conn.execute(
            text(
                """
                INSERT INTO content.review_assignment_issuances (
                    assignment_id, tenant_id, content_id, version_id,
                    reviewer_principal_id, issuer_principal_id, class_ref,
                    issued_at, valid_until, correlation_id
                ) VALUES (
                    :aid, :tid, :cid, :vid, :reviewer, :owner, :cref,
                    :now, :now + interval '1 day', :corr
                )
                """
            ),
            {
                "aid": assignment_id,
                "tid": tenant_id,
                "cid": content_id.value,
                "vid": version_id.value,
                "reviewer": reviewer_id,
                "owner": owner_id,
                "cref": _CLASS_REF,
                "now": _NOW,
                "corr": uuid.uuid7(),
            },
        )
        with pytest.raises(IntegrityError) as exc_info:
            with conn.begin_nested():
                conn.execute(
                    text(
                        """
                        INSERT INTO content.worksheet_learner_hint_version_attestations (
                            attestation_id, tenant_id, content_id, version_id,
                            payload_digest, qa_policy_version, author_principal_id,
                            reviewer_principal_id, per_question_outcomes, attested_at,
                            correlation_id, review_assignment_id
                        ) VALUES (
                            :att_id, :tid, :cid, :vid,
                            :digest, 'worksheet_hint_qa_v1', :owner, :wrong_rev,
                            '[]'::jsonb, :now, :corr, :aid
                        )
                        """
                    ),
                    {
                        "att_id": uuid.uuid7(),
                        "tid": tenant_id,
                        "cid": content_id.value,
                        "vid": version_id.value,
                        "digest": "a" * 64,
                        "owner": owner_id,
                        "wrong_rev": wrong_reviewer,
                        "now": _NOW,
                        "corr": uuid.uuid7(),
                        "aid": assignment_id,
                    },
                )
        assert exc_info.value.orig.sqlstate == "23503"  # type: ignore[union-attr]