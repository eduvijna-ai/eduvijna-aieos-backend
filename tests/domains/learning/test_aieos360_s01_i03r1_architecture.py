"""AIEOS360-S01-I03R1 — composition and application-port architecture proofs."""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from tests.dbutil import REPO_ROOT
from tests.fakes import (
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowClassroomAssessmentAuthorization,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

pytestmark = [pytest.mark.aieos360_s01_i03, pytest.mark.aieos360_s01_i03r1]

SRC_ROOT = REPO_ROOT / "src" / "aieos"
LEARNING_APPLICATION = SRC_ROOT / "domains" / "learning" / "application"
APP_PY = SRC_ROOT / "platform" / "api" / "app.py"
TEACHING_ASSIGNMENT_REPO = (
    SRC_ROOT / "domains" / "teaching" / "infrastructure" / "persistence" / "repositories.py"
)
I03_DOC = REPO_ROOT / "docs" / "AIEOS360-S01-I03-STUDENT-ASSIGNMENT-APPLICATION-API.md"
I03_MIGRATION = (
    REPO_ROOT / "migrations" / "versions" / "a360s010002_learning_attempt_audit_vocab.py"
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


class _UnusedUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("test must not touch persistence")


class _FakeStudentUowFactory:
    def __call__(self, execution_tenant_id):
        raise AssertionError("composed factory must not be invoked in this proof")


class _AllowHuman:
    def require_current_human_principal(self, principal_id):
        return object()


def _base_app(**overrides):
    kwargs = dict(
        uow_factory=_UnusedUowFactory(),
        teaching_uow_factory=_UnusedUowFactory(),
        assessment_uow_factory=_UnusedUowFactory(),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"aieos360-s01-i03r1-openapi-key",
        schema_registry=ContentSchemaRegistry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        principal_classification_authority=_AllowHuman(),
    )
    kwargs.update(overrides)
    return create_app(**kwargs)


class TestCompositionBoundaries:
    def test_r1_20_no_teaching_uow_factory_engine_peek(self) -> None:
        assert "teaching_uow_factory._engine" not in APP_PY.read_text(encoding="utf-8")

    def test_r1_21_learning_application_does_not_import_runtime_uow(self) -> None:
        offenders: list[str] = []
        for path in _py_files(LEARNING_APPLICATION):
            for module in _import_modules(path):
                if module == "aieos.platform.runtime.student_learning_command" or module.startswith(
                    "aieos.platform.runtime.student_learning_command."
                ):
                    offenders.append(f"{path}:{module}")
        assert offenders == []

    def test_r1_22_explicit_student_uow_factory_composition_works(self) -> None:
        app = _base_app(student_learning_uow_factory=_FakeStudentUowFactory())
        assert app.state.start_attempt_service is not None
        assert app.state.list_current_assignments_service is not None
        assert app.state.get_student_home_service is not None

    def test_r1_23_unconfigured_student_command_runtime_fails_closed(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        app = _base_app(
            request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
            security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        )
        assert app.state.start_attempt_service is None
        assert app.state.list_current_assignments_service is None
        client = TestClient(app, raise_server_exceptions=False)
        listed = client.get(
            "/api/v1/student-os/assignments",
            headers={"X-AIEOS-Tenant-ID": str(tenant_id)},
        )
        assert listed.status_code == 503
        assert listed.json()["code"] == "persistence_operation_failed"

    def test_available_from_is_filtered_in_sql_before_limit(self) -> None:
        source = TEACHING_ASSIGNMENT_REPO.read_text(encoding="utf-8")
        method = source.split("def list_for_class_refs", 1)[1].split(
            "def count_current_for_class_refs", 1
        )[0]
        assert "available_from <= now" in method
        assert method.index("available_from <= now") < method.index(".limit(limit)")

    def test_chief_architect_authorized_a360s010002_is_recorded(self) -> None:
        migration = I03_MIGRATION.read_text(encoding="utf-8")
        docs = I03_DOC.read_text(encoding="utf-8")
        assert "Chief Architect authorized a360s010002" in migration
        assert "Chief Architect authorized `a360s010002`" in docs
        normalized = " ".join(docs.lower().split())
        assert "this is **not** a new adr" in normalized
