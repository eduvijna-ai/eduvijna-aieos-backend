"""PostgreSQL acceptance for pedi090002 principal_kind substrate."""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

pytestmark = pytest.mark.tos_dev10_i03s1


@pytest.fixture(autouse=True)
def _head(postgres18, bootstrap_engine: Engine):
    cfg = alembic_config(postgres18["migrator_url"])
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)
    yield
    command.upgrade(cfg, "head")
    provision_runtime_grants(bootstrap_engine)


def _insert_principal(
    conn,
    *,
    principal_id: uuid.UUID,
    status: str = "ACTIVE",
    principal_kind: str | None = None,
) -> None:
    conn.execute(
        text("""
            INSERT INTO security.principals (
                principal_id, status, principal_kind, created_at, updated_at
            ) VALUES (
                :principal_id, :status, :principal_kind,
                clock_timestamp(), clock_timestamp()
            )
            """),
        {
            "principal_id": principal_id,
            "status": status,
            "principal_kind": principal_kind,
        },
    )


def test_current_database_head_is_tosd100001(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "a360s010001"
        )


def test_upgrade_from_tosd090002_adds_nullable_principal_kind(
    postgres18, bootstrap_engine: Engine
) -> None:
    cfg = alembic_config(postgres18["migrator_url"])
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
    command.downgrade(cfg, "tosd090002")
    provision_runtime_grants(bootstrap_engine)
    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "tosd090002"
        )
        cols = {row[0] for row in conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'security'
                      AND table_name = 'principals'
                    """))}
        assert "principal_kind" not in cols

    preexisting = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO security.principals (
                    principal_id, status, created_at, updated_at
                ) VALUES (
                    :id, 'ACTIVE', clock_timestamp(), clock_timestamp()
                )
                """),
            {"id": preexisting},
        )

    command.upgrade(cfg, "pedi090002")
    provision_runtime_grants(bootstrap_engine)
    with bootstrap_engine.connect() as conn:
        kind = conn.execute(
            text("""
                SELECT principal_kind FROM security.principals
                WHERE principal_id = :id
                """),
            {"id": preexisting},
        ).scalar_one()
        assert kind is None
        nullable = conn.execute(text("""
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'security'
                  AND table_name = 'principals'
                  AND column_name = 'principal_kind'
                """)).scalar_one()
        assert nullable == "YES"
        column_default = conn.execute(text("""
                SELECT column_default
                FROM information_schema.columns
                WHERE table_schema = 'security'
                  AND table_name = 'principals'
                  AND column_name = 'principal_kind'
                """)).scalar_one()
        assert column_default is None
        constraint = conn.execute(text("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'ck_security_principals_principal_kind'
                """)).scalar_one()
        assert "HUMAN" in constraint
        assert "WORKLOAD" in constraint


@pytest.mark.parametrize("kind", (None, "HUMAN", "WORKLOAD"))
def test_allowed_principal_kind_values(
    bootstrap_engine: Engine, kind: str | None
) -> None:
    with bootstrap_engine.begin() as conn:
        _insert_principal(conn, principal_id=uuid.uuid7(), principal_kind=kind)


@pytest.mark.parametrize("kind", ("TEACHER", "SERVICE", "ADMIN", "human", "Human"))
def test_rejected_principal_kind_values(bootstrap_engine: Engine, kind: str) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        with bootstrap_engine.begin() as conn:
            _insert_principal(conn, principal_id=uuid.uuid7(), principal_kind=kind)


def test_no_silent_reclassification_of_existing_null_rows(
    bootstrap_engine: Engine,
) -> None:
    principal_id = uuid.uuid7()
    with bootstrap_engine.begin() as conn:
        _insert_principal(conn, principal_id=principal_id, principal_kind=None)
        kind = conn.execute(
            text(
                "SELECT principal_kind FROM security.principals WHERE principal_id = :id"
            ),
            {"id": principal_id},
        ).scalar_one()
        assert kind is None


def test_empty_downgrade_drops_column_and_reupgrade_restores(
    postgres18, bootstrap_engine: Engine
) -> None:
    cfg = alembic_config(postgres18["migrator_url"])
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
    command.downgrade(cfg, "tosd090002")
    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "tosd090002"
        )
        cols = {row[0] for row in conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'security'
                      AND table_name = 'principals'
                    """))}
        assert "principal_kind" not in cols
        assert conn.execute(text("""
                    SELECT count(*) FROM pg_constraint
                    WHERE conname = 'ck_security_principals_principal_kind'
                    """)).scalar_one() == 0
    command.upgrade(cfg, "pedi090002")
    provision_runtime_grants(bootstrap_engine)
    with bootstrap_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "pedi090002"
        )
