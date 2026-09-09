"""Assessment HTTP v1 request/response models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClassroomAssessmentRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_ref: str = Field(min_length=1, max_length=512)
    content_id: UUID
    content_version_id: UUID
    class_result_level: str = Field(min_length=1, max_length=64)
    class_result_note: str | None = Field(default=None, max_length=4096)
    work_id: UUID | None = None
    execution_id: UUID | None = None
    assignment_id: UUID | None = None


class ClassroomAssessmentCorrectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_result_level: str = Field(min_length=1, max_length=64)
    class_result_note: str | None = Field(default=None, max_length=4096)


class ClassroomAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_id: UUID
    teacher_principal_id: UUID
    class_ref: str
    content_id: UUID
    content_version_id: UUID
    class_result_level: str
    class_result_note: str | None
    lifecycle_state: str
    work_id: UUID | None
    execution_id: UUID | None
    assignment_id: UUID | None
    aggregate_revision: int
    recorded_at: datetime
    voided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ClassroomAssessmentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ClassroomAssessmentResponse]


class LearnerAssessmentEvaluationItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    question_type: str
    outcome: str
    evaluation_method: str
    objective_ids: list[str]
    response_kind: str | None = None


class LearnerAssessmentObjectiveEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_id: str
    result: str


class LearnerAssessmentEvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: UUID
    learner_principal_id: UUID
    submission_id: UUID
    attempt_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    evaluation_policy_id: str
    evaluation_policy_version: int
    evaluated_at: datetime
    created_at: datetime
    items: list[LearnerAssessmentEvaluationItemResponse]
    objective_evidence: list[LearnerAssessmentObjectiveEvidenceResponse]
