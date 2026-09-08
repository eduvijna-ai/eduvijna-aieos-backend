"""AIEOS360-S01-I03 — current assignment visibility (I03-14..I03-24)."""

from __future__ import annotations

import uuid

import pytest

from aieos.development.learner_principals import (
    CLASS_REF_5A,
    CLASS_REF_5B,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_B_PRINCIPAL_ID,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ASSIGNMENTS_PATH,
    FUTURE_AVAILABLE,
    HOME_PATH,
    PAST_DUE,
    RaisingMembershipReader,
    build_student_client,
    cancel_assignment,
    clear_i03_side_effects_after_test,
    close_assignment,
    prepare_class_5a_assignment,
    student_client,
)
from tests.domains.teaching.helpers_dev06_i03 import republish_content_to_new_version

pytestmark = pytest.mark.aieos360_s01_i03


class TestCurrentAssignments:
    def test_i03_15_student_a_sees_active_class_5a_assignment(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert any(
            item["assignment_id"] == str(prepared.assignment_id) for item in items
        )
        home = client.get(
            HOME_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert home.status_code == 200
        assert home.json()["current_assignment_count"] >= 1

    def test_i03_16_student_b_does_not_see_class_5a_assignment(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(
            runtime_engine, bootstrap_engine, student_id=STUDENT_B_PRINCIPAL_ID
        )
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.status_code == 200
        assert listed.json()["items"] == []

    def test_i03_17_wrong_tenant_cannot_see_assignment(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        other_tenant = uuid.uuid7()
        client = build_student_client(
            runtime_engine, other_tenant, STUDENT_A_PRINCIPAL_ID
        )
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(other_tenant)}
        )
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        missing = client.get(
            f"{ASSIGNMENTS_PATH}/{prepared.assignment_id}",
            headers={"X-AIEOS-Tenant-ID": str(other_tenant)},
        )
        assert missing.status_code == 404

    def test_i03_18_membership_outage_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.student_id,
            learner_membership_reader=RaisingMembershipReader(RuntimeError("erp down")),
        )
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.status_code == 503
        assert listed.json()["code"] == "school_context_unavailable"

    def test_i03_19_future_available_from_not_currently_consumable(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(
            runtime_engine, bootstrap_engine, available_from=FUTURE_AVAILABLE
        )
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.status_code == 200
        assert listed.json()["items"] == []
        detail = client.get(
            f"{ASSIGNMENTS_PATH}/{prepared.assignment_id}",
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert detail.status_code == 409
        assert detail.json()["code"] == "assignment_not_yet_available"

    def test_i03_20_due_at_past_active_remains_consumable(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(
            runtime_engine, bootstrap_engine, due_at=PAST_DUE
        )
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.status_code == 200
        match = next(
            item
            for item in listed.json()["items"]
            if item["assignment_id"] == str(prepared.assignment_id)
        )
        assert match["currently_consumable"] is True
        assert match["attempt_summary"] == "NOT_STARTED"

    def test_i03_21_closed_not_current(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        close_assignment(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=prepared.assignment,
            idempotency_key=f"close-{uuid.uuid7()}",
        )
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.json()["items"] == []
        detail = client.get(
            f"{ASSIGNMENTS_PATH}/{prepared.assignment_id}",
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert detail.status_code == 404

    def test_i03_22_cancelled_not_current(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        cancel_assignment(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=prepared.assignment,
            idempotency_key=f"cancel-{uuid.uuid7()}",
        )
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.json()["items"] == []

    def test_i03_23_late_join_current_membership_can_see_assignment(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        prepared.membership._memberships[STUDENT_A_PRINCIPAL_ID] = ()
        client = student_client(runtime_engine, prepared)
        empty = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert empty.json()["items"] == []
        prepared.membership._memberships[STUDENT_A_PRINCIPAL_ID] = (
            CurrentLearnerClassMembership(class_ref=CLASS_REF_5A),
        )
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert any(
            item["assignment_id"] == str(prepared.assignment_id)
            for item in listed.json()["items"]
        )

    def test_i03_24_membership_removal_removes_assignment_from_current_list(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        listed = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert listed.json()["items"]
        prepared.membership._memberships[STUDENT_A_PRINCIPAL_ID] = (
            CurrentLearnerClassMembership(class_ref=CLASS_REF_5B),
        )
        after = client.get(
            ASSIGNMENTS_PATH, headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)}
        )
        assert after.json()["items"] == []

    def test_i03_14_assigned_v1_remains_v1_after_later_publication(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        republish_content_to_new_version(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            content_id=prepared.content_id,
            parent_version_id=prepared.content_version_id,
            owner_id=prepared.teacher_id,
        )
        client = student_client(runtime_engine, prepared)
        detail = client.get(
            f"{ASSIGNMENTS_PATH}/{prepared.assignment_id}",
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert detail.status_code == 200
        body = detail.json()
        assert body["content_version_id"] == str(prepared.content_version_id)
        resource = body["resource"]
        assert resource is not None
        assert resource["content_version_id"] == str(prepared.content_version_id)
        assert resource["schema_version"] == 1
        assert "answer" not in str(resource)
        assert "teacher_summary" not in str(resource)
