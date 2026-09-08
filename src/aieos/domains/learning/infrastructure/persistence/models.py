"""SQLAlchemy 2.0 table mapping for the Learning attempt/submission SoR.

Persistence representation only. Teaching/Content identities are opaque UUIDs
with no cross-domain PostgreSQL foreign keys.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from aieos.domains.learning.infrastructure.persistence.metadata import (
    learning_metadata,
)

attempts_table = Table(
    "attempts",
    learning_metadata,
    Column("attempt_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("learner_principal_id", UUID(as_uuid=True), nullable=False),
    Column("teaching_assignment_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("content_version_id", UUID(as_uuid=True), nullable=False),
    Column("class_ref", Text, nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("lifecycle_state", Text, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("last_saved_at", DateTime(timezone=True), nullable=True),
    Column("submitted_at", DateTime(timezone=True), nullable=True),
    Column("submission_id", UUID(as_uuid=True), nullable=True),
    Column("aggregate_revision", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("attempt_id", name="pk_learning_attempts"),
    UniqueConstraint(
        "tenant_id",
        "attempt_id",
        name="uq_learning_attempts_tenant_attempt",
    ),
    UniqueConstraint(
        "tenant_id",
        "learner_principal_id",
        "teaching_assignment_id",
        "attempt_number",
        name="uq_learning_attempts_sequence",
    ),
    CheckConstraint(
        "btrim(class_ref) <> ''",
        name="ck_learning_attempts_class_ref_nonempty",
    ),
    CheckConstraint(
        "char_length(class_ref) <= 512",
        name="ck_learning_attempts_class_ref_length",
    ),
    CheckConstraint(
        "attempt_number >= 1",
        name="ck_learning_attempts_attempt_number_min",
    ),
    CheckConstraint(
        "aggregate_revision >= 0",
        name="ck_learning_attempts_aggregate_revision_nonnegative",
    ),
    CheckConstraint(
        "lifecycle_state IN ('IN_PROGRESS', 'SUBMITTED')",
        name="ck_learning_attempts_lifecycle_state",
    ),
    CheckConstraint(
        "("
        "lifecycle_state = 'IN_PROGRESS' "
        "AND submitted_at IS NULL AND submission_id IS NULL"
        ") OR ("
        "lifecycle_state = 'SUBMITTED' "
        "AND submitted_at IS NOT NULL AND submission_id IS NOT NULL"
        ")",
        name="ck_learning_attempts_lifecycle_evidence",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_learning_attempts_updated_after_created",
    ),
    CheckConstraint(
        "last_saved_at IS NULL OR last_saved_at >= started_at",
        name="ck_learning_attempts_last_saved_after_started",
    ),
    CheckConstraint(
        "submitted_at IS NULL OR submitted_at >= started_at",
        name="ck_learning_attempts_submitted_after_started",
    ),
    Index(
        "ix_learning_attempts_tenant_learner",
        "tenant_id",
        "learner_principal_id",
    ),
    Index(
        "ix_learning_attempts_tenant_assignment",
        "tenant_id",
        "teaching_assignment_id",
    ),
    Index(
        "ix_learning_attempts_tenant_learner_assignment",
        "tenant_id",
        "learner_principal_id",
        "teaching_assignment_id",
    ),
    Index(
        "ix_learning_attempts_tenant_content_version",
        "tenant_id",
        "content_id",
        "content_version_id",
    ),
    Index(
        "uq_learning_attempts_one_in_progress",
        "tenant_id",
        "learner_principal_id",
        "teaching_assignment_id",
        unique=True,
        postgresql_where=text("lifecycle_state = 'IN_PROGRESS'"),
    ),
    schema="learning",
)

attempt_response_items_table = Table(
    "attempt_response_items",
    learning_metadata,
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_id", UUID(as_uuid=True), nullable=False),
    Column("question_id", Text, nullable=False),
    Column("response_kind", Text, nullable=False),
    Column("choice_value", Text, nullable=True),
    Column("text_value", Text, nullable=True),
    Column("boolean_value", Boolean, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint(
        "tenant_id",
        "attempt_id",
        "question_id",
        name="pk_learning_attempt_response_items",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "attempt_id"],
        ["learning.attempts.tenant_id", "learning.attempts.attempt_id"],
        name="fk_learning_attempt_response_items_attempt",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "btrim(question_id) <> ''",
        name="ck_learning_response_items_question_id_nonempty",
    ),
    CheckConstraint(
        "char_length(question_id) <= 128",
        name="ck_learning_response_items_question_id_length",
    ),
    CheckConstraint(
        "response_kind IN ('MULTIPLE_CHOICE', 'SHORT_ANSWER', 'TRUE_FALSE')",
        name="ck_learning_response_items_response_kind",
    ),
    CheckConstraint(
        "("
        "response_kind = 'MULTIPLE_CHOICE' "
        "AND choice_value IS NOT NULL AND btrim(choice_value) <> '' "
        "AND char_length(choice_value) <= 256 "
        "AND text_value IS NULL AND boolean_value IS NULL"
        ") OR ("
        "response_kind = 'SHORT_ANSWER' "
        "AND text_value IS NOT NULL AND btrim(text_value) <> '' "
        "AND char_length(text_value) <= 4096 "
        "AND choice_value IS NULL AND boolean_value IS NULL"
        ") OR ("
        "response_kind = 'TRUE_FALSE' "
        "AND boolean_value IS NOT NULL "
        "AND choice_value IS NULL AND text_value IS NULL"
        ")",
        name="ck_learning_response_items_exclusive_value",
    ),
    CheckConstraint(
        "updated_at >= created_at",
        name="ck_learning_response_items_updated_after_created",
    ),
    schema="learning",
)

submissions_table = Table(
    "submissions",
    learning_metadata,
    Column("submission_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("attempt_id", UUID(as_uuid=True), nullable=False),
    Column("learner_principal_id", UUID(as_uuid=True), nullable=False),
    Column("teaching_assignment_id", UUID(as_uuid=True), nullable=False),
    Column("content_id", UUID(as_uuid=True), nullable=False),
    Column("content_version_id", UUID(as_uuid=True), nullable=False),
    Column("class_ref", Text, nullable=False),
    Column("response_snapshot", JSONB, nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    Column("assignment_revision_at_submit", BigInteger, nullable=False),
    Column("due_at_at_submit", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("submission_id", name="pk_learning_submissions"),
    UniqueConstraint(
        "tenant_id",
        "submission_id",
        name="uq_learning_submissions_tenant_submission",
    ),
    UniqueConstraint(
        "tenant_id",
        "attempt_id",
        name="uq_learning_submissions_tenant_attempt",
    ),
    ForeignKeyConstraint(
        ["tenant_id", "attempt_id"],
        ["learning.attempts.tenant_id", "learning.attempts.attempt_id"],
        name="fk_learning_submissions_attempt",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "btrim(class_ref) <> ''",
        name="ck_learning_submissions_class_ref_nonempty",
    ),
    CheckConstraint(
        "char_length(class_ref) <= 512",
        name="ck_learning_submissions_class_ref_length",
    ),
    CheckConstraint(
        "assignment_revision_at_submit >= 0",
        name="ck_learning_submissions_assignment_revision_nonnegative",
    ),
    CheckConstraint(
        "jsonb_typeof(response_snapshot) = 'array'",
        name="ck_learning_submissions_response_snapshot_array",
    ),
    schema="learning",
)
