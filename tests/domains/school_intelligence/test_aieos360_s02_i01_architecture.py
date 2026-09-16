"""AIEOS360-S02-I01 — architecture isolation and non-authorization guards."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import fields
from pathlib import Path

import pytest

from aieos.domains.school_intelligence.application.ports import (
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
    SCHOOL_INTELLIGENCE_READ,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.authorization.decisions import PrincipalKind
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s02_i01

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
SI_ROOT = SRC_ROOT / "domains" / "school_intelligence"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
PRODUCTION_RUNTIME_FILES = (
    RUNTIME_ROOT / "composition.py",
    RUNTIME_ROOT / "compose_api_dependencies.py",
    RUNTIME_ROOT / "entrypoints" / "api_main.py",
    SRC_ROOT / "platform" / "api" / "app.py",
)
FORBIDDEN_TEACHER_NAMES = (
    "SchoolContextClassReader",
    "SchoolContextClassAuthority",
    "list_assignable_classes",
    "AssignableClassRef",
)
FORBIDDEN_LEARNER_NAMES = (
    "SchoolContextLearnerMembershipReader",
    "SchoolContextLearnerMembershipAuthority",
    "list_current_memberships",
    "CurrentLearnerClassMembership",
    "learner_principal_id",
    "school_learner_ref",
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


class TestCapabilityOwnership:
    def test_capability_constant_exact(self) -> None:
        assert SCHOOL_INTELLIGENCE_READ == "school.intelligence.read"

    def test_catalog_is_exact_closed_set(self) -> None:
        assert AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES == frozenset(
            {SCHOOL_INTELLIGENCE_READ}
        )
        assert "school.*" not in AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES
        assert "school.intelligence.*" not in AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES
        assert "*.read" not in AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES

    def test_decisions_py_does_not_own_school_intelligence_capability(self) -> None:
        decisions = (
            SRC_ROOT / "platform" / "security" / "authorization" / "decisions.py"
        ).read_text(encoding="utf-8")
        assert "school.intelligence.read" not in decisions
        assert "SCHOOL_INTELLIGENCE_READ" not in decisions
        assert "AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES" not in decisions


class TestProductionIsolation:
    def test_production_runtime_does_not_import_development_adapter(self) -> None:
        for path in PRODUCTION_RUNTIME_FILES:
            text = path.read_text(encoding="utf-8")
            assert "aieos.development.principal_school_context" not in text
            assert "DevelopmentSchoolContextPrincipalScopeReader" not in text
            assert "principal_school_context" not in text
            assert "PRINCIPAL_OS_HUMAN_PRINCIPAL_ID" not in text

    def test_school_intelligence_does_not_import_development(self) -> None:
        for path in _py_files(SI_ROOT):
            for module in _import_modules(path):
                assert not module.startswith("aieos.development")

    def test_development_adapter_is_non_production(self) -> None:
        adapter = (
            SRC_ROOT / "development" / "principal_school_context.py"
        ).read_text(encoding="utf-8")
        assert "NON_PRODUCTION" in adapter
        assert "PRINCIPAL_OS_HUMAN_PRINCIPAL_ID" in adapter
        assert "PrincipalKind.PRINCIPAL" not in adapter
        assert "learner_principal_id" not in adapter
        assert "production credentials" not in adapter.lower()


class TestTeacherAndLearnerSchoolContextNotReused:
    def test_school_intelligence_does_not_import_teacher_or_learner_ports(self) -> None:
        offenders: list[str] = []
        for path in _py_files(SI_ROOT):
            text = path.read_text(encoding="utf-8")
            for name in FORBIDDEN_TEACHER_NAMES + FORBIDDEN_LEARNER_NAMES:
                if name in text:
                    offenders.append(f"{path.name}:{name}")
            for module in _import_modules(path):
                if module.startswith("aieos.domains.teaching") or module.startswith(
                    "aieos.domains.learning"
                ):
                    offenders.append(f"{path}:{module}")
        assert offenders == []


class TestNoPersistenceNoApi:
    def test_no_school_intelligence_table_or_migration(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        versions = sorted(
            path.name
            for path in MIGRATIONS.glob("*.py")
            if path.name != "__init__.py"
        )
        assert any(name.startswith("a360s010004_") for name in versions)
        assert not any("school_intelligence" in name.lower() for name in versions)
        assert not any("principal_school" in name.lower() for name in versions)
        assert not any("school_scope" in name.lower() for name in versions)
        for path in _py_files(SI_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "Table(" not in text
            for module in _import_modules(path):
                assert module != "sqlalchemy"
                assert not module.startswith("sqlalchemy.")
                assert module != "alembic"
                assert not module.startswith("alembic.")

    def test_openapi_unchanged_and_no_principal_http(self) -> None:
        schema = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        paths = schema.get("paths") or {}
        assert not any("principal" in path.lower() for path in paths)
        assert not any("school-intelligence" in path.lower() for path in paths)
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == (
            "7B51CE21725651B8D556B9DD6D264473DF0A2E7CAF30D722E1CC776C651FAFBB"
        )
        assert not (SI_ROOT / "api").exists()

    def test_no_school_intelligence_aggregation(self) -> None:
        for path in _py_files(SI_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "TeachingAssignment" not in text
            assert "LearnerAssessmentEvaluation" not in text
            assert "ClassroomAssessment" not in text
            assert "LearnerMastery" not in text
            assert "mastery_score" not in text
            assert "teacher_score" not in text
            assert "leaderboard" not in text


class TestValueContractAndIdentity:
    def test_authorized_class_ref_has_no_learner_or_ranking_fields(self) -> None:
        names = {field.name for field in fields(AuthorizedSchoolClassRef)}
        assert names == {"class_ref", "display_label"}
        assert "learner_principal_id" not in names
        assert "learner_name" not in names
        assert "teacher_score" not in names
        assert "mastery" not in names

    def test_principal_kind_remains_human_workload_only(self) -> None:
        assert set(PrincipalKind) == {PrincipalKind.HUMAN, PrincipalKind.WORKLOAD}
        assert "PRINCIPAL" not in PrincipalKind.__members__
        assert "SCHOOL_ADMIN" not in PrincipalKind.__members__
        assert "TEACHER" not in PrincipalKind.__members__
        assert "STUDENT" not in PrincipalKind.__members__


class TestProductionCatalogComposition:
    def test_compose_api_dependencies_unions_school_intelligence_catalog(self) -> None:
        from aieos.platform.runtime import compose_api_dependencies as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES" in src
        compact_src = " ".join(src.split())
        assert (
            "known_capabilities=( AIEOS_CONTENT_CAPABILITIES"
            " | AIEOS_ASSESSMENT_CAPABILITIES"
            " | AIEOS_TEACHING_WORK_CAPABILITIES"
            " | AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES )" in compact_src
        )
        assert "KernelSchoolIntelligenceAuthorization" not in src
        assert "principal_school_context" not in src
