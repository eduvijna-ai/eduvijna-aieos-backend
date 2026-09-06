"""TOS-DEV10-I03 architecture and frozen-contract guards."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aieos.platform.idempotency.models import (
    TEACHER_OS_MEMORY_CREATE_V1,
    TEACHER_OS_MEMORY_UPDATE_V1,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from aieos.platform.security.audit.actions import SecurityAuditAction
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev10_i03

ROOT = Path(__file__).resolve().parents[3]
TEACHING = ROOT / "src" / "aieos" / "domains" / "teaching"
MIGRATIONS = ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tosd100001_teacher_memory.py"
OPENAPI = ROOT / "contracts" / "openapi" / "aieos-v1.json"
MEMORY_APP = (
    TEACHING / "application" / "memory_create.py",
    TEACHING / "application" / "memory_update.py",
    TEACHING / "application" / "memory_queries.py",
    TEACHING / "domain" / "teacher_memory.py",
    TEACHING / "domain" / "preferences.py",
)


def test_current_head_is_tosd100001() -> None:
    assert EXPECTED_ALEMBIC_HEAD == "tosd100001"
    assert EXPECTED_MIGRATION_HEAD == "tosd100001"
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "tosd100001"' in source
    assert 'down_revision: str | None = "pedi090002"' in source
    assert "principal_kind already exists" in source or "NOT duplicated" in source
    assert "No Principal backfill" in source or "no Principal backfill" in source.lower()
    assert list(MIGRATIONS.glob("tosd100002*.py")) == []
    assert (MIGRATIONS / "pedi090002_principal_kind.py").is_file()


def test_openapi_contains_memory_ops_and_digest_is_frozen() -> None:
    digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
    assert digest == EXPECTED_OPENAPI_SHA256
    contract = OPENAPI.read_text(encoding="utf-8")
    assert "/api/v1/teacher-os/memory" in contract
    assert "teacher_os_memory_get" in contract
    assert "teacher_os_memory_create" in contract
    assert "teacher_os_memory_update" in contract
    # Post-I02 composition must retain Library operations.
    assert "/api/v1/teacher-os/library" in contract
    assert "teacher_os_library_list" in contract
    assert "teacher_os_library_get" in contract
    assert "teacher_os_library_version_get" in contract


def test_owner_resolution_is_wired_and_not_caller_definitional() -> None:
    resolver = (
        TEACHING / "application" / "owner_resolution.py"
    ).read_text(encoding="utf-8")
    assert "resolve_represented_teacher_principal" in resolver
    assert "require_human_teacher_owner" in resolver
    assert "HUMAN" in resolver
    assert "not definitionally" in resolver.lower() or "NOT definitionally" in resolver
    for path in (
        TEACHING / "application" / "memory_create.py",
        TEACHING / "application" / "memory_update.py",
        TEACHING / "application" / "memory_queries.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "require_human_teacher_owner" in source
        assert "Ownership is always TrustedSecurityContext.principal_id" not in source
    domain = (TEACHING / "domain" / "teacher_memory.py").read_text(encoding="utf-8")
    assert "resolve_represented_teacher_principal" in domain
    assert "until principal_kind exists" not in domain


def test_authority_boundaries_exclude_deferred_scope() -> None:
    forbidden = (
        "chat_history",
        "learner_id",
        "student_id",
        "embedding",
        "mastery",
    )
    for path in MEMORY_APP:
        source = path.read_text(encoding="utf-8").lower()
        for needle in forbidden:
            assert needle not in source, f"{path.name}:{needle}"
        assert "inferenc" not in source
    migration = MIGRATION.read_text(encoding="utf-8").lower()
    assert "deliberately absent" in migration
    assert "continuous context" in migration
    assert "automatic preference inference" in migration
    assert "create table teaching.teacher_memories" in migration
    assert "chat_history" not in migration
    assert "learner_id" not in migration
    assert "embedding" not in migration


def test_idempotency_and_audit_actions_are_exact() -> None:
    assert TEACHER_OS_MEMORY_CREATE_V1 == "teacher_os_memory_create.v1"
    assert TEACHER_OS_MEMORY_UPDATE_V1 == "teacher_os_memory_update.v1"
    assert SecurityAuditAction.TEACHING_MEMORY_CREATE.value == "teaching.memory.create"
    assert SecurityAuditAction.TEACHING_MEMORY_UPDATE.value == "teaching.memory.update"


def test_prepare_does_not_consume_memory_as_generation_context() -> None:
    prepare = (TEACHING / "application" / "prepare.py").read_text(encoding="utf-8")
    assert "TeacherMemory" not in prepare
    assert "teacher_memories" not in prepare
    assert "memory_create" not in prepare
