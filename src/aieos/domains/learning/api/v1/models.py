"""Pydantic HTTP DTOs for Learning / Student OS. Not domain entities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class LearnerObjectiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str


class LearnerQuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    question_type: str
    options: list[str]


class LearnerResourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_id: UUID
    content_version_id: UUID
    content_type: str
    schema_id: str
    schema_version: int
    title: str
    learning_objectives: list[LearnerObjectiveResponse]
    instructions: str
    questions: list[LearnerQuestionResponse]


class StudentAssignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: UUID
    class_ref: str
    available_from: datetime
    due_at: datetime | None
    lifecycle_state: str
    currently_consumable: bool
    content_id: UUID
    content_version_id: UUID
    attempt_summary: str
    attempt_id: UUID | None
    resource: LearnerResourceResponse | None = None


class StudentAssignmentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StudentAssignmentResponse]
    next_cursor: str | None
    has_more: bool


class StudentHomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_assignment_count: int
    items: list[StudentAssignmentResponse]


class AttemptResponseItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    response_kind: str
    choice_value: str | None
    text_value: str | None
    boolean_value: bool | None


class AttemptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    attempt_number: int
    lifecycle_state: str
    started_at: datetime
    last_saved_at: datetime | None
    submitted_at: datetime | None
    submission_id: UUID | None
    aggregate_revision: int
    responses: list[AttemptResponseItemResponse]


class AttemptResponseWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    response_kind: str = Field(min_length=1)
    choice_value: str | None = None
    text_value: str | None = None
    boolean_value: StrictBool | None = None


class AttemptResponsesReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responses: list[AttemptResponseWriteRequest] = Field(default_factory=list)
