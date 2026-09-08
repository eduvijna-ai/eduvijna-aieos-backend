"""AIEOS360-S01-I01 — architecture isolation guards."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.authorization.decisions import PrincipalKind
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s01_i01

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
LEARNING_ROOT = SRC_ROOT / "domains" / "learning"
TEACHING_ROOT = SRC_ROOT / "domains" / "teaching"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
PRODUCTION_RUNTIME_FILES = (
    RUNTIME_ROOT / "composition.py",
    RUNTIME_ROOT / "compose_api_dependencies.py",
    RUNTIME_ROOT / "entrypoints" / "api_main.py",
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


class TestLearningDoesNotImportTeaching:
    def test_i01_18_learning_does_not_import_teaching_application(self) -> None:
        offenders: list[str] = []
        for path in _py_files(LEARNING_ROOT):
            for module in _import_modules(path):
                if module == "aieos.domains.teaching" or module.startswith(
                    "aieos.domains.teaching."
                ):
                    offenders.append(f"{path}:{module}")
        assert offenders == []


class TestProductionIsolation:
    def test_i01_19_production_runtime_does_not_import_development_learner_adapter(
        self,
    ) -> None:
        for path in PRODUCTION_RUNTIME_FILES:
            text = path.read_text(encoding="utf-8")
            assert "aieos.development" not in text
            assert "DevelopmentSchoolContextLearnerMembershipReader" not in text
            assert "learner_school_context" not in text
            assert "learner_principals" not in text

    def test_i01_20_no_production_dependency_on_synthetic_principal_constants(
        self,
    ) -> None:
        for path in PRODUCTION_RUNTIME_FILES:
            text = path.read_text(encoding="utf-8")
            assert "STUDENT_A_PRINCIPAL_ID" not in text
            assert "STUDENT_B_PRINCIPAL_ID" not in text
            assert "dev-student-a" not in text


class TestNoPersistenceOrStudentApi:
    def test_i01_21_no_learner_roster_persistence(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "tosd100001"
        assert EXPECTED_MIGRATION_HEAD == "tosd100001"
        versions = sorted(
            path.name
            for path in MIGRATIONS.glob("*.py")
            if path.name != "__init__.py"
        )
        assert versions[-1].startswith("tosd100001_")
        assert not any("learner" in name.lower() for name in versions)
        assert not any("roster" in name.lower() for name in versions)
        assert not any("student" in name.lower() for name in versions)
        assert not (LEARNING_ROOT / "infrastructure").exists()
        for path in _py_files(LEARNING_ROOT):
            modules = _import_modules(path)
            assert not any(
                module == "sqlalchemy" or module.startswith("sqlalchemy.")
                for module in modules
            )
            text = path.read_text(encoding="utf-8")
            assert "Table(" not in text

    def test_i01_22_no_student_http_route_added(self) -> None:
        schema = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))
        paths = schema.get("paths") or {}
        assert not any("student-os" in path for path in paths)
        assert not any("/learning/" in path for path in paths)
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert not (LEARNING_ROOT / "api").exists()

    def test_i01_23_no_learner_attempt_or_submission_implementation(self) -> None:
        for path in _py_files(LEARNING_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "class LearnerAttempt" not in text
            assert "class LearnerSubmission" not in text
            assert "attempt_response" not in text.lower()

    def test_i01_24_teaching_assignment_sources_unmodified_by_learning_imports(
        self,
    ) -> None:
        assignment = TEACHING_ROOT / "application" / "assignment_create.py"
        text = assignment.read_text(encoding="utf-8")
        assert "aieos.domains.learning" not in text
        assert "LearnerClassMembership" not in text
        school = TEACHING_ROOT / "application" / "school_context.py"
        school_text = school.read_text(encoding="utf-8")
        assert "list_current_memberships" not in school_text
        assert "learner_principal_id" not in school_text

    def test_principal_kind_student_not_created(self) -> None:
        assert "STUDENT" not in PrincipalKind.__members__

    def test_development_adapter_is_non_production(self) -> None:
        adapter = SRC_ROOT / "development" / "learner_school_context.py"
        constants = SRC_ROOT / "development" / "learner_principals.py"
        adapter_text = adapter.read_text(encoding="utf-8")
        constants_text = constants.read_text(encoding="utf-8")
        assert "NON_PRODUCTION" in adapter_text
        assert 'CLASS_REF_5A = "class-5a"' in constants_text
        assert 'CLASS_REF_5B = "class-5b"' in constants_text
        assert "teacher_principal_id" not in adapter_text
        assert "list_assignable_classes" not in adapter_text
