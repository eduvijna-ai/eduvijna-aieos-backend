"""CI03-G2-I01 reviewer assignment immutable events + current slot (ADR-AIEOS-064 O64-04).

Revision ID: ci03g020001
Revises: a360s010005
Create Date: 2026-11-24
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from alembic.script import ScriptDirectory
from sqlalchemy import text

_PREDECESSOR_REVISION = "a360s010005"

revision: str = "ci03g020001"
down_revision: str | None = "a360s010005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
RUNTIME_ROLE_ENV = "AIEOS_RUNTIME_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_DOWNGRADE_BLOCKED = (
    "CI03-G2-I01 downgrade refused: reviewer assignment authority evidence exists"
)

_CLASS_REF_CHECK = "class_ref IS NULL OR (btrim(class_ref) <> '' AND char_length(class_ref) <= 128)"


def _predecessor_upgrade_audit_statements() -> tuple[str, ...]:
    migrate_context = op.get_context()
    script = ScriptDirectory.from_config(migrate_context.config)
    current = script.get_revision(revision)
    if current is None:
        raise RuntimeError(f"CI03-G2-I01: migration {revision!r} missing from Alembic")
    pred = script.get_revision(_PREDECESSOR_REVISION)
    if pred is None:
        raise RuntimeError(f"CI03-G2-I01: predecessor {_PREDECESSOR_REVISION!r} not found")
    statements = getattr(pred.module, "UPGRADE_AUDIT_STATEMENTS", None)
    if not statements:
        raise RuntimeError("CI03-G2-I01: predecessor UPGRADE_AUDIT_STATEMENTS missing")
    return tuple(statements)


_CONTENT_ASSIGN_ACTIONS_SQL = """
                    'content.review.assign',
                    'content.review.revoke'
"""

_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE content.review_assignment_events (
        event_id UUID NOT NULL,
        event_kind TEXT NOT NULL,
        assignment_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        content_id UUID NOT NULL,
        version_id UUID NOT NULL,
        reviewer_principal_id UUID NULL,
        issuer_principal_id UUID NULL,
        class_ref TEXT NULL,
        issued_at TIMESTAMPTZ NULL,
        valid_until TIMESTAMPTZ NULL,
        revoked_at TIMESTAMPTZ NULL,
        revoked_by_principal_id UUID NULL,
        correlation_id UUID NOT NULL,
        CONSTRAINT pk_review_assignment_events PRIMARY KEY (event_id),
        CONSTRAINT ck_review_assignment_events_kind
            CHECK (event_kind IN ('ASSIGNED', 'REVOKED')),
        CONSTRAINT ck_review_assignment_events_class_ref
            CHECK ({class_ref_check}),
        CONSTRAINT ck_review_assignment_events_assigned_shape
            CHECK (
                event_kind <> 'ASSIGNED' OR (
                    reviewer_principal_id IS NOT NULL
                    AND issuer_principal_id IS NOT NULL
                    AND class_ref IS NOT NULL
                    AND issued_at IS NOT NULL
                    AND valid_until IS NOT NULL
                    AND valid_until > issued_at
                )
            ),
        CONSTRAINT ck_review_assignment_events_revoked_shape
            CHECK (
                event_kind <> 'REVOKED' OR (
                    revoked_at IS NOT NULL AND revoked_by_principal_id IS NOT NULL
                )
            ),
        CONSTRAINT fk_review_assignment_events_version
            FOREIGN KEY (tenant_id, content_id, version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT
    )
    """.format(
        class_ref_check=_CLASS_REF_CHECK
    ),
    "CREATE INDEX ix_review_assignment_events_tenant_id "
    "ON content.review_assignment_events (tenant_id)",
    "CREATE UNIQUE INDEX uq_review_assignment_assigned_id "
    "ON content.review_assignment_events (assignment_id) "
    "WHERE event_kind = 'ASSIGNED'",
    "CREATE UNIQUE INDEX uq_review_assignment_revoked_id "
    "ON content.review_assignment_events (assignment_id) "
    "WHERE event_kind = 'REVOKED'",
    """
    CREATE TABLE content.review_current_assignments (
        tenant_id UUID NOT NULL,
        content_id UUID NOT NULL,
        version_id UUID NOT NULL,
        active_assignment_id UUID NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CONSTRAINT pk_review_current_assignments PRIMARY KEY (
            tenant_id, content_id, version_id
        ),
        CONSTRAINT fk_review_current_assignments_version
            FOREIGN KEY (tenant_id, content_id, version_id)
            REFERENCES content.content_versions (tenant_id, content_id, version_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE OR REPLACE FUNCTION content.reject_review_assignment_event_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = content, pg_temp
    AS $$
    BEGIN
        RAISE EXCEPTION 'content.review_assignment_events is immutable'
            USING ERRCODE = '27000';
    END;
    $$
    """,
    """
    CREATE TRIGGER review_assignment_events_immutable_update
        BEFORE UPDATE ON content.review_assignment_events
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_review_assignment_event_mutation()
    """,
    """
    CREATE TRIGGER review_assignment_events_immutable_delete
        BEFORE DELETE ON content.review_assignment_events
        FOR EACH ROW
        EXECUTE FUNCTION content.reject_review_assignment_event_mutation()
    """,
    "ALTER TABLE content.review_assignment_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.review_assignment_events FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY review_assignment_events_tenant_isolation
        ON content.review_assignment_events
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
    "ALTER TABLE content.review_current_assignments ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE content.review_current_assignments FORCE ROW LEVEL SECURITY",
    """
    CREATE POLICY review_current_assignments_tenant_isolation
        ON content.review_current_assignments
        FOR ALL
        USING (tenant_id = content.current_tenant_id())
        WITH CHECK (tenant_id = content.current_tenant_id())
    """,
)


def _joined(*parts: str) -> str:
    return ",\n".join(part.strip("\n") for part in parts)


def _load_predecessor_module():
    migrate_context = op.get_context()
    script = ScriptDirectory.from_config(migrate_context.config)
    pred = script.get_revision(_PREDECESSOR_REVISION)
    if pred is None:
        raise RuntimeError(f"CI03-G2-I01: predecessor {_PREDECESSOR_REVISION!r} not found")
    return pred.module


def _upgrade_audit_statements() -> tuple[str, ...]:
    pred = _load_predecessor_module()

    base_actions = _joined(pred._CONTENT_ACTIONS_SQL, pred._ASSET_ACTIONS_SQL)
    actions_sql = _joined(
        base_actions,
        pred._TEACHING_BASE_ACTIONS_SQL,
        pred._MEMORY_ACTIONS_SQL,
        pred._ASSESSMENT_UPGRADE_ACTIONS_SQL,
        pred._LEARNING_ACTIONS_SQL,
        _CONTENT_ASSIGN_ACTIONS_SQL,
    )
    primary_actions_sql = _joined(
        pred._CONTENT_ACTIONS_SQL,
        pred._TEACHING_BASE_ACTIONS_SQL,
        pred._MEMORY_ACTIONS_SQL,
        pred._ASSESSMENT_UPGRADE_ACTIONS_SQL,
        pred._LEARNING_ACTIONS_SQL,
        _CONTENT_ASSIGN_ACTIONS_SQL,
    )
    content_increment = pred._CONTENT_INCREMENT_SQL
    stable_registration = _joined(
        """
                    'content.worksheet_hint.attest',
                    'content.review.assign',
                    'content.review.revoke'
""",
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
{pred._ASSET_ACTIONS_SQL}
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
                OR (action IN ({content_increment})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({stable_registration})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before)
                OR (action = 'asset.create'
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action = 'asset.revision.register'
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before)
                OR (action IN ({pred._ASSET_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({pred._TEACHING_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({pred._TEACHING_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({pred._MEMORY_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({pred._MEMORY_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({pred._ASSESSMENT_UPGRADE_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({pred._ASSESSMENT_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
                OR (action IN ({pred._LEARNING_CREATE_SQL})
                    AND resource_revision_before IS NULL
                    AND resource_revision_after = 0)
                OR (action IN ({pred._LEARNING_INCREMENT_SQL})
                    AND resource_revision_before IS NOT NULL
                    AND resource_revision_after = resource_revision_before + 1)
            )
        """,
    )


def _require_role(env_name: str, *, purpose: str) -> str:
    role = os.environ.get(env_name, "").strip()
    if not role:
        raise RuntimeError(f"{env_name} must be set to the {purpose}")
    if not _ROLE_NAME.fullmatch(role):
        raise RuntimeError(f"{env_name} must be a lowercase unquoted PostgreSQL identifier")
    return role


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
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON content.review_current_assignments TO {runtime}"
    )
    op.execute(f"REVOKE DELETE ON content.review_current_assignments FROM {runtime}")
    op.execute(
        f"GRANT SELECT, INSERT ON content.review_assignment_events TO {runtime}"
    )
    op.execute(f"REVOKE UPDATE, DELETE ON content.review_assignment_events FROM {runtime}")
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in _upgrade_audit_statements():
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
    op.execute(f"SET LOCAL ROLE {content_owner}")
    for table in (
        "content.review_current_assignments",
        "content.review_assignment_events",
    ):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        row_count = bind.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        if int(row_count) > 0:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(f"SET LOCAL ROLE {security_owner}")
    op.execute("ALTER TABLE security.audit_records DISABLE ROW LEVEL SECURITY")
    audit_count = bind.execute(
        text(
            """
            SELECT count(*)
            FROM security.audit_records
            WHERE action IN ('content.review.assign', 'content.review.revoke')
            """
        )
    ).scalar_one()
    op.execute("ALTER TABLE security.audit_records ENABLE ROW LEVEL SECURITY")
    if int(audit_count) > 0:
        op.execute(f"SET LOCAL ROLE {content_owner}")
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(f"SET LOCAL ROLE {content_owner}")
    op.execute("DROP TABLE IF EXISTS content.review_current_assignments")
    op.execute("DROP TABLE IF EXISTS content.review_assignment_events")
    op.execute(
        "DROP FUNCTION IF EXISTS content.reject_review_assignment_event_mutation()"
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in _predecessor_upgrade_audit_statements():
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")