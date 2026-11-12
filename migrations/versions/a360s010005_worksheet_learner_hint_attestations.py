"""PK-V1-HINT-T2 worksheet learner-hint version attestation storage + audit vocab.

Revision ID: a360s010005
Revises: a360s010004
Create Date: 2026-11-11
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from alembic.script import ScriptDirectory
from sqlalchemy import text

_PREDECESSOR_REVISION = "a360s010004"

revision: str = "a360s010005"
down_revision: str | None = "a360s010004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
RUNTIME_ROLE_ENV = "AIEOS_RUNTIME_ROLE"
CONTENT_MIGRATION_RUNTIME_ROLE_ENV = "AIEOS_CONTENT_MIGRATION_RUNTIME_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_DOWNGRADE_BLOCKED = (
    "PK-V1-HINT-T2 downgrade refused: worksheet learner hint attestation evidence exists"
)


def _predecessor_upgrade_audit_statements() -> tuple[str, ...]:
    """Load ``a360s010004`` audit CHECK SQL via Alembic's revision graph (CLI-safe)."""
    migrate_context = op.get_context()
    script = ScriptDirectory.from_config(migrate_context.config)
    current = script.get_revision(revision)
    if current is None:
        raise RuntimeError(
            f"PK-V1-HINT-T2: migration {revision!r} missing from Alembic script directory"
        )
    if current.down_revision != _PREDECESSOR_REVISION:
        raise RuntimeError(
            f"PK-V1-HINT-T2: expected down_revision {_PREDECESSOR_REVISION!r}, "
            f"got {current.down_revision!r}"
        )
    pred = script.get_revision(_PREDECESSOR_REVISION)
    if pred is None:
        raise RuntimeError(
            f"PK-V1-HINT-T2: predecessor revision {_PREDECESSOR_REVISION!r} not found"
        )
    pred_module = pred.module
    statements = getattr(pred_module, "UPGRADE_AUDIT_STATEMENTS", None)
    if not statements:
        raise RuntimeError(
            "PK-V1-HINT-T2: a360s010004 UPGRADE_AUDIT_STATEMENTS missing or empty"
        )
    return tuple(statements)

_CONTENT_ACTIONS_SQL = """
                    'content.create',
                    'content.version.create',
                    'content.review.submit',
                    'content.review.approve',
                    'content.review.request_changes',
                    'content.review.reject',
                    'content.publish',
                    'content.ai.materialize',
                    'content.migration.import',
                    'content.worksheet_hint.attest'
"""
_ASSET_ACTIONS_SQL = """
                    'asset.create',
                    'asset.revision.register',
                    'asset.revision.activate',
                    'asset.lifecycle.withdraw',
                    'asset.lifecycle.restore',
                    'asset.lifecycle.delete',
                    'asset.quarantine.set',
                    'asset.quarantine.clear',
                    'asset.safety.pass',
                    'asset.safety.fail'
"""
_TEACHING_BASE_ACTIONS_SQL = """
                    'teaching.assignment.create',
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel',
                    'teaching.execution.start',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.create',
                    'teaching.execution.observation.correct',
                    'teaching.work.remediation.create',
                    'teaching.memory.create',
                    'teaching.memory.update'
"""
_MEMORY_ACTIONS_SQL = """
                    'teaching.memory.create',
                    'teaching.memory.update'
"""
_LEARNING_ACTIONS_SQL = """
                    'learning.attempt.start',
                    'learning.attempt.save_responses',
                    'learning.attempt.submit'
"""
_ASSESSMENT_UPGRADE_ACTIONS_SQL = """
                    'assessment.classroom.record',
                    'assessment.classroom.correct',
                    'assessment.classroom.void',
                    'assessment.learner_evaluation.ensure'
"""
_CONTENT_INCREMENT_SQL = """
                    'content.version.create',
                    'content.review.submit',
                    'content.review.approve',
                    'content.review.request_changes',
                    'content.review.reject',
                    'content.publish',
                    'content.ai.materialize'
"""
_ASSET_INCREMENT_SQL = """
                    'asset.revision.activate',
                    'asset.lifecycle.withdraw',
                    'asset.lifecycle.restore',
                    'asset.lifecycle.delete',
                    'asset.quarantine.set',
                    'asset.quarantine.clear',
                    'asset.safety.pass',
                    'asset.safety.fail'
"""
_TEACHING_CREATE_SQL = """
                    'teaching.assignment.create',
                    'teaching.execution.start',
                    'teaching.execution.observation.create',
                    'teaching.work.remediation.create',
                    'teaching.memory.create'
"""
_TEACHING_INCREMENT_SQL = """
                    'teaching.assignment.due_update',
                    'teaching.assignment.close',
                    'teaching.assignment.cancel',
                    'teaching.execution.complete',
                    'teaching.execution.cancel',
                    'teaching.execution.observation.correct',
                    'teaching.memory.update'
"""
_MEMORY_CREATE_SQL = """
                    'teaching.memory.create'
"""
_MEMORY_INCREMENT_SQL = """
                    'teaching.memory.update'
"""
_ASSESSMENT_UPGRADE_CREATE_SQL = """
                    'assessment.classroom.record',
                    'assessment.learner_evaluation.ensure'
"""
_ASSESSMENT_INCREMENT_SQL = """
                    'assessment.classroom.correct',
                    'assessment.classroom.void'
"""
_LEARNING_CREATE_SQL = """
                    'learning.attempt.start'
"""
_LEARNING_INCREMENT_SQL = """
                    'learning.attempt.save_responses',
                    'learning.attempt.submit'
"""

_VALIDATE_OUTCOMES_FN = """
    CREATE OR REPLACE FUNCTION content.validate_worksheet_hint_per_question_outcomes(
        value jsonb
    )
    RETURNS boolean
    LANGUAGE plpgsql
    IMMUTABLE
    PARALLEL SAFE
    SET search_path = content, pg_temp
    AS $$
    DECLARE
        n int;
        idx int;
        item jsonb;
        keys text[];
    BEGIN
        IF value IS NULL OR jsonb_typeof(value) <> 'array' THEN
            RETURN false;
        END IF;
        n := jsonb_array_length(value);
        IF n IS NULL OR n < 1 THEN
            RETURN false;
        END IF;
        FOR idx IN 0 .. (n - 1) LOOP
            item := value -> idx;
            IF item IS NULL OR jsonb_typeof(item) <> 'object' THEN
                RETURN false;
            END IF;
            SELECT array_agg(k ORDER BY k)
              INTO keys
              FROM jsonb_object_keys(item) AS k;
            IF keys IS DISTINCT FROM
               ARRAY['hard_block_clear', 'human_review_pass', 'question_id'] THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(item->'question_id') <> 'string' THEN
                RETURN false;
            END IF;
            IF length(trim(item->>'question_id')) < 1 THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(item->'hard_block_clear') <> 'boolean' THEN
                RETURN false;
            END IF;
            IF jsonb_typeof(item->'human_review_pass') <> 'boolean' THEN
                RETURN false;
            END IF;
        END LOOP;
        RETURN true;
    END;
    $$
    """

_TABLE_STATEMENTS: tuple[str, ...] = (
    _VALIDATE_OUTCOMES_FN,
    """
    CREATE TABLE content.worksheet_learner_hint_version_attestations (
        attestation_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        content_id UUID NOT NULL,
        version_id UUID NOT NULL,
        payload_digest TEXT NOT NULL,
        qa_policy_version TEXT NOT NULL,
        author_principal_id UUID NOT NULL,
        reviewer_principal_id UUID NOT NULL,
        per_question_outcomes JSONB NOT NULL,
        attested_at TIMESTAMPTZ NOT NULL,
        correlation_id UUID NOT NULL,
        CONSTRAINT pk_worksheet_hint_attestations PRIMARY KEY (attestation_id),
        CONSTRAINT uq_worksheet_hint_attestations_tenant_version UNIQUE (tenant_id, version_id),
        CONSTRAINT ck_worksheet_hint_attestations_payload_digest
            CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_worksheet_hint_attestations_qa_policy_version
            CHECK (qa_policy_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
        CONSTRAINT ck_worksheet_hint_attestations_outcomes_booleans
            CHECK (
                content.validate_worksheet_hint_per_question_outcomes(
                    per_question_outcomes
                )
            ),
        CONSTRAINT fk_worksheet_hint_attestations_version
            FOREIGN KEY (tenant_id, content_id, version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX ix_worksheet_hint_attestations_tenant_id "
    "ON content.worksheet_learner_hint_version_attestations (tenant_id)",
    """
    CREATE OR REPLACE FUNCTION content.reject_worksheet_hint_attestation_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'content.worksheet_learner_hint_version_attestations is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER worksheet_hint_attestations_immutable_update
        BEFORE UPDATE ON content.worksheet_learner_hint_version_attestations
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_worksheet_hint_attestation_mutation()
    """,
    """
    CREATE TRIGGER worksheet_hint_attestations_immutable_delete
        BEFORE DELETE ON content.worksheet_learner_hint_version_attestations
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_worksheet_hint_attestation_mutation()
    """,
    "ALTER TABLE content.worksheet_learner_hint_version_attestations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.worksheet_learner_hint_version_attestations FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY worksheet_hint_attestations_tenant_isolation
        ON content.worksheet_learner_hint_version_attestations
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
)


def _joined(*parts: str) -> str:
    return ",\n".join(part.strip("\n") for part in parts)


def _require_role(env_name: str, *, purpose: str) -> str:
    role = os.environ.get(env_name, "").strip()
    if not role:
        raise RuntimeError(
            f"{env_name} must be set to the {purpose}; Alembic will not "
            "silently alter security objects as the migrator or content owner."
        )
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(
            f"{env_name} must be a lowercase unquoted PostgreSQL identifier"
        )
    return role


def _upgrade_audit_statements() -> tuple[str, ...]:
    actions_sql = _joined(
        _CONTENT_ACTIONS_SQL,
        _ASSET_ACTIONS_SQL,
        _TEACHING_BASE_ACTIONS_SQL,
        _MEMORY_ACTIONS_SQL,
        _ASSESSMENT_UPGRADE_ACTIONS_SQL,
        _LEARNING_ACTIONS_SQL,
    )
    primary_actions_sql = _joined(
        _CONTENT_ACTIONS_SQL,
        _TEACHING_BASE_ACTIONS_SQL,
        _MEMORY_ACTIONS_SQL,
        _ASSESSMENT_UPGRADE_ACTIONS_SQL,
        _LEARNING_ACTIONS_SQL,
    )
    return (
        "ALTER TABLE security.audit_records DROP CONSTRAINT ck_audit_records_action",
        f"""
        ALTER TABLE security.audit_records
            ADD CONSTRAINT ck_audit_records_action
            CHECK (action IN (
{actions_sql}
            ))
        """,
        "ALTER TABLE security.audit_records "
        "DROP CONSTRAINT ck_audit_records_primary_revision_family",
        f"""
        ALTER TABLE security.audit_records
            ADD CONSTRAINT ck_audit_records_primary_revision_family
            CHECK (
                (
                    action IN (
{primary_actions_sql}
                    )
                    AND primary_resource_revision IS NOT NULL
                    AND primary_resource_revision = resource_revision_after
                )
                OR (
                    action IN (
{_ASSET_ACTIONS_SQL}
                    )
                    AND primary_resource_revision IS NULL
                )
            )
        """,
        "ALTER TABLE security.audit_records "
        "DROP CONSTRAINT ck_audit_records_revision_semantics",
        f"""
        ALTER TABLE security.audit_records
            ADD CONSTRAINT ck_audit_records_revision_semantics
            CHECK (
                (action = 'content.create'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action = 'content.migration.import'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 1)
                OR (action IN ({_CONTENT_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action = 'content.worksheet_hint.attest'
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before)
                OR (action = 'asset.create'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action = 'asset.revision.register'
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before)
                OR (action IN ({_ASSET_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({_TEACHING_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_TEACHING_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({_MEMORY_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_MEMORY_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({_ASSESSMENT_UPGRADE_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_ASSESSMENT_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({_LEARNING_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({_LEARNING_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
            )
        """,
    )


UPGRADE_AUDIT_STATEMENTS = _upgrade_audit_statements()


def upgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
    )
    runtime = _require_role(RUNTIME_ROLE_ENV, purpose="runtime database role")
    for statement in _TABLE_STATEMENTS:
        op.execute(statement)
    table = "content.worksheet_learner_hint_version_attestations"
    op.execute(f"GRANT SELECT, INSERT ON {table} TO {runtime}")
    op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM {runtime}")
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in UPGRADE_AUDIT_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")


def downgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
    )
    op.execute(f"SET LOCAL ROLE {content_owner}")
    table = "content.worksheet_learner_hint_version_attestations"
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    bind = op.get_bind()
    attestation_count = bind.execute(
        text(f"SELECT count(*) FROM {table}")
    ).scalar_one()
    if int(attestation_count) > 0:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(f"SET LOCAL ROLE {security_owner}")
    op.execute("ALTER TABLE security.audit_records DISABLE ROW LEVEL SECURITY")
    audit_count = bind.execute(
        text(
            """
            SELECT count(*)
            FROM security.audit_records
            WHERE action = 'content.worksheet_hint.attest'
            """
        )
    ).scalar_one()
    op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
    if int(audit_count) > 0:
        op.execute(f"SET LOCAL ROLE {content_owner}")
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(f"SET LOCAL ROLE {content_owner}")
    op.execute(
        "DROP TABLE IF EXISTS content.worksheet_learner_hint_version_attestations"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS content.reject_worksheet_hint_attestation_mutation()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS content.validate_worksheet_hint_per_question_outcomes(jsonb)"
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in _predecessor_upgrade_audit_statements():
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")
