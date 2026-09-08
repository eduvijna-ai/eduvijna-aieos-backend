"""AIEOS360-S01-I02 — Learning PostgreSQL / RLS / immutability / Alembic tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from aieos.domains.learning.application.errors import (
    AttemptConcurrencyConflict,
    PersistenceInvariantViolation,
    SubmissionImmutable,
)
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
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import set_tenant
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.aieos360_s01_i02

FIXED_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "migrations" / "versions"
LEARNING_TABLES = ("attempts", "attempt_response_items", "submissions")


def _clear_learning(bootstrap_engine: Engine) -> None:
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


@pytest.fixture(autouse=True)
def _clear_learning_rows_after_test(bootstrap_engine: Engine) -> None:
    yield
    _clear_learning(bootstrap_engine)


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


def _rls_flags(engine: Engine, table: str) -> tuple[bool, bool]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'learning' AND c.relname = :table
                """
            ),
            {"table": table},
        ).one()
    return bool(row[0]), bool(row[1])


class TestMigrationAndSchema:
    def test_i02_21_upgrade_from_tosd100001_to_a360s010001(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010001"
        assert EXPECTED_MIGRATION_HEAD == "a360s010001"
        cfg = alembic_config(postgres18["migrator_url"])
        _clear_learning(bootstrap_engine)
        command.downgrade(cfg, "tosd100001")
        try:
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "tosd100001"
                )
                assert (
                    conn.execute(
                        text(
                            """
                            SELECT COUNT(*) FROM information_schema.schemata
                            WHERE schema_name = 'learning'
                            """
                        )
                    ).scalar_one()
                    == 0
                )
            command.upgrade(cfg, "a360s010001")
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010001"
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_i02_22_learning_schema_exists(self, bootstrap_engine: Engine) -> None:
        insp = inspect(bootstrap_engine)
        assert "learning" in insp.get_schema_names()

    def test_i02_23_all_three_required_tables_exist(
        self, bootstrap_engine: Engine
    ) -> None:
        insp = inspect(bootstrap_engine)
        tables = set(insp.get_table_names(schema="learning"))
        assert tables == set(LEARNING_TABLES)

    def test_i02_24_all_three_tables_enable_and_force_rls(
        self, bootstrap_engine: Engine
    ) -> None:
        for table in LEARNING_TABLES:
            enabled, forced = _rls_flags(bootstrap_engine, table)
            assert enabled is True
            assert forced is True


class TestTenantIsolation:
    def test_i02_25_missing_tenant_context_fails_closed(
        self, runtime_engine: Engine
    ) -> None:
        with runtime_engine.connect() as conn:
            with pytest.raises(Exception, match="aieos.tenant_id") as excinfo:
                conn.execute(text("SELECT learning.current_tenant_id()"))
            assert "aieos.tenant_id" in str(excinfo.value)

    def test_i02_26_wrong_tenant_cannot_read_attempt(
        self, runtime_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_a)
        with factory(tenant_a) as uow:
            uow.attempts.insert(created)
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.attempts.get(created.attempt_id) is None

    def test_i02_27_wrong_tenant_cannot_read_response_items(
        self, runtime_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_a)
        item = AttemptResponseItem.multiple_choice(
            attempt_id=created.attempt_id, question_id="q1", choice_value="A"
        )
        saved = created.record_material_response_save(
            last_saved_at=FIXED_NOW + timedelta(seconds=1)
        )
        with factory(tenant_a) as uow:
            uow.attempts.insert(created)
            uow.persist_working_response_save(
                saved, [item], expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.responses.list_for_attempt(created.attempt_id) == []

    def test_i02_28_wrong_tenant_cannot_read_submission(
        self, runtime_engine: Engine
    ) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_a)
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with factory(tenant_a) as uow:
            uow.attempts.insert(created)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_b) as uow:
            assert uow.submissions.get(submission.submission_id) is None
            assert uow.submissions.get_for_attempt(created.attempt_id) is None

    def test_i02_29_same_tenant_round_trip_attempt(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.attempts.get(created.attempt_id)
        assert loaded is not None
        assert loaded.attempt_id == created.attempt_id
        assert loaded.class_ref == "class-5a"
        assert loaded.lifecycle_state is AttemptLifecycleState.IN_PROGRESS
        assert int(loaded.aggregate_revision) == 0

    def test_i02_30_same_tenant_round_trip_response_items(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        items = [
            AttemptResponseItem.multiple_choice(
                attempt_id=created.attempt_id, question_id="q1", choice_value="A"
            ),
            AttemptResponseItem.true_false(
                attempt_id=created.attempt_id, question_id="q2", boolean_value=False
            ),
        ]
        saved = created.record_material_response_save(
            last_saved_at=FIXED_NOW + timedelta(seconds=2)
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_working_response_save(
                saved, items, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.responses.list_for_attempt(created.attempt_id)
            attempt = uow.attempts.get(created.attempt_id)
        assert [item.question_id for item in loaded] == ["q1", "q2"]
        assert attempt is not None
        assert int(attempt.aggregate_revision) == 1
        assert attempt.last_saved_at == FIXED_NOW + timedelta(seconds=2)

    def test_i02_31_same_tenant_round_trip_submission(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        item = AttemptResponseItem.short_answer(
            attempt_id=created.attempt_id, question_id="q1", text_value="ok"
        )
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [item],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=7,
            due_at_at_submit=FIXED_NOW + timedelta(hours=2),
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.responses.replace_for_attempt(
                created, [item], saved_at=FIXED_NOW + timedelta(seconds=1)
            )
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded_attempt = uow.attempts.get(created.attempt_id)
            loaded_submission = uow.submissions.get_for_attempt(created.attempt_id)
        assert loaded_attempt is not None
        assert loaded_submission is not None
        assert loaded_attempt.lifecycle_state is AttemptLifecycleState.SUBMITTED
        assert loaded_attempt.submission_id == loaded_submission.submission_id
        assert loaded_submission.due_at_at_submit == FIXED_NOW + timedelta(hours=2)
        assert loaded_submission.response_snapshot[0]["question_id"] == "q1"


class TestCardinalityAndGuards:
    def test_i02_32_partial_unique_blocks_two_in_progress(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        learner = uuid.uuid7()
        assignment = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        first = _start(
            tenant_id=tenant_id,
            learner_principal_id=learner,
            teaching_assignment_id=assignment,
        )
        second = _start(
            tenant_id=tenant_id,
            learner_principal_id=learner,
            teaching_assignment_id=assignment,
            attempt_number=2,
            started_at=FIXED_NOW + timedelta(seconds=1),
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(first)
            uow.commit()
        with factory(tenant_id) as uow:
            with pytest.raises(PersistenceInvariantViolation):
                uow.attempts.insert(second)
                uow.commit()

    def test_i02_33_persistence_can_represent_later_attempt_number_after_submit(
        self, runtime_engine: Engine
    ) -> None:
        """PERSISTENCE CAPABILITY ONLY — NOT S01 BUSINESS AUTHORIZATION."""
        tenant_id = uuid.uuid7()
        learner = uuid.uuid7()
        assignment = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        first = _start(
            tenant_id=tenant_id,
            learner_principal_id=learner,
            teaching_assignment_id=assignment,
        )
        submitted, submission = transition_in_progress_attempt_to_submitted(
            first,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        later = _start(
            tenant_id=tenant_id,
            learner_principal_id=learner,
            teaching_assignment_id=assignment,
            attempt_number=2,
            started_at=FIXED_NOW + timedelta(minutes=2),
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(first)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=first.aggregate_revision
            )
            uow.attempts.insert(later)
            uow.commit()
        with factory(tenant_id) as uow:
            assert uow.attempts.get(later.attempt_id) is not None
            assert later.attempt_number == 2

    def test_i02_34_duplicate_attempt_number_blocked(
        self, runtime_engine: Engine
    ) -> None:
        """PERSISTENCE CAPABILITY ONLY — NOT S01 BUSINESS AUTHORIZATION."""
        tenant_id = uuid.uuid7()
        learner = uuid.uuid7()
        assignment = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        first = _start(
            tenant_id=tenant_id,
            learner_principal_id=learner,
            teaching_assignment_id=assignment,
        )
        submitted, submission = transition_in_progress_attempt_to_submitted(
            first,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        duplicate_number = _start(
            tenant_id=tenant_id,
            learner_principal_id=learner,
            teaching_assignment_id=assignment,
            attempt_number=1,
            started_at=FIXED_NOW + timedelta(minutes=2),
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(first)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=first.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            with pytest.raises(PersistenceInvariantViolation):
                uow.attempts.insert(duplicate_number)
                uow.commit()

    def test_i02_35_response_item_fk_tenant_consistency_enforced(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            with pytest.raises(DBAPIError):
                uow.connection.execute(
                    text(
                        """
                        INSERT INTO learning.attempt_response_items (
                            tenant_id, attempt_id, question_id, response_kind,
                            choice_value, text_value, boolean_value,
                            created_at, updated_at
                        ) VALUES (
                            :tenant_id, :attempt_id, 'q1', 'MULTIPLE_CHOICE',
                            'A', NULL, NULL, :ts, :ts
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "attempt_id": uuid.uuid7(),
                        "ts": FIXED_NOW,
                    },
                )
                uow.commit()

    def test_i02_36_response_mutation_after_submit_blocked_at_db(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            with pytest.raises(DBAPIError, match="IN_PROGRESS"):
                uow.connection.execute(
                    text(
                        """
                        INSERT INTO learning.attempt_response_items (
                            tenant_id, attempt_id, question_id, response_kind,
                            choice_value, text_value, boolean_value,
                            created_at, updated_at
                        ) VALUES (
                            :tenant_id, :attempt_id, 'q1', 'MULTIPLE_CHOICE',
                            'A', NULL, NULL, :ts, :ts
                        )
                        """
                    ),
                    {
                        "tenant_id": tenant_id,
                        "attempt_id": created.attempt_id.value,
                        "ts": FIXED_NOW + timedelta(minutes=2),
                    },
                )
                uow.commit()

    def test_i02_37_submission_update_blocked_even_privileged(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(DBAPIError, match="submissions is immutable"):
                conn.execute(
                    text(
                        """
                        UPDATE learning.submissions
                           SET class_ref = 'tamper'
                         WHERE submission_id = :sid
                        """
                    ),
                    {"sid": submission.submission_id.value},
                )

    def test_i02_38_submission_delete_blocked_even_privileged(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(DBAPIError, match="submissions is immutable"):
                conn.execute(
                    text(
                        "DELETE FROM learning.submissions WHERE submission_id = :sid"
                    ),
                    {"sid": submission.submission_id.value},
                )
        assert SqlAlchemyLearningUnitOfWorkFactory  # repository has no delete path
        from aieos.domains.learning.infrastructure.persistence.repositories import (
            SqlAlchemyLearnerSubmissionRepository,
        )

        repo = SqlAlchemyLearnerSubmissionRepository
        dummy = object()
        instance = repo.__new__(repo)
        with pytest.raises(SubmissionImmutable):
            instance.delete(dummy)

    def test_i02_39_submitted_attempt_cannot_revert_to_in_progress(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, tenant_id)
            with pytest.raises(DBAPIError, match="SUBMITTED row is terminal"):
                conn.execute(
                    text(
                        """
                        UPDATE learning.attempts
                           SET lifecycle_state = 'IN_PROGRESS',
                               submitted_at = NULL,
                               submission_id = NULL
                         WHERE attempt_id = :aid
                        """
                    ),
                    {"aid": created.attempt_id.value},
                )


class TestConcurrencyAndAtomicity:
    def test_i02_40_optimistic_concurrency_stale_revision_zero_mutation(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        first_item = AttemptResponseItem.multiple_choice(
            attempt_id=created.attempt_id, question_id="q1", choice_value="A"
        )
        stale_item = AttemptResponseItem.multiple_choice(
            attempt_id=created.attempt_id, question_id="q1", choice_value="B"
        )
        first_save = created.record_material_response_save(
            last_saved_at=FIXED_NOW + timedelta(seconds=1)
        )
        stale_save = created.record_material_response_save(
            last_saved_at=FIXED_NOW + timedelta(seconds=2)
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_working_response_save(
                first_save,
                [first_item],
                expected_revision=created.aggregate_revision,
            )
            uow.commit()
        with factory(tenant_id) as uow:
            with pytest.raises(AttemptConcurrencyConflict):
                uow.persist_working_response_save(
                    stale_save,
                    [stale_item],
                    expected_revision=AggregateRevision(0),
                )
            uow.rollback()
        with factory(tenant_id) as uow:
            loaded = uow.attempts.get(created.attempt_id)
            items = uow.responses.list_for_attempt(created.attempt_id)
        assert loaded is not None
        assert int(loaded.aggregate_revision) == 1
        assert items[0].choice_value == "A"

    def test_i02_41_submission_insert_and_attempt_transition_atomic(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=2,
            due_at_at_submit=None,
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.commit()
        with factory(tenant_id) as uow:
            loaded = uow.attempts.get(created.attempt_id)
            evidence = uow.submissions.get(submission.submission_id)
        assert loaded is not None
        assert evidence is not None
        assert loaded.lifecycle_state is AttemptLifecycleState.SUBMITTED
        assert loaded.submission_id == evidence.submission_id
        assert loaded.submitted_at == evidence.submitted_at

    def test_i02_42_rollback_leaves_neither_partial_submission_nor_submitted_state(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        submitted, submission = transition_in_progress_attempt_to_submitted(
            created,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.commit()
        with factory(tenant_id) as uow:
            uow.persist_pure_submit_transition(
                submitted, submission, expected_revision=created.aggregate_revision
            )
            uow.rollback()
        with factory(tenant_id) as uow:
            loaded = uow.attempts.get(created.attempt_id)
            evidence = uow.submissions.get(submission.submission_id)
        assert loaded is not None
        assert loaded.lifecycle_state is AttemptLifecycleState.IN_PROGRESS
        assert loaded.submission_id is None
        assert evidence is None


class TestDowngradeSafety:
    def test_i02_43_downgrade_empty_schema_succeeds(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        _clear_learning(bootstrap_engine)
        command.downgrade(cfg, "tosd100001")
        try:
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "tosd100001"
                )
                assert (
                    conn.execute(
                        text(
                            """
                            SELECT COUNT(*) FROM information_schema.schemata
                            WHERE schema_name = 'learning'
                            """
                        )
                    ).scalar_one()
                    == 0
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_i02_44_downgrade_with_learning_evidence_fails_closed(
        self, postgres18, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            uow.attempts.insert(created)
            uow.commit()
        try:
            with pytest.raises(Exception) as exc:
                command.downgrade(cfg, "tosd100001")
            message = str(exc.value)
            cause = exc.value.__cause__
            if cause is not None:
                message = f"{message} {cause}"
            assert "Learning attempt/submission evidence exists" in message
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010001"
                )
            with factory(tenant_id) as uow:
                assert uow.attempts.get(created.attempt_id) is not None
        finally:
            _clear_learning(bootstrap_engine)
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)


class TestConnectionComposability:
    def test_repositories_accept_uow_connection(self, runtime_engine: Engine) -> None:
        tenant_id = uuid.uuid7()
        factory = SqlAlchemyLearningUnitOfWorkFactory(runtime_engine)
        created = _start(tenant_id=tenant_id)
        with factory(tenant_id) as uow:
            assert uow.connection is not None
            uow.attempts.insert(created)
            uow.commit()
        assert not hasattr(uow.attempts, "engine")
