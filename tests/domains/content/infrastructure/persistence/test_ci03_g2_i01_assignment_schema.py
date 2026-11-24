"""CI03-G2-I01 assignment schema, RLS, immutability."""

from __future__ import annotations

import json
import uuid

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.ci03_g2_i01


def test_head_and_tables(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "ci03g020001"
        )
    insp = inspect(bootstrap_engine)
    names = set(insp.get_table_names(schema="content"))
    assert "review_assignment_issuances" in names
    assert "review_assignment_revocations" in names
    assert "review_current_assignments" in names
    security_names = set(insp.get_table_names(schema="security"))
    assert "authorization_denial_records" in security_names


def test_runtime_rejects_cross_version_slot_binding(
    bootstrap_engine: Engine, runtime_engine: Engine, postgres18: str
) -> None:
    _ = postgres18
    from tests.conftest import provision_runtime_grants

    provision_runtime_grants(bootstrap_engine)
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
        with pytest.raises(Exception):
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


def test_downgrade_refuses_nonempty_assignment_evidence(
    bootstrap_engine: Engine, postgres18: dict
) -> None:
    provision_runtime_grants(bootstrap_engine)
    tenant_id = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO content.review_assignment_issuances (
                    assignment_id, tenant_id, content_id, version_id,
                    reviewer_principal_id, issuer_principal_id, class_ref,
                    issued_at, valid_until, correlation_id
                ) VALUES (
                    :aid, :tid, :cid, :vid, :reviewer, :issuer, 'class-x',
                    now(), now() + interval '1 day', :corr
                )
                """
            ),
            {
                "aid": uuid.uuid7(),
                "tid": tenant_id,
                "cid": uuid.uuid7(),
                "vid": uuid.uuid7(),
                "reviewer": uuid.uuid7(),
                "issuer": uuid.uuid7(),
                "corr": uuid.uuid7(),
            },
        )
    cfg = alembic_config(postgres18["migrator_url"])
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
    with pytest.raises(Exception, match="refused"):
        command.downgrade(cfg, "a360s010005")