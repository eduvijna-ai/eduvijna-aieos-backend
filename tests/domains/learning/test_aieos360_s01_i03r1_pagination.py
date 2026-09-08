"""AIEOS360-S01-I03R1 — real current-assignment cursor pagination and home count."""

from __future__ import annotations

import uuid

import pytest

from aieos.development.learner_principals import CLASS_REF_5A, CLASS_REF_5B
from aieos.domains.learning.application.current_assignments import HOME_SLICE
from aieos.platform.api.pagination import CursorCodec, StudentAssignmentCursor
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ASSIGNMENTS_PATH,
    CURSOR_KEY,
    FUTURE_AVAILABLE,
    HOME_PATH,
    cancel_assignment,
    clear_i03_side_effects_after_test,
    close_assignment,
    create_learner_assignment,
    prepare_class_5a_assignment,
    student_client,
)

pytestmark = [pytest.mark.aieos360_s01_i03, pytest.mark.aieos360_s01_i03r1]


def _headers(tenant_id):
    return {"X-AIEOS-Tenant-ID": str(tenant_id)}


def _create_more(runtime_engine, prepared, *, count: int, **kwargs):
    created = []
    for _ in range(count):
        created.append(
            create_learner_assignment(
                runtime_engine,
                tenant_id=prepared.tenant_id,
                principal_id=prepared.teacher_id,
                content_id=prepared.content_id,
                content_version_id=prepared.content_version_id,
                idempotency_key=f"create-{uuid.uuid7()}",
                **kwargs,
            )
        )
    return created


class TestCurrentAssignmentPagination:
    def test_r1_12_13_14_adjacent_pages_are_bounded_and_disjoint(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        _create_more(runtime_engine, prepared, count=7)
        client = student_client(runtime_engine, prepared)
        first = client.get(
            ASSIGNMENTS_PATH,
            params={"limit": 5},
            headers=_headers(prepared.tenant_id),
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert len(body["items"]) == 5
        assert body["next_cursor"]
        assert body["has_more"] is True
        assert body["has_more"] == (body["next_cursor"] is not None)
        ids1 = [item["assignment_id"] for item in body["items"]]
        second = client.get(
            ASSIGNMENTS_PATH,
            params={"limit": 5, "cursor": body["next_cursor"]},
            headers=_headers(prepared.tenant_id),
        )
        assert second.status_code == 200, second.text
        ids2 = [item["assignment_id"] for item in second.json()["items"]]
        assert ids2
        assert not set(ids1) & set(ids2)
        assert second.json()["has_more"] == (second.json()["next_cursor"] is not None)

    def test_r1_15_tampered_cursor_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH,
            params={"limit": 1},
            headers=_headers(prepared.tenant_id),
        )
        assert listed.status_code == 200
        cursor = listed.json().get("next_cursor")
        if not cursor:
            codec = CursorCodec(CURSOR_KEY)
            cursor = codec.encode_student_assignments(
                StudentAssignmentCursor(
                    tenant_id=prepared.tenant_id,
                    updated_at=prepared.assignment.updated_at,
                    assignment_id=prepared.assignment_id,
                )
            )
        tampered = cursor[:-2] + ("A" if cursor[-2] != "A" else "B") + cursor[-1]
        bad = client.get(
            ASSIGNMENTS_PATH,
            params={"cursor": tampered},
            headers=_headers(prepared.tenant_id),
        )
        assert bad.status_code == 400
        assert bad.json()["code"] == "invalid_cursor"

    def test_r1_16_future_assignments_do_not_starve_current_pages(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        current = [prepared.assignment, *_create_more(runtime_engine, prepared, count=5)]
        _create_more(
            runtime_engine, prepared, count=8, available_from=FUTURE_AVAILABLE
        )
        client = student_client(runtime_engine, prepared)
        seen: list[str] = []
        cursor = None
        for _ in range(4):
            params: dict = {"limit": 3}
            if cursor is not None:
                params["cursor"] = cursor
            page = client.get(
                ASSIGNMENTS_PATH, params=params, headers=_headers(prepared.tenant_id)
            )
            assert page.status_code == 200, page.text
            body = page.json()
            seen.extend(item["assignment_id"] for item in body["items"])
            cursor = body["next_cursor"]
            if cursor is None:
                break
        expected = {str(item.assignment_id.value) for item in current}
        assert expected <= set(seen)
        assert len(seen) == len(expected)
        assert len(seen) == len(set(seen))

    def test_r1_17_closed_and_cancelled_absent_from_every_page(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        extras = _create_more(runtime_engine, prepared, count=4)
        closed = close_assignment(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=extras[0],
            idempotency_key=f"close-{uuid.uuid7()}",
        )
        cancelled = cancel_assignment(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=extras[1],
            idempotency_key=f"cancel-{uuid.uuid7()}",
        )
        client = student_client(runtime_engine, prepared)
        hidden = {str(closed.assignment_id.value), str(cancelled.assignment_id.value)}
        cursor = None
        seen: list[str] = []
        for _ in range(4):
            params: dict = {"limit": 2}
            if cursor is not None:
                params["cursor"] = cursor
            page = client.get(
                ASSIGNMENTS_PATH, params=params, headers=_headers(prepared.tenant_id)
            )
            assert page.status_code == 200, page.text
            body = page.json()
            ids = [item["assignment_id"] for item in body["items"]]
            assert not set(ids) & hidden
            seen.extend(ids)
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert hidden.isdisjoint(seen)

    def test_r1_18_membership_class_ref_enforced_on_every_page(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        _create_more(runtime_engine, prepared, count=4, class_ref=CLASS_REF_5A)
        foreign = _create_more(
            runtime_engine, prepared, count=4, class_ref=CLASS_REF_5B
        )
        client = student_client(runtime_engine, prepared)
        foreign_ids = {str(item.assignment_id.value) for item in foreign}
        cursor = None
        seen: list[str] = []
        for _ in range(5):
            params: dict = {"limit": 2}
            if cursor is not None:
                params["cursor"] = cursor
            page = client.get(
                ASSIGNMENTS_PATH, params=params, headers=_headers(prepared.tenant_id)
            )
            assert page.status_code == 200, page.text
            body = page.json()
            for item in body["items"]:
                assert item["class_ref"] == CLASS_REF_5A
                assert item["assignment_id"] not in foreign_ids
                seen.append(item["assignment_id"])
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert foreign_ids.isdisjoint(seen)

    def test_r1_19_home_count_is_exact_while_items_are_sliced(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        extra = HOME_SLICE + 2
        _create_more(runtime_engine, prepared, count=extra)
        client = student_client(runtime_engine, prepared)
        home = client.get(HOME_PATH, headers=_headers(prepared.tenant_id))
        assert home.status_code == 200, home.text
        body = home.json()
        assert body["current_assignment_count"] == extra + 1
        assert len(body["items"]) == HOME_SLICE
        assert body["current_assignment_count"] > len(body["items"])
