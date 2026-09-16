"""AIEOS360-S02-I02 — architecture, privacy, and OpenAPI guards."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

from aieos.domains.school_intelligence.api.v1.models import (
    PrincipalSchoolIntelligenceClassCardResponse,
    PrincipalSchoolIntelligenceResponse,
    PrincipalSchoolIntelligenceSummaryResponse,
)
from aieos.domains.school_intelligence.application.models import (
    MAX_AUTHORIZED_CLASS_COUNT,
    PrincipalSchoolIntelligenceClassCard,
    PrincipalSchoolIntelligenceReadModel,
    PrincipalSchoolIntelligenceSummary,
)
from aieos.platform.runtime.activation import READ_ONLY_OPERATION_IDS
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.authorization.decisions import PrincipalKind
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s02_i02

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
SI_ROOT = SRC_ROOT / "domains" / "school_intelligence"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
READER = SI_ROOT / "infrastructure" / "read_projection.py"

FORBIDDEN_RESPONSE_FIELDS = (
    "learner_principal_id",
    "learner_name",
    "learners",
    "response_snapshot",
    "choice_value",
    "text_value",
    "boolean_value",
    "question_id",
    "objective_id",
    "class_result_level",
    "class_result_note",
    "PRIVATE_EXECUTION_NOTE",
    "teacher_principal_id",
    "teacher_score",
    "teacher_rank",
    "leaderboard",
    "mastery",
    "competency_attained",
    "predicted_exam_outcome",
    "school_performance_score",
)

FORBIDDEN_SOURCE_TABLES = (
    "attempt_response_items",
    "learner_assessment_evaluation_items",
    "learner_assessment_objective_evidence",
    "execution_observations",
)

TEACHER_INTELLIGENCE_TYPES = (
    "TeacherAssessmentIntelligenceResponse",
    "TeacherAssessmentIntelligenceLearnerResponse",
)


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
        assert not any("school_intelligence" in name.lower() for name in versions)
        assert not any("principal_intelligence" in name.lower() for name in versions)

    def test_application_has_no_sqlalchemy(self) -> None:
        application = SI_ROOT / "application"
        for path in _py_files(application):
            for module in _import_modules(path):
                assert module != "sqlalchemy"
                assert not module.startswith("sqlalchemy.")

    def test_no_school_intelligence_table_definition(self) -> None:
        for path in _py_files(SI_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "Table(" not in text


class TestPrivacyStaticGuards:
    def test_read_models_have_no_forbidden_fields(self) -> None:
        for model in (
            PrincipalSchoolIntelligenceReadModel,
            PrincipalSchoolIntelligenceSummary,
            PrincipalSchoolIntelligenceClassCard,
            PrincipalSchoolIntelligenceResponse,
            PrincipalSchoolIntelligenceSummaryResponse,
            PrincipalSchoolIntelligenceClassCardResponse,
        ):
            if is_dataclass(model):
                names = {field.name for field in fields(model)}
            else:
                names = set(model.model_fields)
            for forbidden in FORBIDDEN_RESPONSE_FIELDS:
                assert forbidden not in names

    def test_package_source_does_not_declare_forbidden_identity_fields(self) -> None:
        for path in _py_files(SI_ROOT):
            text = path.read_text(encoding="utf-8")
            for forbidden in (
                "learner_principal_id",
                "learner_name",
                "teacher_principal_id",
                "teacher_score",
                "teacher_rank",
                "leaderboard",
                "mastery_score",
                "class_result_level",
                "class_result_note",
                "response_snapshot",
                "PRIVATE_EXECUTION_NOTE",
            ):
                assert forbidden not in text

    def test_facts_reader_does_not_query_sensitive_tables(self) -> None:
        sql = READER.read_text(encoding="utf-8")
        for table in FORBIDDEN_SOURCE_TABLES:
            assert table not in sql

    def test_no_teacher_assessment_intelligence_reuse(self) -> None:
        for path in _py_files(SI_ROOT):
            text = path.read_text(encoding="utf-8")
            for name in TEACHER_INTELLIGENCE_TYPES:
                assert name not in text
            for module in _import_modules(path):
                assert "assessment.application.intelligence" not in module
                assert "assessment.api.v1.models" not in module
                assert "assessment.application.evaluation_ensure" not in module

    def test_principal_kind_unchanged(self) -> None:
        assert set(PrincipalKind) == {PrincipalKind.HUMAN, PrincipalKind.WORKLOAD}
        assert "PRINCIPAL" not in PrincipalKind.__members__


class TestFilterBeforeAggregation:
    def test_authorized_class_refs_constrain_every_source_query(self) -> None:
        sql = READER.read_text(encoding="utf-8")
        assert "AND class_ref IN :class_refs" in sql
        assert "AND source_class_ref IN :class_refs" in sql
        assert sql.count("GROUP BY") >= 6
        assignment_block = sql.split("_ASSIGNMENT_SQL")[1].split("_SUBMISSION_SQL")[0]
        assert "class_ref IN :class_refs" in assignment_block
        assert assignment_block.find("class_ref IN :class_refs") < assignment_block.find(
            "GROUP BY"
        )

    def test_bounded_showcase_capacity_is_explicit(self) -> None:
        assert MAX_AUTHORIZED_CLASS_COUNT == 100

    def test_classroom_assignment_count_joins_teaching_lineage(self) -> None:
        sql = READER.read_text(encoding="utf-8")
        classroom_block = sql.split("_CLASSROOM_SQL")[1].split("_EXECUTION_SQL")[0]
        assert "LEFT JOIN teaching.assignments" in classroom_block
        assert classroom_block.find("assessments.class_ref IN :class_refs") < (
            classroom_block.find("GROUP BY")
        )
        assert "class_result_level" not in classroom_block
        assert "class_result_note" not in classroom_block
        assert "teacher_principal_id" not in classroom_block


class TestOpenApiContract:
    def test_exact_get_path_and_no_mutation(self) -> None:
        schema = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        paths = schema.get("paths") or {}
        path = "/api/v1/principal-os/school-intelligence"
        assert path in paths
        methods = {method.lower() for method in paths[path] if not method.startswith("x-")}
        assert methods == {"get"}
        operation = paths[path]["get"]
        assert operation["operationId"] == "principal_os_school_intelligence_get"
        assert "principal_os_school_intelligence_get" in READ_ONLY_OPERATION_IDS
        params = operation.get("parameters") or []
        names = {
            item.get("name")
            for item in params
            if isinstance(item, dict) and item.get("in") == "header"
        }
        assert "Idempotency-Key" not in names
        assert "If-Match" not in names
        responses = operation.get("responses") or {}
        assert "401" in responses
        assert "403" in responses
        assert "503" in responses
        other_principal = [
            item for item in paths if "principal-os" in item and item != path
        ]
        assert other_principal == []

    def test_response_schema_has_no_forbidden_fields(self) -> None:
        schema = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        schemas = (schema.get("components") or {}).get("schemas") or {}
        assert "TeacherAssessmentIntelligenceResponse" in schemas
        assert "PrincipalSchoolIntelligenceResponse" in schemas
        dumped = json.dumps(schemas["PrincipalSchoolIntelligenceResponse"])
        for forbidden in FORBIDDEN_RESPONSE_FIELDS:
            assert forbidden not in dumped
        summary = json.dumps(schemas.get("PrincipalSchoolIntelligenceSummaryResponse", {}))
        card = json.dumps(
            schemas.get("PrincipalSchoolIntelligenceClassCardResponse", {})
        )
        for forbidden in FORBIDDEN_RESPONSE_FIELDS:
            assert forbidden not in summary
            assert forbidden not in card

    def test_openapi_digest_matches_pin(self) -> None:
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest != (
            "7B51CE21725651B8D556B9DD6D264473DF0A2E7CAF30D722E1CC776C651FAFBB"
        )

    def test_production_does_not_import_development_adapter(self) -> None:
        production = (
            SRC_ROOT / "platform" / "runtime" / "compose_api_dependencies.py"
        ).read_text(encoding="utf-8")
        assert "principal_school_context" not in production
        assert "DevelopmentSchoolIntelligencePermit" not in production
        assert "UnconfiguredSchoolContextPrincipalScopeReader" in production
