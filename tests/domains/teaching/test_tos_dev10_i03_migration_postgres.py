"""TOS-DEV10-I03 Teacher Memory migration PostgreSQL acceptance."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.tos_dev10_i03

FIXED_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _head(postgres18, bootstrap_engine: Engine):
    cfg = alembic_config(postgres18["migrator_url"])
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)
    yield
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)


def _clear_memory_evidence(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(text("DELETE FROM teaching.teacher_memories"))
        conn.execute(
            text(
                """
                DELETE FROM security.audit_records
                WHERE action IN (
                    'teaching.memory.create',
                    'teaching.memory.update'
                )
                """
            )
        )


def _principal_kind_column_intact(conn) -> None:
    nullable = conn.execute(
        text(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'security'
              AND table_name = 'principals'
              AND column_name = 'principal_kind'
            """
        )
    ).scalar_one()
    assert nullable == "YES"
    column_default = conn.execute(
        text(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'security'
              AND table_name = 'principals'
              AND column_name = 'principal_kind'
            """
        )
    ).scalar_one()
    assert column_default is None
    constraint = conn.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = 'ck_security_principals_principal_kind'
            """
        )
    ).scalar_one()
    assert "HUMAN" in constraint
    assert "WORKLOAD" in constraint


def test_upgrade_from_pedi090002_creates_table_preserves_principal_kind(
    postgres18, bootstrap_engine: Engine
) -> None:
    cfg = alembic_config(postgres18["migrator_url"])
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
    _clear_memory_evidence(bootstrap_engine)
    command.downgrade(cfg, "pedi090002")
    provision_runtime_grants(bootstrap_engine)

    preexisting = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "pedi090002"
        )
        exists_before = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'teaching'
                      AND table_name = 'teacher_memories'
                )
                """
            )
        ).scalar_one()
        assert exists_before is False
        _principal_kind_column_intact(conn)
        conn.execute(
            text(
                """
                INSERT INTO security.principals (
                    principal_id, status, principal_kind, created_at, updated_at
                ) VALUES (
                    :id, 'ACTIVE', NULL, clock_timestamp(), clock_timestamp()
                )
                """
            ),
            {"id": preexisting},
        )

    command.upgrade(cfg, "tosd100001")
    provision_runtime_grants(bootstrap_engine)

    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "tosd100001"
        )
        cols = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'teaching'
                      AND table_name = 'teacher_memories'
                    """
                )
            )
        }
        assert {
            "memory_id",
            "tenant_id",
            "teacher_principal_id",
            "schema_version",
            "preferences",
            "aggregate_revision",
            "created_at",
            "updated_at",
        } <= cols
        rls = conn.execute(
            text(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'teaching' AND c.relname = 'teacher_memories'
                """
            )
        ).one()
        assert tuple(rls) == (True, True)
        indexes = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT indexname FROM pg_indexes
                    WHERE schemaname = 'teaching'
                      AND tablename = 'teacher_memories'
                    """
                )
            )
        }
        assert "ix_teaching_teacher_memories_tenant_teacher" in indexes
        uniques = {
            row[0]
            for row in conn.execute(
                text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE conrelid = 'teaching.teacher_memories'::regclass
                      AND contype = 'u'
                    """
                )
            )
        }
        assert "uq_teaching_teacher_memories_tenant_teacher" in uniques
        assert "uq_teaching_teacher_memories_tenant_memory" in uniques
        _principal_kind_column_intact(conn)
        kind = conn.execute(
            text(
                """
                SELECT principal_kind FROM security.principals
                WHERE principal_id = :id
                """
            ),
            {"id": preexisting},
        ).scalar_one()
        assert kind is None


def test_empty_downgrade_to_pedi090002(
    postgres18, bootstrap_engine: Engine
) -> None:
    cfg = alembic_config(postgres18["migrator_url"])
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
    _clear_memory_evidence(bootstrap_engine)
    command.downgrade(cfg, "pedi090002")
    provision_runtime_grants(bootstrap_engine)
    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "pedi090002"
        )
        exists = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'teaching'
                      AND table_name = 'teacher_memories'
                )
                """
            )
        ).scalar_one()
        assert exists is False
        _principal_kind_column_intact(conn)


def test_memory_audit_actions_accepted_and_downgrade_blocked_with_evidence(
    postgres18, bootstrap_engine: Engine
) -> None:
    tenant_id = uuid.uuid7()
    principal = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO security.audit_records (
                    audit_record_id, tenant_id, action,
                    primary_resource_type, primary_resource_id,
                    primary_resource_revision,
                    resource_revision_before, resource_revision_after,
                    related_resource_refs,
                    initiating_principal_id, effective_actor_id,
                    executing_principal_id, delegation_id, execution_channel,
                    correlation_id, causation_id, trace_id, occurred_at
                ) VALUES (
                    :audit_id, :tenant_id, 'teaching.memory.create',
                    'teaching.memory', :resource_id, 0,
                    NULL, 0, CAST('[]' AS jsonb),
                    :principal, :principal, :principal, NULL, 'API',
                    :correlation_id, :causation_id, NULL, :occurred_at
                )
                """
            ),
            {
                "audit_id": uuid.uuid7(),
                "tenant_id": tenant_id,
                "resource_id": uuid.uuid7(),
                "principal": principal,
                "correlation_id": uuid.uuid7(),
                "causation_id": uuid.uuid7(),
                "occurred_at": FIXED_NOW,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO teaching.teacher_memories (
                    memory_id, tenant_id, teacher_principal_id, schema_version,
                    preferences, aggregate_revision, created_at, updated_at
                ) VALUES (
                    :memory_id, :tenant_id, :principal, 1,
                    CAST(:prefs AS jsonb), 0, :now, :now
                )
                """
            ),
            {
                "memory_id": uuid.uuid7(),
                "tenant_id": tenant_id,
                "principal": principal,
                "prefs": '{"teaching_style":"balanced","preferred_difficulty":"standard","preparation_detail":"balanced","output_format":"structured","include_differentiation":false}',
                "now": FIXED_NOW,
            },
        )

    cfg = alembic_config(postgres18["migrator_url"])
    with pytest.raises(Exception):
        command.downgrade(cfg, "pedi090002")

    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "tosd100001"
        )
