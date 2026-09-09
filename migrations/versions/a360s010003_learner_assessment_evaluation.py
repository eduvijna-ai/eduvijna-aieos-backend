"""AIEOS360-S01-I05-B1 LearnerAssessmentEvaluation PostgreSQL schema.

Creates Assessment-owned append-only evaluation persistence:

  * assessment.learner_assessment_evaluations
  * assessment.learner_assessment_evaluation_items
  * assessment.learner_assessment_objective_evidence

Durable evaluation of one immutable LearnerSubmission against one exact
ContentVersion under one explicit evaluation policy version.

Deliberately absent:

  * Assessment HTTP / OpenAPI / request-response models
  * Teacher Assessment Intelligence projection
  * Learning / Content / Teaching schema mutation
  * cross-domain PostgreSQL FKs
  * optimistic concurrency revision
  * event plane / workflow plane
  * production data backfill

Revision ID: a360s010003
Revises: a360s010002
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "a360s010003"
down_revision: str | None = "a360s010002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOWNGRADE_BLOCKED = (
    "AIEOS360-S01-I05-B1 downgrade refused: LearnerAssessmentEvaluation "
    "evidence exists and must not be deleted"
)

_EVALUATION_TABLES = (
    "learner_assessment_objective_evidence",
    "learner_assessment_evaluation_items",
    "learner_assessment_evaluations",
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE assessment.learner_assessment_evaluations (
        evaluation_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        learner_principal_id UUID NOT NULL,
        submission_id UUID NOT NULL,
        attempt_id UUID NOT NULL,
        teaching_assignment_id UUID NOT NULL,
        content_id UUID NOT NULL,
        content_version_id UUID NOT NULL,
        class_ref TEXT NOT NULL,
        evaluation_policy_id TEXT NOT NULL,
        evaluation_policy_version INTEGER NOT NULL,
        evaluated_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_assessment_learner_assessment_evaluations
            PRIMARY KEY (evaluation_id),
        CONSTRAINT uq_assessment_learner_assessment_evaluations_tenant_evaluation
            UNIQUE (tenant_id, evaluation_id),
        CONSTRAINT uq_assessment_learner_assessment_evaluations_business_identity
            UNIQUE (
                tenant_id,
                submission_id,
                evaluation_policy_id,
                evaluation_policy_version
            ),
        CONSTRAINT ck_assessment_lae_class_ref_nonempty
            CHECK (btrim(class_ref) <> ''),
        CONSTRAINT ck_assessment_lae_class_ref_length
            CHECK (char_length(class_ref) <= 512),
        CONSTRAINT ck_assessment_lae_policy_id_nonempty
            CHECK (btrim(evaluation_policy_id) <> ''),
        CONSTRAINT ck_assessment_lae_policy_id_length
            CHECK (char_length(evaluation_policy_id) <= 128),
        CONSTRAINT ck_assessment_lae_policy_version_positive
            CHECK (evaluation_policy_version >= 1),
        CONSTRAINT ck_assessment_lae_created_equals_evaluated
            CHECK (created_at = evaluated_at)
    )
    """,
    """
    CREATE INDEX ix_assessment_lae_tenant_submission
        ON assessment.learner_assessment_evaluations (
            tenant_id, submission_id
        )
    """,
    """
    CREATE INDEX ix_assessment_lae_tenant_assignment
        ON assessment.learner_assessment_evaluations (
            tenant_id, teaching_assignment_id
        )
    """,
    """
    CREATE TABLE assessment.learner_assessment_evaluation_items (
        tenant_id UUID NOT NULL,
        evaluation_id UUID NOT NULL,
        question_id TEXT NOT NULL,
        item_ordinal INTEGER NOT NULL,
        question_type TEXT NOT NULL,
        outcome TEXT NOT NULL,
        evaluation_method TEXT NOT NULL,
        objective_ids TEXT[] NOT NULL,
        response_kind TEXT NULL,
        CONSTRAINT pk_assessment_learner_assessment_evaluation_items
            PRIMARY KEY (tenant_id, evaluation_id, question_id),
        CONSTRAINT uq_assessment_lae_items_ordinal
            UNIQUE (tenant_id, evaluation_id, item_ordinal),
        CONSTRAINT fk_assessment_lae_items_evaluation
            FOREIGN KEY (tenant_id, evaluation_id)
            REFERENCES assessment.learner_assessment_evaluations (
                tenant_id, evaluation_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT ck_assessment_lae_items_question_id_nonempty
            CHECK (btrim(question_id) <> ''),
        CONSTRAINT ck_assessment_lae_items_question_id_length
            CHECK (char_length(question_id) <= 128),
        CONSTRAINT ck_assessment_lae_items_ordinal_nonnegative
            CHECK (item_ordinal >= 0),
        CONSTRAINT ck_assessment_lae_items_question_type_nonempty
            CHECK (btrim(question_type) <> ''),
        CONSTRAINT ck_assessment_lae_items_question_type_length
            CHECK (char_length(question_type) <= 64),
        CONSTRAINT ck_assessment_lae_items_outcome
            CHECK (
                outcome IN (
                    'CORRECT',
                    'INCORRECT',
                    'UNANSWERED',
                    'OPEN_RESPONSE_UNEVALUATED',
                    'UNEVALUATED_POLICY_REJECT'
                )
            ),
        CONSTRAINT ck_assessment_lae_items_evaluation_method
            CHECK (
                evaluation_method IN (
                    'DETERMINISTIC_CONTENT_ANSWER',
                    'NO_RESPONSE',
                    'OPEN_RESPONSE_BASELINE',
                    'POLICY_REJECT'
                )
            ),
        CONSTRAINT ck_assessment_lae_items_outcome_method_pairing
            CHECK (
                (
                    outcome = 'UNANSWERED'
                    AND evaluation_method = 'NO_RESPONSE'
                    AND response_kind IS NULL
                )
                OR (
                    outcome IN ('CORRECT', 'INCORRECT')
                    AND evaluation_method = 'DETERMINISTIC_CONTENT_ANSWER'
                    AND response_kind IS NOT NULL
                )
                OR (
                    outcome = 'OPEN_RESPONSE_UNEVALUATED'
                    AND evaluation_method = 'OPEN_RESPONSE_BASELINE'
                    AND response_kind IS NOT NULL
                )
                OR (
                    outcome = 'UNEVALUATED_POLICY_REJECT'
                    AND evaluation_method = 'POLICY_REJECT'
                    AND response_kind IS NOT NULL
                )
            ),
        CONSTRAINT ck_assessment_lae_items_response_kind_length
            CHECK (
                response_kind IS NULL
                OR (
                    btrim(response_kind) <> ''
                    AND char_length(response_kind) <= 64
                )
            )
    )
    """,
    """
    CREATE TABLE assessment.learner_assessment_objective_evidence (
        tenant_id UUID NOT NULL,
        evaluation_id UUID NOT NULL,
        objective_id TEXT NOT NULL,
        result TEXT NOT NULL,
        CONSTRAINT pk_assessment_learner_assessment_objective_evidence
            PRIMARY KEY (tenant_id, evaluation_id, objective_id),
        CONSTRAINT fk_assessment_lae_objective_evidence_evaluation
            FOREIGN KEY (tenant_id, evaluation_id)
            REFERENCES assessment.learner_assessment_evaluations (
                tenant_id, evaluation_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT ck_assessment_lae_objective_id_nonempty
            CHECK (btrim(objective_id) <> ''),
        CONSTRAINT ck_assessment_lae_objective_id_length
            CHECK (char_length(objective_id) <= 128),
        CONSTRAINT ck_assessment_lae_objective_result
            CHECK (
                result IN (
                    'INSUFFICIENT_EVIDENCE',
                    'DEMONSTRATED_ON_SUBMITTED_ITEMS',
                    'MIXED_ON_SUBMITTED_ITEMS',
                    'NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS'
                )
            )
    )
    """,
    """
    CREATE OR REPLACE FUNCTION assessment.reject_immutable_evaluation_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = assessment, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION
            USING MESSAGE = 'assessment.' || TG_TABLE_NAME || ' is immutable',
                ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER learner_assessment_evaluations_immutable_update
        BEFORE UPDATE ON assessment.learner_assessment_evaluations
        FOR EACH ROW
        EXECUTE FUNCTION assessment.reject_immutable_evaluation_mutation()
    """,
    """
    CREATE TRIGGER learner_assessment_evaluations_immutable_delete
        BEFORE DELETE ON assessment.learner_assessment_evaluations
        FOR EACH ROW
        EXECUTE FUNCTION assessment.reject_immutable_evaluation_mutation()
    """,
    """
    CREATE TRIGGER learner_assessment_evaluation_items_immutable_update
        BEFORE UPDATE ON assessment.learner_assessment_evaluation_items
        FOR EACH ROW
        EXECUTE FUNCTION assessment.reject_immutable_evaluation_mutation()
    """,
    """
    CREATE TRIGGER learner_assessment_evaluation_items_immutable_delete
        BEFORE DELETE ON assessment.learner_assessment_evaluation_items
        FOR EACH ROW
        EXECUTE FUNCTION assessment.reject_immutable_evaluation_mutation()
    """,
    """
    CREATE TRIGGER learner_assessment_objective_evidence_immutable_update
        BEFORE UPDATE ON assessment.learner_assessment_objective_evidence
        FOR EACH ROW
        EXECUTE FUNCTION assessment.reject_immutable_evaluation_mutation()
    """,
    """
    CREATE TRIGGER learner_assessment_objective_evidence_immutable_delete
        BEFORE DELETE ON assessment.learner_assessment_objective_evidence
        FOR EACH ROW
        EXECUTE FUNCTION assessment.reject_immutable_evaluation_mutation()
    """,
    "ALTER TABLE assessment.learner_assessment_evaluations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE assessment.learner_assessment_evaluations FORCE ROW LEVEL SECURITY",
    "ALTER TABLE assessment.learner_assessment_evaluation_items ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE assessment.learner_assessment_evaluation_items FORCE ROW LEVEL SECURITY",
    "ALTER TABLE assessment.learner_assessment_objective_evidence ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE assessment.learner_assessment_objective_evidence FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY assessment_learner_assessment_evaluations_tenant_isolation
        ON assessment.learner_assessment_evaluations
        FOR ALL
        USING (tenant_id = assessment.current_tenant_id())
        WITH CHECK (tenant_id = assessment.current_tenant_id())
    """,
    """
    CREATE POLICY assessment_learner_assessment_evaluation_items_tenant_isolation
        ON assessment.learner_assessment_evaluation_items
        FOR ALL
        USING (tenant_id = assessment.current_tenant_id())
        WITH CHECK (tenant_id = assessment.current_tenant_id())
    """,
    """
    CREATE POLICY assessment_learner_assessment_objective_evidence_tenant_isolation
        ON assessment.learner_assessment_objective_evidence
        FOR ALL
        USING (tenant_id = assessment.current_tenant_id())
        WITH CHECK (tenant_id = assessment.current_tenant_id())
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    """
    DROP POLICY IF EXISTS
        assessment_learner_assessment_objective_evidence_tenant_isolation
        ON assessment.learner_assessment_objective_evidence
    """,
    """
    DROP POLICY IF EXISTS
        assessment_learner_assessment_evaluation_items_tenant_isolation
        ON assessment.learner_assessment_evaluation_items
    """,
    """
    DROP POLICY IF EXISTS
        assessment_learner_assessment_evaluations_tenant_isolation
        ON assessment.learner_assessment_evaluations
    """,
    """
    DROP TRIGGER IF EXISTS learner_assessment_objective_evidence_immutable_delete
        ON assessment.learner_assessment_objective_evidence
    """,
    """
    DROP TRIGGER IF EXISTS learner_assessment_objective_evidence_immutable_update
        ON assessment.learner_assessment_objective_evidence
    """,
    """
    DROP TRIGGER IF EXISTS learner_assessment_evaluation_items_immutable_delete
        ON assessment.learner_assessment_evaluation_items
    """,
    """
    DROP TRIGGER IF EXISTS learner_assessment_evaluation_items_immutable_update
        ON assessment.learner_assessment_evaluation_items
    """,
    """
    DROP TRIGGER IF EXISTS learner_assessment_evaluations_immutable_delete
        ON assessment.learner_assessment_evaluations
    """,
    """
    DROP TRIGGER IF EXISTS learner_assessment_evaluations_immutable_update
        ON assessment.learner_assessment_evaluations
    """,
    "DROP TABLE IF EXISTS assessment.learner_assessment_objective_evidence",
    "DROP TABLE IF EXISTS assessment.learner_assessment_evaluation_items",
    "DROP TABLE IF EXISTS assessment.learner_assessment_evaluations",
    "DROP FUNCTION IF EXISTS assessment.reject_immutable_evaluation_mutation()",
)


def _assessment_table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'assessment'
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar()
    )


def _disable_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE assessment.{table_name} DISABLE ROW LEVEL SECURITY")


def _restore_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE assessment.{table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE assessment.{table_name} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    existing = [name for name in _EVALUATION_TABLES if _assessment_table_exists(bind, name)]
    for name in existing:
        _disable_rls(name)
    blocked = False
    for name in existing:
        blocked = bool(
            bind.execute(
                text(f"SELECT EXISTS (SELECT 1 FROM assessment.{name})")
            ).scalar()
        )
        if blocked:
            break
    if blocked:
        for name in existing:
            _restore_rls(name)
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
