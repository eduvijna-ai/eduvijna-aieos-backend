"""Deterministic Learner Assessment evaluation policy v1.

evaluation_policy_id = aieos.learner_assessment.deterministic
evaluation_policy_version = 1

This module is the named, versioned evaluator. A grading-rule change or
bugfix that affects evaluation semantics requires a new policy version class.
No AI, model, or provider call is permitted.
"""

from __future__ import annotations

from typing import Final

from aieos.domains.assessment.domain.evaluation import (
    LearnerAssessmentEvaluation,
    LearnerAssessmentEvaluationItem,
    LearnerAssessmentObjectiveEvidence,
)
from aieos.domains.assessment.domain.evaluation_input import (
    EvaluationContentQuestion,
    EvaluationSubmittedResponse,
    LearnerAssessmentEvaluationRequest,
)
from aieos.domains.assessment.domain.evaluation_vocabulary import (
    EvaluationMethod,
    EvaluationQuestionType,
    EvaluationResponseKind,
    ItemOutcome,
    ObjectiveEvidenceResult,
    QUESTION_TYPE_TO_RESPONSE_KIND,
)

DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID: Final = (
    "aieos.learner_assessment.deterministic"
)
DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION: Final = 1

_TRUE_FALSE_CONTENT_ANSWERS: Final = {"true": True, "false": False}
_AUTHORITATIVE_OUTCOMES: Final = frozenset(
    {ItemOutcome.CORRECT, ItemOutcome.INCORRECT}
)
_INSUFFICIENT_OUTCOMES: Final = frozenset(
    {
        ItemOutcome.UNANSWERED,
        ItemOutcome.OPEN_RESPONSE_UNEVALUATED,
        ItemOutcome.UNEVALUATED_POLICY_REJECT,
    }
)


def _question_type_value(question: EvaluationContentQuestion) -> str:
    question_type = question.question_type
    return question_type.value if hasattr(question_type, "value") else str(question_type)


def _response_kind_value(response: EvaluationSubmittedResponse) -> str:
    kind = response.response_kind
    return kind.value if hasattr(kind, "value") else str(kind)


def _policy_reject(
    question: EvaluationContentQuestion, response: EvaluationSubmittedResponse
) -> LearnerAssessmentEvaluationItem:
    return LearnerAssessmentEvaluationItem(
        question_id=question.question_id,
        question_type=_question_type_value(question),
        outcome=ItemOutcome.UNEVALUATED_POLICY_REJECT,
        evaluation_method=EvaluationMethod.POLICY_REJECT,
        objective_ids=question.objective_ids,
        response_kind=_response_kind_value(response),
    )


def _unanswered(question: EvaluationContentQuestion) -> LearnerAssessmentEvaluationItem:
    return LearnerAssessmentEvaluationItem(
        question_id=question.question_id,
        question_type=_question_type_value(question),
        outcome=ItemOutcome.UNANSWERED,
        evaluation_method=EvaluationMethod.NO_RESPONSE,
        objective_ids=question.objective_ids,
        response_kind=None,
    )


def _kinds_compatible(
    question: EvaluationContentQuestion, response: EvaluationSubmittedResponse
) -> bool:
    question_type = question.question_type
    response_kind = response.response_kind
    if not isinstance(question_type, EvaluationQuestionType):
        return False
    expected = QUESTION_TYPE_TO_RESPONSE_KIND[question_type]
    return response_kind is expected or response_kind == expected.value


class DeterministicLearnerAssessmentEvaluatorV1:
    """Offline deterministic evaluator for policy version 1."""

    POLICY_ID: Final = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID
    POLICY_VERSION: Final = DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION

    def evaluate(
        self, request: LearnerAssessmentEvaluationRequest
    ) -> LearnerAssessmentEvaluation:
        responses = {item.question_id: item for item in request.responses}
        items = tuple(
            self._evaluate_question(question, responses.get(question.question_id))
            for question in request.questions
        )
        return LearnerAssessmentEvaluation.issue(
            tenant_id=request.tenant_id,
            learner_principal_id=request.learner_principal_id,
            submission_id=request.submission_id,
            attempt_id=request.attempt_id,
            teaching_assignment_id=request.teaching_assignment_id,
            content_id=request.content_id,
            content_version_id=request.content_version_id,
            class_ref=request.class_ref,
            evaluation_policy_id=self.POLICY_ID,
            evaluation_policy_version=self.POLICY_VERSION,
            evaluated_at=request.evaluated_at,
            items=items,
            objective_evidence=self._objective_evidence(items),
        )

    def _evaluate_question(
        self,
        question: EvaluationContentQuestion,
        response: EvaluationSubmittedResponse | None,
    ) -> LearnerAssessmentEvaluationItem:
        if response is None:
            return _unanswered(question)
        if not _kinds_compatible(question, response):
            return _policy_reject(question, response)
        question_type = question.question_type
        if question_type is EvaluationQuestionType.MULTIPLE_CHOICE:
            return self._evaluate_multiple_choice(question, response)
        if question_type is EvaluationQuestionType.TRUE_FALSE:
            return self._evaluate_true_false(question, response)
        if question_type is EvaluationQuestionType.SHORT_ANSWER:
            return self._evaluate_short_answer(question, response)
        return _policy_reject(question, response)

    def _evaluate_multiple_choice(
        self,
        question: EvaluationContentQuestion,
        response: EvaluationSubmittedResponse,
    ) -> LearnerAssessmentEvaluationItem:
        if not isinstance(response.value, str):
            return _policy_reject(question, response)
        if response.value not in question.options:
            return _policy_reject(question, response)
        if question.answer not in question.options:
            return _policy_reject(question, response)
        outcome = (
            ItemOutcome.CORRECT
            if response.value == question.answer
            else ItemOutcome.INCORRECT
        )
        return LearnerAssessmentEvaluationItem(
            question_id=question.question_id,
            question_type=_question_type_value(question),
            outcome=outcome,
            evaluation_method=EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER,
            objective_ids=question.objective_ids,
            response_kind=_response_kind_value(response),
        )

    def _evaluate_true_false(
        self,
        question: EvaluationContentQuestion,
        response: EvaluationSubmittedResponse,
    ) -> LearnerAssessmentEvaluationItem:
        if type(response.value) is not bool:
            return _policy_reject(question, response)
        normalized = question.answer.strip().lower()
        expected = _TRUE_FALSE_CONTENT_ANSWERS.get(normalized)
        if expected is None:
            return _policy_reject(question, response)
        outcome = (
            ItemOutcome.CORRECT
            if response.value is expected
            else ItemOutcome.INCORRECT
        )
        return LearnerAssessmentEvaluationItem(
            question_id=question.question_id,
            question_type=_question_type_value(question),
            outcome=outcome,
            evaluation_method=EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER,
            objective_ids=question.objective_ids,
            response_kind=_response_kind_value(response),
        )

    def _evaluate_short_answer(
        self,
        question: EvaluationContentQuestion,
        response: EvaluationSubmittedResponse,
    ) -> LearnerAssessmentEvaluationItem:
        if not isinstance(response.value, str):
            return _policy_reject(question, response)
        return LearnerAssessmentEvaluationItem(
            question_id=question.question_id,
            question_type=_question_type_value(question),
            outcome=ItemOutcome.OPEN_RESPONSE_UNEVALUATED,
            evaluation_method=EvaluationMethod.OPEN_RESPONSE_BASELINE,
            objective_ids=question.objective_ids,
            response_kind=_response_kind_value(response),
        )

    def _objective_evidence(
        self, items: tuple[LearnerAssessmentEvaluationItem, ...]
    ) -> tuple[LearnerAssessmentObjectiveEvidence, ...]:
        by_objective: dict[str, list[ItemOutcome]] = {}
        for item in items:
            for objective_id in item.objective_ids:
                by_objective.setdefault(objective_id, []).append(item.outcome)
        rows: list[LearnerAssessmentObjectiveEvidence] = []
        for objective_id in sorted(by_objective):
            outcomes = by_objective[objective_id]
            rows.append(
                LearnerAssessmentObjectiveEvidence(
                    objective_id=objective_id,
                    result=self._rollup(outcomes),
                )
            )
        return tuple(rows)

    def _rollup(self, outcomes: list[ItemOutcome]) -> ObjectiveEvidenceResult:
        if any(outcome in _INSUFFICIENT_OUTCOMES for outcome in outcomes):
            return ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE
        if not outcomes or not all(
            outcome in _AUTHORITATIVE_OUTCOMES for outcome in outcomes
        ):
            return ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE
        if all(outcome is ItemOutcome.CORRECT for outcome in outcomes):
            return ObjectiveEvidenceResult.DEMONSTRATED_ON_SUBMITTED_ITEMS
        if all(outcome is ItemOutcome.INCORRECT for outcome in outcomes):
            return ObjectiveEvidenceResult.NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS
        return ObjectiveEvidenceResult.MIXED_ON_SUBMITTED_ITEMS
