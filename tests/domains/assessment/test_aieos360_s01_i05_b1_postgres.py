"""AIEOS360-S01-I05-B1 — LearnerAssessmentEvaluation PostgreSQL / RLS / Alembic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from aieos.domains.assessment.domain.evaluation import LearnerAssessmentEvaluation
from aieos.domains.assessment.domain.evaluation_input import (
    EvaluationContentQuestion,
    EvaluationSubmittedResponse,
    LearnerAssessmentEvaluationRequest,
)
from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    DeterministicLearnerAssessmentEvaluatorV1,
)
from aieos.domains.assessment.domain.evaluation_vocabulary import ItemOutcome
from aieos.domains.assessment.infrastructure.persistence.models import (
    learner_assessment_evaluation_items_table,
    learner_assessment_evaluations_table,
    learner_assessment_objective_evidence_table,
)
from aieos.domains.assessment.infrastructure.persistence.repositories import (
    SqlAlchemyLearnerAssessmentEvaluationRepository,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD
from tests.conftest import alembic_config, provision_runtime_grants
from tests.dbutil import set_tenant
from tools.release.common import EXPECTED_MIGRATION_HEAD

pytestmark = pytest.mark.aieos360_s01_i05_b1

FIXED_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION = (
    REPO_ROOT
    / "migrations"
    / "versions"
    / "a360s010003_learner_assessment_evaluation.py"
)
EVALUATION_TABLES = (
    "learner_assessment_objective_evidence",
    "learner_assessment_evaluation_items",
    "learner_assessment_evaluations",
)
EVALUATOR = DeterministicLearnerAssessmentEvaluatorV1()


def _clear_evaluations(bootstrap_engine: Engine) -> None:
    with bootstrap_engine.begin() as conn:
        exists = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'assessment'
                      AND table_name = 'learner_assessment_evaluations'
                )
                """
            )
        ).scalar()
        if not exists:
            return
        for table, trigger in (
            (
                "learner_assessment_objective_evidence",
                "learner_assessment_objective_evidence_immutable_delete",
            ),
            (
                "learner_assessment_evaluation_items",
                "learner_assessment_evaluation_items_immutable_delete",
            ),
            (
                "learner_assessment_evaluations",
                "learner_assessment_evaluations_immutable_delete",
            ),
        ):
            conn.execute(
                text(f"ALTER TABLE assessment.{table} DISABLE TRIGGER {trigger}")
            )
            conn.execute(
                text(f"ALTER TABLE assessment.{table} DISABLE ROW LEVEL SECURITY")
            )
        conn.execute(text("DELETE FROM assessment.learner_assessment_objective_evidence"))
        conn.execute(text("DELETE FROM assessment.learner_assessment_evaluation_items"))
        conn.execute(text("DELETE FROM assessment.learner_assessment_evaluations"))
        for table, trigger in (
            (
                "learner_assessment_objective_evidence",
                "learner_assessment_objective_evidence_immutable_delete",
            ),
            (
                "learner_assessment_evaluation_items",
                "learner_assessment_evaluation_items_immutable_delete",
            ),
            (
                "learner_assessment_evaluations",
                "learner_assessment_evaluations_immutable_delete",
            ),
        ):
            conn.execute(
                text(f"ALTER TABLE assessment.{table} ENABLE TRIGGER {trigger}")
            )
            conn.execute(
                text(f"ALTER TABLE assessment.{table} ENABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text(f"ALTER TABLE assessment.{table} FORCE ROW LEVEL SECURITY")
            )


@pytest.fixture(autouse=True)
def _clear_evaluation_rows_after_test(bootstrap_engine: Engine) -> None:
    yield
    _clear_evaluations(bootstrap_engine)


def _evaluation(**overrides) -> LearnerAssessmentEvaluation:
    tenant_id = overrides.pop("tenant_id", uuid.uuid7())
    questions = overrides.pop(
        "questions",
        (
            EvaluationContentQuestion(
                question_id="q1",
                question_type="multiple_choice",
                options=("A", "B", "C"),
                answer="A",
                objective_ids=("obj-1",),
            ),
            EvaluationContentQuestion(
                question_id="q2",
                question_type="true_false",
                options=(),
                answer="true",
                objective_ids=("obj-1",),
            ),
        ),
    )
    responses = overrides.pop(
        "responses",
        (
            EvaluationSubmittedResponse(
                question_id="q1",
                response_kind="MULTIPLE_CHOICE",
                value="A",
            ),
        ),
    )
    policy_id = overrides.pop(
        "evaluation_policy_id", DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID
    )
    policy_version = overrides.pop("evaluation_policy_version", 1)
    request = LearnerAssessmentEvaluationRequest(
        tenant_id=tenant_id,
        learner_principal_id=overrides.pop("learner_principal_id", uuid.uuid7()),
        submission_id=overrides.pop("submission_id", uuid.uuid7()),
        attempt_id=overrides.pop("attempt_id", uuid.uuid7()),
        teaching_assignment_id=overrides.pop("teaching_assignment_id", uuid.uuid7()),
        content_id=overrides.pop("content_id", uuid.uuid7()),
        content_version_id=overrides.pop("content_version_id", uuid.uuid7()),
        class_ref=overrides.pop("class_ref", "class-5a"),
        questions=questions,
        responses=responses,
        evaluated_at=overrides.pop("evaluated_at", FIXED_NOW),
    )
    evaluation = EVALUATOR.evaluate(request)
    if policy_id != evaluation.evaluation_policy_id or policy_version != 1:
        return LearnerAssessmentEvaluation.issue(
            tenant_id=evaluation.tenant_id,
            learner_principal_id=evaluation.learner_principal_id,
            submission_id=evaluation.submission_id,
            attempt_id=evaluation.attempt_id,
            teaching_assignment_id=evaluation.teaching_assignment_id,
            content_id=evaluation.content_id,
            content_version_id=evaluation.content_version_id,
            class_ref=evaluation.class_ref,
            evaluation_policy_id=policy_id,
            evaluation_policy_version=policy_version,
            evaluated_at=evaluation.evaluated_at,
            items=evaluation.items,
            objective_evidence=evaluation.objective_evidence,
            evaluation_id=evaluation.evaluation_id,
        )
    return evaluation


class TestMigrationAndShape:
    def test_33_upgrade_from_a360s010002(
        self, postgres18, bootstrap_engine: Engine
    ) -> None:
        cfg = alembic_config(postgres18["migrator_url"])
        _clear_evaluations(bootstrap_engine)
        command.downgrade(cfg, "a360s010002")
        try:
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010002"
                )
                assert (
                    conn.execute(
                        text(
                            """
                            SELECT COUNT(*) FROM information_schema.tables
                            WHERE table_schema = 'assessment'
                              AND table_name = 'learner_assessment_evaluations'
                            """
                        )
                    ).scalar_one()
                    == 0
                )
            command.upgrade(cfg, "a360s010003")
            with bootstrap_engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == "a360s010003"
                )
        finally:
            command.upgrade(cfg, "head")
            provision_runtime_grants(bootstrap_engine)

    def test_34_resulting_head_is_a360s010003(self, bootstrap_engine: Engine) -> None:
        assert EXPECTED_ALEMBIC_HEAD == "a360s010004"
        assert EXPECTED_MIGRATION_HEAD == "a360s010004"
        with bootstrap_engine.connect() as conn:
            assert (
                conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "a360s010004"
            )
        text_sql = MIGRATION.read_text(encoding="utf-8")
        assert 'revision: str = "a360s010003"' in text_sql
        assert 'down_revision: str | None = "a360s010002"' in text_sql

    def test_physical_table_names(self, bootstrap_engine: Engine) -> None:
        insp = inspect(bootstrap_engine)
        tables = set(insp.get_table_names(schema="assessment"))
        assert "learner_assessment_evaluations" in tables
        assert "learner_assessment_evaluation_items" in tables
        assert "learner_assessment_objective_evidence" in tables
        assert "aggregate_revision" not in {
            col["name"]
            for col in insp.get_columns(
                "learner_assessment_evaluations", schema="assessment"
            )
        }
        assert "aggregate_revision" not in {
            col.name for col in learner_assessment_evaluations_table.columns
        }
        model_tables = {
            learner_assessment_evaluations_table.name,
            learner_assessment_evaluation_items_table.name,
            learner_assessment_objective_evidence_table.name,
        }
        assert model_tables <= tables

    def test_26_exact_business_uniqueness_constraint(
        self, bootstrap_engine: Engine
    ) -> None:
        with bootstrap_engine.connect() as conn:
            name = conn.execute(
                text(
                    """
                    SELECT conname
                      FROM pg_constraint
                     WHERE conrelid =
                           'assessment.learner_assessment_evaluations'::regclass
                       AND contype = 'u'
                       AND conname =
                           'uq_assessment_learner_assessment_evaluations_business_identity'
                    """
                )
            ).scalar_one()
            cols = conn.execute(
                text(
                    """
                    SELECT a.attname
                      FROM pg_constraint c
                      JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                        ON true
                      JOIN pg_attribute a
                        ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                     WHERE c.conname =
                           'uq_assessment_learner_assessment_evaluations_business_identity'
                     ORDER BY k.ord
                    """
                )
            ).scalars().all()
        assert name == (
            "uq_assessment_learner_assessment_evaluations_business_identity"
        )
        assert cols == [
            "tenant_id",
            "submission_id",
            "evaluation_policy_id",
            "evaluation_policy_version",
        ]

    def test_30_no_cross_domain_foreign_keys(self, bootstrap_engine: Engine) -> None:
        with bootstrap_engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT nf.nspname
                      FROM pg_constraint c
                      JOIN pg_class rel ON rel.oid = c.conrelid
                      JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
                      JOIN pg_class frel ON frel.oid = c.confrelid
                      JOIN pg_namespace nf ON nf.oid = frel.relnamespace
                     WHERE c.contype = 'f'
                       AND nsp.nspname = 'assessment'
                       AND rel.relname LIKE 'learner_assessment_%'
                    """
                )
            ).scalars().all()
        assert rows
        assert set(rows) == {"assessment"}
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "REFERENCES learning." not in sql
        assert "REFERENCES content." not in sql
        assert "REFERENCES teaching." not in sql
        assert "REFERENCES security." not in sql


class TestPersistenceRoundTrip:
    def test_23_24_25_evaluation_item_and_objective_round_trip(
        self, runtime_engine: Engine
    ) -> None:
        created = _evaluation()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        with factory(created.tenant_id) as uow:
            stored = uow.learner_assessment_evaluations.insert(created)
            uow.commit()
        with factory(created.tenant_id) as uow:
            loaded = uow.learner_assessment_evaluations.get(created.evaluation_id)
        assert stored.evaluation_id == created.evaluation_id
        assert loaded is not None
        assert loaded.evaluation_id == created.evaluation_id
        assert loaded.submission_id == created.submission_id
        assert [item.question_id for item in loaded.items] == [
            item.question_id for item in created.items
        ]
        assert loaded.items[0].outcome is ItemOutcome.CORRECT
        assert loaded.items[1].outcome is ItemOutcome.UNANSWERED
        assert loaded.items[1].response_kind is None
        assert loaded.objective_evidence[0].objective_id == "obj-1"
        assert not hasattr(loaded, "aggregate_revision")

    def test_27_same_identity_cannot_create_two_durable_evaluations(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        submission_id = uuid.uuid7()
        first = _evaluation(tenant_id=tenant_id, submission_id=submission_id)
        second = _evaluation(tenant_id=tenant_id, submission_id=submission_id)
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            stored = uow.learner_assessment_evaluations.insert(first)
            uow.commit()
        with factory(tenant_id) as uow:
            replayed = uow.learner_assessment_evaluations.insert(second)
            uow.commit()
        assert stored.evaluation_id == first.evaluation_id
        assert replayed.evaluation_id == first.evaluation_id
        with factory(tenant_id) as uow:
            assert (
                uow.learner_assessment_evaluations.get_by_business_identity(
                    submission_id=submission_id,
                    evaluation_policy_id=DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
                    evaluation_policy_version=1,
                )
                is not None
            )
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                      FROM assessment.learner_assessment_evaluations
                     WHERE tenant_id = :tenant_id
                       AND submission_id = :submission_id
                    """
                ),
                {"tenant_id": tenant_id, "submission_id": submission_id},
            ).scalar_one()
        assert count == 1

    def test_28_different_policy_version_creates_new_immutable_row(
        self, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        submission_id = uuid.uuid7()
        first = _evaluation(tenant_id=tenant_id, submission_id=submission_id)
        second = _evaluation(
            tenant_id=tenant_id,
            submission_id=submission_id,
            evaluation_policy_version=2,
        )
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_id) as uow:
            uow.learner_assessment_evaluations.insert(first)
            uow.commit()
        with factory(tenant_id) as uow:
            stored = uow.learner_assessment_evaluations.insert(second)
            uow.commit()
        assert stored.evaluation_id != first.evaluation_id
        assert stored.evaluation_policy_version == 2
        with runtime_engine.connect() as conn:
            set_tenant(conn, tenant_id)
            count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                      FROM assessment.learner_assessment_evaluations
                     WHERE tenant_id = :tenant_id
                       AND submission_id = :submission_id
                    """
                ),
                {"tenant_id": tenant_id, "submission_id": submission_id},
            ).scalar_one()
        assert count == 2

    def test_29_no_aggregate_revision_and_no_update_method(self) -> None:
        assert (
            "update"
            not in SqlAlchemyLearnerAssessmentEvaluationRepository.__dict__
        )
        created = _evaluation()
        assert not hasattr(created, "aggregate_revision")

    def test_31_tenant_isolation_rls(self, runtime_engine: Engine) -> None:
        tenant_a = uuid.uuid7()
        tenant_b = uuid.uuid7()
        created = _evaluation(tenant_id=tenant_a)
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        with factory(tenant_a) as uow:
            uow.learner_assessment_evaluations.insert(created)
            uow.commit()
        with factory(tenant_b) as uow:
            assert (
                uow.learner_assessment_evaluations.get(created.evaluation_id) is None
            )

    def test_32_update_and_delete_immutability(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        created = _evaluation()
        factory = SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine)
        with factory(created.tenant_id) as uow:
            uow.learner_assessment_evaluations.insert(created)
            uow.commit()
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, created.tenant_id)
            with pytest.raises(DBAPIError, match="is immutable"):
                conn.execute(
                    text(
                        """
                        UPDATE assessment.learner_assessment_evaluations
                           SET class_ref = 'tamper'
                         WHERE evaluation_id = :eid
                        """
                    ),
                    {"eid": created.evaluation_id.value},
                )
        with bootstrap_engine.begin() as conn:
            set_tenant(conn, created.tenant_id)
            with pytest.raises(DBAPIError, match="is immutable"):
                conn.execute(
                    text(
                        """
                        DELETE FROM assessment.learner_assessment_evaluations
                         WHERE evaluation_id = :eid
                        """
                    ),
                    {"eid": created.evaluation_id.value},
                )
        with factory(created.tenant_id) as uow:
            loaded = uow.learner_assessment_evaluations.get(created.evaluation_id)
        assert loaded is not None
        assert loaded.class_ref == "class-5a"
