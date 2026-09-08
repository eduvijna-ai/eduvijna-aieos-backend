"""Positive-allowlist learner-safe ContentVersion projection.

Explicitly constructs learner DTOs. Does not pass through payload.model_dump,
dict(payload), or denylist-only stripping.
"""

from __future__ import annotations

from typing import Mapping
from uuid import UUID

from pydantic import ValidationError

from aieos.domains.education.content_payloads_v1 import HomeworkV1, QuizV1
from aieos.domains.education.schema import (
    HOMEWORK_CONTENT_TYPE,
    HOMEWORK_SCHEMA_ID,
    HOMEWORK_SCHEMA_VERSION,
    QUIZ_CONTENT_TYPE,
    QUIZ_SCHEMA_ID,
    QUIZ_SCHEMA_VERSION,
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
)
from aieos.domains.education.worksheet_v1 import QuestionType, WorksheetV1
from aieos.domains.learning.application.errors import ContentNotLearnerConsumable
from aieos.domains.learning.application.models import (
    ExactAssignedContent,
    LearnerObjective,
    LearnerQuestion,
    LearnerResource,
)
from aieos.domains.learning.domain.response_kind import AttemptResponseKind

_QUESTION_TYPE_MAP = {
    QuestionType.MULTIPLE_CHOICE: AttemptResponseKind.MULTIPLE_CHOICE.value,
    QuestionType.SHORT_ANSWER: AttemptResponseKind.SHORT_ANSWER.value,
    QuestionType.TRUE_FALSE: AttemptResponseKind.TRUE_FALSE.value,
}

_SUPPORTED: dict[tuple[str, str, int], type[WorksheetV1] | type[QuizV1] | type[HomeworkV1]] = {
    (WORKSHEET_CONTENT_TYPE, WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION): WorksheetV1,
    (QUIZ_CONTENT_TYPE, QUIZ_SCHEMA_ID, QUIZ_SCHEMA_VERSION): QuizV1,
    (HOMEWORK_CONTENT_TYPE, HOMEWORK_SCHEMA_ID, HOMEWORK_SCHEMA_VERSION): HomeworkV1,
}


def project_learner_resource(assigned: ExactAssignedContent) -> LearnerResource:
    key = (assigned.content_type, assigned.schema_id, assigned.schema_version)
    model_cls = _SUPPORTED.get(key)
    if model_cls is None:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )
    payload = _payload_for_model(model_cls, assigned.payload)
    try:
        parsed = model_cls.model_validate(payload)
    except ValidationError as exc:
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        ) from exc
    return _from_parsed(
        content_id=assigned.content_id,
        content_version_id=assigned.content_version_id,
        content_type=assigned.content_type,
        schema_id=assigned.schema_id,
        schema_version=assigned.schema_version,
        parsed=parsed,
    )


def _payload_for_model(
    model_cls: type[WorksheetV1] | type[QuizV1] | type[HomeworkV1],
    payload: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )
    fields = model_cls.model_fields
    return {key: payload[key] for key in fields if key in payload}


def _from_parsed(
    *,
    content_id: UUID,
    content_version_id: UUID,
    content_type: str,
    schema_id: str,
    schema_version: int,
    parsed: WorksheetV1 | QuizV1 | HomeworkV1,
) -> LearnerResource:
    instructions = getattr(parsed, "instructions", "")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ContentNotLearnerConsumable(
            "exact assigned ContentVersion is not learner-consumable"
        )
    questions: list[LearnerQuestion] = []
    for question in parsed.questions:
        mapped = _QUESTION_TYPE_MAP.get(question.question_type)
        if mapped is None:
            raise ContentNotLearnerConsumable(
                "exact assigned ContentVersion is not learner-consumable"
            )
        questions.append(
            LearnerQuestion(
                id=question.id,
                prompt=question.prompt,
                question_type=mapped,
                options=tuple(question.options),
            )
        )
    return LearnerResource(
        content_id=content_id,
        content_version_id=content_version_id,
        content_type=content_type,
        schema_id=schema_id,
        schema_version=schema_version,
        title=parsed.title,
        learning_objectives=tuple(
            LearnerObjective(id=obj.id, text=obj.text)
            for obj in parsed.learning_objectives
        ),
        instructions=instructions,
        questions=tuple(questions),
    )
