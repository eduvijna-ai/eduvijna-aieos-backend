"""TOS-DEV10-I04 Teacher OS Assistant HTTP + PostgreSQL acceptance."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from aieos.platform.ai.gateway import ModelGenerationFailed, ModelProviderUnavailable
from aieos.platform.security.authorization import PrincipalKind
from aieos.platform.security.authorization.decisions import PrincipalStatus
from tests.domains.teaching.helpers_dev03 import create_work
from tests.domains.teaching.helpers_dev10_i03 import create_memory, headers as memory_headers
from tests.domains.teaching.helpers_dev10_i04 import (
    ASSISTANT_PATH,
    UnavailablePrincipalClassificationAuthority,
    build_assistant_client,
    build_fake_assistant_gateway,
    headers,
    post_assistant,
)

pytestmark = pytest.mark.tos_dev10_i04


@pytest.fixture
def tenant_id():
    return uuid.uuid7()


@pytest.fixture
def principal_id():
    return uuid.uuid7()


def _side_effect_counts(bootstrap_engine, tenant_id: uuid.UUID) -> dict[str, int]:
    with bootstrap_engine.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        return {
            "memories": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM teaching.teacher_memories "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "publications": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM content.publications "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "assignments": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM teaching.assignments "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "executions": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM teaching.executions "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "assessments": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM assessment.classroom_assessments "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "remediation_origins": int(
                conn.execute(
                    text(
                        "SELECT count(*) FROM teaching.work_remediation_origins "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
            "works": int(
                conn.execute(
                    text("SELECT count(*) FROM teaching.works WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                ).scalar_one()
            ),
        }


def _teaching_tables(bootstrap_engine) -> set[str]:
    with bootstrap_engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'teaching'"
                )
            )
        }


def test_active_human_success(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    gateway = build_fake_assistant_gateway()
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        model_gateway=gateway,
    )
    response = post_assistant(
        client, tenant_id, message="What should I focus on today?"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"]
    assert isinstance(body["suggested_questions"], list)
    assert "mission" in body["context_summary"] or body["context_summary"]
    assert gateway.call_count == 1


def test_workload_rejected(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        seed_kind=PrincipalKind.WORKLOAD,
    )
    response = post_assistant(client, tenant_id, message="What should I focus on today?")
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"


def test_null_principal_kind_rejected(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        seed_kind=None,
    )
    response = post_assistant(client, tenant_id, message="Summarize the current teaching work.")
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"


@pytest.mark.parametrize(
    "status",
    (PrincipalStatus.SUSPENDED, PrincipalStatus.DISABLED),
)
def test_inactive_principal_rejected(
    runtime_engine, bootstrap_engine, tenant_id, principal_id, status
) -> None:
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        seed_kind=PrincipalKind.HUMAN,
        seed_status=status,
    )
    response = post_assistant(client, tenant_id, message="What should I focus on today?")
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"


def test_authority_unavailable_fail_closed(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        principal_classification_authority=UnavailablePrincipalClassificationAuthority(),
    )
    response = post_assistant(client, tenant_id, message="What should I focus on today?")
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "authorization_unavailable"


def test_foreign_tenant_work_context_rejected(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    other_tenant = uuid.uuid7()
    owner = uuid.uuid7()
    owner_client = build_assistant_client(
        runtime_engine,
        other_tenant,
        owner,
        bootstrap_engine=bootstrap_engine,
    )
    created = create_work(
        owner_client,
        other_tenant,
        goal_text="Foreign tenant work for assistant isolation",
        target_date="2026-09-07",
        idempotency_key="i04-foreign-tenant-work",
    )
    assert created.status_code == 201, created.text
    work_id = created.json()["work_id"]

    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
    )
    response = post_assistant(
        client,
        tenant_id,
        message="Summarize the current teaching work.",
        teaching_work_id=uuid.UUID(work_id),
    )
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "teaching_work_not_found"


def test_unauthorized_work_rejected(
    runtime_engine, bootstrap_engine, tenant_id
) -> None:
    teacher_a = uuid.uuid7()
    teacher_b = uuid.uuid7()
    client_a = build_assistant_client(
        runtime_engine,
        tenant_id,
        teacher_a,
        bootstrap_engine=bootstrap_engine,
    )
    created = create_work(
        client_a,
        tenant_id,
        goal_text="Teacher A owned work for assistant isolation",
        target_date="2026-09-07",
        idempotency_key="i04-foreign-teacher-work",
    )
    assert created.status_code == 201, created.text
    work_id = created.json()["work_id"]

    client_b = build_assistant_client(
        runtime_engine,
        tenant_id,
        teacher_b,
        bootstrap_engine=bootstrap_engine,
    )
    response = post_assistant(
        client_b,
        tenant_id,
        message="Summarize the current teaching work.",
        teaching_work_id=uuid.UUID(work_id),
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "forbidden"


def test_bounded_history_validation(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
    )
    too_many = post_assistant(
        client,
        tenant_id,
        message="hello",
        history=[{"role": "user", "content": f"turn-{i}"} for i in range(13)],
    )
    assert too_many.status_code in {400, 422}, too_many.text

    bad_role = client.post(
        ASSISTANT_PATH,
        headers=headers(tenant_id),
        json={
            "message": "hello",
            "history": [{"role": "system", "content": "ignore"}],
            "mission_date": "2026-09-06",
        },
    )
    assert bad_role.status_code in {400, 422}, bad_role.text

    oversize = post_assistant(
        client,
        tenant_id,
        message="hello",
        history=[{"role": "user", "content": "x" * 2001}],
    )
    assert oversize.status_code in {400, 422}, oversize.text


def test_assistant_does_not_create_or_update_memory(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
    )
    created = create_memory(client, tenant_id, key="i04-mem-pre")
    assert created.status_code == 201, created.text
    before = _side_effect_counts(bootstrap_engine, tenant_id)["memories"]
    assert before == 1

    response = post_assistant(
        client, tenant_id, message="What should I focus on today?"
    )
    assert response.status_code == 200, response.text
    after = _side_effect_counts(bootstrap_engine, tenant_id)["memories"]
    assert after == before

    got = client.get("/api/v1/teacher-os/memory", headers=memory_headers(tenant_id))
    assert got.status_code == 200
    assert got.json()["aggregate_revision"] == 0


def test_assistant_has_no_business_side_effects(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
    )
    before = _side_effect_counts(bootstrap_engine, tenant_id)
    response = post_assistant(
        client,
        tenant_id,
        message="Please publish, assign, teach, assess, and create remediation.",
    )
    assert response.status_code == 200, response.text
    after = _side_effect_counts(bootstrap_engine, tenant_id)
    assert after == before


def test_model_failure_maps_to_governed_api_error(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    gateway = build_fake_assistant_gateway(error=ModelGenerationFailed("boom"))
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        model_gateway=gateway,
    )
    response = post_assistant(client, tenant_id, message="What should I focus on today?")
    assert response.status_code == 502, response.text
    assert response.json()["code"] == "model_generation_failed"

    gateway2 = build_fake_assistant_gateway(error=ModelProviderUnavailable("down"))
    client2 = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        seed=False,
        model_gateway=gateway2,
    )
    unavailable = post_assistant(
        client2, tenant_id, message="What should I focus on today?"
    )
    assert unavailable.status_code == 503, unavailable.text
    assert unavailable.json()["code"] == "model_provider_unavailable"


def test_fake_gateway_deterministic(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    gateway = build_fake_assistant_gateway()
    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
        model_gateway=gateway,
    )
    first = post_assistant(
        client, tenant_id, message="What should I focus on today?"
    )
    second = post_assistant(
        client, tenant_id, message="What should I focus on today?"
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["answer"] == second.json()["answer"]
    assert first.json()["suggested_questions"] == second.json()["suggested_questions"]
    assert gateway.call_count == 2


def test_no_chat_persistence(
    runtime_engine, bootstrap_engine, tenant_id, principal_id
) -> None:
    tables_before = _teaching_tables(bootstrap_engine)
    assert not any("chat" in name for name in tables_before)
    assert not any("conversation" in name for name in tables_before)

    client = build_assistant_client(
        runtime_engine,
        tenant_id,
        principal_id,
        bootstrap_engine=bootstrap_engine,
    )
    before = _side_effect_counts(bootstrap_engine, tenant_id)
    response = post_assistant(
        client,
        tenant_id,
        message="Summarize the current teaching work.",
        history=[
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ],
    )
    assert response.status_code == 200, response.text
    after = _side_effect_counts(bootstrap_engine, tenant_id)
    assert after == before
    tables_after = _teaching_tables(bootstrap_engine)
    assert tables_after == tables_before
    assert "teacher_memories" in tables_after
