"""AIEOS360-S01-I03 — save responses (I03-41..I03-56)."""

from __future__ import annotations

import uuid

import pytest

from aieos.development.learner_principals import STUDENT_B_PRINCIPAL_ID
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ATTEMPT_PATH,
    build_student_client,
    clear_i03_side_effects_after_test,
    close_assignment,
    etag,
    fetch_responses,
    prepare_class_5a_assignment,
    save_responses,
    start_attempt,
    student_client,
    submit_attempt,
    valid_mc_write,
    valid_short_write,
    valid_tf_write,
)

pytestmark = pytest.mark.aieos360_s01_i03


def _started(runtime_engine, bootstrap_engine):
    prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
    client = student_client(runtime_engine, prepared)
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    return prepared, client, attempt_id, started.headers["ETag"]


class TestSaveResponses:
    def test_i03_41_owner_can_save_valid_multiple_choice(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        response = save_responses(
            client, prepared, attempt_id, [valid_mc_write()], if_match=match
        )
        assert response.status_code == 200, response.text
        items = response.json()["responses"]
        assert items[0]["question_id"] == "q-1"
        assert items[0]["response_kind"] == "MULTIPLE_CHOICE"
        assert items[0]["choice_value"] == "1/2"

    def test_i03_42_owner_can_save_short_answer(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        response = save_responses(
            client, prepared, attempt_id, [valid_short_write()], if_match=match
        )
        assert response.status_code == 200, response.text
        assert response.json()["responses"][0]["text_value"] == "1/2"

    def test_i03_43_owner_can_save_true_false(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        response = save_responses(
            client, prepared, attempt_id, [valid_tf_write()], if_match=match
        )
        assert response.status_code == 200, response.text
        assert response.json()["responses"][0]["boolean_value"] is True

    def test_i03_44_wrong_question_id_rejected(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        write = valid_mc_write()
        write["question_id"] = "q-missing"
        response = save_responses(
            client, prepared, attempt_id, [write], if_match=match
        )
        assert response.status_code == 422
        assert response.json()["code"] == "response_validation_failed"

    def test_i03_45_response_kind_mismatch_rejected(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        write = valid_short_write()
        write["question_id"] = "q-1"
        response = save_responses(
            client, prepared, attempt_id, [write], if_match=match
        )
        assert response.status_code == 422

    def test_i03_46_mc_option_not_in_learner_resource_rejected(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        write = valid_mc_write()
        write["choice_value"] = "not-an-option"
        response = save_responses(
            client, prepared, attempt_id, [write], if_match=match
        )
        assert response.status_code == 422

    def test_i03_47_duplicate_question_rejected(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        response = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write(), valid_mc_write()],
            if_match=match,
        )
        assert response.status_code == 422

    def test_i03_48_save_increments_aggregate_revision_once(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        response = save_responses(
            client, prepared, attempt_id, [valid_mc_write()], if_match=match
        )
        assert response.status_code == 200
        assert response.json()["aggregate_revision"] == 1
        assert response.headers["ETag"] == etag(1)

    def test_i03_49_stale_if_match_is_412(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        first = save_responses(
            client, prepared, attempt_id, [valid_mc_write()], if_match=match
        )
        assert first.status_code == 200
        stale = save_responses(
            client, prepared, attempt_id, [valid_short_write()], if_match=match
        )
        assert stale.status_code == 412

    def test_i03_50_another_learner_cannot_save(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        other = build_student_client(
            runtime_engine, prepared.tenant_id, STUDENT_B_PRINCIPAL_ID
        )
        response = save_responses(
            other, prepared, attempt_id, [valid_mc_write()], if_match=match
        )
        assert response.status_code == 404
        assert response.json()["code"] == "attempt_not_found"

    def test_i03_51_membership_revoked_denies_save(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        prepared.membership._memberships[prepared.student_id] = ()
        response = save_responses(
            client, prepared, attempt_id, [valid_mc_write()], if_match=match
        )
        assert response.status_code == 404

    def test_i03_52_assignment_closed_denies_save(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        close_assignment(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=prepared.assignment,
            idempotency_key=f"close-{uuid.uuid7()}",
        )
        response = save_responses(
            client, prepared, attempt_id, [valid_mc_write()], if_match=match
        )
        assert response.status_code == 409
        assert response.json()["code"] == "assignment_closed_or_cancelled"

    def test_i03_53_submitted_attempt_cannot_save(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        submitted = submit_attempt(client, prepared, attempt_id, if_match=match)
        assert submitted.status_code == 200, submitted.text
        response = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=submitted.headers["ETag"],
        )
        assert response.status_code == 409
        assert response.json()["code"] == "attempt_already_submitted"

    def test_i03_54_exact_save_idempotency_replay_no_second_revision(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        key = f"save-replay-{uuid.uuid7()}"
        first = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=match,
            idempotency_key=key,
        )
        second = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=match,
            idempotency_key=key,
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["aggregate_revision"] == 1
        assert second.json()["aggregate_revision"] == 1

    def test_i03_55_same_key_different_payload_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        key = f"save-conflict-{uuid.uuid7()}"
        first = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=match,
            idempotency_key=key,
        )
        assert first.status_code == 200
        conflict = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_short_write()],
            if_match=match,
            idempotency_key=key,
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_key_reused"

    def test_i03_56_put_replacement_removes_omitted_rows(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        first = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write(), valid_short_write()],
            if_match=match,
        )
        assert first.status_code == 200
        second = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_tf_write()],
            if_match=first.headers["ETag"],
        )
        assert second.status_code == 200
        body = second.json()["responses"]
        assert [item["question_id"] for item in body] == ["q-3"]
        rows = fetch_responses(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )
        assert [row["question_id"] for row in rows] == ["q-3"]
        got = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert [item["question_id"] for item in got.json()["responses"]] == ["q-3"]
