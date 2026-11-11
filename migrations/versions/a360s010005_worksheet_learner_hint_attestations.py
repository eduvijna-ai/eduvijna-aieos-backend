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
from sqlalchemy import text

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

_TABLE_STATEMENTS: tuple[str, ...] = (
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
                jsonb_typeof(per_question_outcomes) = 'array'
                AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(per_question_outcomes) AS elem
                    WHERE jsonb_typeof(elem->'hard_block_clear') <> 'boolean'
                       OR jsonb_typeof(elem->'human_review_pass') <> 'boolean'
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
    migration_runtime = _require_role(
        CONTENT_MIGRATION_RUNTIME_ROLE_ENV,
        purpose="Content migration runtime database role",
    )
    for statement in _TABLE_STATEMENTS:
        op.execute(statement)
    table = "content.worksheet_learner_hint_version_attestations"
    op.execute(f"GRANT SELECT, INSERT ON {table} TO {runtime}")
    op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM {runtime}")
    op.execute(f"GRANT SELECT, INSERT ON {table} TO {migration_runtime}")
    op.execute(f"REVOKE UPDATE, DELETE ON {table} FROM {migration_runtime}")
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
    bind = op.get_bind()
    count = bind.execute(
        text(
            "SELECT count(*) FROM content.worksheet_learner_hint_version_attestations"
        )
    ).scalar_one()
    if int(count) > 0:
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(
        "DROP TABLE IF EXISTS content.worksheet_learner_hint_version_attestations"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS content.reject_worksheet_hint_attestation_mutation()"
    )
    from migrations.versions import a360s010004_learner_evaluation_audit_vocab as prev

    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in prev.UPGRADE_AUDIT_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")
