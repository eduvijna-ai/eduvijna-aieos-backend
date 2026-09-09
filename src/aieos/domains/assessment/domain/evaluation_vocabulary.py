"""Canonical LearnerAssessmentEvaluation vocabularies (ADR-AIEOS-059).

Persisted explicitly. A later evaluation-policy version may reuse these
codes; it must not silently redefine them.
"""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.assessment.domain.errors import (
    InvalidLearnerAssessmentEvaluationError,
)


class ItemOutcome(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    UNANSWERED = "UNANSWERED"
    OPEN_RESPONSE_UNEVALUATED = "OPEN_RESPONSE_UNEVALUATED"
    UNEVALUATED_POLICY_REJECT = "UNEVALUATED_POLICY_REJECT"


class EvaluationMethod(StrEnum):
    DETERMINISTIC_CONTENT_ANSWER = "DETERMINISTIC_CONTENT_ANSWER"
    NO_RESPONSE = "NO_RESPONSE"
    OPEN_RESPONSE_BASELINE = "OPEN_RESPONSE_BASELINE"
    POLICY_REJECT = "POLICY_REJECT"


class ObjectiveEvidenceResult(StrEnum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    DEMONSTRATED_ON_SUBMITTED_ITEMS = "DEMONSTRATED_ON_SUBMITTED_ITEMS"
    MIXED_ON_SUBMITTED_ITEMS = "MIXED_ON_SUBMITTED_ITEMS"
    NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS = (
        "NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS"
    )


class EvaluationQuestionType(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    SHORT_ANSWER = "short_answer"


class EvaluationResponseKind(StrEnum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    TRUE_FALSE = "TRUE_FALSE"
    SHORT_ANSWER = "SHORT_ANSWER"


QUESTION_TYPE_TO_RESPONSE_KIND: dict[EvaluationQuestionType, EvaluationResponseKind] = {
    EvaluationQuestionType.MULTIPLE_CHOICE: EvaluationResponseKind.MULTIPLE_CHOICE,
    EvaluationQuestionType.TRUE_FALSE: EvaluationResponseKind.TRUE_FALSE,
    EvaluationQuestionType.SHORT_ANSWER: EvaluationResponseKind.SHORT_ANSWER,
}


def parse_item_outcome(value: ItemOutcome | str) -> ItemOutcome:
    if isinstance(value, ItemOutcome):
        return value
    if not isinstance(value, str):
        raise InvalidLearnerAssessmentEvaluationError("outcome must be a string")
    try:
        return ItemOutcome(value)
    except ValueError as exc:
        raise InvalidLearnerAssessmentEvaluationError(
            f"outcome is not a canonical item outcome: {value!r}"
        ) from exc


def parse_evaluation_method(value: EvaluationMethod | str) -> EvaluationMethod:
    if isinstance(value, EvaluationMethod):
        return value
    if not isinstance(value, str):
        raise InvalidLearnerAssessmentEvaluationError(
            "evaluation_method must be a string"
        )
    try:
        return EvaluationMethod(value)
    except ValueError as exc:
        raise InvalidLearnerAssessmentEvaluationError(
            f"evaluation_method is not a canonical evaluation method: {value!r}"
        ) from exc


def parse_objective_evidence_result(
    value: ObjectiveEvidenceResult | str,
) -> ObjectiveEvidenceResult:
    if isinstance(value, ObjectiveEvidenceResult):
        return value
    if not isinstance(value, str):
        raise InvalidLearnerAssessmentEvaluationError(
            "objective evidence result must be a string"
        )
    try:
        return ObjectiveEvidenceResult(value)
    except ValueError as exc:
        raise InvalidLearnerAssessmentEvaluationError(
            f"objective evidence result is not canonical: {value!r}"
        ) from exc


def parse_evaluation_question_type(
    value: EvaluationQuestionType | str,
) -> EvaluationQuestionType | str:
    if isinstance(value, EvaluationQuestionType):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidLearnerAssessmentEvaluationError(
            "question_type must be a non-empty string"
        )
    stripped = value.strip()
    try:
        return EvaluationQuestionType(stripped)
    except ValueError:
        return stripped


def parse_evaluation_response_kind(
    value: EvaluationResponseKind | str,
) -> EvaluationResponseKind | str:
    if isinstance(value, EvaluationResponseKind):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidLearnerAssessmentEvaluationError(
            "response_kind must be a non-empty string"
        )
    stripped = value.strip()
    try:
        return EvaluationResponseKind(stripped)
    except ValueError:
        return stripped
