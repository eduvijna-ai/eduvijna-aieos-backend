"""TOS-DEV10-I03 Teacher Memory v1 HTTP + PostgreSQL acceptance."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.domains.teaching.helpers_dev10_i03 import (
    DEFAULT_PREFERENCES,
    MEMORY_PATH,
    build_memory_client,
    create_memory,
    headers,
)

pytestmark = pytest.mark.tos_dev10_i03


@pytest.fixture
def tenant_id():
    return uuid.uuid7()


@pytest.fixture
def principal_id():
    return uuid.uuid7()


def test_get_absent_returns_404(runtime_engine, tenant_id, principal_id) -> None:
    client = build_memory_client(runtime_engine, tenant_id, principal_id)
    response = client.get(MEMORY_PATH, headers=headers(tenant_id))
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "teacher_memory_not_found"


def test_create_get_update_revision_and_persistence(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    client = build_memory_client(runtime_engine, tenant_id, principal_id)
    created = create_memory(client, tenant_id, key="mem-create-1")
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["aggregate_revision"] == 0
    assert body["schema_version"] == 1
    assert body["preferences"] == DEFAULT_PREFERENCES
    assert "tenant_id" not in body
    assert "teacher_principal_id" not in body
    assert created.headers["etag"] == '"r0"'
    memory_id = body["memory_id"]

    got = client.get(MEMORY_PATH, headers=headers(tenant_id))
    assert got.status_code == 200, got.text
    assert got.json()["memory_id"] == memory_id
    assert got.headers["etag"] == '"r0"'

    updated = client.put(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-upd-1", if_match='"r0"'),
        json={
            "preferences": {
                **DEFAULT_PREFERENCES,
                "teaching_style": "inquiry_led",
                "include_differentiation": True,
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["aggregate_revision"] == 1
    assert updated.json()["preferences"]["teaching_style"] == "inquiry_led"
    assert updated.headers["etag"] == '"r1"'

    # Persistence reload via fresh client
    client2 = build_memory_client(runtime_engine, tenant_id, principal_id)
    reloaded = client2.get(MEMORY_PATH, headers=headers(tenant_id))
    assert reloaded.status_code == 200
    assert reloaded.json()["preferences"]["include_differentiation"] is True
    assert reloaded.json()["aggregate_revision"] == 1

    with bootstrap_engine.connect() as conn:
        conn.execute(text("SELECT set_config('aieos.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
        row = conn.execute(
            text(
                """
                SELECT teacher_principal_id, aggregate_revision
                FROM teaching.teacher_memories
                WHERE memory_id = :mid
                """
            ),
            {"mid": memory_id},
        ).mappings().one()
        assert row["teacher_principal_id"] == principal_id
        assert int(row["aggregate_revision"]) == 1


def test_duplicate_create_is_deterministic(
    runtime_engine, tenant_id, principal_id
) -> None:
    client = build_memory_client(runtime_engine, tenant_id, principal_id)
    first = create_memory(client, tenant_id, key="mem-dup-a")
    assert first.status_code == 201, first.text
    second = create_memory(
        client,
        tenant_id,
        key="mem-dup-b",
        preferences={**DEFAULT_PREFERENCES, "teaching_style": "collaborative"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["memory_id"] == first.json()["memory_id"]
    # Deterministic duplicate must not rewrite preferences.
    assert second.json()["preferences"]["teaching_style"] == "balanced"
    assert second.json()["aggregate_revision"] == 0

    replay = create_memory(client, tenant_id, key="mem-dup-a")
    assert replay.status_code == 201
    assert replay.json()["memory_id"] == first.json()["memory_id"]


def test_stale_if_match_conflict(runtime_engine, tenant_id, principal_id) -> None:
    client = build_memory_client(runtime_engine, tenant_id, principal_id)
    assert create_memory(client, tenant_id, key="mem-stale-c").status_code == 201
    stale = client.put(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-stale-u", if_match='"r9"'),
        json={"preferences": DEFAULT_PREFERENCES},
    )
    assert stale.status_code == 412, stale.text
    assert stale.json()["code"] == "resource_revision_conflict"


def test_teacher_and_tenant_isolation(runtime_engine, tenant_id) -> None:
    teacher_a = uuid.uuid7()
    teacher_b = uuid.uuid7()
    other_tenant = uuid.uuid7()
    client_a = build_memory_client(runtime_engine, tenant_id, teacher_a)
    client_b = build_memory_client(runtime_engine, tenant_id, teacher_b)
    client_other = build_memory_client(runtime_engine, other_tenant, teacher_a)

    created = create_memory(client_a, tenant_id, key="mem-iso-a")
    assert created.status_code == 201, created.text
    memory_id = created.json()["memory_id"]

    missing_b = client_b.get(MEMORY_PATH, headers=headers(tenant_id))
    assert missing_b.status_code == 404

    missing_tenant = client_other.get(MEMORY_PATH, headers=headers(other_tenant))
    assert missing_tenant.status_code == 404

    created_b = create_memory(client_b, tenant_id, key="mem-iso-b")
    assert created_b.status_code == 201
    assert created_b.json()["memory_id"] != memory_id


def test_typed_validation_and_extra_forbid(
    runtime_engine, tenant_id, principal_id
) -> None:
    client = build_memory_client(runtime_engine, tenant_id, principal_id)
    bad_enum = client.post(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-bad-enum"),
        json={"preferences": {**DEFAULT_PREFERENCES, "teaching_style": "socratic"}},
    )
    assert bad_enum.status_code in {400, 422}, bad_enum.text

    extra = client.post(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-extra"),
        json={
            "preferences": {**DEFAULT_PREFERENCES, "favorite_color": "blue"},
        },
    )
    assert extra.status_code in {400, 422}, extra.text

    owner_supplied = client.post(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-owner"),
        json={
            "preferences": DEFAULT_PREFERENCES,
            "teacher_principal_id": str(uuid.uuid7()),
        },
    )
    assert owner_supplied.status_code in {400, 422}, owner_supplied.text


def test_default_and_reset_preferences(
    runtime_engine, tenant_id, principal_id
) -> None:
    client = build_memory_client(runtime_engine, tenant_id, principal_id)
    created = client.post(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-default"),
        json={},
    )
    assert created.status_code == 201, created.text
    assert created.json()["preferences"] == DEFAULT_PREFERENCES

    updated = client.put(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-reset", if_match='"r0"'),
        json={
            "preferences": {
                "teaching_style": "direct_instruction",
                "preferred_difficulty": "challenging",
                "preparation_detail": "detailed",
                "output_format": "print_friendly",
                "include_differentiation": True,
            }
        },
    )
    assert updated.status_code == 200, updated.text
    reset = client.put(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key="mem-reset-2", if_match='"r1"'),
        json={"preferences": DEFAULT_PREFERENCES},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["preferences"] == DEFAULT_PREFERENCES
    assert reset.json()["aggregate_revision"] == 2


def test_get_never_creates(runtime_engine, bootstrap_engine, tenant_id, principal_id) -> None:
    client = build_memory_client(runtime_engine, tenant_id, principal_id)
    assert client.get(MEMORY_PATH, headers=headers(tenant_id)).status_code == 404
    with bootstrap_engine.connect() as conn:
        conn.execute(text("SELECT set_config('aieos.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
        count = conn.execute(
            text("SELECT count(*) FROM teaching.teacher_memories WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).scalar_one()
        assert count == 0
