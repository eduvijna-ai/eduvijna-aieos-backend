"""AIEOS360-S01-I05-B1 — architecture guards for evaluation persistence."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s01_i05_b1

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
ASSESSMENT_ROOT = SRC_ROOT / "domains" / "assessment"
DOMAIN_ROOT = ASSESSMENT_ROOT / "domain"
EVALUATOR = DOMAIN_ROOT / "evaluation_policy_v1.py"
MIGRATION = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "a360s010003_learner_assessment_evaluation.py"
)
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
ROUTES = ASSESSMENT_ROOT / "api" / "v1" / "routes.py"


def _py_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if path.is_file()]


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestB1ArchitectureGuards:
    def test_alembic_head_and_parent(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["a360s010004"]
        sql = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "a360s010003"' in sql
        assert 'down_revision: str | None = "a360s010002"' in sql

    def test_evaluator_is_pure_and_versioned(self) -> None:
        text = EVALUATOR.read_text(encoding="utf-8")
        assert "class DeterministicLearnerAssessmentEvaluatorV1" in text
        assert 'aieos.learner_assessment.deterministic' in text
        assert "POLICY_VERSION" in text
        assert "def grade(" not in text
        imports = _imports(EVALUATOR)
        for name in imports:
            assert "domains.content" not in name
            assert "domains.learning" not in name
            assert "sqlalchemy" not in name
            assert "fastapi" not in name
            assert "temporalio" not in name
            assert "nats" not in name
        for path in _py_files(DOMAIN_ROOT):
            for name in _imports(path):
                assert "domains.content" not in name
                assert "domains.learning" not in name

    def test_no_assessment_http_added(self) -> None:
        routes = ROUTES.read_text(encoding="utf-8")
        assert "learner-assessment-evaluations" not in routes
        assert "operation_id=\"learner_assessment" not in routes
        assert "/intelligence" not in routes

    def test_openapi_unchanged(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256

    def test_no_nats_temporal_outbox(self) -> None:
        for path in _py_files(ASSESSMENT_ROOT):
            if path.name in {
                "evaluation.py",
                "evaluation_policy_v1.py",
                "evaluation_input.py",
                "evaluation_vocabulary.py",
            } or "a360s010003" in path.name:
                text = path.read_text(encoding="utf-8").lower()
                assert "nats" not in text
                assert "temporal" not in text
                assert "outbox" not in text
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "outbox" not in sql.lower()
        assert "temporalio" not in sql.lower()
        assert "nats-py" not in sql.lower()
        assert "CREATE TABLE assessment.learner_assessment_evaluations" in sql

    def test_no_cross_domain_fk_in_b1_migration(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "REFERENCES learning." not in sql
        assert "REFERENCES content." not in sql
        assert "REFERENCES teaching." not in sql
        assert "fk_assessment_lae_items_evaluation" in sql
        assert "aggregate_revision" not in sql
        assert "SUPERSEDED" not in sql
        assert "lifecycle_state" not in sql
