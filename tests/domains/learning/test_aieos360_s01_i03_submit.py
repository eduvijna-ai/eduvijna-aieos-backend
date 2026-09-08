"""AIEOS360-S01-I03 — submit, outbox/audit, historical reads (I03-57..I03-72, 78-92)."""

from __future__ import annotations

import json
import uuid

import pytest

from aieos.development.learner_principals import STUDENT_B_PRINCIPAL_ID
from aieos.platform.events.constants import (
    EVENT_LEARNING_ATTEMPT_STARTED_V1,
    EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ATTEMPT_PATH,
    PAST_DUE,
    OnceThenDeniedMembershipReader,
    RaisingMembershipReader,
    build_student_client,
    clear_i03_side_effects_after_test,
    close_assignment,
    fetch_audit,
    fetch_outbox,
    fetch_responses,
    fetch_submissions,
    prepare_class_5a_assignment,
    save_responses,
    start_attempt,
    student_client,
    submit_attempt,
    valid_mc_write,
    valid_short_write,
)

pytestmark = pytest.mark.aieos360_s01_i03

_FORBIDDEN_FACT_KEYS = (
    "answer",
    "explanation",
    "score",
    "grade",
    "mastery",
    "correct",
    "correctness",
    "responses",
    "choice_value",
    "text_value",
    "boolean_value",
)


def _started(runtime_engine, bootstrap_engine, **kwargs):
    prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine, **kwargs)
    client = student_client(runtime_engine, prepared)
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    return prepared, client, attempt_id, started


class TestSubmitAttempt:
    def test_i03_57_through_63_valid_submit_creates_immutable_evidence(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine, due_at=PAST_DUE
        )
        saved = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write(), valid_short_write()],
            if_match=started.headers["ETag"],
        )
        assert saved.status_code == 200, saved.text
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=saved.headers["ETag"]
        )
        assert submitted.status_code == 200, submitted.text
        body = submitted.json()
        assert body["lifecycle_state"] == "SUBMITTED"
        assert body["submission_id"] is not None
        rows = fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )
        assert len(rows) == 1
        row = rows[0]
        assert str(row["attempt_id"]) == str(attempt_id)
        assert str(row["teaching_assignment_id"]) == str(prepared.assignment_id)
        assert str(row["learner_principal_id"]) == str(prepared.student_id)
        assert str(row["content_id"]) == str(prepared.content_id)
        assert str(row["content_version_id"]) == str(prepared.content_version_id)
        assert int(row["assignment_revision_at_submit"]) == int(
            prepared.assignment.aggregate_revision
        )
        assert row["due_at_at_submit"] is not None
        blob = json.dumps(row["response_snapshot"], default=str).lower()
        for forbidden in ("score", "grade", "mastery"):
            assert forbidden not in blob
            assert forbidden not in json.dumps(body).lower()

    def test_i03_62_due_past_active_submit_succeeds(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine, due_at=PAST_DUE
        )
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submitted.status_code == 200

    def test_i03_64_second_submit_fresh_command_rejected(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        first = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert first.status_code == 200
        second = submit_attempt(
            client, prepared, attempt_id, if_match=first.headers["ETag"]
        )
        assert second.status_code == 409
        assert second.json()["code"] == "attempt_already_submitted"

    def test_i03_65_66_67_idempotent_submit_replay_no_duplicate_facts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        key = f"submit-replay-{uuid.uuid7()}"
        first = submit_attempt(
            client,
            prepared,
            attempt_id,
            if_match=started.headers["ETag"],
            idempotency_key=key,
        )
        second = submit_attempt(
            client,
            prepared,
            attempt_id,
            if_match=started.headers["ETag"],
            idempotency_key=key,
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["submission_id"] == second.json()["submission_id"]
        started_events = fetch_outbox(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            event_type=EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
            aggregate_id=attempt_id,
        )
        assert len(started_events) == 1
        audits = fetch_audit(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            action="learning.attempt.submit",
            resource_id=attempt_id,
        )
        assert len(audits) == 1

    def test_i03_68_stale_if_match_is_412(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        saved = save_responses(
            client, prepared, attempt_id, [valid_mc_write()], if_match=started.headers["ETag"]
        )
        assert saved.status_code == 200
        stale = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert stale.status_code == 412

    def test_i03_69_another_learner_cannot_submit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        other = build_student_client(
            runtime_engine, prepared.tenant_id, STUDENT_B_PRINCIPAL_ID
        )
        response = submit_attempt(
            other, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert response.status_code == 404
        assert response.json()["code"] == "attempt_not_found"

    def test_i03_70_membership_unavailable_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        denied = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.student_id,
            learner_membership_reader=RaisingMembershipReader(RuntimeError("erp")),
        )
        response = submit_attempt(
            denied, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert response.status_code == 503

    def test_i03_71_response_snapshot_equals_persisted_working_responses(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        saved = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write(), valid_short_write()],
            if_match=started.headers["ETag"],
        )
        assert saved.status_code == 200
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=saved.headers["ETag"]
        )
        assert submitted.status_code == 200
        working = fetch_responses(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )
        snapshot = fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )[0]["response_snapshot"]
        by_id = {item["question_id"]: item for item in snapshot}
        assert set(by_id) == {row["question_id"] for row in working}

    def test_i03_72_incomplete_empty_responses_are_not_auto_filled(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submitted.status_code == 200
        assert submitted.json()["responses"] == []
        snapshot = fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )[0]["response_snapshot"]
        assert snapshot == [] or snapshot == ()


class TestOutboxAndAudit:
    def test_i03_78_through_87_outbox_and_audit_facts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        save_key = f"save-{uuid.uuid7()}"
        saved = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=started.headers["ETag"],
            idempotency_key=save_key,
        )
        assert saved.status_code == 200
        replay_save = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=started.headers["ETag"],
            idempotency_key=save_key,
        )
        assert replay_save.status_code == 200
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=saved.headers["ETag"]
        )
        assert submitted.status_code == 200
        started_events = fetch_outbox(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            event_type=EVENT_LEARNING_ATTEMPT_STARTED_V1,
        )
        submitted_events = fetch_outbox(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            event_type=EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
        )
        assert len(started_events) == 1
        assert len(submitted_events) == 1
        all_events = fetch_outbox(bootstrap_engine, tenant_id=prepared.tenant_id)
        learning_types = {
            row["event_type"]
            for row in all_events
            if str(row["event_type"]).startswith("io.eduvijna.aieos.learning.")
        }
        assert learning_types == {
            EVENT_LEARNING_ATTEMPT_STARTED_V1,
            EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
        }
        envelope_blob = json.dumps(
            [row["envelope"] for row in started_events + submitted_events],
            default=str,
        ).lower()
        for forbidden in _FORBIDDEN_FACT_KEYS:
            assert forbidden not in envelope_blob
        start_audits = fetch_audit(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            action="learning.attempt.start",
            resource_id=attempt_id,
        )
        save_audits = fetch_audit(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            action="learning.attempt.save_responses",
            resource_id=attempt_id,
        )
        submit_audits = fetch_audit(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            action="learning.attempt.submit",
            resource_id=attempt_id,
        )
        assert len(start_audits) == 1
        assert len(save_audits) == 1
        assert len(submit_audits) == 1
        audit_blob = json.dumps(start_audits + save_audits + submit_audits, default=str)
        assert "1/2" not in audit_blob
        assert start_audits[0]["executing_principal_id"] == prepared.student_id
        assert start_audits[0]["effective_actor_id"] == prepared.student_id


class TestHistoricalReads:
    def test_i03_89_submitted_readable_after_close(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submitted.status_code == 200
        close_assignment(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=prepared.assignment,
            idempotency_key=f"close-{uuid.uuid7()}",
        )
        loaded = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert loaded.status_code == 200
        assert loaded.json()["lifecycle_state"] == "SUBMITTED"

    def test_i03_90_submitted_readable_after_membership_removal(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submitted.status_code == 200
        prepared.membership._memberships[prepared.student_id] = ()
        loaded = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert loaded.status_code == 200

    def test_i03_91_different_learner_cannot_read_submitted_evidence(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submitted.status_code == 200
        other = build_student_client(
            runtime_engine, prepared.tenant_id, STUDENT_B_PRINCIPAL_ID
        )
        loaded = other.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert loaded.status_code == 404
        assert loaded.json()["code"] == "attempt_not_found"

    def test_i03_92_in_progress_after_membership_removal_fails_closed(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, started = _started(
            runtime_engine, bootstrap_engine
        )
        prepared.membership._memberships[prepared.student_id] = ()
        loaded = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers={"X-AIEOS-Tenant-ID": str(prepared.tenant_id)},
        )
        assert loaded.status_code == 404
        saved = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=started.headers["ETag"],
        )
        assert saved.status_code == 404
        submitted = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submitted.status_code == 404
        remaining = fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )
        assert remaining == []

    def test_i03_77_membership_race_already_authorized_command_may_commit(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        """No atomic ERP↔AIEOS ordering and no 2PC are claimed.

        The first observed membership check may authorize a local command that
        still commits after the external authority later denies the learner.
        The next fresh command observes the denial.
        """
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        reader = OnceThenDeniedMembershipReader(prepared.tenant_id)
        client = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.student_id,
            learner_membership_reader=reader,
        )
        started = start_attempt(client, prepared)
        assert started.status_code == 201, started.text
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        denied = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=started.headers["ETag"],
        )
        assert denied.status_code == 404
        assert reader.calls >= 2
