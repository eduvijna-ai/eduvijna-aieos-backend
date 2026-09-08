"""AIEOS360-S01-I03R1 — established idempotent replay after authority change."""

from __future__ import annotations

import uuid

import pytest

from aieos.development.learner_principals import STUDENT_B_PRINCIPAL_ID
from aieos.platform.events.constants import (
    EVENT_LEARNING_ATTEMPT_STARTED_V1,
    EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    build_student_client,
    clear_i03_side_effects_after_test,
    fetch_attempts,
    fetch_audit,
    fetch_outbox,
    fetch_submissions,
    prepare_class_5a_assignment,
    save_responses,
    start_attempt,
    student_client,
    submit_attempt,
    valid_mc_write,
    valid_short_write,
)

pytestmark = [pytest.mark.aieos360_s01_i03, pytest.mark.aieos360_s01_i03r1]


def _started(runtime_engine, bootstrap_engine):
    prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
    client = student_client(runtime_engine, prepared)
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    return prepared, client, attempt_id, started.headers["ETag"]


class TestEstablishedReplay:
    def test_r1_01_start_replay_after_membership_removal(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        key = f"start-replay-removed-{uuid.uuid7()}"
        first = start_attempt(client, prepared, idempotency_key=key)
        assert first.status_code == 201, first.text
        attempt_id = first.json()["attempt_id"]
        prepared.membership._memberships[prepared.student_id] = ()
        replayed = start_attempt(client, prepared, idempotency_key=key)
        assert replayed.status_code == 201, replayed.text
        assert replayed.json()["attempt_id"] == attempt_id
        assert replayed.json()["aggregate_revision"] == first.json()["aggregate_revision"]
        attempts = fetch_attempts(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            assignment_id=prepared.assignment_id,
        )
        assert len(attempts) == 1
        started_events = fetch_outbox(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            event_type=EVENT_LEARNING_ATTEMPT_STARTED_V1,
        )
        assert len(started_events) == 1
        audits = fetch_audit(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            action="learning.attempt.start",
        )
        assert len(audits) == 1
        denied = start_attempt(client, prepared)
        assert denied.status_code == 404

    def test_r1_02_save_replay_after_membership_removal(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        key = f"save-replay-removed-{uuid.uuid7()}"
        writes = [valid_mc_write()]
        first = save_responses(
            client, prepared, attempt_id, writes, if_match=match, idempotency_key=key
        )
        assert first.status_code == 200, first.text
        revision = first.json()["aggregate_revision"]
        prepared.membership._memberships[prepared.student_id] = ()
        replayed = save_responses(
            client, prepared, attempt_id, writes, if_match=match, idempotency_key=key
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["aggregate_revision"] == revision
        audits = fetch_audit(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            action="learning.attempt.save_responses",
            resource_id=attempt_id,
        )
        assert len(audits) == 1
        denied = save_responses(
            client, prepared, attempt_id, writes, if_match=match
        )
        assert denied.status_code == 404

    def test_r1_03_submit_replay_after_membership_removal(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        key = f"submit-replay-removed-{uuid.uuid7()}"
        first = submit_attempt(
            client, prepared, attempt_id, if_match=match, idempotency_key=key
        )
        assert first.status_code == 200, first.text
        submission_id = first.json()["submission_id"]
        prepared.membership._memberships[prepared.student_id] = ()
        replayed = submit_attempt(
            client, prepared, attempt_id, if_match=match, idempotency_key=key
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["submission_id"] == submission_id
        assert len(fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )) == 1
        submitted_events = fetch_outbox(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            event_type=EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
            aggregate_id=attempt_id,
        )
        assert len(submitted_events) == 1
        audits = fetch_audit(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            action="learning.attempt.submit",
            resource_id=attempt_id,
        )
        assert len(audits) == 1

    def test_r1_04_same_key_different_material_still_conflicts(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        key = f"save-conflict-r1-{uuid.uuid7()}"
        first = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write()],
            if_match=match,
            idempotency_key=key,
        )
        assert first.status_code == 200
        prepared.membership._memberships[prepared.student_id] = ()
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

    def test_r1_05_another_learner_cannot_replay_outcome(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        key = f"start-other-learner-{uuid.uuid7()}"
        first = start_attempt(client, prepared, idempotency_key=key)
        assert first.status_code == 201, first.text
        owner_attempt = first.json()["attempt_id"]
        other = build_student_client(
            runtime_engine, prepared.tenant_id, STUDENT_B_PRINCIPAL_ID
        )
        stolen_start = start_attempt(other, prepared, idempotency_key=key)
        assert stolen_start.status_code == 404
        stolen_save = save_responses(
            other,
            prepared,
            uuid.UUID(owner_attempt),
            [valid_mc_write()],
            if_match=first.headers["ETag"],
            idempotency_key=key,
        )
        assert stolen_save.status_code == 404
        attempts = fetch_attempts(
            bootstrap_engine,
            tenant_id=prepared.tenant_id,
            assignment_id=prepared.assignment_id,
        )
        assert [str(row["attempt_id"]) for row in attempts] == [owner_attempt]
        assert all(
            row["learner_principal_id"] == prepared.student_id for row in attempts
        )
