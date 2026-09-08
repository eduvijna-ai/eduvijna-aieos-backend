"""AIEOS360-S01-I02 — concurrent stale-save and submit-vs-save proofs.

Does not claim TeachingAssignment cancel/submit cross-domain serialization.
That belongs to S01-I03.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.learning.application.errors import AttemptConcurrencyConflict
from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.identities import AggregateRevision
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.response_item import AttemptResponseItem
from aieos.domains.learning.domain.submit import (
    transition_in_progress_attempt_to_submitted,
)
from aieos.domains.learning.infrastructure.persistence.uow import (
    SqlAlchemyLearningUnitOfWorkFactory,
)

pytestmark = pytest.mark.aieos360_s01_i02

FIXED_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
LEARNING_TABLES = ("attempts", "attempt_response_items", "submissions")


@pytest.fixture(autouse=True)
def _clear_learning_rows_after_test(bootstrap_engine: Engine) -> None:
    yield
    with bootstrap_engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'learning' AND table_name = 'attempts'
                )
                """
            )
        ).scalar()
        if not exists:
            return
        conn.execute(
            text(
                "ALTER TABLE learning.attempt_response_items "
                "DISABLE TRIGGER learning_attempt_response_items_in_progress_delete"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE learning.submissions "
                "DISABLE TRIGGER learning_submissions_immutable_delete"
            )
        )
        for table in LEARNING_TABLES:
            conn.execute(text(f"ALTER TABLE learning.{table} DISABLE ROW LEVEL SECURITY"))
        conn.execute(text("DELETE FROM learning.attempt_response_items"))
        conn.execute(text("DELETE FROM learning.submissions"))
        conn.execute(text("DELETE FROM learning.attempts"))
        conn.execute(
            text(
                "ALTER TABLE learning.attempt_response_items "
                "ENABLE TRIGGER learning_attempt_response_items_in_progress_delete"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE learning.submissions "
                "ENABLE TRIGGER learning_submissions_immutable_delete"
            )
        )
        for table in LEARNING_TABLES:
            conn.execute(text(f"ALTER TABLE learning.{table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(
                text(f"ALTER TABLE learning.{table} FORCE ROW LEVEL SECURITY")
            )


def _start(**overrides) -> LearnerAttempt:
    values = {
        "tenant_id": uuid.uuid7(),
        "learner_principal_id": uuid.uuid7(),
        "teaching_assignment_id": uuid.uuid7(),
        "content_id": uuid.uuid7(),
        "content_version_id": uuid.uuid7(),
        "class_ref": "class-5a",
        "started_at": FIXED_NOW,
    }
    values.update(overrides)
    return LearnerAttempt.start_in_progress(**values)


class TestConcurrentStaleSave:
    def test_two_writers_same_revision_one_winner_no_lost_update(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.commit()

        def _save(choice: str, offset: int) -> str:
            item = AttemptResponseItem.multiple_choice(
                attempt_id=created.attempt_id,
                question_id="q1",
                choice_value=choice,
            )
            updated = created.record_material_response_save(
                last_saved_at=FIXED_NOW + timedelta(seconds=offset)
            )
            try:
                with factory(tenant_id) as uow:
                    uow.persist_working_response_save(
                        updated,
                        [item],
                        expected_revision=AggregateRevision(0),
                    )
                    uow.commit()
                return "ok"
            except AttemptConcurrencyConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(_save, "A", 1)
            second = pool.submit(_save, "B", 2)
            results = sorted([first.result(), second.result()])
        assert results == ["conflict", "ok"]
        with factory(tenant_id) as uow:
            loaded = uow.attempts.get(created.attempt_id)
            items = uow.responses.list_for_attempt(created.attempt_id)
        assert loaded is not None
        assert int(loaded.aggregate_revision) == 1
        assert loaded.lifecycle_state is AttemptLifecycleState.IN_PROGRESS
        assert len(items) == 1
        assert items[0].choice_value in {"A", "B"}

    def test_submit_then_stale_save_cannot_mutate_submitted_evidence(
        self, runtime_engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        original = AttemptResponseItem.short_answer(
            attempt_id=created.attempt_id,
            question_id="q1",
            text_value="original",
        )
        saved = created.record_material_response_save(
            last_saved_at=FIXED_NOW + timedelta(seconds=1)
        )
        submitted, submission = transition_in_progress_attempt_to_submitted(
            saved,
            [original],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_working_response_save(
                saved, [original], expected_revision=created.aggregate_revision
            )
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=saved.aggregate_revision
            )
            uow.commit()
        stale = AttemptResponseItem.short_answer(
            attempt_id=created.attempt_id,
            question_id="q1",
            text_value="tamper",
        )
        stale_attempt = created.record_material_response_save(
            last_saved_at=FIXED_NOW + timedelta(minutes=2)
        )
        with factory(tenant_id) as uow:
            with pytest.raises(AttemptConcurrencyConflict):
                uow.persist_working_response_save(
                    stale_attempt,
                    [stale],
                    expected_revision=AggregateRevision(0),
                )
            uow.rollback()
        with factory(tenant_id) as uow:
            loaded = uow.attempts.get(created.attempt_id)
            items = uow.responses.list_for_attempt(created.attempt_id)
            evidence = uow.submissions.get_for_attempt(created.attempt_id)
        assert loaded is not None
        assert loaded.lifecycle_state is AttemptLifecycleState.SUBMITTED
        assert evidence is not None
        assert evidence.response_snapshot[0]["value"] == "original"
        assert items[0].text_value == "original"
