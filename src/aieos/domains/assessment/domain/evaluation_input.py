"""Immutable evaluator input contracts for LearnerAssessmentEvaluation.

These are Assessment-owned copies of exact ContentVersion questions and the
immutable LearnerSubmission snapshot. They are not Content or Learning
aggregates and must not query those domains.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from aieos.domains.assessment.domain.errors import (
    InvalidLearnerAssessmentEvaluationError,
)
from aieos.domains.assessment.domain.identities import require_foreign_uuid
from aieos.domains.assessment.domain.evaluation_vocabulary import (
    EvaluationQuestionType,
    EvaluationResponseKind,
    parse_evaluation_question_type,
    parse_evaluation_response_kind,
)

MAX_CLASS_REF_LENGTH: Final = 512
MAX_QUESTION_ID_LENGTH: Final = 128
MAX_OBJECTIVE_ID_LENGTH: Final = 128
MAX_OPTION_LENGTH: Final = 256
MAX_ANSWER_LENGTH: Final = 4096
MAX_QUESTION_TYPE_LENGTH: Final = 64
MAX_RESPONSE_KIND_LENGTH: Final = 64


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidLearnerAssessmentEvaluationError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidLearnerAssessmentEvaluationError(
            f"{label} must be timezone-aware"
        )
    return value


def _require_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidLearnerAssessmentEvaluationError(
            f"{label} must be a non-empty string"
        )
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidLearnerAssessmentEvaluationError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


def _require_question_id(value: str) -> str:
    return _require_text(
        value, label="question_id", max_length=MAX_QUESTION_ID_LENGTH
    )


def _require_objective_ids(value: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidLearnerAssessmentEvaluationError(
            "objective_ids must be a sequence of strings"
        )
    seen: set[str] = set()
    rows: list[str] = []
    for item in value:
        oid = _require_text(
            value=item, label="objective_id", max_length=MAX_OBJECTIVE_ID_LENGTH
        )
        if oid in seen:
            continue
        seen.add(oid)
        rows.append(oid)
    return tuple(rows)


def _require_options(value: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidLearnerAssessmentEvaluationError(
            "options must be a sequence of strings"
        )
    rows: list[str] = []
    for item in value:
        rows.append(
            _require_text(item, label="option", max_length=MAX_OPTION_LENGTH)
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class EvaluationContentQuestion:
    """Exact ContentVersion question facts required by policy v1."""

    question_id: str
    question_type: EvaluationQuestionType | str
    options: tuple[str, ...]
    answer: str
    objective_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "question_id", _require_question_id(self.question_id))
        set_(
            self,
            "question_type",
            parse_evaluation_question_type(self.question_type),
        )
        question_type = self.question_type
        if isinstance(question_type, str) and len(question_type) > MAX_QUESTION_TYPE_LENGTH:
            raise InvalidLearnerAssessmentEvaluationError(
                f"question_type must be at most {MAX_QUESTION_TYPE_LENGTH} characters"
            )
        set_(self, "options", _require_options(self.options))
        if not isinstance(self.answer, str):
            raise InvalidLearnerAssessmentEvaluationError("answer must be a string")
        if len(self.answer) > MAX_ANSWER_LENGTH:
            raise InvalidLearnerAssessmentEvaluationError(
                f"answer must be at most {MAX_ANSWER_LENGTH} characters"
            )
        set_(self, "answer", self.answer.strip())
        set_(self, "objective_ids", _require_objective_ids(self.objective_ids))


@dataclass(frozen=True, slots=True)
class EvaluationSubmittedResponse:
    """One immutable snapshot response. Absent questions are omitted."""

    question_id: str
    response_kind: EvaluationResponseKind | str
    value: str | bool

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "question_id", _require_question_id(self.question_id))
        set_(
            self,
            "response_kind",
            parse_evaluation_response_kind(self.response_kind),
        )
        kind = self.response_kind
        if isinstance(kind, str) and len(kind) > MAX_RESPONSE_KIND_LENGTH:
            raise InvalidLearnerAssessmentEvaluationError(
                f"response_kind must be at most {MAX_RESPONSE_KIND_LENGTH} characters"
            )
        if type(self.value) is bool:
            return
        if not isinstance(self.value, str) or not self.value.strip():
            raise InvalidLearnerAssessmentEvaluationError(
                "response value must be a boolean or a non-empty string"
            )
        stripped = self.value.strip()
        if len(stripped) > MAX_ANSWER_LENGTH:
            raise InvalidLearnerAssessmentEvaluationError(
                f"response value must be at most {MAX_ANSWER_LENGTH} characters"
            )
        set_(self, "value", stripped)


@dataclass(frozen=True, slots=True)
class LearnerAssessmentEvaluationRequest:
    """Lineage plus exact-version questions and the immutable snapshot."""

    tenant_id: UUID
    learner_principal_id: UUID
    submission_id: UUID
    attempt_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    questions: tuple[EvaluationContentQuestion, ...]
    responses: tuple[EvaluationSubmittedResponse, ...]
    evaluated_at: datetime

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.learner_principal_id, label="learner_principal_id"
        )
        require_foreign_uuid(self.submission_id, label="submission_id")
        require_foreign_uuid(self.attempt_id, label="attempt_id")
        require_foreign_uuid(
            self.teaching_assignment_id, label="teaching_assignment_id"
        )
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        set_(
            self,
            "class_ref",
            _require_text(
                self.class_ref, label="class_ref", max_length=MAX_CLASS_REF_LENGTH
            ),
        )
        set_(
            self,
            "evaluated_at",
            _require_aware(self.evaluated_at, label="evaluated_at"),
        )
        if not isinstance(self.questions, Sequence) or isinstance(
            self.questions, (str, bytes)
        ):
            raise InvalidLearnerAssessmentEvaluationError(
                "questions must be a sequence"
            )
        if not isinstance(self.responses, Sequence) or isinstance(
            self.responses, (str, bytes)
        ):
            raise InvalidLearnerAssessmentEvaluationError(
                "responses must be a sequence"
            )
        questions = tuple(self.questions)
        responses = tuple(self.responses)
        seen_questions: set[str] = set()
        for question in questions:
            if not isinstance(question, EvaluationContentQuestion):
                raise InvalidLearnerAssessmentEvaluationError(
                    "questions must be EvaluationContentQuestion values"
                )
            if question.question_id in seen_questions:
                raise InvalidLearnerAssessmentEvaluationError(
                    "exact-version questions cannot contain duplicate question_id"
                )
            seen_questions.add(question.question_id)
        seen_responses: set[str] = set()
        for response in responses:
            if not isinstance(response, EvaluationSubmittedResponse):
                raise InvalidLearnerAssessmentEvaluationError(
                    "responses must be EvaluationSubmittedResponse values"
                )
            if response.question_id in seen_responses:
                raise InvalidLearnerAssessmentEvaluationError(
                    "snapshot cannot contain duplicate question_id"
                )
            seen_responses.add(response.question_id)
            if response.question_id not in seen_questions:
                raise InvalidLearnerAssessmentEvaluationError(
                    "snapshot question_id is not on the exact ContentVersion"
                )
        set_(self, "questions", questions)
        set_(self, "responses", responses)
