"""AIEOS360-S01-I05-B2 — architecture / governance guards."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from aieos.domains.assessment.application.ports import (
    AIEOS_ASSESSMENT_CAPABILITIES,
    ASSESSMENT_LEARNER_EVALUATION_ENSURE,
)
from aieos.platform.idempotency.models import (
    ASSESSMENT_ASSIGNMENT_EVALUATIONS_ENSURE_V1,
    ASSESSMENT_LEARNER_EVALUATION_ENSURE_V1,
)
from aieos.platform.runtime.activation import FROZEN_API_MUTATION_OPERATION_IDS
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.audit.actions import SecurityAuditAction
from aieos.platform.security.authorization.assessment_adapters import (
    AIEOS_ASSESSMENT_CAPABILITIES as ADAPTER_CAPABILITIES,
)
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s01_i05_b2

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
ASSESSMENT_ROOT = SRC_ROOT / "domains" / "assessment"
ENSURE = ASSESSMENT_ROOT / "application" / "evaluation_ensure.py"
ROUTES = ASSESSMENT_ROOT / "api" / "v1" / "routes.py"
OPENAPI = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATION = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "a360s010004_learner_evaluation_audit_vocab.py"
)
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


class TestB2ArchitectureGuards:
    def test_59_60_capability_and_audit_action_frozen(self) -> None:
        assert ASSESSMENT_LEARNER_EVALUATION_ENSURE == (
            "assessment.learner_evaluation.ensure"
        )
        assert ASSESSMENT_LEARNER_EVALUATION_ENSURE in AIEOS_ASSESSMENT_CAPABILITIES
        assert AIEOS_ASSESSMENT_CAPABILITIES == ADAPTER_CAPABILITIES
        assert (
            SecurityAuditAction.ASSESSMENT_LEARNER_EVALUATION_ENSURE.value
            == "assessment.learner_evaluation.ensure"
        )

    def test_51_52_53_54_http_surface(self) -> None:
        routes = ROUTES.read_text(encoding="utf-8")
        assert (
            '"/assessment/submissions/{submission_id}/actions/evaluate"' in routes
        )
        assert (
            '"/assessment/assignments/{assignment_id}/actions/ensure-evaluations"'
            in routes
        )
        assert 'operation_id="assessment_learner_evaluation_ensure"' in routes
        assert 'operation_id="assessment_assignment_evaluations_ensure"' in routes
        assert "assessment_learner_evaluation_ensure" in FROZEN_API_MUTATION_OPERATION_IDS
        assert (
            "assessment_assignment_evaluations_ensure"
            in FROZEN_API_MUTATION_OPERATION_IDS
        )

    def test_v_idempotency_operations(self) -> None:
        assert (
            ASSESSMENT_LEARNER_EVALUATION_ENSURE_V1
            == "assessment_learner_evaluation_ensure.v1"
        )
        assert (
            ASSESSMENT_ASSIGNMENT_EVALUATIONS_ENSURE_V1
            == "assessment_assignment_evaluations_ensure.v1"
        )

    def test_no_learning_persistence_in_application_service(self) -> None:
        imports = _imports(ENSURE)
        for name in imports:
            assert "domains.learning.infrastructure" not in name
            assert "domains.content.infrastructure" not in name
            assert "sqlalchemy" not in name
            assert "fastapi" not in name
        text = ENSURE.read_text(encoding="utf-8")
        assert "learner_membership" not in text
        assert "CurrentLearnerClassMembership" not in text
        assert "published_version_id" not in text
        assert "nats" not in text.lower()
        assert "temporal" not in text.lower()
        assert "outbox" not in text.lower()
        assert "CreateClassroomAssessment" not in text
        assert "RecordClassroomAssessment" not in text
        assert "CreateTeachingWork" not in text
        assert "roster" not in text.lower()

    def test_64_65_66_migration_identity_and_scope(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["a360s010004"]
        sql = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "a360s010004"' in sql
        assert 'down_revision: str | None = "a360s010003"' in sql
        assert "ALTER TABLE security.audit_records" in sql
        assert "CREATE TABLE" not in sql
        assert "ALTER TABLE assessment." not in sql
        assert "ALTER TABLE learning." not in sql
        assert "ALTER TABLE teaching." not in sql
        assert "ALTER TABLE content." not in sql
        assert "assessment.learner_evaluation.ensure" in sql

    def test_67_68_openapi_contains_b2_commands(self) -> None:
        digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        schema = OPENAPI.read_text(encoding="utf-8")
        assert "/api/v1/assessment/submissions/{submission_id}/actions/evaluate" in schema
        assert (
            "/api/v1/assessment/assignments/{assignment_id}/actions/ensure-evaluations"
            in schema
        )
        assert "assessment_learner_evaluation_ensure" in schema
        assert "assessment_assignment_evaluations_ensure" in schema

    def test_72_73_nats_temporal_present_and_unrelated(self) -> None:
        assert NATS_ROOT.is_dir()
        assert TEMPORAL_ROOT.is_dir()
        ensure = ENSURE.read_text(encoding="utf-8")
        assert "nats" not in ensure.lower()
        assert "temporalio" not in ensure.lower()
