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
        ALTER TABLE content.worksheet_learner_hint_version_attestations
        ADD CONSTRAINT fk_worksheet_hint_attestations_assignment
        FOREIGN KEY (tenant_id, content_id, version_id, review_assignment_id)
        REFERENCES content.review_assignment_issuances (
            tenant_id, content_id, version_id, assignment_id
        )
        ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        ALTER TABLE content.review_decisions
        ADD CONSTRAINT fk_review_decisions_assignment
        FOREIGN KEY (tenant_id, content_id, version_id, review_assignment_id)
        REFERENCES content.review_assignment_issuances (
            tenant_id, content_id, version_id, assignment_id
        )
        ON DELETE RESTRICT
        """
    )
    op.execute(f"GRANT SELECT, INSERT ON content.worksheet_learner_hint_version_attestations TO {runtime}")
    op.execute(f"GRANT SELECT, INSERT ON content.review_decisions TO {runtime}")


def downgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    bind = op.get_bind()
    op.execute(f"SET LOCAL ROLE {content_owner}")
    for table, column in (
        ("content.worksheet_learner_hint_version_attestations", "review_assignment_id"),
        ("content.review_decisions", "review_assignment_id"),
    ):
        count = bind.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL")
        ).scalar_one()
        if int(count) > 0:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.execute(
        "ALTER TABLE content.worksheet_learner_hint_version_attestations "
        "DROP CONSTRAINT IF EXISTS fk_worksheet_hint_attestations_assignment"
    )
    op.execute(
        "ALTER TABLE content.review_decisions "
        "DROP CONSTRAINT IF EXISTS fk_review_decisions_assignment"
    )
    op.execute(
        "ALTER TABLE content.worksheet_learner_hint_version_attestations "
        "DROP COLUMN IF EXISTS review_assignment_id"
    )
    op.execute(
        "ALTER TABLE content.review_decisions DROP COLUMN IF EXISTS review_assignment_id"
    )