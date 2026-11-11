"""Shared helpers for GCI-I02 PostgreSQL tests. Not production runtime."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]

WORKSHEET_HINT_ATTESTATION_REVISION = "a360s010005"
WORKSHEET_HINT_ATTESTATION_TABLE = "worksheet_learner_hint_version_attestations"

CONTENT_TABLES_WITHOUT_WORKSHEET_HINT_ATTESTATION = frozenset(
    {
        "contents",
        "content_versions",
        "review_decisions",
        "publications",
        "version_asset_refs",
        "migration_import_records",
    }
)
CONTENT_TABLES_WITH_WORKSHEET_HINT_ATTESTATION = (
    CONTENT_TABLES_WITHOUT_WORKSHEET_HINT_ATTESTATION
    | {WORKSHEET_HINT_ATTESTATION_TABLE}
)


def worksheet_hint_attestation_table_exists(conn) -> bool:
    """Return whether the T2 attestation table is present in the current database."""
    return bool(
        conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'content' "
                f"AND table_name = '{WORKSHEET_HINT_ATTESTATION_TABLE}'"
                ")"
            )
        ).scalar()
    )


def current_alembic_revision(conn) -> str | None:
    """Return the single Alembic version_num, or None when no version table exists."""
    if not conn.execute(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
            ")"
        )
    ).scalar():
        return None
    return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def assert_worksheet_hint_attestation_present_at_forward_head(conn) -> None:
    """Fail closed when forward Alembic head lacks the T2 attestation table."""
    from aieos.platform.runtime.readiness import EXPECTED_ALEMBIC_HEAD

    revision = current_alembic_revision(conn)
    if revision != EXPECTED_ALEMBIC_HEAD:
        return
    if not worksheet_hint_attestation_table_exists(conn):
        msg = (
            f"T2 attestation table missing at Alembic head {EXPECTED_ALEMBIC_HEAD}"
        )
        raise AssertionError(msg)


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
                "OR action LIKE 'assessment.%' "
                "OR action LIKE 'learning.%' "
                "OR action = 'content.worksheet_hint.attest'"
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
        evaluations_exist = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'assessment' "
                "AND table_name = 'learner_assessment_evaluations'"
                ")"
            )
        ).scalar()
        if evaluations_exist:
            for table, trigger in (
                (
                    "learner_assessment_objective_evidence",
                    "learner_assessment_objective_evidence_immutable_delete",
                ),
                (
                    "learner_assessment_evaluation_items",
                    "learner_assessment_evaluation_items_immutable_delete",
                ),
                (
                    "learner_assessment_evaluations",
                    "learner_assessment_evaluations_immutable_delete",
                ),
            ):
                conn.execute(
                    text(
                        f"ALTER TABLE assessment.{table} DISABLE TRIGGER {trigger}"
                    )
                )
                conn.execute(
                    text(
                        f"ALTER TABLE assessment.{table} DISABLE ROW LEVEL SECURITY"
                    )
                )
            conn.execute(
                text("DELETE FROM assessment.learner_assessment_objective_evidence")
            )
            conn.execute(
                text("DELETE FROM assessment.learner_assessment_evaluation_items")
            )
            conn.execute(
                text("DELETE FROM assessment.learner_assessment_evaluations")
            )
            for table, trigger in (
                (
                    "learner_assessment_objective_evidence",
                    "learner_assessment_objective_evidence_immutable_delete",
                ),
                (
                    "learner_assessment_evaluation_items",
                    "learner_assessment_evaluation_items_immutable_delete",
                ),
                (
                    "learner_assessment_evaluations",
                    "learner_assessment_evaluations_immutable_delete",
                ),
            ):
                conn.execute(
                    text(
                        f"ALTER TABLE assessment.{table} ENABLE TRIGGER {trigger}"
                    )
                )
                conn.execute(
                    text(
                        f"ALTER TABLE assessment.{table} ENABLE ROW LEVEL SECURITY"
                    )
                )
                conn.execute(
                    text(
                        f"ALTER TABLE assessment.{table} FORCE ROW LEVEL SECURITY"
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
        hint_attestations_exist = conn.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'content' "
                "AND table_name = 'worksheet_learner_hint_version_attestations'"
                ")"
            )
        ).scalar()
        if hint_attestations_exist:
            conn.execute(
                text(
                    "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                    "DISABLE TRIGGER worksheet_hint_attestations_immutable_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text("DELETE FROM content.worksheet_learner_hint_version_attestations")
            )
            conn.execute(
                text(
                    "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                    "ENABLE TRIGGER worksheet_hint_attestations_immutable_delete"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
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
