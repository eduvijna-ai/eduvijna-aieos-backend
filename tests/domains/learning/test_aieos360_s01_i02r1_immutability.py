"""AIEOS360-S01-I02R1 — deeply immutable LearnerSubmission snapshot proofs."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from aieos.domains.learning.domain.errors import InvalidLearnerSubmissionError
from aieos.domains.learning.domain.identities import AttemptId, SubmissionId
from aieos.domains.learning.domain.response_item import (
    MAX_CHOICE_VALUE_LENGTH,
    MAX_QUESTION_ID_LENGTH,
    MAX_TEXT_VALUE_LENGTH,
)
from aieos.domains.learning.domain.response_kind import AttemptResponseKind
from aieos.domains.learning.domain.submission import (
    LearnerSubmission,
    SubmissionResponseItem,
)

pytestmark = pytest.mark.aieos360_s01_i02

FIXED_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _direct_submission(*, response_snapshot: object) -> LearnerSubmission:
    return LearnerSubmission(
        submission_id=SubmissionId.generate(),
        tenant_id=uuid.uuid7(),
        attempt_id=AttemptId.generate(),
        learner_principal_id=uuid.uuid7(),
        teaching_assignment_id=uuid.uuid7(),
        content_id=uuid.uuid7(),
        content_version_id=uuid.uuid7(),
        class_ref="class-5a",
        response_snapshot=response_snapshot,  # type: ignore[arg-type]
        submitted_at=FIXED_NOW,
        assignment_revision_at_submit=0,
        due_at_at_submit=None,
        created_at=FIXED_NOW,
    )


class TestDeeplyImmutableSnapshot:
    def test_r1_01_caller_owned_input_mutation_does_not_alias_evidence(self) -> None:
        owned = {
            "question_id": "q1",
            "response_kind": "MULTIPLE_CHOICE",
            "value": "A",
        }
        rows = [owned]
        submission = _direct_submission(response_snapshot=rows)
        owned["value"] = "TAMPER"
        owned["question_id"] = "q-other"
        rows.append(
            {
                "question_id": "q2",
                "response_kind": "SHORT_ANSWER",
                "value": "injected",
            }
        )
        assert len(submission.response_snapshot) == 1
        item = submission.response_snapshot[0]
        assert item.question_id == "q1"
        assert item.response_kind is AttemptResponseKind.MULTIPLE_CHOICE
        assert item.value == "A"
        leaked = item.as_persistable_mapping()
        leaked["value"] = "LEAK"
        assert item.value == "A"

    def test_r1_02_snapshot_item_index_assignment_is_impossible(self) -> None:
        submission = _direct_submission(
            response_snapshot=[
                {
                    "question_id": "q1",
                    "response_kind": "SHORT_ANSWER",
                    "value": "original",
                }
            ]
        )
        with pytest.raises(TypeError):
            submission.response_snapshot[0]["value"] = "tamper"  # type: ignore[index]
        assert submission.response_snapshot[0].value == "original"

    def test_r1_03_snapshot_item_attribute_mutation_is_impossible(self) -> None:
        submission = _direct_submission(
            response_snapshot=[
                {
                    "question_id": "q1",
                    "response_kind": "TRUE_FALSE",
                    "value": True,
                }
            ]
        )
        item = submission.response_snapshot[0]
        with pytest.raises(FrozenInstanceError):
            item.value = False  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            item.question_id = "q-other"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            item.response_kind = AttemptResponseKind.MULTIPLE_CHOICE  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            submission.response_snapshot = ()  # type: ignore[misc]
        assert item.value is True
        assert item.question_id == "q1"
        assert isinstance(item, SubmissionResponseItem)

    def test_r1_04_oversized_question_id_in_direct_snapshot_rejected(self) -> None:
        with pytest.raises(InvalidLearnerSubmissionError, match="question_id"):
            _direct_submission(
                response_snapshot=[
                    {
                        "question_id": "q" * (MAX_QUESTION_ID_LENGTH + 1),
                        "response_kind": "MULTIPLE_CHOICE",
                        "value": "A",
                    }
                ]
            )

    def test_r1_05_oversized_multiple_choice_value_rejected(self) -> None:
        with pytest.raises(InvalidLearnerSubmissionError, match="MULTIPLE_CHOICE"):
            _direct_submission(
                response_snapshot=[
                    {
                        "question_id": "q1",
                        "response_kind": "MULTIPLE_CHOICE",
                        "value": "A" * (MAX_CHOICE_VALUE_LENGTH + 1),
                    }
                ]
            )

    def test_r1_06_oversized_short_answer_value_rejected(self) -> None:
        with pytest.raises(InvalidLearnerSubmissionError, match="SHORT_ANSWER"):
            _direct_submission(
                response_snapshot=[
                    {
                        "question_id": "q1",
                        "response_kind": "SHORT_ANSWER",
                        "value": "x" * (MAX_TEXT_VALUE_LENGTH + 1),
                    }
                ]
            )

    def test_r1_07_invalid_true_false_non_bool_rejected(self) -> None:
        with pytest.raises(InvalidLearnerSubmissionError, match="TRUE_FALSE"):
            _direct_submission(
                response_snapshot=[
                    {
                        "question_id": "q1",
                        "response_kind": "TRUE_FALSE",
                        "value": "true",
                    }
                ]
            )
        with pytest.raises(InvalidLearnerSubmissionError, match="TRUE_FALSE"):
            _direct_submission(
                response_snapshot=[
                    {
                        "question_id": "q1",
                        "response_kind": "TRUE_FALSE",
                        "value": 1,
                    }
                ]
            )
