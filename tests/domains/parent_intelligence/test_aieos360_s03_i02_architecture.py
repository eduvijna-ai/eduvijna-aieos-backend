"""AIEOS360-S03-I02 — architecture, privacy, and OpenAPI guards."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from aieos.domains.parent_intelligence.api.v1.models import (
    ParentAssignmentStatusResponse,
    ParentChildCardResponse,
    ParentIntelligenceResponse,
)
from aieos.domains.parent_intelligence.application.models import (
    MAX_ASSIGNMENTS_PER_LEARNER,
    MAX_AUTHORIZED_LEARNER_COUNT,
    ParentAssignmentStatus,
    ParentChildCard,
    ParentIntelligenceReadModel,
)
from aieos.platform.runtime.activation import READ_ONLY_OPERATION_IDS
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s03_i02

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
PI_ROOT = SRC_ROOT / "domains" / "parent_intelligence"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
READER = PI_ROOT / "infrastructure" / "read_projection.py"
PRE_I02_OPENAPI_SHA256 = (
    "BE60CC2A4612F77AB333088D264B9501B9AB842995AEC1539DA89EA0E8462B47"
)

FORBIDDEN_RESPONSE_FIELDS = (
    "sources",
    "class_ref",
    "school_learner_ref",
    "content_id",
    "content_version_id",
    "assignment_lifecycle",
    "attempt_id",
    "submission_id",
    "evaluation_id",
    "teacher_principal_id",
    "presentation_label",
    "display_name",
    "legal_name",
    "email",
    "username",
    "relationship_type",
    "guardian_type",
    "custody_type",
    "mastery",
    "competency",
    "diagnosis",
    "response_snapshot",
    "learners",
    "teacher_notes",
    "PRIVATE_EXECUTION_NOTE",
    "class_result_level",
    "class_result_note",
)

FORBIDDEN_SOURCE_TABLES = (
    "learner_assessment_evaluations",
    "learner_assessment_evaluation_items",
    "learner_assessment_objective_evidence",
    "classroom_assessments",
    "execution_observations",
    "work_remediation_origins",
)

FORBIDDEN_STUDENT_SERVICES = (
    "ListCurrentAssignmentsService",
    "GetCurrentAssignmentService",
    "GetStudentHomeService",
)

HOME_PATH = "/api/v1/parent-os/home"
CHILD_PATH = "/api/v1/parent-os/children/{learner_principal_id}"


def _py_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.py") if p.is_file()]


def _import_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class TestNoPersistence:
    def test_alembic_head_unchanged(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        versions = sorted(
            path.name
            for path in MIGRATIONS.glob("*.py")
            if path.name != "__init__.py"
        )
        assert any(name.startswith("a360s010004_") for name in versions)
        assert not any("parent_intelligence" in name.lower() for name in versions)
        assert not any("parent_learner" in name.lower() for name in versions)

    def test_application_has_no_sqlalchemy(self) -> None:
        application = PI_ROOT / "application"
        for path in _py_files(application):
            for module in _import_modules(path):
                assert module != "sqlalchemy"
                assert not module.startswith("sqlalchemy.")

    def test_no_parent_intelligence_table_definition(self) -> None:
        for path in _py_files(PI_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "Table(" not in text


class TestPrivacyStaticGuards:
    def test_read_models_have_no_forbidden_fields(self) -> None:
        for model in (
            ParentIntelligenceReadModel,
            ParentChildCard,
            ParentAssignmentStatus,
            ParentIntelligenceResponse,
            ParentChildCardResponse,
            ParentAssignmentStatusResponse,
        ):
            if is_dataclass(model):
                names = {field.name for field in fields(model)}
            else:
                names = set(model.model_fields)
            for forbidden in FORBIDDEN_RESPONSE_FIELDS:
                assert forbidden not in names

    def test_facts_reader_does_not_query_evaluation_or_teacher_tables(self) -> None:
        sql = READER.read_text(encoding="utf-8")
        for table in FORBIDDEN_SOURCE_TABLES:
            assert table not in sql
        assert "payload" not in sql
        assert "response_snapshot" not in sql
        assert "teacher_principal_id" not in sql

    def test_does_not_reuse_student_actor_services(self) -> None:
        for path in _py_files(PI_ROOT):
            text = path.read_text(encoding="utf-8")
            for name in FORBIDDEN_STUDENT_SERVICES:
                assert name not in text
            for module in _import_modules(path):
                assert "learning.application.current_assignments" not in module
                assert "assessment.application.intelligence" not in module
                assert "school_intelligence.application.intelligence" not in module

    def test_application_does_not_treat_membership_as_access_authority(self) -> None:
        access = (PI_ROOT / "application" / "learner_access.py").read_text(
            encoding="utf-8"
        )
        intelligence = (PI_ROOT / "application" / "intelligence.py").read_text(
            encoding="utf-8"
        )
        assert "list_current_memberships" not in access
        assert "list_current_memberships" not in intelligence
        assert "SchoolContextLearnerMembershipReader" not in access
        assert "SchoolContextLearnerMembershipReader" not in intelligence


class TestOpenApiContract:
    def test_exact_get_paths_and_no_mutation(self) -> None:
        schema = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        paths = schema.get("paths") or {}
        assert HOME_PATH in paths
        assert CHILD_PATH in paths
        for path in (HOME_PATH, CHILD_PATH):
            methods = {
                method.lower() for method in paths[path] if not method.startswith("x-")
            }
            assert methods == {"get"}
            responses = paths[path]["get"].get("responses") or {}
            for status in ("401", "403", "404", "422", "500", "503"):
                assert status in responses
            params = paths[path]["get"].get("parameters") or []
            names = {
                item.get("name")
                for item in params
                if isinstance(item, dict) and item.get("in") == "header"
            }
            assert "Idempotency-Key" not in names
            assert "If-Match" not in names
        assert paths[HOME_PATH]["get"]["operationId"] == "parent_os_home_get"
        assert paths[CHILD_PATH]["get"]["operationId"] == "parent_os_child_get"
        assert "parent_os_home_get" in READ_ONLY_OPERATION_IDS
        assert "parent_os_child_get" in READ_ONLY_OPERATION_IDS
        other_parent = [
            item
            for item in paths
            if "parent-os" in item and item not in {HOME_PATH, CHILD_PATH}
        ]
        assert other_parent == []

    def test_response_schema_has_no_forbidden_fields(self) -> None:
        schema = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        schemas = (schema.get("components") or {}).get("schemas") or {}
        dumped = json.dumps(schemas.get("ParentIntelligenceResponse", {}))
        for forbidden in FORBIDDEN_RESPONSE_FIELDS:
            assert forbidden not in dumped

    def test_openapi_digest_matches_pin_and_changed_from_pre_i02(self) -> None:
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest != PRE_I02_OPENAPI_SHA256

    def test_production_does_not_import_development_adapter(self) -> None:
        production = (
            SRC_ROOT / "platform" / "runtime" / "compose_api_dependencies.py"
        ).read_text(encoding="utf-8")
        assert "aieos.development.parent_learner_access" not in production
        assert "DevelopmentSchoolContextParentLearnerAccessReader" not in production
        assert "DevelopmentSchoolContextLearnerMembershipReader" not in production
        assert "UnconfiguredSchoolContextParentLearnerAccessReader" in production
        assert "UnconfiguredSchoolContextLearnerMembershipReader" in production
        assert "SqlAlchemyParentIntelligenceFactsReader" in production


class TestProtectionLimits:
    def test_operational_limits_are_explicit(self) -> None:
        assert MAX_AUTHORIZED_LEARNER_COUNT == 100
        assert MAX_ASSIGNMENTS_PER_LEARNER == 100
