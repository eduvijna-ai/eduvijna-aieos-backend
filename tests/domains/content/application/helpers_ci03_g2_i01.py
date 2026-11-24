"""Governed worksheet@2 fixtures for CI03-G2-I01 PG18 tests."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.version import ContentPayload
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2
from aieos.domains.education.worksheet_v2 import normalized_worksheet_v2_mapping_from_raw


def hint_v2_payload() -> dict:
    return {
        "title": "CI03 G2 worksheet",
        "questions": [
            {
                "question_id": "q1",
                "type": "short_answer",
                "stem": "2+2?",
                "answer": "4",
                "explanation": "basic",
                "learner_hint": "count on fingers",
            }
        ],
    }


def seed_worksheet_in_review_governed(
    runtime_engine: Engine,
    *,
    tenant_id: uuid.UUID,
    owner_id: uuid.UUID,
    class_ref: str,
    now: datetime,
) -> tuple[uuid.UUID, uuid.UUID]:
    content_id = uuid.uuid7()
    parent_version_id = uuid.uuid7()
    version_id = uuid.uuid7()
    normalized = normalized_worksheet_v2_mapping_from_raw(hint_v2_payload())
    domain_payload = ContentPayload.from_mapping(normalized)
    payload_sha256 = domain_payload.sha256.value
    prov = {
        "kind": "worksheet_learner_hint",
        "schema_version": 1,
        "authoring_class_ref": class_ref,
        "authoring_principal_id": str(owner_id),
    }
    with runtime_engine.begin() as conn:
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
                    '', 'en-IN', 'IN_REVIEW', :version_id,
                    NULL, 2, :now, :owner, :now, NULL
                )
                """
            ),
            {
                "content_id": content_id,
                "tenant_id": tenant_id,
                "owner": owner_id,
                "version_id": version_id,
                "now": now,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :version_id, :tenant_id, :content_id, 1, NULL,
                    :schema_id, 1, '{"marker":"v1"}'::jsonb, :sha, 'HUMAN',
                    NULL, :now, :owner
                )
                """
            ),
            {
                "version_id": parent_version_id,
                "tenant_id": tenant_id,
                "content_id": content_id,
                "schema_id": WORKSHEET_SCHEMA_ID,
                "sha": payload_sha256,
                "now": now,
                "owner": owner_id,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO content.content_versions (
                    version_id, tenant_id, content_id, version_number, parent_version_id,
                    schema_id, schema_version, payload, payload_sha256, origin,
                    provenance, created_at, created_by_principal_id
                ) VALUES (
                    :version_id, :tenant_id, :content_id, 2, :parent,
                    :schema_id, :schema_version, CAST(:payload AS jsonb), :sha, 'HUMAN',
                    CAST(:prov AS jsonb), :now, :owner
                )
                """
            ),
            {
                "version_id": version_id,
                "tenant_id": tenant_id,
                "content_id": content_id,
                "parent": parent_version_id,
                "schema_id": WORKSHEET_SCHEMA_ID,
                "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                "payload": json.dumps(normalized),
                "sha": payload_sha256,
                "prov": json.dumps(prov),
                "now": now,
                "owner": owner_id,
            },
        )
    return content_id, version_id