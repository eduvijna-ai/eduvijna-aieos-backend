"""AIEOS360-S01-I05-B1 — deterministic evaluator policy v1 domain tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid7

import pytest

from aieos.domains.assessment.domain.errors import (
    InvalidLearnerAssessmentEvaluationError,
)
from aieos.domains.assessment.domain.evaluation_input import (
    EvaluationContentQuestion,
    EvaluationSubmittedResponse,
    LearnerAssessmentEvaluationRequest,
)
from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
    DeterministicLearnerAssessmentEvaluatorV1,
)
from aieos.domains.assessment.domain.evaluation_vocabulary import (
    EvaluationMethod,
    ItemOutcome,
    ObjectiveEvidenceResult,
)

pytestmark = pytest.mark.aieos360_s01_i05_b1

FIXED_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
EVALUATOR = DeterministicLearnerAssessmentEvaluatorV1()


def _mc(
    question_id: str = "q-mc",
    *,
    options: tuple[str, ...] = ("A", "B", "C"),
    answer: str = "A",
    objective_ids: tuple[str, ...] = ("obj-1",),
) -> EvaluationContentQuestion:
    return EvaluationContentQuestion(
        question_id=question_id,
        question_type="multiple_choice",
        options=options,
        answer=answer,
        objective_ids=objective_ids,
    )


def _tf(
    question_id: str = "q-tf",
    *,
    answer: str = "true",
    objective_ids: tuple[str, ...] = ("obj-1",),
) -> EvaluationContentQuestion:
    return EvaluationContentQuestion(
        question_id=question_id,
        question_type="true_false",
        options=(),
        answer=answer,
        objective_ids=objective_ids,
    )


def _sa(
    question_id: str = "q-sa",
    *,
    answer: str = "ignored",
    objective_ids: tuple[str, ...] = ("obj-1",),
) -> EvaluationContentQuestion:
    return EvaluationContentQuestion(
        question_id=question_id,
        question_type="short_answer",
        options=(),
        answer=answer,
        objective_ids=objective_ids,
    )


def _response(
    question_id: str, response_kind: str, value: str | bool
) -> EvaluationSubmittedResponse:
    return EvaluationSubmittedResponse(
        question_id=question_id,
        response_kind=response_kind,
        value=value,
    )


def _request(
    questions: tuple[EvaluationContentQuestion, ...],
    responses: tuple[EvaluationSubmittedResponse, ...] = (),
) -> LearnerAssessmentEvaluationRequest:
    return LearnerAssessmentEvaluationRequest(
        tenant_id=uuid7(),
        learner_principal_id=uuid7(),
        submission_id=uuid7(),
        attempt_id=uuid7(),
        teaching_assignment_id=uuid7(),
        content_id=uuid7(),
        content_version_id=uuid7(),
        class_ref="class-5a",
        questions=questions,
        responses=responses,
        evaluated_at=FIXED_NOW,
    )


def _item(evaluation, question_id: str):
    return next(item for item in evaluation.items if item.question_id == question_id)


class TestMultipleChoicePolicy:
    def test_01_mc_correct(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_mc(),), (_response("q-mc", "MULTIPLE_CHOICE", "A"),))
        )
        item = _item(evaluation, "q-mc")
        assert item.outcome is ItemOutcome.CORRECT
        assert item.evaluation_method is EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER
        assert item.response_kind == "MULTIPLE_CHOICE"

    def test_02_mc_incorrect(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_mc(),), (_response("q-mc", "MULTIPLE_CHOICE", "B"),))
        )
        item = _item(evaluation, "q-mc")
        assert item.outcome is ItemOutcome.INCORRECT
        assert item.evaluation_method is EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER

    def test_03_mc_unanswered(self) -> None:
        evaluation = EVALUATOR.evaluate(_request((_mc(),), ()))
        item = _item(evaluation, "q-mc")
        assert item.outcome is ItemOutcome.UNANSWERED
        assert item.evaluation_method is EvaluationMethod.NO_RESPONSE
        assert item.response_kind is None

    def test_04_mc_illegal_option_policy_reject(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_mc(),), (_response("q-mc", "MULTIPLE_CHOICE", "Z"),))
        )
        item = _item(evaluation, "q-mc")
        assert item.outcome is ItemOutcome.UNEVALUATED_POLICY_REJECT
        assert item.evaluation_method is EvaluationMethod.POLICY_REJECT
        assert item.outcome is not ItemOutcome.INCORRECT

    def test_05_mc_kind_mismatch_policy_reject(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_mc(),), (_response("q-mc", "TRUE_FALSE", True),))
        )
        item = _item(evaluation, "q-mc")
        assert item.outcome is ItemOutcome.UNEVALUATED_POLICY_REJECT
        assert item.evaluation_method is EvaluationMethod.POLICY_REJECT
        assert item.response_kind == "TRUE_FALSE"


class TestTrueFalsePolicy:
    def test_06_true_false_correct(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_tf(answer="TRUE "),), (_response("q-tf", "TRUE_FALSE", True),))
        )
        item = _item(evaluation, "q-tf")
        assert item.outcome is ItemOutcome.CORRECT
        assert item.evaluation_method is EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER

    def test_07_true_false_incorrect(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_tf(answer="False"),), (_response("q-tf", "TRUE_FALSE", True),))
        )
        item = _item(evaluation, "q-tf")
        assert item.outcome is ItemOutcome.INCORRECT
        assert item.evaluation_method is EvaluationMethod.DETERMINISTIC_CONTENT_ANSWER

    def test_08_true_false_unanswered(self) -> None:
        evaluation = EVALUATOR.evaluate(_request((_tf(),), ()))
        item = _item(evaluation, "q-tf")
        assert item.outcome is ItemOutcome.UNANSWERED
        assert item.evaluation_method is EvaluationMethod.NO_RESPONSE
        assert item.response_kind is None

    def test_09_invalid_content_answer_policy_reject(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_tf(answer="yes"),), (_response("q-tf", "TRUE_FALSE", True),))
        )
        item = _item(evaluation, "q-tf")
        assert item.outcome is ItemOutcome.UNEVALUATED_POLICY_REJECT
        assert item.evaluation_method is EvaluationMethod.POLICY_REJECT

    def test_10_true_false_kind_mismatch_policy_reject(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_tf(),), (_response("q-tf", "MULTIPLE_CHOICE", "true"),))
        )
        item = _item(evaluation, "q-tf")
        assert item.outcome is ItemOutcome.UNEVALUATED_POLICY_REJECT
        assert item.evaluation_method is EvaluationMethod.POLICY_REJECT


class TestShortAnswerAndCompleteness:
    def test_11_short_answer_submitted_open_response_unevaluated(self) -> None:
        evaluation = EVALUATOR.evaluate(
            _request((_sa(),), (_response("q-sa", "SHORT_ANSWER", "ignored"),))
        )
        item = _item(evaluation, "q-sa")
        assert item.outcome is ItemOutcome.OPEN_RESPONSE_UNEVALUATED
        assert item.evaluation_method is EvaluationMethod.OPEN_RESPONSE_BASELINE
        assert item.outcome not in {ItemOutcome.CORRECT, ItemOutcome.INCORRECT}

    def test_12_short_answer_unanswered(self) -> None:
        evaluation = EVALUATOR.evaluate(_request((_sa(),), ()))
        item = _item(evaluation, "q-sa")
        assert item.outcome is ItemOutcome.UNANSWERED
        assert item.evaluation_method is EvaluationMethod.NO_RESPONSE

    def test_13_empty_submission_all_exact_questions_unanswered(self) -> None:
        questions = (_mc("q1"), _tf("q2"), _sa("q3"))
        evaluation = EVALUATOR.evaluate(_request(questions, ()))
        assert [item.question_id for item in evaluation.items] == ["q1", "q2", "q3"]
        assert all(item.outcome is ItemOutcome.UNANSWERED for item in evaluation.items)
        assert all(
            item.evaluation_method is EvaluationMethod.NO_RESPONSE
            for item in evaluation.items
        )

    def test_14_partial_submission_missing_exact_questions_unanswered(self) -> None:
        questions = (_mc("q1"), _tf("q2"), _sa("q3"))
        evaluation = EVALUATOR.evaluate(
            _request(questions, (_response("q1", "MULTIPLE_CHOICE", "A"),))
        )
        assert _item(evaluation, "q1").outcome is ItemOutcome.CORRECT
        assert _item(evaluation, "q2").outcome is ItemOutcome.UNANSWERED
        assert _item(evaluation, "q3").outcome is ItemOutcome.UNANSWERED

    def test_15_evaluator_iterates_content_version_not_snapshot_only(self) -> None:
        questions = (_mc("only-in-content"), _tf("also-in-content"))
        evaluation = EVALUATOR.evaluate(
            _request(questions, (_response("also-in-content", "TRUE_FALSE", True),))
        )
        assert [item.question_id for item in evaluation.items] == [
            "only-in-content",
            "also-in-content",
        ]
        assert _item(evaluation, "only-in-content").outcome is ItemOutcome.UNANSWERED

    def test_snapshot_question_not_on_content_version_fails_closed(self) -> None:
        with pytest.raises(InvalidLearnerAssessmentEvaluationError):
            _request(
                (_mc("q1"),),
                (_response("unknown", "MULTIPLE_CHOICE", "A"),),
            )


class TestObjectiveRollup:
    def test_16_all_deterministic_correct_demonstrated(self) -> None:
        questions = (
            _mc("q1", objective_ids=("obj-1",)),
            _tf("q2", objective_ids=("obj-1",)),
        )
        evaluation = EVALUATOR.evaluate(
            _request(
                questions,
                (
                    _response("q1", "MULTIPLE_CHOICE", "A"),
                    _response("q2", "TRUE_FALSE", True),
                ),
            )
        )
        assert evaluation.objective_evidence[0].result is (
            ObjectiveEvidenceResult.DEMONSTRATED_ON_SUBMITTED_ITEMS
        )

    def test_17_correct_plus_incorrect_mixed(self) -> None:
        questions = (
            _mc("q1", objective_ids=("obj-1",)),
            _tf("q2", answer="false", objective_ids=("obj-1",)),
        )
        evaluation = EVALUATOR.evaluate(
            _request(
                questions,
                (
                    _response("q1", "MULTIPLE_CHOICE", "A"),
                    _response("q2", "TRUE_FALSE", True),
                ),
            )
        )
        assert evaluation.objective_evidence[0].result is (
            ObjectiveEvidenceResult.MIXED_ON_SUBMITTED_ITEMS
        )

    def test_18_all_incorrect_not_yet_demonstrated(self) -> None:
        questions = (
            _mc("q1", objective_ids=("obj-1",)),
            _tf("q2", answer="false", objective_ids=("obj-1",)),
        )
        evaluation = EVALUATOR.evaluate(
            _request(
                questions,
                (
                    _response("q1", "MULTIPLE_CHOICE", "B"),
                    _response("q2", "TRUE_FALSE", True),
                ),
            )
        )
        assert evaluation.objective_evidence[0].result is (
            ObjectiveEvidenceResult.NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS
        )

    def test_19_correct_plus_unanswered_insufficient(self) -> None:
        questions = (
            _mc("q1", objective_ids=("obj-1",)),
            _tf("q2", objective_ids=("obj-1",)),
        )
        evaluation = EVALUATOR.evaluate(
            _request(questions, (_response("q1", "MULTIPLE_CHOICE", "A"),))
        )
        assert evaluation.objective_evidence[0].result is (
            ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE
        )

    def test_20_correct_plus_open_response_insufficient(self) -> None:
        questions = (
            _mc("q1", objective_ids=("obj-1",)),
            _sa("q2", objective_ids=("obj-1",)),
        )
        evaluation = EVALUATOR.evaluate(
            _request(
                questions,
                (
                    _response("q1", "MULTIPLE_CHOICE", "A"),
                    _response("q2", "SHORT_ANSWER", "because"),
                ),
            )
        )
        assert evaluation.objective_evidence[0].result is (
            ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE
        )

    def test_21_incorrect_plus_unanswered_insufficient(self) -> None:
        questions = (
            _mc("q1", objective_ids=("obj-1",)),
            _tf("q2", objective_ids=("obj-1",)),
        )
        evaluation = EVALUATOR.evaluate(
            _request(questions, (_response("q1", "MULTIPLE_CHOICE", "B"),))
        )
        assert evaluation.objective_evidence[0].result is (
            ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE
        )
        assert evaluation.objective_evidence[0].result is not (
            ObjectiveEvidenceResult.NOT_YET_DEMONSTRATED_ON_SUBMITTED_ITEMS
        )

    def test_22_all_unanswered_insufficient(self) -> None:
        questions = (
            _mc("q1", objective_ids=("obj-1",)),
            _tf("q2", objective_ids=("obj-1",)),
        )
        evaluation = EVALUATOR.evaluate(_request(questions, ()))
        assert evaluation.objective_evidence[0].result is (
            ObjectiveEvidenceResult.INSUFFICIENT_EVIDENCE
        )


class TestPolicyIdentityAndImmutability:
    def test_policy_id_and_version_are_frozen_v1(self) -> None:
        evaluation = EVALUATOR.evaluate(_request((_mc(),), ()))
        assert evaluation.evaluation_policy_id == (
            DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID
        )
        assert evaluation.evaluation_policy_version == (
            DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION
        )
        assert evaluation.created_at == evaluation.evaluated_at == FIXED_NOW
        assert not hasattr(evaluation, "aggregate_revision")
        with pytest.raises(FrozenInstanceError):
            evaluation.class_ref = "tamper"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            evaluation.items[0].outcome = ItemOutcome.INCORRECT  # type: ignore[misc]
