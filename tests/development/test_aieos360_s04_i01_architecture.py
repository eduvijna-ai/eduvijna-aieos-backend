"""AIEOS360-S04-I01 — architecture isolation and non-authorization guards."""

from __future__ import annotations

import ast
import hashlib
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

pytestmark = pytest.mark.aieos360_s04_i01

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "aieos"
PROVIDER = SRC_ROOT / "development" / "coherent_school_context.py"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
PRODUCTION_RUNTIME_FILES = (
    RUNTIME_ROOT / "composition.py",
    RUNTIME_ROOT / "compose_api_dependencies.py",
    RUNTIME_ROOT / "entrypoints" / "api_main.py",
    SRC_ROOT / "platform" / "api" / "app.py",
)
FORBIDDEN_PROVIDER_MARKERS = (
    "aieos.development.coherent_school_context",
    "DevelopmentCoherentSchoolContextProvider",
    "development_coherent_school_context_provider",
)
FORBIDDEN_NETWORK_DB_MODULES = (
    "sqlalchemy",
    "alembic",
    "asyncpg",
    "psycopg",
    "psycopg2",
    "httpx",
    "aiohttp",
    "requests",
    "nats",
    "nkeys",
    "temporalio",
    "fastapi",
)
FORBIDDEN_GIANT_CONTRACTS = (
    "class SchoolContextService",
    "class UniversalSchoolContextPort",
    "class AdminSchoolService",
)


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


class TestProductionIsolation:
    def test_production_runtime_does_not_import_coherent_provider(self) -> None:
        for path in PRODUCTION_RUNTIME_FILES:
            text = path.read_text(encoding="utf-8")
            for marker in FORBIDDEN_PROVIDER_MARKERS:
                assert marker not in text

    def test_non_development_source_does_not_import_coherent_provider(self) -> None:
        for path in _py_files(SRC_ROOT):
            if "development" in path.relative_to(SRC_ROOT).parts:
                continue
            modules = _import_modules(path)
            assert "aieos.development.coherent_school_context" not in modules
            text = path.read_text(encoding="utf-8")
            assert "DevelopmentCoherentSchoolContextProvider" not in text

    def test_domains_do_not_import_development(self) -> None:
        for path in _py_files(SRC_ROOT / "domains"):
            for module in _import_modules(path):
                assert not module.startswith("aieos.development")


class TestProviderClassification:
    def test_provider_is_explicitly_non_production(self) -> None:
        text = PROVIDER.read_text(encoding="utf-8")
        assert "NON_PRODUCTION" in text
        assert "ADR-AIEOS-062" in text
        assert "Must never be imported by production runtime composition" in text
        assert "Not an ERP/SIS master" in text
        assert "DEVELOPMENT PROVIDER CONTROL ONLY" in text
        assert "PrincipalKind.ADMIN" not in text
        for name in FORBIDDEN_GIANT_CONTRACTS:
            assert name not in text

    def test_provider_has_no_network_or_persistence(self) -> None:
        modules = _import_modules(PROVIDER)
        text = PROVIDER.read_text(encoding="utf-8")
        for module in modules:
            root = module.split(".", 1)[0]
            assert root not in FORBIDDEN_NETWORK_DB_MODULES
            assert module not in FORBIDDEN_NETWORK_DB_MODULES
        assert "Table(" not in text
        assert "create_engine" not in text
        assert "APIRouter" not in text
        assert "@app." not in text
        assert "/api/v1/" not in text


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
        provider_text = PROVIDER.read_text(encoding="utf-8")
        for name in FORBIDDEN_GIANT_CONTRACTS:
            assert name not in provider_text

    def test_domain_contract_files_were_not_collapsed(self) -> None:
        teaching = (
            SRC_ROOT / "domains" / "teaching" / "application" / "school_context.py"
        ).read_text(encoding="utf-8")
        learning = (
            SRC_ROOT / "domains" / "learning" / "application" / "learner_membership.py"
        ).read_text(encoding="utf-8")
        school = (
            SRC_ROOT
            / "domains"
            / "school_intelligence"
            / "application"
            / "school_scope.py"
        ).read_text(encoding="utf-8")
        parent = (
            SRC_ROOT
            / "domains"
            / "parent_intelligence"
            / "application"
            / "learner_access.py"
        ).read_text(encoding="utf-8")
        assert "class SchoolContextClassReader" in teaching
        assert "class SchoolContextLearnerMembershipReader" in learning
        assert "class SchoolContextPrincipalScopeReader" in school
        assert "class SchoolContextParentLearnerAccessReader" in parent
        assert "list_assignable_classes" in teaching
        assert "list_current_memberships" in learning
        assert "list_current_authorized_classes" in school
        assert "list_current_authorized_learners" in parent


class TestIdentityAndCapabilityBoundary:
    def test_principal_kind_admin_was_not_created(self) -> None:
        assert set(PrincipalKind) == {PrincipalKind.HUMAN, PrincipalKind.WORKLOAD}
        assert "ADMIN" not in PrincipalKind.__members__
        assert "PARENT" not in PrincipalKind.__members__
        assert "GUARDIAN" not in PrincipalKind.__members__
        assert "STUDENT" not in PrincipalKind.__members__
        assert "LEARNER" not in PrincipalKind.__members__
        assert "PRINCIPAL" not in PrincipalKind.__members__
        decisions = (
            SRC_ROOT / "platform" / "security" / "authorization" / "decisions.py"
        ).read_text(encoding="utf-8")
        assert "ADMIN" not in decisions

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
        assert not any("roster" in name.lower() for name in versions)
        assert not any("enrollment" in name.lower() for name in versions)
        assert not any("family" in name.lower() for name in versions)

    def test_openapi_digest_unchanged(self) -> None:
        digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert digest == EXPECTED_OPENAPI_SHA256
        assert digest == "4042FB2725DA70A02A70EE09563B7698AE2E5DA82927614CAF1B5F7E6AA7C1D0"
