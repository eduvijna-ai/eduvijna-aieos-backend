"""AIEOS360-S01-I02 — architecture isolation guards."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from aieos.platform.runtime.readiness import (
    EXPECTED_ALEMBIC_HEAD,
    _CONTENT_OWNED_SCHEMAS,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s01_i02

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
LEARNING_ROOT = SRC_ROOT / "domains" / "learning"
TEACHING_ROOT = SRC_ROOT / "domains" / "teaching"
ASSESSMENT_ROOT = SRC_ROOT / "domains" / "assessment"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
I02_MIGRATION = MIGRATIONS / "a360s010001_learning_attempt_submission.py"
ASSIGNMENT_MIGRATION = MIGRATIONS / "tosd060001_teaching_assignments.py"
ASSESSMENT_MIGRATION = MIGRATIONS / "tosd080001_classroom_assessments.py"
EVENTS_ROOT = SRC_ROOT / "platform" / "events"
NATS_ROOT = EVENTS_ROOT / "nats"


def _py_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.is_file()]


def _import_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _learning_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in _py_files(LEARNING_ROOT)
    )


class TestArchitectureIsolation:
    def test_i02_45_learning_does_not_import_teaching_application(self) -> None:
        offenders: list[str] = []
        for path in _py_files(LEARNING_ROOT):
            for module in _import_modules(path):
                if module == "aieos.domains.teaching" or module.startswith(
                    "aieos.domains.teaching."
                ):
                    offenders.append(f"{path}:{module}")
        assert offenders == []

    def test_i02_46_no_cross_domain_postgresql_fk_to_teaching(self) -> None:
        sql = I02_MIGRATION.read_text(encoding="utf-8")
        assert "REFERENCES teaching." not in sql
        assert "teaching.assignments" not in sql

    def test_i02_47_no_cross_domain_postgresql_fk_to_content(self) -> None:
        sql = I02_MIGRATION.read_text(encoding="utf-8")
        assert "REFERENCES content." not in sql
        assert "content.contents" not in sql
        assert "content.content_versions" not in sql

    def test_i02_48_principal_kind_student_absent(self) -> None:
        assert "STUDENT" not in PrincipalKind.__members__
        assert set(PrincipalKind.__members__) == {"HUMAN", "WORKLOAD"}

    def test_i02_49_teaching_assignment_migration_unchanged(self) -> None:
        text = ASSIGNMENT_MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd060001"' in text
        assert "learner_principal_id" not in text
        assert "max_attempts" not in text
        assignment_models = (
            TEACHING_ROOT / "infrastructure" / "persistence" / "models.py"
        ).read_text(encoding="utf-8")
        assert "max_attempts" not in assignment_models

    def test_i02_50_classroom_assessment_unchanged(self) -> None:
        text = ASSESSMENT_MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "tosd080001"' in text
        assert "learner_principal_id" not in text
        assessment_models = (
            ASSESSMENT_ROOT / "infrastructure" / "persistence" / "models.py"
        ).read_text(encoding="utf-8")
        assert "learner_attempt" not in assessment_models.lower()

    def test_i02_51_student_http_is_composed_outside_api_main(self) -> None:
        schema = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        paths = schema.get("paths") or {}
        assert any("student-os" in path for path in paths)
        assert any("/learning/" in path for path in paths)
        assert (LEARNING_ROOT / "api").is_dir()
        routes = SRC_ROOT / "platform" / "runtime" / "entrypoints" / "api_main.py"
        main = routes.read_text(encoding="utf-8")
        assert "student-os" not in main
        assert "domains.learning.api" not in main

    def test_i02_52_openapi_digest_matches_release_pin(self) -> None:
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256

    def test_i02_53_no_temporal(self) -> None:
        for path in _py_files(LEARNING_ROOT):
            modules = _import_modules(path)
            assert "temporalio" not in modules
            assert not any(module.startswith("temporalio.") for module in modules)
            assert "temporal" not in path.read_text(encoding="utf-8").lower()

    def test_i02_54_no_groq_openai_model_gateway_dependency(self) -> None:
        text = _learning_text().lower()
        assert "groq" not in text
        assert "openai" not in text
        assert "model gateway" not in text
        assert "aieos.platform.ai" not in _learning_text()

    def test_i02_55_no_mcp_student_agent(self) -> None:
        text = _learning_text().lower()
        assert "mcp" not in text
        assert "student agent" not in text
        assert "student_agent" not in text

    def test_i02_56_no_production_nats_learning_publication_permission(self) -> None:
        from aieos.platform.events.constants import PRODUCTION_EVENT_PUBLISH_PREFIXES

        learning_text = _learning_text()
        assert "nats" not in learning_text.lower()
        assert "io.eduvijna.aieos.learning.attempt.started.v1" not in learning_text
        assert "io.eduvijna.aieos.learning.attempt.submitted.v1" not in learning_text
        nats_blob = "\n".join(
            path.read_text(encoding="utf-8") for path in _py_files(NATS_ROOT)
        )
        assert "io.eduvijna.aieos.learning." not in nats_blob
        assert "learning.attempt" not in nats_blob
        assert PRODUCTION_EVENT_PUBLISH_PREFIXES == (
            "io.eduvijna.aieos.content.",
            "io.eduvijna.aieos.teaching.",
        )
        assert not any("learning" in prefix for prefix in PRODUCTION_EVENT_PUBLISH_PREFIXES)

    def test_i02_57_no_score_grade_mastery_schema(self) -> None:
        from aieos.domains.learning.infrastructure.persistence.models import (
            attempt_response_items_table,
            attempts_table,
            submissions_table,
        )

        columns = {
            column.name
            for table in (
                attempts_table,
                attempt_response_items_table,
                submissions_table,
            )
            for column in table.columns
        }
        for forbidden in (
            "score",
            "grade",
            "mastery",
            "misconception",
            "recommendation",
            "was_late",
            "late",
        ):
            assert forbidden not in columns

    def test_i02_58_no_roster_student_profile_schema(self) -> None:
        sql = I02_MIGRATION.read_text(encoding="utf-8")
        assert "CREATE TABLE learning.roster" not in sql
        assert "CREATE TABLE learning.student_profiles" not in sql
        assert "CREATE TABLE learning.enrollments" not in sql
        versions = [
            path.name.lower()
            for path in MIGRATIONS.glob("*.py")
            if path.name != "__init__.py"
        ]
        assert not any("roster" in name for name in versions)
        assert not any(
            "student-profile" in name or "student_profile" in name for name in versions
        )

    def test_alembic_head_and_learning_schema_ownership(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010003"
        assert EXPECTED_MIGRATION_HEAD == "a360s010003"
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["a360s010003"]
        assert "learning" in _CONTENT_OWNED_SCHEMAS
        sql = I02_MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "a360s010001"' in sql
        assert 'down_revision: str | None = "tosd100001"' in sql
        assert "CREATE SCHEMA learning" in sql
        assert "FOR UPDATE" in sql
        assert "parent LearnerAttempt not found" in sql
        uow = (
            LEARNING_ROOT / "infrastructure" / "persistence" / "uow.py"
        ).read_text(encoding="utf-8")
        assert "SqlAlchemyLearningUnitOfWork" in uow
        assert "S01-I03" in uow
        assert "SqlAlchemyTeachingUnitOfWork" not in uow
        assert "aieos.domains.teaching" not in uow
        learning_text = _learning_text()
        assert "start_learner_attempt" not in learning_text
        assert "submit_learner_attempt" not in learning_text
