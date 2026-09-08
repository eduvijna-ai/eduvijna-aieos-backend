"""AIEOS360-S01-I03R1 — a360s010002 Chief Architect-authorized migration proofs."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import REPO_ROOT
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    clear_i03_side_effects,
    fetch_audit,
    prepare_class_5a_assignment,
    start_attempt,
    student_client,
)
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = [pytest.mark.aieos360_s01_i03, pytest.mark.aieos360_s01_i03r1]

I03_MIGRATION = REPO_ROOT / "migrations" / "versions" / "a360s010002_learning_attempt_audit_vocab.py"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"

_RETAINED_ACTIONS = (
    "content.create",
    "teaching.assignment.create",
    "assessment.classroom.record",
    "teaching.memory.create",
    "asset.create",
)
_LEARNING_ACTIONS = (
    "learning.attempt.start",
    "learning.attempt.save_responses",
    "learning.attempt.submit",
)


class TestA360s010002Static:
    def test_r1_24_25_revision_chain(self) -> None:
        sql = I03_MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "a360s010002"' in sql
        assert 'down_revision: str | None = "a360s010001"' in sql
        assert EXPECTED_ALEMBIC_HEAD == "a360s010002"
        assert EXPECTED_MIGRATION_HEAD == "a360s010002"

    def test_r1_26_27_28_29_scope_is_security_audit_vocabulary_only(self) -> None:
        sql = I03_MIGRATION.read_text(encoding="utf-8")
        assert "ALTER TABLE security.audit_records" in sql
        assert "CREATE TABLE" not in sql
        assert "ALTER TABLE learning." not in sql
        assert "ALTER TABLE teaching." not in sql
        assert "ALTER TABLE content." not in sql
        assert "DROP TABLE" not in sql
        names = [path.name for path in MIGRATIONS.glob("a360s010003*.py")]
        assert names == []

    def test_r1_30_33_learning_actions_added_and_existing_vocab_retained(self) -> None:
        sql = I03_MIGRATION.read_text(encoding="utf-8")
        for action in _LEARNING_ACTIONS:
            assert f"'{action}'" in sql
        for action in _RETAINED_ACTIONS:
            assert f"'{action}'" in sql


class TestA360s010002Postgres:
    def test_r1_30_upgrade_accepts_learning_audit_actions(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        assert started.status_code == 201, started.text
        actions = {
            row["action"]
            for row in fetch_audit(
                bootstrap_engine,
                tenant_id=prepared.tenant_id,
                action="learning.attempt.start",
            )
        }
        assert "learning.attempt.start" in actions

    def test_r1_31_downgrade_without_learning_audit_evidence_succeeds(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        clear_i03_side_effects(bootstrap_engine)
        command.downgrade(cfg, "a360s010001")
        try:
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010001"
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_r1_32_downgrade_with_learning_audit_evidence_fails_closed(
        self, postgres18, runtime_engine, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        assert started.status_code == 201, started.text
        try:
            with pytest.raises(Exception) as exc:
                command.downgrade(cfg, "a360s010001")
            message = str(exc.value)
            cause = exc.value.__cause__
            if cause is not None:
                message = f"{message} {cause}"
            assert "learning attempt security audit" in message.lower()
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010002"
                )
        finally:
            clear_i03_side_effects(bootstrap_engine)
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)
