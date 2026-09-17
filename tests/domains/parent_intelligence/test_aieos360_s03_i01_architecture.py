"""AIEOS360-S03-I01 — architecture isolation and non-authorization guards."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import fields
from pathlib import Path

import pytest

from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
)
from aieos.domains.parent_intelligence.application.ports import (
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
    PARENT_INTELLIGENCE_READ,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.authorization import (
    AIEOS_ASSESSMENT_CAPABILITIES,
    AIEOS_CONTENT_CAPABILITIES,
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES as ADAPTER_CAPABILITIES,
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
    AIEOS_TEACHING_WORK_CAPABILITIES,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s03_i01

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
PI_ROOT = SRC_ROOT / "domains" / "parent_intelligence"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
PRODUCTION_RUNTIME_FILES = (
    RUNTIME_ROOT / "composition.py",
    RUNTIME_ROOT / "compose_api_dependencies.py",
    RUNTIME_ROOT / "entrypoints" / "api_main.py",
    SRC_ROOT / "platform" / "api" / "app.py",
)
FORBIDDEN_NEIGHBOR_NAMES = (
    "SchoolContextClassReader",
    "SchoolContextClassAuthority",
    "list_assignable_classes",
    "AssignableClassRef",
    "SchoolContextLearnerMembershipReader",
    "SchoolContextLearnerMembershipAuthority",
    "list_current_memberships",
    "CurrentLearnerClassMembership",
    "SchoolContextPrincipalScopeReader",
    "list_current_authorized_classes",
    "AuthorizedSchoolClassRef",
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
        assert PARENT_INTELLIGENCE_READ == "parent.intelligence.read"

    def test_catalog_is_exact_closed_set(self) -> None:
        assert AIEOS_PARENT_INTELLIGENCE_CAPABILITIES == frozenset(
            {PARENT_INTELLIGENCE_READ}
        )
        assert "parent.*" not in AIEOS_PARENT_INTELLIGENCE_CAPABILITIES
        assert "parent.intelligence.*" not in AIEOS_PARENT_INTELLIGENCE_CAPABILITIES
        assert "*.read" not in AIEOS_PARENT_INTELLIGENCE_CAPABILITIES
        assert not any(
            "*" in capability
            for capability in AIEOS_PARENT_INTELLIGENCE_CAPABILITIES
        )

    def test_adapter_catalog_matches_application_catalog(self) -> None:
        assert AIEOS_PARENT_INTELLIGENCE_CAPABILITIES == ADAPTER_CAPABILITIES

    def test_decisions_py_does_not_own_parent_intelligence_capability(self) -> None:
        decisions = (
            SRC_ROOT / "platform" / "security" / "authorization" / "decisions.py"
        ).read_text(encoding="utf-8")
        assert "parent.intelligence.read" not in decisions
        assert "PARENT_INTELLIGENCE_READ" not in decisions
        assert "AIEOS_PARENT_INTELLIGENCE_CAPABILITIES" not in decisions

    def test_exact_parent_capability_appears_in_known_kernel_catalog(self) -> None:
        union = (
            AIEOS_CONTENT_CAPABILITIES
            | AIEOS_ASSESSMENT_CAPABILITIES
            | AIEOS_TEACHING_WORK_CAPABILITIES
            | AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES
            | AIEOS_PARENT_INTELLIGENCE_CAPABILITIES
        )
        assert PARENT_INTELLIGENCE_READ in union
        assert not any("*" in capability for capability in union)
        assert "parent.*" not in union
        assert "parent.intelligence.*" not in union


class TestProductionIsolation:
    def test_production_runtime_does_not_import_development_adapter(self) -> None:
        for path in PRODUCTION_RUNTIME_FILES:
            text = path.read_text(encoding="utf-8")
            assert "aieos.development.parent_learner_access" not in text
            assert "DevelopmentSchoolContextParentLearnerAccessReader" not in text
            assert "DevelopmentParentIntelligencePermit" not in text
            assert "PARENT_OS_HUMAN_ADULT_A_ID" not in text

    def test_parent_intelligence_does_not_import_development(self) -> None:
        for path in _py_files(PI_ROOT):
            for module in _import_modules(path):
                assert not module.startswith("aieos.development")

    def test_development_adapter_is_non_production(self) -> None:
        adapter = (SRC_ROOT / "development" / "parent_learner_access.py").read_text(
            encoding="utf-8"
        )
        assert "NON_PRODUCTION" in adapter
        assert "PARENT_OS_HUMAN_ADULT_A_ID" in adapter
        assert "PrincipalKind.PARENT" not in adapter
        assert "presentation_label" not in adapter
        assert "display_name" not in adapter
        assert "production credentials" not in adapter.lower()
        assert "Not ERP/SIS integration" in adapter

    def test_compose_api_application_does_not_register_parent_http(self) -> None:
        composition = (RUNTIME_ROOT / "composition.py").read_text(encoding="utf-8")
        assert "parent_learner_access_service" in composition
        start = composition.index("app = create_app(")
        end = composition.index("app.state.release_identity")
        create_app_block = composition[start:end]
        assert "parent_intelligence" not in create_app_block
        assert "parent_learner" not in create_app_block
        app_src = (SRC_ROOT / "platform" / "api" / "app.py").read_text(encoding="utf-8")
        assert "parent-os" not in app_src
        assert "/api/v1/parent-os" not in app_src


class TestNeighborPortsNotReused:
    def test_parent_intelligence_does_not_import_neighbor_authority_ports(self) -> None:
        offenders: list[str] = []
        for path in _py_files(PI_ROOT):
            text = path.read_text(encoding="utf-8")
            for name in FORBIDDEN_NEIGHBOR_NAMES:
                if name in text:
                    offenders.append(f"{path.name}:{name}")
            for module in _import_modules(path):
                if module.startswith(
                    (
                        "aieos.domains.teaching",
                        "aieos.domains.learning",
                        "aieos.domains.school_intelligence",
                        "aieos.domains.assessment",
                    )
                ):
                    offenders.append(f"{path}:{module}")
        assert offenders == []


class TestNoPersistenceNoApi:
    def test_no_parent_intelligence_table_or_migration(self) -> None:
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
        assert not any("guardian" in name.lower() for name in versions)
        assert not any("custody" in name.lower() for name in versions)
        for path in _py_files(PI_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "Table(" not in text
            for module in _import_modules(path):
                assert module != "sqlalchemy"
                assert not module.startswith("sqlalchemy.")
                assert module != "alembic"
                assert not module.startswith("alembic.")

    def test_openapi_snapshot_unchanged_and_has_no_parent_route(self) -> None:
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == (
            "BE60CC2A4612F77AB333088D264B9501B9AB842995AEC1539DA89EA0E8462B47"
        )
        snapshot = OPENAPI_SNAPSHOT.read_text(encoding="utf-8")
        assert "/api/v1/parent-os" not in snapshot
        assert "parent.intelligence.read" not in snapshot

    def test_no_parent_intelligence_aggregation(self) -> None:
        for path in _py_files(PI_ROOT):
            text = path.read_text(encoding="utf-8")
            assert "TeachingAssignment" not in text
            assert "LearnerAttempt" not in text
            assert "LearnerSubmission" not in text
            assert "LearnerAssessmentEvaluation" not in text
            assert "presentation_label" not in text
            assert "display_name" not in text


class TestValueContractAndIdentity:
    def test_authorized_learner_access_has_only_principal_id(self) -> None:
        names = {field.name for field in fields(AuthorizedLearnerAccess)}
        assert names == {"learner_principal_id"}
        assert "presentation_label" not in names
        assert "display_name" not in names
        assert "legal_name" not in names
        assert "email" not in names
        assert "username" not in names
        assert "relationship_type" not in names
        assert "guardian_type" not in names
        assert "custody_type" not in names
        assert "school_learner_ref" not in names
        assert "class_ref" not in names

    def test_principal_kind_remains_human_workload_only(self) -> None:
        assert set(PrincipalKind) == {PrincipalKind.HUMAN, PrincipalKind.WORKLOAD}
        assert "PARENT" not in PrincipalKind.__members__
        assert "GUARDIAN" not in PrincipalKind.__members__
        assert "STUDENT" not in PrincipalKind.__members__
        assert "LEARNER" not in PrincipalKind.__members__

    def test_learner_integrity_does_not_reuse_actor_active_methods(self) -> None:
        integrity = (
            SRC_ROOT
            / "platform"
            / "security"
            / "authorization"
            / "learner_principal_integrity.py"
        ).read_text(encoding="utf-8")
        assert "require_current_human_principal" not in integrity
        assert "resolve_current_principal_kind" not in integrity
        access = (PI_ROOT / "application" / "learner_access.py").read_text(
            encoding="utf-8"
        )
        assert "resolve_current_principal_kind" not in access
        assert "validate_learner_subject" in access


class TestProductionCatalogComposition:
    def test_compose_api_dependencies_unions_parent_intelligence_catalog(self) -> None:
        from aieos.platform.runtime import compose_api_dependencies as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "AIEOS_PARENT_INTELLIGENCE_CAPABILITIES" in src
        compact_src = " ".join(src.split())
        assert (
            "known_capabilities=( AIEOS_CONTENT_CAPABILITIES"
            " | AIEOS_ASSESSMENT_CAPABILITIES"
            " | AIEOS_TEACHING_WORK_CAPABILITIES"
            " | AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES"
            " | AIEOS_PARENT_INTELLIGENCE_CAPABILITIES )" in compact_src
        )
        assert "KernelParentIntelligenceAuthorization" in src
        assert "UnconfiguredSchoolContextParentLearnerAccessReader" in src
        assert "CurrentParentLearnerAccessService" in src
        assert "SecurityAuthorityLearnerPrincipalIntegrity" in src
        assert "aieos.development.parent_learner_access" not in src
        assert "DevelopmentSchoolContextParentLearnerAccessReader" not in src
