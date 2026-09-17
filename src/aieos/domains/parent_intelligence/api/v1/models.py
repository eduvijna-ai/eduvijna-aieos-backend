"""Parent Intelligence HTTP v1 request/response models.

Strict positive-allowlist Pydantic schemas. Distinct from Student, Teacher,
and Principal representations.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ParentIntelligenceTimeWindowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    start: datetime | None
    end: datetime


class ParentAssignmentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: UUID
    title: str
    content_type: str
    available_from: datetime
    due_at: datetime | None
    attempt_status: str
    submitted_at: datetime | None


class ParentChildCardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learner_principal_id: UUID
    assignments: list[ParentAssignmentStatusResponse] = Field(default_factory=list)


class ParentIntelligenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    projection_mode: str
    time_window: ParentIntelligenceTimeWindowResponse
    children: list[ParentChildCardResponse] = Field(default_factory=list)
