"""AIEOS360-S02-I02 — PostgreSQL derived-fact projection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.engine import Engine

from aieos.development.principal_school_context import CLASS_REF_6A, CLASS_REF_6B
from aieos.domains.school_intelligence.application.errors import (
    SchoolIntelligenceReadUnavailable,
)
from aieos.domains.school_intelligence.infrastructure.read_projection import (
    SqlAlchemySchoolIntelligenceFactsReader,
)
from tests.dbutil import clear_asset_audit_rows_for_schema_downgrade
from tests.domains.school_intelligence.helpers_s02_i02 import (
    CROSS_TENANT_CLASS_REF,
    FIXED_NOW,
    OBSOLETE_POLICY_ID,
    OBSOLETE_POLICY_VERSION,
    UNAUTHORIZED_CLASS_REF,
    insert_assignment,
    insert_classroom_assessment,
    insert_evaluation,
    insert_execution,
    insert_remediation_origin,
    insert_submission,
    insert_work,
    seed_content,
)

pytestmark = pytest.mark.aieos360_s02_i02


@pytest.fixture(autouse=True)
def _clear_immutable_rows_after_test(bootstrap_engine: Engine) -> None:
    yield
    clear_asset_audit_rows_for_schema_downgrade(bootstrap_engine)


CURRENT_POLICY_ID = "aieos.learner_assessment.deterministic"
CURRENT_POLICY_VERSION = 1
FUTURE = datetime(2027, 1, 1, tzinfo=UTC)


def _reader(runtime_engine: Engine) -> SqlAlchemySchoolIntelligenceFactsReader:
    return SqlAlchemySchoolIntelligenceFactsReader(runtime_engine)


def _read(runtime_engine: Engine, tenant_id, class_refs= (CLASS_REF_6A, CLASS_REF_6B)):
    return _reader(runtime_engine).read_authorized_class_facts(
        tenant_id=tenant_id,
        authorized_class_refs=class_refs,
        evaluation_policy_id=CURRENT_POLICY_ID,
        evaluation_policy_version=CURRENT_POLICY_VERSION,
    )


class TestEmptyAndAssignments:
    def test_successful_empty_is_truthful_zero(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        snapshot = _read(runtime_engine, tenant_id)
        by_ref = {row.class_ref: row for row in snapshot.classes}
        assert by_ref[CLASS_REF_6A].teaching_assignment_count == 0
        assert by_ref[CLASS_REF_6B].learner_submission_count == 0
        assert snapshot.generated_at.tzinfo is not None

    def test_sql_reader_returns_explicit_zero_row_for_authorized_class(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        snapshot = _read(runtime_engine, tenant_id, class_refs=(CLASS_REF_6A,))
        assert len(snapshot.classes) == 1
        row = snapshot.classes[0]
        assert row.class_ref == CLASS_REF_6A
        assert row.teaching_assignment_count == 0
        assert row.learner_submission_count == 0
        assert row.current_policy_evaluation_count == 0
        assert row.has_recorded_classroom_assessment is False
        assert row.assignments_with_recorded_classroom_assessment_count == 0
        assert row.completed_teaching_execution_count == 0
        assert row.remediation_activity_count == 0

    def test_assignment_lifecycle_counts(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            lifecycle_state="ACTIVE",
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            lifecycle_state="CLOSED",
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            lifecycle_state="CANCELLED",
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assigned_at=FUTURE,
        )
        row = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}[
            CLASS_REF_6A
        ]
        assert row.teaching_assignment_count == 3
        assert row.assignment_lifecycle.active == 1
        assert row.assignment_lifecycle.closed == 1
        assert row.assignment_lifecycle.cancelled == 1
        assert (
            row.assignment_lifecycle.active
            + row.assignment_lifecycle.closed
            + row.assignment_lifecycle.cancelled
            == row.teaching_assignment_count
        )


class TestSubmissionsEvaluationsAndActivity:
    def test_submissions_and_current_policy_only(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        learner_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        assignment_id = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        other_assignment = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=UNAUTHORIZED_CLASS_REF,
        )
        sub_a, attempt_a = insert_submission(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=learner_id,
            assignment_id=assignment_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        sub_b, attempt_b = insert_submission(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=uuid.uuid7(),
            assignment_id=assignment_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        insert_submission(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=uuid.uuid7(),
            assignment_id=other_assignment,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=UNAUTHORIZED_CLASS_REF,
        )
        insert_evaluation(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=learner_id,
            submission_id=sub_a,
            attempt_id=attempt_a,
            assignment_id=assignment_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        insert_evaluation(
            bootstrap_engine,
            tenant_id=tenant_id,
            learner_id=uuid.uuid7(),
            submission_id=sub_b,
            attempt_id=attempt_b,
            assignment_id=assignment_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            policy_id=OBSOLETE_POLICY_ID,
            policy_version=OBSOLETE_POLICY_VERSION,
        )
        row = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}[
            CLASS_REF_6A
        ]
        assert row.learner_submission_count == 2
        assert row.current_policy_evaluation_count == 1
        assert 0 <= row.current_policy_evaluation_count <= row.learner_submission_count

    def test_classroom_execution_and_remediation(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        assignment_id = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=assignment_id,
            lifecycle_state="RECORDED",
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=assignment_id,
            lifecycle_state="VOIDED",
        )
        work_id = insert_work(
            bootstrap_engine, tenant_id=tenant_id, teacher_id=teacher_id
        )
        insert_execution(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            work_id=work_id,
            class_ref=CLASS_REF_6A,
            lifecycle_state="COMPLETED",
        )
        insert_execution(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            work_id=work_id,
            class_ref=CLASS_REF_6A,
            lifecycle_state="IN_PROGRESS",
        )
        insert_execution(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            work_id=work_id,
            class_ref=CLASS_REF_6A,
            lifecycle_state="CANCELLED",
        )
        insert_remediation_origin(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            source_class_ref=CLASS_REF_6A,
            class_label="Grade 6B",
        )
        insert_remediation_origin(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            source_class_ref=UNAUTHORIZED_CLASS_REF,
            class_label="Grade 6A",
        )
        rows = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}
        assert rows[CLASS_REF_6A].has_recorded_classroom_assessment is True
        assert rows[CLASS_REF_6A].assignments_with_recorded_classroom_assessment_count == 1
        assert rows[CLASS_REF_6A].completed_teaching_execution_count == 1
        assert rows[CLASS_REF_6A].remediation_activity_count == 1
        assert rows[CLASS_REF_6B].remediation_activity_count == 0


class TestIsolationAndFailures:
    def test_same_tenant_unauthorized_class_does_not_alter_metrics(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        baseline = _read(runtime_engine, tenant_id)
        insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=UNAUTHORIZED_CLASS_REF,
        )
        after = _read(runtime_engine, tenant_id)
        assert after.classes == baseline.classes

    def test_cross_tenant_facts_are_excluded(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        teacher_id = uuid.uuid7()
        other_teacher = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        other_content, other_version = seed_content(
            bootstrap_engine, tenant_id=other_tenant, owner_id=other_teacher
        )
        insert_assignment(
            bootstrap_engine,
            tenant_id=other_tenant,
            teacher_id=other_teacher,
            content_id=other_content,
            content_version_id=other_version,
            class_ref=CROSS_TENANT_CLASS_REF,
        )
        rows = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}
        assert rows[CLASS_REF_6A].teaching_assignment_count == 0
        assert rows[CLASS_REF_6B].teaching_assignment_count == 0

    def test_source_family_failure_is_unavailable(
        self, runtime_engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tenant_id = uuid.uuid7()
        reader = _reader(runtime_engine)

        def fail(self, connection, params):
            raise RuntimeError("source exploded")

        for method in (
            "_read_assignment_facts",
            "_read_submission_facts",
            "_read_evaluation_facts",
            "_read_classroom_facts",
            "_read_execution_facts",
            "_read_remediation_facts",
        ):
            monkeypatch.setattr(
                SqlAlchemySchoolIntelligenceFactsReader, method, fail
            )
            with pytest.raises(SchoolIntelligenceReadUnavailable):
                reader.read_authorized_class_facts(
                    tenant_id=tenant_id,
                    authorized_class_refs=(CLASS_REF_6A,),
                    evaluation_policy_id=CURRENT_POLICY_ID,
                    evaluation_policy_version=CURRENT_POLICY_VERSION,
                )
            monkeypatch.undo()


class TestClassroomAssessmentLineage:
    def test_coherent_recorded_assignment_counts(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        assignment_id = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=assignment_id,
            lifecycle_state="RECORDED",
        )
        row = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}[
            CLASS_REF_6A
        ]
        assert row.has_recorded_classroom_assessment is True
        assert row.assignments_with_recorded_classroom_assessment_count == 1

    def test_null_assignment_id_is_activity_without_assignment_count(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=None,
            lifecycle_state="RECORDED",
        )
        row = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}[
            CLASS_REF_6A
        ]
        assert row.has_recorded_classroom_assessment is True
        assert row.assignments_with_recorded_classroom_assessment_count == 0

    def test_nonexistent_assignment_id_does_not_count(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=uuid.uuid7(),
            lifecycle_state="RECORDED",
        )
        row = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}[
            CLASS_REF_6A
        ]
        assert row.has_recorded_classroom_assessment is True
        assert row.assignments_with_recorded_classroom_assessment_count == 0

    def test_cross_class_assignment_mismatch_does_not_count(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        assignment_6b = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6B,
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=assignment_6b,
            lifecycle_state="RECORDED",
        )
        rows = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}
        assert rows[CLASS_REF_6A].has_recorded_classroom_assessment is True
        assert rows[CLASS_REF_6A].assignments_with_recorded_classroom_assessment_count == 0
        assert rows[CLASS_REF_6B].has_recorded_classroom_assessment is False
        assert rows[CLASS_REF_6B].assignments_with_recorded_classroom_assessment_count == 0
        assert rows[CLASS_REF_6B].teaching_assignment_count == 1

    def test_cross_tenant_assignment_cannot_satisfy_lineage(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        other_tenant = uuid.uuid7()
        teacher_id = uuid.uuid7()
        other_teacher = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        other_content, other_version = seed_content(
            bootstrap_engine, tenant_id=other_tenant, owner_id=other_teacher
        )
        foreign_assignment = insert_assignment(
            bootstrap_engine,
            tenant_id=other_tenant,
            teacher_id=other_teacher,
            content_id=other_content,
            content_version_id=other_version,
            class_ref=CLASS_REF_6A,
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=foreign_assignment,
            lifecycle_state="RECORDED",
        )
        row = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}[
            CLASS_REF_6A
        ]
        assert row.has_recorded_classroom_assessment is True
        assert row.assignments_with_recorded_classroom_assessment_count == 0

    def test_voided_assessment_is_not_current_recorded_activity(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        content_id, version_id = seed_content(
            bootstrap_engine, tenant_id=tenant_id, owner_id=teacher_id
        )
        assignment_id = insert_assignment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
        )
        insert_classroom_assessment(
            bootstrap_engine,
            tenant_id=tenant_id,
            teacher_id=teacher_id,
            content_id=content_id,
            content_version_id=version_id,
            class_ref=CLASS_REF_6A,
            assignment_id=assignment_id,
            lifecycle_state="VOIDED",
        )
        row = {item.class_ref: item for item in _read(runtime_engine, tenant_id).classes}[
            CLASS_REF_6A
        ]
        assert row.has_recorded_classroom_assessment is False
        assert row.assignments_with_recorded_classroom_assessment_count == 0
