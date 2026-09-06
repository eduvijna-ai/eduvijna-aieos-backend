"""TOS-DEV10-I04 architecture and frozen-contract guards."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tools.release.common import EXPECTED_MIGRATION_HEAD, EXPECTED_OPENAPI_SHA256

pytestmark = pytest.mark.tos_dev10_i04

ROOT = Path(__file__).resolve().parents[3]
TEACHING = ROOT / "src" / "aieos" / "domains" / "teaching"
MIGRATIONS = ROOT / "migrations" / "versions"
MIGRATION = MIGRATIONS / "tosd100001_teacher_memory.py"
OPENAPI = ROOT / "contracts" / "openapi" / "aieos-v1.json"
ASSISTANT_APP = (
    TEACHING / "application" / "assistant.py",
    TEACHING / "application" / "assistant_context.py",
    TEACHING / "application" / "assistant_models.py",
    TEACHING / "application" / "assistant_answer_v1.py",
    TEACHING / "api" / "v1" / "routes.py",
)


def test_current_head_is_tosd100001() -> None:
    assert EXPECTED_ALEMBIC_HEAD == "tosd100001"
    assert EXPECTED_MIGRATION_HEAD == "tosd100001"
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "tosd100001"' in source
    assert list(MIGRATIONS.glob("tosd100002*.py")) == []
    assert list(MIGRATIONS.glob("*chat*.py")) == []


def test_openapi_contains_assistant_op_and_digest_is_frozen() -> None:
    digest = hashlib.sha256(OPENAPI.read_bytes()).hexdigest().upper()
    assert digest == EXPECTED_OPENAPI_SHA256
    contract = OPENAPI.read_text(encoding="utf-8")
    assert "/api/v1/teacher-os/assistant" in contract
    assert "teacher_os_assistant_respond" in contract
    assert "/api/v1/teacher-os/memory" in contract
    assert "/api/v1/teacher-os/library" in contract


def test_no_chat_migration_and_no_chat_sor_tables_in_memory_migration() -> None:
    migration = MIGRATION.read_text(encoding="utf-8").lower()
    assert "create table teaching.teacher_memories" in migration
    assert "create table teaching.chat" not in migration
    assert "create table teaching.assistant_messages" not in migration
    assert "create table teaching.conversations" not in migration
    for path in MIGRATIONS.glob("*.py"):
        name = path.name.lower()
        assert "chat" not in name
        assert "conversation" not in name
        body = path.read_text(encoding="utf-8").lower()
        assert "create table teaching.chat" not in body
        assert "create table teaching.conversations" not in body


def test_read_reason_suggest_proofs_in_source() -> None:
    assistant = (TEACHING / "application" / "assistant.py").read_text(encoding="utf-8")
    assert "READ / REASON / SUGGEST" in assistant
    assert "never persists chat" in assistant.lower()
    assert "MUST NOT Publish" in assistant
    routes = (TEACHING / "api" / "v1" / "routes.py").read_text(encoding="utf-8")
    assert "READ / REASON / SUGGEST" in routes
    assert "Does not persist chat" in routes
    answer = (TEACHING / "application" / "assistant_answer_v1.py").read_text(
        encoding="utf-8"
    )
    assert "READ / REASON / SUGGEST" in answer
    forbidden_tables = (
        "teaching.chat",
        "assistant_messages",
        "conversation_turns",
    )
    for path in ASSISTANT_APP:
        source = path.read_text(encoding="utf-8").lower()
        for needle in forbidden_tables:
            assert needle not in source, f"{path.name}:{needle}"
