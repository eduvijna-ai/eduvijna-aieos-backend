"""AIEOS360-S01-I03 — start attempt (I03-25..I03-40)."""

from __future__ import annotations

import uuid

import pytest

from aieos.development.learner_principals import STUDENT_B_PRINCIPAL_ID
from aieos.platform.security.authorization.decisions import PrincipalKind, PrincipalStatus
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ATTEMPT_PATH,
    FUTURE_AVAILABLE,
    PAST_DUE,
    RaisingMembershipReader,
    build_student_client,
    clear_i03_side_effects_after_test,
    close_assignment,
    etag,
    prepare_class_5a_assignment,
    start_attempt,
    student_client,
)
from tests.platform.security.authorization.helpers import seed_principal

pytestmark = pytest.mark.aieos360_s01_i03


class TestStartAttempt:
    def test_i03_25_through_29_active_human_member_can_start(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        response = start_attempt(client, prepared)
        assert response.status_code == 201, response.text
        body = response.json()
        assert response.headers["Location"] == ATTEMPT_PATH.format(
            attempt_id=body["attempt_id"]
        )
        assert response.headers["ETag"] == etag(0)
        assert body["attempt_number"] == 1
        assert body["aggregate_revision"] == 0
        assert body["lifecycle_state"] == "IN_PROGRESS"
        assert body["teaching_assignment_id"] == str(prepared.assignment_id)
        assert body["content_id"] == str(prepared.content_id)
        assert body["content_version_id"] == str(prepared.content_version_id)
        assert body["class_ref"] == "class-5a"
        loaded = client.get(
            ATTEMPT_PATH.format(attempt_id=body["attempt_id"]),
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert loaded.status_code == 200
        assert loaded.json()["attempt_id"] == body["attempt_id"]

    def test_i03_30_workload_denied(self, runtime_engine, bootstrap_engine) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        workload = uuid.uuid7()
        seed_principal(
            bootstrap_engine, workload, principal_kind=PrincipalKind.WORKLOAD
        )
        client = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            workload,
            learner_membership_reader=prepared.membership,
        )
        response = start_attempt(client, prepared)
        assert response.status_code == 403

    def test_i03_31_inactive_human_denied(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        inactive = uuid.uuid7()
        seed_principal(
            bootstrap_engine,
            inactive,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.DISABLED,
        )
        client = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            inactive,
            learner_membership_reader=prepared.membership,
        )
        response = start_attempt(client, prepared)
        assert response.status_code == 403

    def test_i03_32_membership_denied_concealed_as_not_found(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(
            runtime_engine, bootstrap_engine, student_id=STUDENT_B_PRINCIPAL_ID
        )
        client = student_client(runtime_engine, prepared)
        response = start_attempt(client, prepared)
        assert response.status_code == 404
        assert response.json()["code"] == "assignment_not_found"

    def test_i03_33_membership_unavailable_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.student_id,
            learner_membership_reader=RaisingMembershipReader(RuntimeError("erp")),
        )
        response = start_attempt(client, prepared)
        assert response.status_code == 503

    def test_i03_34_closed_denies_start(
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
        response = start_attempt(client, prepared)
        assert response.status_code == 409
        assert response.json()["code"] == "assignment_closed_or_cancelled"

    def test_i03_35_future_available_from_denies_start(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(
            runtime_engine, bootstrap_engine, available_from=FUTURE_AVAILABLE
        )
        client = student_client(runtime_engine, prepared)
        response = start_attempt(client, prepared)
        assert response.status_code == 409
        assert response.json()["code"] == "assignment_not_yet_available"

    def test_i03_36_due_past_active_permits_start(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(
            runtime_engine, bootstrap_engine, due_at=PAST_DUE
        )
        client = student_client(runtime_engine, prepared)
        response = start_attempt(client, prepared)
        assert response.status_code == 201

    def test_i03_37_second_fresh_start_while_in_progress_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        first = start_attempt(client, prepared)
        assert first.status_code == 201
        second = start_attempt(client, prepared)
        assert second.status_code == 409
        assert second.json()["code"] == "attempt_in_progress_conflict"

    def test_i03_38_fresh_start_after_submitted_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        from tests.domains.learning.helpers_aieos360_s01_i03 import submit_attempt

        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        again = start_attempt(client, prepared)
        assert again.status_code == 409
        assert again.json()["code"] == "second_attempt_not_authorized"

    def test_i03_39_same_idempotency_key_replays_same_attempt(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        key = f"start-replay-{uuid.uuid7()}"
        first = start_attempt(client, prepared, idempotency_key=key)
        second = start_attempt(client, prepared, idempotency_key=key)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["attempt_id"] == second.json()["attempt_id"]
        assert first.json()["aggregate_revision"] == second.json()["aggregate_revision"]

    def test_i03_40_same_key_different_request_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        from tests.domains.learning.helpers_aieos360_s01_i03 import (
            create_learner_assignment,
            seed_published_learner_content,
        )

        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        content_id, version_id = seed_published_learner_content(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            owner_id=prepared.teacher_id,
        )
        second_assignment = create_learner_assignment(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            principal_id=prepared.teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            idempotency_key=f"create-other-{uuid.uuid7()}",
        )
        client = student_client(runtime_engine, prepared)
        key = f"start-conflict-{uuid.uuid7()}"
        first = start_attempt(client, prepared, idempotency_key=key)
        assert first.status_code == 201
        conflict = start_attempt(
            client,
            prepared,
            idempotency_key=key,
            assignment_id=second_assignment.assignment_id.value,
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_key_reused"
