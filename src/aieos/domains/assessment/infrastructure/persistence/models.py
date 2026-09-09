"""SQLAlchemy 2.0 table mapping for assessment.classroom_assessments.

Persistence representation only; the ClassroomAssessment aggregate remains the
domain authority. Composition references are stored without cross-domain
PostgreSQL foreign keys.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from aieos.domains.assessment.infrastructure.persistence.metadata import (
    assessment_metadata,
)

classroom_assessments_table = Table(
    "classroom_assessments",
    assessment_metadata,
    Column("assessment_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("teacher_principal_id", UUID(as_uuid=True), nullable=False),
    # Opaque School Context ClassRef. NOT a Class SoR foreign key.
    Column("class_ref", Text, nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("content_version_id", UUID(as_uuid=True), nullable=False),
    Column("class_result_level", Text, nullable=False),
    Column("class_result_note", Text, nullable=True),
    Column("lifecycle_state", Text, nullable=False),
    Column("work_id", UUID(as_uuid=True), nullable=True),
    Column("execution_id", UUID(as_uuid=True), nullable=True),
    Column("assignment_id", UUID(as_uuid=True), nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("voided_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "assessment_id", name="pk_assessment_classroom_assessments"
    ),
    UniqueConstraint(
        "tenant_id",
        "assessment_id",
        name="uq_assessment_classroom_assessments_tenant_assessment",
    ),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_assessment_classroom_assessments_aggregate_revision_nonnegative",
    ),
    CheckConstraint(
        "btrim(class_ref) <> ''",
        name="ck_assessment_classroom_assessments_class_ref_nonempty",
    ),
    CheckConstraint(
        "class_result_level IN ('DEMONSTRATED', 'MIXED', 'NOT_YET_DEMONSTRATED')",
        name="ck_assessment_classroom_assessments_class_result_level",
    ),
    CheckConstraint(
        "class_result_note IS NULL OR char_length(class_result_note) <= 4096",
        name="ck_assessment_classroom_assessments_class_result_note_length",
    ),
    CheckConstraint(
        "lifecycle_state IN ('RECORDED', 'VOIDED')",
        name="ck_assessment_classroom_assessments_lifecycle_state",
    ),
    CheckConstraint(
        "("
        "lifecycle_state = 'RECORDED' AND voided_at IS NULL"
        ") OR ("
        "lifecycle_state = 'VOIDED' AND voided_at IS NOT NULL"
        ")",
        name="ck_assessment_classroom_assessments_lifecycle_timestamps",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_assessment_classroom_assessments_updated_after_created",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_teacher",
        "tenant_id",
        "teacher_principal_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_teacher_lifecycle",
        "tenant_id",
        "teacher_principal_id",
        "lifecycle_state",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_class_ref",
        "tenant_id",
        "class_ref",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_content_version",
        "tenant_id",
        "content_id",
        "content_version_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_execution",
        "tenant_id",
        "execution_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_assignment",
        "tenant_id",
        "assignment_id",
    ),
    Index(
        "ix_assessment_classroom_assessments_tenant_work",
        "tenant_id",
        "work_id",
    ),
    schema="assessment",
)

learner_assessment_evaluations_table = Table(
    "learner_assessment_evaluations",
    assessment_metadata,
    Column("evaluation_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("learner_principal_id", UUID(as_uuid=True), nullable=False),
    Column("submission_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_id", UUID(as_uuid=True), nullable=False),
    Column("teaching_assignment_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("content_version_id", UUID(as_uuid=True), nullable=False),
    Column("class_ref", Text, nullable=False),
    Column("evaluation_policy_id", Text, nullable=False),
    Column("evaluation_policy_version", Integer, nullable=False),
    Column("evaluated_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "evaluation_id", name="pk_assessment_learner_assessment_evaluations"
    ),
    UniqueConstraint(
        "tenant_id",
        "evaluation_id",
        name="uq_assessment_learner_assessment_evaluations_tenant_evaluation",
    ),
    UniqueConstraint(
        "tenant_id",
        "submission_id",
        "evaluation_policy_id",
        "evaluation_policy_version",
        name="uq_assessment_learner_assessment_evaluations_business_identity",
    ),
    CheckConstraint(
        "btrim(class_ref) <> ''",
        name="ck_assessment_lae_class_ref_nonempty",
    ),
    CheckConstraint(
        "char_length(class_ref) <= 512",
        name="ck_assessment_lae_class_ref_length",
    ),
    CheckConstraint(
        "btrim(evaluation_policy_id) <> ''",
        name="ck_assessment_lae_policy_id_nonempty",
    ),
    CheckConstraint(
        "char_length(evaluation_policy_id) <= 128",
        name="ck_assessment_lae_policy_id_length",
    ),
    CheckConstraint(
        "evaluation_policy_version >= 1",
        name="ck_assessment_lae_policy_version_positive",
    ),
    CheckConstraint(
        "created_at = evaluated_at",
        name="ck_assessment_lae_created_equals_evaluated",
    ),
    Index(
        "ix_assessment_lae_tenant_submission",
        "tenant_id",
        "submission_id",
    ),
    Index(
        "ix_assessment_lae_tenant_assignment",
        "tenant_id",
        "teaching_assignment_id",
    ),
    schema="assessment",
)

learner_assessment_evaluation_items_table = Table(
    "learner_assessment_evaluation_items",
    assessment_metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("evaluation_id", UUID(as_uuid=True), nullable=False),
    Column("question_id", Text, nullable=False),
    Column("item_ordinal", Integer, nullable=False),
    Column("question_type", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("evaluation_method", Text, nullable=False),
    Column("objective_ids", ARRAY(Text), nullable=False),
    Column("response_kind", Text, nullable=True),
    PrimaryKeyConstraint(
        "tenant_id",
        "evaluation_id",
        "question_id",
        name="pk_assessment_learner_assessment_evaluation_items",
    ),
    UniqueConstraint(
        "tenant_id",
        "evaluation_id",
        "item_ordinal",
        name="uq_assessment_lae_items_ordinal",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "evaluation_id"],
        [
            "assessment.learner_assessment_evaluations.tenant_id",
            "assessment.learner_assessment_evaluations.evaluation_id",
        ],
        name="fk_assessment_lae_items_evaluation",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "btrim(question_id) <> ''",
        name="ck_assessment_lae_items_question_id_nonempty",
    ),
    CheckConstraint(
        "char_length(question_id) <= 128",
        name="ck_assessment_lae_items_question_id_length",
    ),
    CheckConstraint(
        "item_ordinal >= 0",
        name="ck_assessment_lae_items_ordinal_nonnegative",
    ),
    CheckConstraint(
        "btrim(question_type) <> ''",
        name="ck_assessment_lae_items_question_type_nonempty",
    ),
    CheckConstraint(
        "char_length(question_type) <= 64",
        name="ck_assessment_lae_items_question_type_length",
    ),
    CheckConstraint(
        "outcome IN ("
        "'CORRECT', 'INCORRECT', 'UNANSWERED', "
        "'OPEN_RESPONSE_UNEVALUATED', 'UNEVALUATED_POLICY_REJECT'"
        ")",
        name="ck_assessment_lae_items_outcome",
    ),
    CheckConstraint(
        "evaluation_method IN ("
        "'DETERMINISTIC_CONTENT_ANSWER', 'NO_RESPONSE', "
        "'OPEN_RESPONSE_BASELINE', 'POLICY_REJECT'"
        ")",
        name="ck_assessment_lae_items_evaluation_method",
    ),
    CheckConstraint(
        "("
        "outcome = 'UNANSWERED' AND evaluation_method = 'NO_RESPONSE' "
        "AND response_kind IS NULL"
        ") OR ("
        "outcome IN ('CORRECT', 'INCORRECT') "
        "AND evaluation_method = 'DETERMINISTIC_CONTENT_ANSWER' "
        "AND response_kind IS NOT NULL"
        ") OR ("
        "outcome = 'OPEN_RESPONSE_UNEVALUATED' "
        "AND evaluation_method = 'OPEN_RESPONSE_BASELINE' "
        "AND response_kind IS NOT NULL"
        ") OR ("
        "outcome = 'UNEVALUATED_POLICY_REJECT' "
        "AND evaluation_method = 'POLICY_REJECT' "
        "AND response_kind IS NOT NULL"
        ")",
        name="ck_assessment_lae_items_outcome_method_pairing",
    ),
    CheckConstraint(
        "response_kind IS NULL OR ("
        "btrim(response_kind) <> '' AND char_length(response_kind) <= 64"
        ")",
        name="ck_assessment_lae_items_response_kind_length",
    ),
    schema="assessment",
)

learner_assessment_objective_evidence_table = Table(
    "learner_assessment_objective_evidence",
    assessment_metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("evaluation_id", UUID(as_uuid=True), nullable=False),
    Column("objective_id", Text, nullable=False),
    Column("result", Text, nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "evaluation_id",
        "objective_id",
        name="pk_assessment_learner_assessment_objective_evidence",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "evaluation_id"],
        [
            "assessment.learner_assessment_evaluations.tenant_id",
            "assessment.learner_assessment_evaluations.evaluation_id",
        ],
        name="fk_assessment_lae_objective_evidence_evaluation",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "btrim(objective_id) <> ''",
        name="ck_assessment_lae_objective_id_nonempty",
    ),
    CheckConstraint(
        "char_length(objective_id) <= 128",
        name="ck_assessment_lae_objective_id_length",
    ),
    CheckConstraint(
        "result IN ("
        "'INSUFFICIENT_EVIDENCE', "
        "'DEMONSTRATED_ON_SUBMITTED_ITEMS', "
        "'MIXED_ON_SUBMITTED_ITEMS', "
        "'NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS'"
        ")",
        name="ck_assessment_lae_objective_result",
    ),
    schema="assessment",
)
