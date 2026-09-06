"""TOS-DEV10-I03S1 Principal kind classification on security.principals.

Revision ID: pedi090002
Revises: tosd090002
Create Date: 2026-09-06

Adds nullable ``principal_kind`` (HUMAN | WORKLOAD) without backfill or DEFAULT.
Existing rows remain NULL (unclassified). Classification is current SoR
authority — never supplied by JWT/headers.

Executes under AIEOS_SECURITY_SCHEMA_OWNER_ROLE, then restores
AIEOS_SCHEMA_OWNER_ROLE so Content migrations stay on the content owner.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "pedi090002"
down_revision: str | None = "tosd090002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA_OWNER_ROLE_ENV = "AIEOS_SCHEMA_OWNER_ROLE"
SECURITY_SCHEMA_OWNER_ROLE_ENV = "AIEOS_SECURITY_SCHEMA_OWNER_ROLE"
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


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


UPGRADE_STATEMENTS: tuple[str, ...] = (
    """
    ALTER TABLE security.principals
        ADD COLUMN principal_kind TEXT NULL
    """,
    """
    ALTER TABLE security.principals
        ADD CONSTRAINT ck_security_principals_principal_kind
        CHECK (
            principal_kind IS NULL
            OR principal_kind IN ('HUMAN', 'WORKLOAD')
        )
    """,
)

DOWNGRADE_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE security.principals "
    "DROP CONSTRAINT IF EXISTS ck_security_principals_principal_kind",
    "ALTER TABLE security.principals DROP COLUMN IF EXISTS principal_kind",
)


def upgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV,
        purpose="security schema-owner role",
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")


def downgrade() -> None:
    content_owner = _require_role(
        SCHEMA_OWNER_ROLE_ENV, purpose="Generic Content schema-owner role"
    )
    security_owner = _require_role(
        SECURITY_SCHEMA_OWNER_ROLE_ENV,
        purpose="security schema-owner role",
    )
    op.execute(f"SET LOCAL ROLE {security_owner}")
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
    op.execute(f"SET LOCAL ROLE {content_owner}")
