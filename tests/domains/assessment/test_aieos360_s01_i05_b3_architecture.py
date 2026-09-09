"""AIEOS360-S01-I05-B3 — architecture / governance guards."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from aieos.domains.assessment.application.ports import (
    AIEOS_ASSESSMENT_CAPABILITIES,
    ASSESSMENT_ASSIGNMENT_INTELLIGENCE_READ,
)
from aieos.platform.runtime.activation import READ_ONLY_OPERATION_IDS
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.authorization.assessment_adapters import (
    AIEOS_ASSESSMENT_CAPABILITIES as ADAPTER_CAPABILITIES,
)
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s01_i05_b3

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
ASSESSMENT_ROOT = SRC_ROOT / "domains" / "assessment"
INTELLIGENCE = ASSESSMENT_ROOT / "application" / "intelligence.py"
ROUTES = ASSESSMENT_ROOT / "api" / "v1" / "routes.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
NATS_ROOT = SRC_ROOT / "platform" / "events" / "nats"
TEMPORAL_ROOT = SRC_ROOT / "platform" / "workflows"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestB3ArchitectureGuards:
    def test_capability_and_readonly_surface(self) -> None:
        assert ASSESSMENT_ASSIGNMENT_INTELLIGENCE_READ == (
            "assessment.assignment.intelligence.read"
        )
        assert ASSESSMENT_ASSIGNMENT_INTELLIGENCE_READ in AIEOS_ASSESSMENT_CAPABILITIES
        assert AIEOS_ASSESSMENT_CAPABILITIES == ADAPTER_CAPABILITIES
        routes = ROUTES.read_text(encoding="utf-8")
        assert (
            '"/assessment/assignments/{assignment_id}/intelligence"' in routes
        )
        assert 'operation_id="assessment_assignment_intelligence"' in routes
        assert "assessment_assignment_intelligence" in READ_ONLY_OPERATION_IDS

    def test_alembic_head_unchanged(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["a360s010004"]

    def test_no_persistence_migration_for_b3(self) -> None:
        versions = REPO_ROOT / "migrations" / "versions"
        assert not any(path.name.startswith("a360s010005") for path in versions.glob("*.py"))

    def test_intelligence_service_is_read_only_composition(self) -> None:
        imports = _imports(INTELLIGENCE)
        for name in imports:
            assert "domains.learning.infrastructure" not in name
            assert "domains.content.infrastructure" not in name
            assert "sqlalchemy" not in name
            assert "fastapi" not in name
        text = INTELLIGENCE.read_text(encoding="utf-8")
        assert "ensure_submission" not in text
        assert "ensure_assignment" not in text
        assert "insert(" not in text
        assert "EnsureLearnerAssessmentEvaluationService" not in text
        assert "RecordClassroomAssessment" not in text
        assert "CreateTeachingWork" not in text
        assert "not_submitted" not in text.lower()
        assert "nats" not in text.lower()
        assert "temporal" not in text.lower()
        assert "outbox" not in text.lower()
        assert "mastery" not in text.lower()

    def test_openapi_contains_intelligence_and_matches_pin(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        schema = OPENAPI.read_text(encoding="utf-8")
        assert (
            "/api/v1/assessment/assignments/{assignment_id}/intelligence" in schema
        )
        assert "assessment_assignment_intelligence" in schema

    def test_nats_temporal_unrelated(self) -> None:
        assert NATS_ROOT.is_dir()
        assert TEMPORAL_ROOT.is_dir()
        text = INTELLIGENCE.read_text(encoding="utf-8")
        assert "nats" not in text.lower()
        assert "temporalio" not in text.lower()
