"""AIEOS360-S04-I02 — architecture isolation and composition guards."""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthority,
    SchoolContextLearnerMembershipReader,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    CurrentParentLearnerAccessService,
    SchoolContextParentLearnerAccessReader,
)
from aieos.domains.school_intelligence.application.school_scope import (
    CurrentPrincipalSchoolScopeService,
    SchoolContextPrincipalScopeReader,
)
from aieos.domains.teaching.application.school_context import (
    SchoolContextClassAuthority,
    SchoolContextClassReader,
)
from aieos.platform.runtime.composition import (
    ApiRuntimeDependencies,
    compose_api_application,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.authorization import (
    AIEOS_ASSESSMENT_CAPABILITIES,
    AIEOS_ASSET_CAPABILITIES,
    AIEOS_CONTENT_CAPABILITIES,
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
    AIEOS_TEACHING_WORK_CAPABILITIES,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s04_i02

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
PROVIDER = SRC_ROOT / "development" / "coherent_school_context.py"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
COMPOSITION = RUNTIME_ROOT / "composition.py"
PRODUCTION_COMPOSE = RUNTIME_ROOT / "compose_api_dependencies.py"
LOCAL_COMPOSE = REPO_ROOT / "tools" / "dev" / "compose_local_api.py"
LOCAL_AUTH = REPO_ROOT / "tools" / "dev" / "local_auth.py"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
HISTORICAL_ADAPTERS = (
    SRC_ROOT / "development" / "school_context.py",
    SRC_ROOT / "development" / "learner_school_context.py",
    SRC_ROOT / "development" / "principal_school_context.py",
    SRC_ROOT / "development" / "parent_learner_access.py",
)
PRODUCTION_RUNTIME_FILES = (
    COMPOSITION,
    PRODUCTION_COMPOSE,
    RUNTIME_ROOT / "entrypoints" / "api_main.py",
    SRC_ROOT / "platform" / "api" / "app.py",
)
FORBIDDEN_PROVIDER_MARKERS = (
    "aieos.development.coherent_school_context",
    "DevelopmentCoherentSchoolContextProvider",
    "development_coherent_school_context_provider",
)
FORBIDDEN_GIANT_CONTRACTS = (
    "class SchoolContextService",
    "class UniversalSchoolContextPort",
    "class AdminSchoolService",
)


def _import_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class TestProductionIsolation:
    def test_production_runtime_does_not_import_coherent_provider(self) -> None:
        for path in PRODUCTION_RUNTIME_FILES:
            text = path.read_text(encoding="utf-8")
            for marker in FORBIDDEN_PROVIDER_MARKERS:
                assert marker not in text

    def test_production_compose_does_not_reference_coherent_provider(self) -> None:
        text = PRODUCTION_COMPOSE.read_text(encoding="utf-8")
        for marker in FORBIDDEN_PROVIDER_MARKERS:
            assert marker not in text

    def test_neutral_composition_carrier_does_not_import_development_provider(
        self,
    ) -> None:
        modules = _import_modules(COMPOSITION)
        assert "aieos.development.coherent_school_context" not in modules
        text = COMPOSITION.read_text(encoding="utf-8")
        assert "DevelopmentCoherentSchoolContextProvider" not in text


class TestLocalCompositionMayImportProvider:
    def test_local_dev_composition_imports_coherent_provider(self) -> None:
        modules = _import_modules(LOCAL_COMPOSE)
        assert "aieos.development.coherent_school_context" in modules
        text = LOCAL_COMPOSE.read_text(encoding="utf-8")
        assert "DevelopmentCoherentSchoolContextProvider" in text
        assert "DevelopmentSchoolContextPrincipalScopeReader" not in text
        assert "DevelopmentSchoolContextParentLearnerAccessReader" not in text
        assert "DevelopmentSchoolContextLearnerMembershipReader" not in text

    def test_local_auth_was_not_extended_for_multi_role_tokens(self) -> None:
        text = LOCAL_AUTH.read_text(encoding="utf-8")
        assert "teacher token" not in text.lower()
        assert "student token" not in text.lower()
        assert "principal token" not in text.lower()
        assert "parent token" not in text.lower()
        assert "multi-role" not in text.lower()


class TestProviderClassification:
    def test_provider_remains_explicitly_non_production(self) -> None:
        text = PROVIDER.read_text(encoding="utf-8")
        assert "NON_PRODUCTION" in text
        assert "ADR-AIEOS-062" in text
        assert "Must never be imported by production runtime composition" in text


class TestContractsRemainDistinct:
    def test_four_existing_application_contracts_remain_distinct(self) -> None:
        ports = (
            SchoolContextClassReader,
            SchoolContextLearnerMembershipReader,
            SchoolContextPrincipalScopeReader,
            SchoolContextParentLearnerAccessReader,
        )
        assert len(set(ports)) == 4
        assert SchoolContextClassAuthority is not SchoolContextLearnerMembershipAuthority
        assert (
            CurrentPrincipalSchoolScopeService is not CurrentParentLearnerAccessService
        )

    def test_no_giant_consolidated_school_context_contract(self) -> None:
        for path in (
            COMPOSITION,
            LOCAL_COMPOSE,
            PROVIDER,
            SRC_ROOT / "domains" / "teaching" / "application" / "school_context.py",
            SRC_ROOT / "domains" / "learning" / "application" / "learner_membership.py",
            SRC_ROOT
            / "domains"
            / "school_intelligence"
            / "application"
            / "school_scope.py",
            SRC_ROOT
            / "domains"
            / "parent_intelligence"
            / "application"
            / "learner_access.py",
        ):
            text = path.read_text(encoding="utf-8")
            for name in FORBIDDEN_GIANT_CONTRACTS:
                assert name not in text


class TestNeutralCarrierExtension:
    def test_api_runtime_dependencies_carries_all_four_reader_fields(self) -> None:
        sig = inspect.signature(ApiRuntimeDependencies)
        assert "school_context_class_reader" in sig.parameters
        assert "learner_membership_reader" in sig.parameters
        assert "school_context_principal_scope_reader" in sig.parameters
        assert "school_context_parent_learner_access_reader" in sig.parameters
        assert sig.parameters["school_context_class_reader"].default is None
        assert sig.parameters["learner_membership_reader"].default is None

    def test_compose_api_application_forwards_teacher_and_learner_readers(self) -> None:
        source = inspect.getsource(compose_api_application)
        assert (
            "school_context_class_reader=dependencies.school_context_class_reader"
            in source
        )
        assert (
            "learner_membership_reader=dependencies.learner_membership_reader" in source
        )


class TestIdentityAndCapabilityBoundary:
    def test_principal_kind_remains_human_and_workload_only(self) -> None:
        assert set(PrincipalKind) == {PrincipalKind.HUMAN, PrincipalKind.WORKLOAD}
        assert "ADMIN" not in PrincipalKind.__members__

    def test_no_admin_capability_created(self) -> None:
        union = (
            AIEOS_CONTENT_CAPABILITIES
            | AIEOS_ASSESSMENT_CAPABILITIES
            | AIEOS_TEACHING_WORK_CAPABILITIES
            | AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES
            | AIEOS_PARENT_INTELLIGENCE_CAPABILITIES
            | AIEOS_ASSET_CAPABILITIES
        )
        assert not any(capability.startswith("admin.") for capability in union)
        assert "admin.*" not in union


class TestHistoricalAdaptersPreserved:
    def test_historical_disconnected_adapters_still_exist(self) -> None:
        for path in HISTORICAL_ADAPTERS:
            assert path.is_file()
            text = path.read_text(encoding="utf-8")
            assert len(text) > 0


class TestNoPersistenceNoOpenApi:
    def test_alembic_head_unchanged(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        versions = sorted(
            path.name
            for path in MIGRATIONS.glob("*.py")
            if path.name != "__init__.py"
        )
        assert any(name.startswith("a360s010004_") for name in versions)
        assert not any("school_context" in name.lower() for name in versions)

    def test_openapi_digest_unchanged(self) -> None:
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == "4042FB2725DA70A02A70EE09563B7698AE2E5DA82927614CAF1B5F7E6AA7C1D0"
