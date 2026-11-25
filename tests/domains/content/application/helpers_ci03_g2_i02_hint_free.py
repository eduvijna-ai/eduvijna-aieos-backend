"""Lawful hint-free IN_REVIEW approve without assignment binding (NULL review_assignment_id)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.http_append import HttpAppendContentVersionService
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import SqlAlchemyContentUnitOfWorkFactory
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import integrated_review_service
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    CLASS_REF,
    _event,
    _services,
)
from tests.domains.content.application.test_ci03_g2_i02_mutation_postgres import _seed_reviewers
from tests.domains.education.test_aieos_v1_hint_t1 import (
    _hint_free_v2_payload,
    build_preparation_content_schema_registry,
)
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    _append_v1,
    _event_context,
    _seed_worksheet_content,
)
from tests.domains.education.test_aieos_v1_hint_t2 import _review_service
from tests.fakes import AllowAssetReferenceValidation, IDEMPOTENCY_RETENTION


@dataclass(frozen=True, slots=True)
class HintFreeApprovedScenario:
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    reviewer_id: uuid.UUID
    content_id: ContentId
    version_id: ContentVersionId
    revision: AggregateRevision
    approve_idem: str
    review_decision_id: uuid.UUID


def build_hint_free_approved_scenario(
    bootstrap: Engine,
    runtime: Engine,
    *,
    now: datetime,
    tag: str,
) -> HintFreeApprovedScenario:
    provision_runtime_grants(bootstrap)
    tenant_id = uuid.uuid7()
    owner_id = uuid.uuid7()
    reviewer_id = uuid.uuid7()
    _seed_reviewers(bootstrap, tenant_id, owner_id, reviewer_id)
    content_id = _seed_worksheet_content(
        bootstrap, tenant_id=tenant_id, owner_principal_id=owner_id
    )
    _, rev_v1 = _append_v1(
        runtime, tenant_id=tenant_id, principal_id=owner_id, content_id=content_id
    )
    version_read, revision = HttpAppendContentVersionService(
        SqlAlchemyContentUnitOfWorkFactory(runtime),
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
        idempotency_key=f"{tag}-hf-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=now,
    )
    _, _, provider, *_ = _services(runtime, tenant_id)
    provider.set_reviewer_class_eligibility(reviewer_id, (CLASS_REF,))
    submitted = _review_service(runtime).submit(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=revision,
        idempotency_key=f"{tag}-submit-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=now,
    )
    approve_idem = f"{tag}-apr-{uuid.uuid7()}"
    integrated_review_service(
        runtime,
        tenant_id,
        clock=lambda: now,
        school_context_provider=provider,
    ).approve(
        tenant_id,
        reviewer_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=submitted.aggregate_revision,
        reason_code=None,
        comment=None,
        idempotency_key=approve_idem,
        event_context=_event(reviewer_id),
        audit_provenance=api_mutation_audit_provenance(reviewer_id),
        now=now,
    )
    with bootstrap.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT review_decision_id, review_assignment_id
                FROM content.review_decisions
                WHERE tenant_id = :tid AND version_id = :vid
                """
            ),
            {"tid": tenant_id, "vid": version_read.version_id.value},
        ).one()
    decision_id, assignment_bind = row[0], row[1]
    assert assignment_bind is None
    return HintFreeApprovedScenario(
        tenant_id=tenant_id,
        owner_id=owner_id,
        reviewer_id=reviewer_id,
        content_id=content_id,
        version_id=version_read.version_id,
        revision=submitted.aggregate_revision,
        approve_idem=approve_idem,
        review_decision_id=decision_id,
    )