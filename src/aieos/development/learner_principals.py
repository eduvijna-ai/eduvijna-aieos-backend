"""NON_PRODUCTION synthetic Student Principal identities (AIEOS360-S01-I01).

Deterministic UUID values for development/tests. Not production data.
Not a student profile SoR. Not a PrincipalKind.STUDENT.

Must never be imported by production runtime composition.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.teacher_os_review_scenario import (
    SCENARIO_NAMESPACE,
    SYNTHETIC_TENANT_ID,
)

NON_PRODUCTION = True

STUDENT_A_PRINCIPAL_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s01.synthetic-student-a"
)
STUDENT_B_PRINCIPAL_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s01.synthetic-student-b"
)

CLASS_REF_5A = "class-5a"
CLASS_REF_5B = "class-5b"

# Opaque development bearer aliases. Never treated as PrincipalId.
DEVELOPMENT_STUDENT_A_TOKEN = "dev-student-a"
DEVELOPMENT_STUDENT_B_TOKEN = "dev-student-b"

# Optional opaque School Context correlation metadata. Not authentication identity.
STUDENT_A_SCHOOL_LEARNER_REF = "dev-s01-student-a"

__all__ = [
    "NON_PRODUCTION",
    "SYNTHETIC_TENANT_ID",
    "STUDENT_A_PRINCIPAL_ID",
    "STUDENT_B_PRINCIPAL_ID",
    "CLASS_REF_5A",
    "CLASS_REF_5B",
    "DEVELOPMENT_STUDENT_A_TOKEN",
    "DEVELOPMENT_STUDENT_B_TOKEN",
    "STUDENT_A_SCHOOL_LEARNER_REF",
    "ensure_synthetic_student_principals",
]


def ensure_synthetic_student_principals(
    bootstrap_engine: Engine,
    *,
    principal_ids: tuple[UUID, ...] = (
        STUDENT_A_PRINCIPAL_ID,
        STUDENT_B_PRINCIPAL_ID,
    ),
) -> None:
    """Upsert ACTIVE HUMAN Student principals via bootstrap role.

    Reuses existing ``security.principals``. No student-profile table.
    """
    with bootstrap_engine.begin() as conn:
        for principal_id in principal_ids:
            conn.execute(
                text(
                    """
                    INSERT INTO security.principals (
                        principal_id, status, principal_kind, created_at, updated_at
                    ) VALUES (
                        :principal_id, 'ACTIVE', 'HUMAN',
                        clock_timestamp(), clock_timestamp()
                    )
                    ON CONFLICT (principal_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        principal_kind = EXCLUDED.principal_kind,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {"principal_id": principal_id},
            )
