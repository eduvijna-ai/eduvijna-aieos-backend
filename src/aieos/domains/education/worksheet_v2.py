"""WorksheetV2 structured educational payload with optional learner_hint (ADR-063 T1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aieos.domains.education.worksheet_hint_safety import (
    LearnerHintValidationError,
    validate_learner_hint_string,
)
from aieos.domains.education.worksheet_v1 import LearningObjectiveV1, WorksheetQuestionV1


class WorksheetQuestionV2(WorksheetQuestionV1):
    """Worksheet question with optional validated learner_hint (education.worksheet@2)."""

    learner_hint: Annotated[str | None, Field(default=None)] = None

    @field_validator("learner_hint", mode="before")
    @classmethod
    def _validate_learner_hint(cls, value: object) -> str | None:
        if value is None:
            raise ValueError("learner_hint failed validation")
        try:
            return validate_learner_hint_string(value)
        except LearnerHintValidationError as exc:
            raise ValueError("learner_hint failed validation") from exc


class WorksheetV2(BaseModel):
    """Strict Worksheet payload for education.worksheet@2 (retains V1 invariants)."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    teacher_summary: str = Field(min_length=1)
    learning_objectives: list[LearningObjectiveV1] = Field(min_length=1)
    instructions: str = Field(min_length=1)
    questions: list[WorksheetQuestionV2] = Field(min_length=6, max_length=12)
    teacher_notes: str | None = None

    @model_validator(mode="after")
    def _ids_and_mappings(self) -> WorksheetV2:
        objective_ids = [obj.id for obj in self.learning_objectives]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("learning objective IDs must be unique")
        question_ids = [q.id for q in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question IDs must be unique")
        known = set(objective_ids)
        for question in self.questions:
            if any(oid not in known for oid in question.objective_ids):
                raise ValueError("every question must map to valid objective IDs")
        return self


def normalized_worksheet_v2_mapping(parsed: WorksheetV2) -> dict[str, object]:
    """Immutable append mapping with trimmed hints and absent optional keys."""
    return parsed.model_dump(mode="json", exclude_none=True)


def normalized_worksheet_v2_mapping_from_raw(raw: Mapping[str, object]) -> dict[str, object]:
    """Validate raw worksheet@2 input and return the normalized append mapping."""
    return normalized_worksheet_v2_mapping(WorksheetV2.model_validate(dict(raw)))
