"""Shared helpers for GCI-I02 PostgreSQL tests. Not production runtime."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]


def set_tenant(conn, tenant_id: uuid.UUID) -> None:
    conn.execute(
        text("SELECT set_config('aieos.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


def clear_asset_audit_rows_for_schema_downgrade(engine) -> None:
    """TEST-ONLY isolation for historical Alembic cycle tests.

    Production downgrade paths remain fail-closed and never delete audit
    evidence. The shared pytest PostgreSQL is session-scoped; immutable
    asset.*, teaching.*, assessment.*, teacher_memories, and learning.* rows
    would otherwise block unrelated downgrades.
    """
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'security' AND table_name = 'audit_records'"
                ")"
            )
        ).scalar()
        if not exists:
            return
        conn.execute(
            text(
                "ALTER TABLE security.audit_records "
                "DISABLE TRIGGER audit_records_immutable_delete"
            )
        )
        conn.execute(
            text(
                "DELETE FROM security.audit_records "
                "WHERE action LIKE 'asset.%' "
                "OR action LIKE 'teaching.%' "
                "OR action LIKE 'assessment.%'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE security.audit_records "
                "ENABLE TRIGGER audit_records_immutable_delete"
            )
        )
        assessment_exists = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'assessment' "
                "AND table_name = 'classroom_assessments'"
                ")"
            )
        ).scalar()
        if assessment_exists:
            conn.execute(
                text(
                    "ALTER TABLE assessment.classroom_assessments "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(text("DELETE FROM assessment.classroom_assessments"))
            conn.execute(
                text(
                    "ALTER TABLE assessment.classroom_assessments "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE assessment.classroom_assessments "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
        remediation_origins_exist = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'teaching' "
                "AND table_name = 'work_remediation_origins'"
                ")"
            )
        ).scalar()
        if remediation_origins_exist:
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "DISABLE TRIGGER "
                    "teaching_work_remediation_origins_immutable_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(text("DELETE FROM teaching.work_remediation_origins"))
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "ENABLE TRIGGER "
                    "teaching_work_remediation_origins_immutable_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.work_remediation_origins "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
        teaching_works_exist = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'teaching' AND table_name = 'works'"
                ")"
            )
        ).scalar()
        if teaching_works_exist:
            conn.execute(
                text("ALTER TABLE teaching.works DISABLE ROW LEVEL SECURITY")
            )
            conn.execute(
                text(
                    "DELETE FROM teaching.works "
                    "WHERE intent_type = 'remediate_class'"
                )
            )
            conn.execute(text("ALTER TABLE teaching.works ENABLE ROW LEVEL SECURITY"))
            conn.execute(text("ALTER TABLE teaching.works FORCE ROW LEVEL SECURITY"))
        teacher_memories_exist = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'teaching' "
                "AND table_name = 'teacher_memories'"
                ")"
            )
        ).scalar()
        if teacher_memories_exist:
            conn.execute(
                text(
                    "ALTER TABLE teaching.teacher_memories "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(text("DELETE FROM teaching.teacher_memories"))
            conn.execute(
                text(
                    "ALTER TABLE teaching.teacher_memories "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE teaching.teacher_memories "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
        learning_exists = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'learning' AND table_name = 'attempts'"
                ")"
            )
        ).scalar()
        if learning_exists:
            for table, disable_triggers in (
                (
                    "attempt_response_items",
                    (
                        "learning_attempt_response_items_in_progress_delete",
                    ),
                ),
                (
                    "submissions",
                    (
                        "learning_submissions_immutable_delete",
                    ),
                ),
                ("attempts", ()),
            ):
                for trigger in disable_triggers:
                    conn.execute(
                        text(
                            f"ALTER TABLE learning.{table} "
                            f"DISABLE TRIGGER {trigger}"
                        )
                    )
                conn.execute(
                    text(
                        f"ALTER TABLE learning.{table} DISABLE ROW LEVEL SECURITY"
                    )
                )
            conn.execute(text("DELETE FROM learning.attempt_response_items"))
            conn.execute(text("DELETE FROM learning.submissions"))
            conn.execute(text("DELETE FROM learning.attempts"))
            for table, enable_triggers in (
                (
                    "attempt_response_items",
                    (
                        "learning_attempt_response_items_in_progress_delete",
                    ),
                ),
                (
                    "submissions",
                    ("learning_submissions_immutable_delete",),
                ),
                ("attempts", ()),
            ):
                for trigger in enable_triggers:
                    conn.execute(
                        text(
                            f"ALTER TABLE learning.{table} "
                            f"ENABLE TRIGGER {trigger}"
                        )
                    )
                conn.execute(
                    text(f"ALTER TABLE learning.{table} ENABLE ROW LEVEL SECURITY")
                )
                conn.execute(
                    text(
                        f"ALTER TABLE learning.{table} FORCE ROW LEVEL SECURITY"
                    )
                )
