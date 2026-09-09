"""AIEOS360-S01-I05-B2 — a360s010004 security audit vocabulary proofs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import REPO_ROOT
from tests.domains.assessment.helpers_s01_i05_b2 import (
    build_client,
    headers,
    seed_world,
)
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.aieos360_s01_i05_b2

MIGRATION = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "a360s010004_learner_evaluation_audit_vocab.py"
)
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
FIXED_NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


class TestA360s010004Static:
    def test_64_65_revision_chain(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "a360s010004"' in sql
        assert 'down_revision: str | None = "a360s010003"' in sql
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"

    def test_66_scope_is_security_audit_vocabulary_only(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "ALTER TABLE security.audit_records" in sql
        assert "CREATE TABLE" not in sql
        assert "ALTER TABLE learning." not in sql
        assert "ALTER TABLE teaching." not in sql
        assert "ALTER TABLE content." not in sql
        assert "ALTER TABLE assessment." not in sql
        assert "DROP TABLE" not in sql
        names = [path.name for path in MIGRATIONS.glob("a360s010005*.py")]
        assert names == []


class TestA360s010004Postgres:
    def test_current_head_is_a360s010004(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "a360s010004"
            )

    def test_valid_evaluation_ensure_audit_inserts(
        self, bootstrap_engine: Engine
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
                        :audit_id, :tenant_id, 'assessment.learner_evaluation.ensure',
                        'assessment.learner_evaluation', :resource_id, 0,
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

    def test_invalid_revision_transition_rejected(
        self, bootstrap_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        principal = uuid.uuid7()
        with pytest.raises(Exception):
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
                            :audit_id, :tenant_id, 'assessment.learner_evaluation.ensure',
                            'assessment.learner_evaluation', :resource_id, 0,
                            0, 0, CAST('[]' AS jsonb),
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

    def test_empty_downgrade_to_a360s010003_then_reupgrade(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade

        cfg = alembic_config(postgres18["migrator_url"])
        clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)
        command.downgrade(cfg, "a360s010003")
        try:
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010003"
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_nonempty_downgrade_refused(
        self, postgres18, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        from tests.domains.assessment.helpers_s01_i05_b2 import (
            SINGLE_PATH,
        )

        cfg = alembic_config(postgres18["migrator_url"])
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="mig-audit"),
        )
        assert response.status_code == 200, response.text
        try:
            with pytest.raises(Exception) as exc:
                command.downgrade(cfg, "a360s010003")
            message = str(exc.value)
            cause = exc.value.__cause__
            if cause is not None:
                message = f"{message} {cause}"
            assert "learner evaluation security audit" in message.lower()
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010004"
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)
