"""AIEOS360-S01-I03 — architecture isolation and OpenAPI contract."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.domain.schema import ContentSchemaRegistry
from aieos.platform.api.app import create_app
from aieos.platform.api.openapi import build_openapi
from aieos.platform.events.constants import PRODUCTION_EVENT_PUBLISH_PREFIXES
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.authorization.decisions import PrincipalKind
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
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.aieos360_s01_i03

SRC_ROOT = REPO_ROOT / "src" / "aieos"
LEARNING_ROOT = SRC_ROOT / "domains" / "learning"
RUNTIME_ROOT = SRC_ROOT / "platform" / "runtime"
NATS_ROOT = SRC_ROOT / "platform" / "events" / "nats"
OPENAPI_SNAPSHOT = REPO_ROOT / "contracts" / "openapi" / "aieos-v1.json"
I03_MIGRATION = REPO_ROOT / "migrations" / "versions" / "a360s010002_learning_attempt_audit_vocab.py"
COMMAND_UOW = RUNTIME_ROOT / "student_learning_command.py"

_STUDENT_PATHS = {
    "/api/v1/student-os/home",
    "/api/v1/student-os/assignments",
    "/api/v1/student-os/assignments/{assignment_id}",
    "/api/v1/learning/assignments/{assignment_id}/attempts",
    "/api/v1/learning/attempts/{attempt_id}",
    "/api/v1/learning/attempts/{attempt_id}/responses",
    "/api/v1/learning/attempts/{attempt_id}/actions/submit",
}


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
        raise AssertionError("OpenAPI export must not touch persistence")


def _schema() -> dict:
    app = create_app(
        uow_factory=_UnusedUowFactory(),
        teaching_uow_factory=_UnusedUowFactory(),
        assessment_uow_factory=_UnusedUowFactory(),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(uuid4()),
        security_resolver=StubSecurityContextResolver(uuid4(), uuid4()),
        content_types=StaticContentTypeCatalog({"test.generic"}),
        cursor_signing_key=b"aieos360-s01-i03-openapi-export-key",
        schema_registry=ContentSchemaRegistry(),
        idempotency_retention=timedelta(hours=24),
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
    )
    return build_openapi(app)


def _header_names(operation: dict) -> set[str]:
    return {
        p["name"]
        for p in operation.get("parameters", [])
        if isinstance(p, dict) and p.get("in") == "header"
    }


class TestArchitectureIsolation:
    def test_learning_does_not_import_teaching(self) -> None:
        offenders: list[str] = []
        for path in _py_files(LEARNING_ROOT):
            for module in _import_modules(path):
                if module == "aieos.domains.teaching" or module.startswith(
                    "aieos.domains.teaching."
                ):
                    offenders.append(f"{path}:{module}")
        assert offenders == []

    def test_composed_uow_does_not_import_domain_uows(self) -> None:
        text = COMMAND_UOW.read_text(encoding="utf-8")
        assert "SqlAlchemyTeachingUnitOfWork" not in text
        assert "SqlAlchemyLearningUnitOfWork" not in text
        modules = _import_modules(COMMAND_UOW)
        assert "aieos.domains.teaching.infrastructure.persistence.uow" not in modules
        assert "aieos.domains.learning.infrastructure.persistence.uow" not in modules

    def test_i03_88_production_nats_learning_pub_absent(self) -> None:
        nats_blob = "\n".join(
            path.read_text(encoding="utf-8") for path in _py_files(NATS_ROOT)
        )
        assert "io.eduvijna.aieos.learning." not in nats_blob
        assert PRODUCTION_EVENT_PUBLISH_PREFIXES == (
            "io.eduvijna.aieos.content.",
            "io.eduvijna.aieos.teaching.",
        )
        assert not any(
            "learning" in prefix for prefix in PRODUCTION_EVENT_PUBLISH_PREFIXES
        )

    def test_no_student_kind_max_attempts_or_abandoned(self) -> None:
        from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState

        assert "STUDENT" not in PrincipalKind.__members__
        assert set(AttemptLifecycleState.__members__) == {"IN_PROGRESS", "SUBMITTED"}
        learning = "\n".join(
            path.read_text(encoding="utf-8") for path in _py_files(LEARNING_ROOT)
        )
        assert "max_attempts" not in learning
        assert "temporal" not in learning.lower()
        assert "mcp" not in learning.lower()
        assert "student agent" not in learning.lower()

    def test_alembic_head_and_i03_migration_is_audit_only(self) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        sql = I03_MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "a360s010002"' in sql
        assert 'down_revision: str | None = "a360s010001"' in sql
        assert "CREATE TABLE learning" not in sql
        assert "CREATE TABLE teaching" not in sql
        assert "learning.attempt.start" in sql
        assert "learning.attempt.save_responses" in sql
        assert "learning.attempt.submit" in sql
        assert "Chief Architect authorized a360s010002" in sql
        assert "CREATE TABLE learning" not in sql
        assert "CREATE TABLE teaching" not in sql

    def test_event_type_strings_live_outside_learning_domain(self) -> None:
        learning = "\n".join(
            path.read_text(encoding="utf-8") for path in _py_files(LEARNING_ROOT)
        )
        assert "io.eduvijna.aieos.learning.attempt.started.v1" not in learning
        assert "io.eduvijna.aieos.learning.attempt.submitted.v1" not in learning


class TestOpenApiContract:
    def test_student_and_learning_paths_exist(self) -> None:
        schema = _schema()
        paths = schema["paths"]
        for path in _STUDENT_PATHS:
            assert path in paths
        start = paths["/api/v1/learning/assignments/{assignment_id}/attempts"]["post"]
        assert start["operationId"] == "learning_attempt_start"
        assert "Idempotency-Key" in _header_names(start)
        assert "201" in start["responses"]
        save = paths["/api/v1/learning/attempts/{attempt_id}/responses"]["put"]
        assert save["operationId"] == "learning_attempt_save_responses"
        assert {"Idempotency-Key", "If-Match"} <= _header_names(save)
        submit = paths["/api/v1/learning/attempts/{attempt_id}/actions/submit"]["post"]
        assert submit["operationId"] == "learning_attempt_submit"
        assert {"Idempotency-Key", "If-Match"} <= _header_names(submit)
        get_attempt = paths["/api/v1/learning/attempts/{attempt_id}"]["get"]
        assert get_attempt["operationId"] == "learning_attempt_get"
        assert "If-Match" not in _header_names(get_attempt)
        listed = paths["/api/v1/student-os/assignments"]["get"]
        assert listed["operationId"] == "student_os_assignment_list"
        list_params = {
            p["name"]
            for p in listed.get("parameters", [])
            if isinstance(p, dict) and p.get("in") == "query"
        }
        assert {"limit", "cursor"} <= list_params
        list_schema = listed["responses"]["200"]["content"]["application/json"]["schema"]
        assert list_schema is not None
        list_model = schema["components"]["schemas"]["StudentAssignmentListResponse"]
        assert "next_cursor" in list_model["properties"]
        assert "items" in list_model["properties"]
        assert "has_more" in list_model["properties"]

    def test_openapi_snapshot_matches_release_pin(self) -> None:
        schema = _schema()
        for path in _STUDENT_PATHS:
            assert path in schema["paths"]
        file_digest = hashlib.sha256(OPENAPI_SNAPSHOT.read_bytes()).hexdigest().upper()
        assert file_digest == EXPECTED_OPENAPI_SHA256
        snapshot_paths = json.loads(OPENAPI_SNAPSHOT.read_text(encoding="utf-8"))["paths"]
        assert snapshot_paths.keys() >= _STUDENT_PATHS
