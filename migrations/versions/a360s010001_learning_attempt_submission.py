"""AIEOS360-S01-I02 Learning attempt and submission PostgreSQL schema.

Creates schema learning with:

  * learning.attempts
  * learning.attempt_response_items
  * learning.submissions

I02R1 (unmerged in-place correction): response-item terminal guard
SELECT ... FOR UPDATE on the parent learning.attempts row. Snapshot
JSONB persist shape is unchanged.

Durable Learning SoR for LearnerAttempt working state, typed
AttemptResponseItems, and immutable LearnerSubmission evidence.

Deliberately absent:

  * Student HTTP / /api/v1/student-os / public /api/v1/learning routes
  * Authoritative start/submit application orchestration (S01-I03)
  * Cross-domain PostgreSQL FKs to teaching.* or content.*
  * score / grade / mastery / misconception / recommendation / AI columns
  * ABANDONED / GRADED / MASTERED lifecycle states
  * generic late Boolean
  * roster / student-profile tables
  * NATS / Temporal / MCP / outbox facts

Revision ID: a360s010001
Revises: tosd100001
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "a360s010001"
down_revision: str | None = "tosd100001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOWNGRADE_BLOCKED = (
    "AIEOS360-S01-I02 downgrade refused: Learning attempt/submission "
    "evidence exists and must not be deleted"
)

UPGRADE_STATEMENTS: tuple[str, ...] = (
    "CREATE SCHEMA learning",
    """
    CREATE OR REPLACE FUNCTION learning.current_tenant_id()
    RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SET search_path = learning, pg_temp
    AS $$
    DECLARE
        raw text;
    BEGIN
        raw := nullif(current_setting('aieos.tenant_id', true), '');
        IF raw IS NULL THEN
            RAISE EXCEPTION 'aieos.tenant_id is not set'
                USING ERRCODE = '42501';
        END IF;
        RETURN raw::uuid;
    END;
    $$
    """,
    """
    CREATE TABLE learning.attempts (
        attempt_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        learner_principal_id UUID NOT NULL,
        teaching_assignment_id UUID NOT NULL,
        content_id UUID NOT NULL,
        content_version_id UUID NOT NULL,
        class_ref TEXT NOT NULL,
        attempt_number INTEGER NOT NULL,
        lifecycle_state TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        last_saved_at TIMESTAMPTZ NULL,
        submitted_at TIMESTAMPTZ NULL,
        submission_id UUID NULL,
        aggregate_revision BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_learning_attempts
            PRIMARY KEY (attempt_id),
        CONSTRAINT uq_learning_attempts_tenant_attempt
            UNIQUE (tenant_id, attempt_id),
        CONSTRAINT uq_learning_attempts_sequence
            UNIQUE (
                tenant_id,
                learner_principal_id,
                teaching_assignment_id,
                attempt_number
            ),
        CONSTRAINT ck_learning_attempts_class_ref_nonempty
            CHECK (btrim(class_ref) <> ''),
        CONSTRAINT ck_learning_attempts_class_ref_length
            CHECK (char_length(class_ref) <= 512),
        CONSTRAINT ck_learning_attempts_attempt_number_min
            CHECK (attempt_number >= 1),
        CONSTRAINT ck_learning_attempts_aggregate_revision_nonnegative
            CHECK (aggregate_revision >= 0),
        CONSTRAINT ck_learning_attempts_lifecycle_state
            CHECK (lifecycle_state IN ('IN_PROGRESS', 'SUBMITTED')),
        CONSTRAINT ck_learning_attempts_lifecycle_evidence
            CHECK (
                (
                    lifecycle_state = 'IN_PROGRESS'
                    AND submitted_at IS NULL
                    AND submission_id IS NULL
                )
                OR (
                    lifecycle_state = 'SUBMITTED'
                    AND submitted_at IS NOT NULL
                    AND submission_id IS NOT NULL
                )
            ),
        CONSTRAINT ck_learning_attempts_updated_after_created
            CHECK (updated_at >= created_at),
        CONSTRAINT ck_learning_attempts_last_saved_after_started
            CHECK (last_saved_at IS NULL OR last_saved_at >= started_at),
        CONSTRAINT ck_learning_attempts_submitted_after_started
            CHECK (submitted_at IS NULL OR submitted_at >= started_at)
    )
    """,
    """
    CREATE INDEX ix_learning_attempts_tenant_learner
        ON learning.attempts (tenant_id, learner_principal_id)
    """,
    """
    CREATE INDEX ix_learning_attempts_tenant_assignment
        ON learning.attempts (tenant_id, teaching_assignment_id)
    """,
    """
    CREATE INDEX ix_learning_attempts_tenant_learner_assignment
        ON learning.attempts (
            tenant_id, learner_principal_id, teaching_assignment_id
        )
    """,
    """
    CREATE INDEX ix_learning_attempts_tenant_content_version
        ON learning.attempts (tenant_id, content_id, content_version_id)
    """,
    """
    CREATE UNIQUE INDEX uq_learning_attempts_one_in_progress
        ON learning.attempts (
            tenant_id, learner_principal_id, teaching_assignment_id
        )
        WHERE lifecycle_state = 'IN_PROGRESS'
    """,
    """
    CREATE TABLE learning.attempt_response_items (
        tenant_id UUID NOT NULL,
        attempt_id UUID NOT NULL,
        question_id TEXT NOT NULL,
        response_kind TEXT NOT NULL,
        choice_value TEXT NULL,
        text_value TEXT NULL,
        boolean_value BOOLEAN NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_learning_attempt_response_items
            PRIMARY KEY (tenant_id, attempt_id, question_id),
        CONSTRAINT fk_learning_attempt_response_items_attempt
            FOREIGN KEY (tenant_id, attempt_id)
            REFERENCES learning.attempts (tenant_id, attempt_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_learning_response_items_question_id_nonempty
            CHECK (btrim(question_id) <> ''),
        CONSTRAINT ck_learning_response_items_question_id_length
            CHECK (char_length(question_id) <= 128),
        CONSTRAINT ck_learning_response_items_response_kind
            CHECK (
                response_kind IN (
                    'MULTIPLE_CHOICE',
                    'SHORT_ANSWER',
                    'TRUE_FALSE'
                )
            ),
        CONSTRAINT ck_learning_response_items_exclusive_value
            CHECK (
                (
                    response_kind = 'MULTIPLE_CHOICE'
                    AND choice_value IS NOT NULL
                    AND btrim(choice_value) <> ''
                    AND char_length(choice_value) <= 256
                    AND text_value IS NULL
                    AND boolean_value IS NULL
                )
                OR (
                    response_kind = 'SHORT_ANSWER'
                    AND text_value IS NOT NULL
                    AND btrim(text_value) <> ''
                    AND char_length(text_value) <= 4096
                    AND choice_value IS NULL
                    AND boolean_value IS NULL
                )
                OR (
                    response_kind = 'TRUE_FALSE'
                    AND boolean_value IS NOT NULL
                    AND choice_value IS NULL
                    AND text_value IS NULL
                )
            ),
        CONSTRAINT ck_learning_response_items_updated_after_created
            CHECK (updated_at >= created_at)
    )
    """,
    """
    CREATE TABLE learning.submissions (
        submission_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        attempt_id UUID NOT NULL,
        learner_principal_id UUID NOT NULL,
        teaching_assignment_id UUID NOT NULL,
        content_id UUID NOT NULL,
        content_version_id UUID NOT NULL,
        class_ref TEXT NOT NULL,
        response_snapshot JSONB NOT NULL,
        submitted_at TIMESTAMPTZ NOT NULL,
        assignment_revision_at_submit BIGINT NOT NULL,
        due_at_at_submit TIMESTAMPTZ NULL,
        created_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_learning_submissions
            PRIMARY KEY (submission_id),
        CONSTRAINT uq_learning_submissions_tenant_submission
            UNIQUE (tenant_id, submission_id),
        CONSTRAINT uq_learning_submissions_tenant_attempt
            UNIQUE (tenant_id, attempt_id),
        CONSTRAINT fk_learning_submissions_attempt
            FOREIGN KEY (tenant_id, attempt_id)
            REFERENCES learning.attempts (tenant_id, attempt_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_learning_submissions_class_ref_nonempty
            CHECK (btrim(class_ref) <> ''),
        CONSTRAINT ck_learning_submissions_class_ref_length
            CHECK (char_length(class_ref) <= 512),
        CONSTRAINT ck_learning_submissions_assignment_revision_nonnegative
            CHECK (assignment_revision_at_submit >= 0),
        CONSTRAINT ck_learning_submissions_response_snapshot_array
            CHECK (jsonb_typeof(response_snapshot) = 'array')
    )
    """,
    """
    CREATE OR REPLACE FUNCTION learning.reject_response_item_when_attempt_not_in_progress()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = learning, pg_temp
    AS $$
    DECLARE
        parent_state text;
        parent_attempt uuid;
        parent_tenant uuid;
    BEGIN
        IF TG_OP = 'DELETE' THEN
            parent_attempt := OLD.attempt_id;
            parent_tenant := OLD.tenant_id;
        ELSE
            parent_attempt := NEW.attempt_id;
            parent_tenant := NEW.tenant_id;
        END IF;
        -- Parent-row serialization: lock the authoritative LearnerAttempt
        -- before accepting response INSERT/UPDATE/DELETE. Missing parent
        -- and non-IN_PROGRESS parent fail closed. Does not lock Teaching.
        SELECT lifecycle_state INTO parent_state
          FROM learning.attempts
         WHERE attempt_id = parent_attempt
           AND tenant_id = parent_tenant
           FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'learning.attempt_response_items parent LearnerAttempt not found'
                USING ERRCODE = '27000';
        END IF;
        IF parent_state IS DISTINCT FROM 'IN_PROGRESS' THEN
            RAISE EXCEPTION
                'learning.attempt_response_items cannot mutate unless parent attempt is IN_PROGRESS'
                USING ERRCODE = '27000';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER learning_attempt_response_items_in_progress_insert
        BEFORE INSERT ON learning.attempt_response_items
        FOR EACH ROW
        EXECUTE FUNCTION learning.reject_response_item_when_attempt_not_in_progress()
    """,
    """
    CREATE TRIGGER learning_attempt_response_items_in_progress_update
        BEFORE UPDATE ON learning.attempt_response_items
        FOR EACH ROW
        EXECUTE FUNCTION learning.reject_response_item_when_attempt_not_in_progress()
    """,
    """
    CREATE TRIGGER learning_attempt_response_items_in_progress_delete
        BEFORE DELETE ON learning.attempt_response_items
        FOR EACH ROW
        EXECUTE FUNCTION learning.reject_response_item_when_attempt_not_in_progress()
    """,
    """
    CREATE OR REPLACE FUNCTION learning.reject_submitted_attempt_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = learning, pg_temp
    AS $$
    BEGIN
        IF OLD.lifecycle_state = 'SUBMITTED' THEN
            RAISE EXCEPTION 'learning.attempts SUBMITTED row is terminal'
                USING ERRCODE = '27000';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER learning_attempts_submitted_terminal_update
        BEFORE UPDATE ON learning.attempts
        FOR EACH ROW
        EXECUTE FUNCTION learning.reject_submitted_attempt_mutation()
    """,
    """
    CREATE OR REPLACE FUNCTION learning.reject_submission_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = learning, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'learning.submissions is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER learning_submissions_immutable_update
        BEFORE UPDATE ON learning.submissions
        FOR EACH ROW
        EXECUTE FUNCTION learning.reject_submission_mutation()
    """,
    """
    CREATE TRIGGER learning_submissions_immutable_delete
        BEFORE DELETE ON learning.submissions
        FOR EACH ROW
        EXECUTE FUNCTION learning.reject_submission_mutation()
    """,
    "ALTER TABLE learning.attempts ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE learning.attempts FORCE ROW LEVEL SECURITY",
    "ALTER TABLE learning.attempt_response_items ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE learning.attempt_response_items FORCE ROW LEVEL SECURITY",
    "ALTER TABLE learning.submissions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE learning.submissions FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY learning_attempts_tenant_isolation
        ON learning.attempts
        FOR ALL
        USING (tenant_id = learning.current_tenant_id())
        WITH CHECK (tenant_id = learning.current_tenant_id())
    """,
    """
    CREATE POLICY learning_attempt_response_items_tenant_isolation
        ON learning.attempt_response_items
        FOR ALL
        USING (tenant_id = learning.current_tenant_id())
        WITH CHECK (tenant_id = learning.current_tenant_id())
    """,
    """
    CREATE POLICY learning_submissions_tenant_isolation
        ON learning.submissions
        FOR ALL
        USING (tenant_id = learning.current_tenant_id())
        WITH CHECK (tenant_id = learning.current_tenant_id())
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    "DROP POLICY IF EXISTS learning_submissions_tenant_isolation ON learning.submissions",
    """
    DROP POLICY IF EXISTS learning_attempt_response_items_tenant_isolation
        ON learning.attempt_response_items
    """,
    "DROP POLICY IF EXISTS learning_attempts_tenant_isolation ON learning.attempts",
    "DROP TRIGGER IF EXISTS learning_submissions_immutable_delete ON learning.submissions",
    "DROP TRIGGER IF EXISTS learning_submissions_immutable_update ON learning.submissions",
    """
    DROP TRIGGER IF EXISTS learning_attempts_submitted_terminal_update
        ON learning.attempts
    """,
    """
    DROP TRIGGER IF EXISTS learning_attempt_response_items_in_progress_delete
        ON learning.attempt_response_items
    """,
    """
    DROP TRIGGER IF EXISTS learning_attempt_response_items_in_progress_update
        ON learning.attempt_response_items
    """,
    """
    DROP TRIGGER IF EXISTS learning_attempt_response_items_in_progress_insert
        ON learning.attempt_response_items
    """,
    "DROP FUNCTION IF EXISTS learning.reject_submission_mutation()",
    "DROP FUNCTION IF EXISTS learning.reject_submitted_attempt_mutation()",
    """
    DROP FUNCTION IF EXISTS
        learning.reject_response_item_when_attempt_not_in_progress()
    """,
    "DROP TABLE IF EXISTS learning.submissions",
    "DROP TABLE IF EXISTS learning.attempt_response_items",
    "DROP TABLE IF EXISTS learning.attempts",
    "DROP FUNCTION IF EXISTS learning.current_tenant_id()",
    "DROP SCHEMA IF EXISTS learning",
)


def _learning_table_exists(bind, table_name: str) -> bool:
    return bool(
        bind.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'learning'
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        ).scalar()
    )


def _disable_learning_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE learning.{table_name} DISABLE ROW LEVEL SECURITY")


def _restore_learning_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE learning.{table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE learning.{table_name} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    tables = ("attempts", "attempt_response_items", "submissions")
    existing = [name for name in tables if _learning_table_exists(bind, name)]
    for name in existing:
        _disable_learning_rls(name)
    blocked = False
    for name in existing:
        blocked = bool(
            bind.execute(text(f"SELECT EXISTS (SELECT 1 FROM learning.{name})")).scalar()
        )
        if blocked:
            break
    if blocked:
        for name in existing:
            _restore_learning_rls(name)
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
