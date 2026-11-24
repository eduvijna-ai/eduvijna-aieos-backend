"""Governed worksheet@2 IN_REVIEW fixtures for CI03-G2-I01 PG18 tests."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.engine import Engine

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from tests.domains.education.test_aieos_v1_hint_t1 import _worksheet_v2_payload
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    _append_v1,
    _event_context,
    _guarded_service,
    _seed_worksheet_content,
)
from tests.domains.education.test_aieos_v1_hint_t2 import _review_service
from aieos.domains.content.application.audit import api_mutation_audit_provenance


def seed_worksheet_in_review_governed(
    bootstrap_engine: Engine,
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    class_ref: str,
    now: datetime,
) -> tuple[ContentId, ContentVersionId]:
    """Author v1, guarded hint@2 append, submit for review — no version SQL."""
    content_id = _seed_worksheet_content(
        bootstrap_engine, tenant_id=tenant_id, owner_principal_id=owner_id
    )
    _, rev_after_v1 = _append_v1(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=owner_id,
        content_id=content_id,
    )
    version_read, revision = _guarded_service(runtime_engine).append(
        tenant_id,
        owner_id,
        content_id=content_id,
        expected_aggregate_revision=rev_after_v1,
        payload=_worksheet_v2_payload(hint="Think step by step."),
        authoring_class_ref=class_ref,
        idempotency_key=f"ci03-g2-hint-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=now,
    )
    _review_service(runtime_engine).submit(
        tenant_id,
        owner_id,
        content_id=content_id,
        version_id=version_read.version_id,
        expected_aggregate_revision=revision,
        idempotency_key=f"ci03-g2-submit-{uuid.uuid7()}",
        event_context=_event_context(owner_id),
        audit_provenance=api_mutation_audit_provenance(owner_id),
        now=now,
    )
    return content_id, version_read.version_id