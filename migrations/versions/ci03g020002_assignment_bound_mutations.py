"""CI03-G2-I02 assignment-bound attestation and review decisions (ADR-AIEOS-064 O64-05).

Revision ID: ci03g020002
Revises: ci03g020001
Create Date: 2026-11-24
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "ci03g020002"
down_revision: str | None = "ci03g020001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
RUNTIME_ROLE_ENV = "AIEOS_RUNTIME_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

_DOWNGRADE_BLOCKED = (
    "CI03-G2-I02 downgrade refused: assignment-bound mutation evidence exists"
)


def _require_role(env_key: str, *, purpose: str) -> str:
    value = os.environ.get(env_key)
    if value is None or not _ROLE_NAME.fullmatch(value):
        raise RuntimeError(f"CI03-G2-I02: {purpose} requires {env_key}")
    return value


def upgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
    )
    runtime = _require_role(RUNTIME_ROLE_ENV, purpose="runtime role")
    op.execute(f"SET LOCAL ROLE {content_owner}")
    op.execute(
        """
        ALTER TABLE content.worksheet_learner_hint_version_attestations
        ADD COLUMN review_assignment_id UUID NULL
        """
    )
    op.execute(
        """
        ALTER TABLE content.review_decisions
        ADD COLUMN review_assignment_id UUID NULL
        """
    )
    op.execute(
        """
        ALTER TABLE content.review_assignment_issuances
        ADD CONSTRAINT uq_review_assignment_issuances_resource_reviewer
            UNIQUE (
                tenant_id,
                content_id,
                version_id,
                assignment_id,
                reviewer_principal_id
            )
        """
    )
    op.execute(
        """
        ALTER TABLE content.worksheet_learner_hint_version_attestations
        ADD CONSTRAINT fk_worksheet_hint_attestations_assignment
        FOREIGN KEY (
            tenant_id,
            content_id,
            version_id,
            review_assignment_id,
            reviewer_principal_id
        )
        REFERENCES content.review_assignment_issuances (
            tenant_id,
            content_id,
            version_id,
            assignment_id,
            reviewer_principal_id
        )
        ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        ALTER TABLE content.review_decisions
        ADD CONSTRAINT fk_review_decisions_assignment
        FOREIGN KEY (
            tenant_id,
            content_id,
            version_id,
            review_assignment_id,
            reviewer_principal_id
        )
        REFERENCES content.review_assignment_issuances (
            tenant_id,
            content_id,
            version_id,
            assignment_id,
            reviewer_principal_id
        )
        ON DELETE RESTRICT
        """
    )
    op.execute(f"GRANT SELECT, INSERT ON content.worksheet_learner_hint_version_attestations TO {runtime}")
    op.execute(f"GRANT SELECT, INSERT ON content.review_decisions TO {runtime}")
    op.execute(f"SET LOCAL ROLE {security_owner}")
    op.execute(
        """
        ALTER TABLE security.authorization_denial_records
        DROP CONSTRAINT ck_authorization_denial_records_action
        """
    )
    op.execute(
        """
        ALTER TABLE security.authorization_denial_records
        ADD CONSTRAINT ck_authorization_denial_records_action
            CHECK (
                action IN (
                    'content.review.assign.denied',
                    'content.review.revoke.denied',
                    'content.review.mutation.denied'
                )
            )
        """
    )


def downgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV, purpose="security schema-owner role"
    )
    bind = op.get_bind()
    op.execute(f"SET LOCAL ROLE {content_owner}")
    for table, column in (
        ("content.worksheet_learner_hint_version_attestations", "review_assignment_id"),
        ("content.review_decisions", "review_assignment_id"),
    ):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        count = bind.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL")
        ).scalar_one()
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        if int(count) > 0:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(f"SET LOCAL ROLE {security_owner}")
    op.execute("ALTER TABLE security.authorization_denial_records DISABLE ROW LEVEL SECURITY")
    mutation_denial_count = bind.execute(
        text(
            """
            SELECT count(*)
            FROM security.authorization_denial_records
            WHERE action = 'content.review.mutation.denied'
            """
        )
    ).scalar_one()
    op.execute("ALTER TABLE security.authorization_denial_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE security.authorization_denial_records FORCE ROW LEVEL SECURITY")
    if int(mutation_denial_count) > 0:
        op.execute(f"SET LOCAL ROLE {content_owner}")
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(
        "ALTER TABLE security.authorization_denial_records "
        "DROP CONSTRAINT IF EXISTS ck_authorization_denial_records_action"
    )
    op.execute(
        """
        ALTER TABLE security.authorization_denial_records
        ADD CONSTRAINT ck_authorization_denial_records_action
            CHECK (
                action IN (
                    'content.review.assign.denied',
                    'content.review.revoke.denied'
                )
            )
        """
    )
    op.execute(f"SET LOCAL ROLE {content_owner}")
    op.execute(
        "ALTER TABLE content.worksheet_learner_hint_version_attestations "
        "DROP CONSTRAINT IF EXISTS fk_worksheet_hint_attestations_assignment"
    )
    op.execute(
        "ALTER TABLE content.review_decisions "
        "DROP CONSTRAINT IF EXISTS fk_review_decisions_assignment"
    )
    op.execute(
        "ALTER TABLE content.review_assignment_issuances "
        "DROP CONSTRAINT IF EXISTS uq_review_assignment_issuances_resource_reviewer"
    )
    op.execute(
        "ALTER TABLE content.worksheet_learner_hint_version_attestations "
        "DROP COLUMN IF EXISTS review_assignment_id"
    )
    op.execute(
        "ALTER TABLE content.review_decisions DROP COLUMN IF EXISTS review_assignment_id"
    )