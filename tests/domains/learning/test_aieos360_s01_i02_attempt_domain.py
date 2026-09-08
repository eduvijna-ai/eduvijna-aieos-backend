"""AIEOS360-S01-I02 — LearnerAttempt / response / submission domain tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from aieos.domains.learning.domain.attempt import LearnerAttempt
from aieos.domains.learning.domain.errors import (
    AttemptAlreadySubmittedError,
    InvalidAttemptResponseError,
    InvalidLearnerAttemptError,
)
from aieos.domains.learning.domain.identities import AggregateRevision, AttemptId
from aieos.domains.learning.domain.lifecycle import AttemptLifecycleState
from aieos.domains.learning.domain.response_item import (
    MAX_TEXT_VALUE_LENGTH,
    AttemptResponseItem,
)
from aieos.domains.learning.domain.response_kind import AttemptResponseKind
from aieos.domains.learning.domain.submission import canonical_response_snapshot
from aieos.domains.learning.domain.submit import (
    transition_in_progress_attempt_to_submitted,
)

pytestmark = pytest.mark.aieos360_s01_i02

FIXED_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _start(**overrides) -> LearnerAttempt:
    values = {
        "tenant_id": uuid.uuid7(),
        "learner_principal_id": uuid.uuid7(),
        "teaching_assignment_id": uuid.uuid7(),
        "content_id": uuid.uuid7(),
        "content_version_id": uuid.uuid7(),
        "class_ref": "class-5a",
        "started_at": FIXED_NOW,
    }
    values.update(overrides)
    return LearnerAttempt.start_in_progress(**values)


class TestAttemptConstruction:
    def test_i02_01_new_attempt_starts_in_progress_revision_zero(self) -> None:
        attempt = _start()
        assert attempt.lifecycle_state is AttemptLifecycleState.IN_PROGRESS
        assert int(attempt.aggregate_revision) == 0
        assert attempt.submitted_at is None
        assert attempt.submission_id is None
        assert attempt.last_saved_at is None
        assert attempt.attempt_number == 1
        assert attempt.attempt_id.value.version == 7

    def test_i02_02_required_assignment_content_class_identities_retained(self) -> None:
        teaching_assignment_id = uuid.uuid7()
        content_id = uuid.uuid7()
        content_version_id = uuid.uuid7()
        attempt = _start(
            teaching_assignment_id=teaching_assignment_id,
            content_id=content_id,
            content_version_id=content_version_id,
            class_ref="class-5a",
        )
        assert attempt.teaching_assignment_id == teaching_assignment_id
        assert attempt.content_id == content_id
        assert attempt.content_version_id == content_version_id
        assert attempt.class_ref == "class-5a"

    def test_i02_03_blank_class_ref_rejected(self) -> None:
        with pytest.raises(InvalidLearnerAttemptError, match="class_ref"):
            _start(class_ref="   ")

    def test_i02_04_attempt_number_less_than_one_rejected(self) -> None:
        with pytest.raises(InvalidLearnerAttemptError, match="attempt_number"):
            _start(attempt_number=0)


class TestResponseItems:
    def test_i02_05_multiple_choice_valid(self) -> None:
        item = AttemptResponseItem.multiple_choice(
            attempt_id=AttemptId.generate(),
            question_id="q1",
            choice_value="B",
        )
        assert item.response_kind is AttemptResponseKind.MULTIPLE_CHOICE
        assert item.choice_value == "B"
        assert item.text_value is None
        assert item.boolean_value is None
        assert item.snapshot_value() == "B"

    def test_i02_06_short_answer_valid(self) -> None:
        item = AttemptResponseItem.short_answer(
            attempt_id=AttemptId.generate(),
            question_id="q2",
            text_value="photosynthesis",
        )
        assert item.response_kind is AttemptResponseKind.SHORT_ANSWER
        assert item.text_value == "photosynthesis"
        assert item.choice_value is None
        assert item.boolean_value is None

    def test_i02_07_true_false_valid(self) -> None:
        item = AttemptResponseItem.true_false(
            attempt_id=AttemptId.generate(),
            question_id="q3",
            boolean_value=True,
        )
        assert item.response_kind is AttemptResponseKind.TRUE_FALSE
        assert item.boolean_value is True
        assert item.choice_value is None
        assert item.text_value is None

    def test_i02_08_mismatched_response_kind_value_rejected(self) -> None:
        attempt_id = AttemptId.generate()
        with pytest.raises(InvalidAttemptResponseError, match="MULTIPLE_CHOICE"):
            AttemptResponseItem(
                attempt_id=attempt_id,
                question_id="q1",
                response_kind=AttemptResponseKind.MULTIPLE_CHOICE,
                choice_value="A",
                text_value="nope",
                boolean_value=None,
            )
        with pytest.raises(InvalidAttemptResponseError, match="TRUE_FALSE"):
            AttemptResponseItem.true_false(
                attempt_id=attempt_id,
                question_id="q2",
                boolean_value="yes",  # type: ignore[arg-type]
            )

    def test_i02_09_blank_question_id_rejected(self) -> None:
        with pytest.raises(InvalidAttemptResponseError, match="question_id"):
            AttemptResponseItem.multiple_choice(
                attempt_id=AttemptId.generate(),
                question_id="  ",
                choice_value="A",
            )

    def test_i02_10_oversized_response_rejected(self) -> None:
        with pytest.raises(InvalidAttemptResponseError, match="text_value"):
            AttemptResponseItem.short_answer(
                attempt_id=AttemptId.generate(),
                question_id="q1",
                text_value="x" * (MAX_TEXT_VALUE_LENGTH + 1),
            )


class TestRevisionAndSubmit:
    def test_i02_11_material_response_save_increments_revision_once(self) -> None:
        attempt = _start()
        saved = attempt.record_material_response_save(
            last_saved_at=FIXED_NOW + timedelta(seconds=5)
        )
        assert int(saved.aggregate_revision) == 1
        assert saved.last_saved_at == FIXED_NOW + timedelta(seconds=5)
        assert saved.lifecycle_state is AttemptLifecycleState.IN_PROGRESS

    def test_i02_12_submitted_attempt_rejects_response_mutation(self) -> None:
        attempt = _start()
        item = AttemptResponseItem.multiple_choice(
            attempt_id=attempt.attempt_id,
            question_id="q1",
            choice_value="A",
        )
        submitted, _submission = transition_in_progress_attempt_to_submitted(
            attempt,
            [item],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=3,
            due_at_at_submit=FIXED_NOW + timedelta(hours=1),
        )
        with pytest.raises(AttemptAlreadySubmittedError):
            submitted.record_material_response_save(
                last_saved_at=FIXED_NOW + timedelta(minutes=2)
            )

    def test_i02_13_submit_produces_immutable_learner_submission(self) -> None:
        attempt = _start()
        item = AttemptResponseItem.short_answer(
            attempt_id=attempt.attempt_id,
            question_id="q1",
            text_value="answer",
        )
        submitted, submission = transition_in_progress_attempt_to_submitted(
            attempt,
            [item],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=0,
            due_at_at_submit=None,
        )
        assert submission.submission_id.value.version == 7
        assert submitted.submission_id == submission.submission_id
        assert not hasattr(submission, "grade")
        assert not hasattr(submission, "score")

    def test_i02_14_submission_ids_equal_parent_attempt(self) -> None:
        attempt = _start()
        item = AttemptResponseItem.true_false(
            attempt_id=attempt.attempt_id,
            question_id="q1",
            boolean_value=False,
        )
        submitted, submission = transition_in_progress_attempt_to_submitted(
            attempt,
            [item],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=4,
            due_at_at_submit=FIXED_NOW,
        )
        assert submission.attempt_id == submitted.attempt_id
        assert submission.tenant_id == submitted.tenant_id
        assert submission.learner_principal_id == submitted.learner_principal_id
        assert submission.teaching_assignment_id == submitted.teaching_assignment_id
        assert submission.content_id == submitted.content_id
        assert submission.content_version_id == submitted.content_version_id
        assert submission.class_ref == submitted.class_ref
        assert submission.submitted_at == submitted.submitted_at
        assert submission.due_at_at_submit == FIXED_NOW
        assert not hasattr(submission, "late")
        assert not hasattr(submission, "was_late_at_submit")

    def test_i02_15_submission_snapshot_canonical_deterministic(self) -> None:
        attempt = _start()
        later = AttemptResponseItem.multiple_choice(
            attempt_id=attempt.attempt_id,
            question_id="q-b",
            choice_value="B",
        )
        earlier = AttemptResponseItem.short_answer(
            attempt_id=attempt.attempt_id,
            question_id="q-a",
            text_value="first",
        )
        snapshot = canonical_response_snapshot([later, earlier])
        assert [row.question_id for row in snapshot] == ["q-a", "q-b"]
        _submitted, submission = transition_in_progress_attempt_to_submitted(
            attempt,
            [later, earlier],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        assert submission.response_snapshot == snapshot
        assert (
            submission.response_snapshot[0].response_kind
            is AttemptResponseKind.SHORT_ANSWER
        )
        assert submission.response_snapshot[1].value == "B"

    def test_i02_16_submission_contains_no_score_grade_mastery_ai_fields(self) -> None:
        attempt = _start()
        item = AttemptResponseItem.multiple_choice(
            attempt_id=attempt.attempt_id,
            question_id="q1",
            choice_value="A",
        )
        _submitted, submission = transition_in_progress_attempt_to_submitted(
            attempt,
            [item],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        blob = str(submission.response_snapshot).lower()
        for forbidden in (
            "score",
            "grade",
            "mastery",
            "misconception",
            "recommendation",
            "openai",
            "groq",
        ):
            assert forbidden not in blob
        for row in submission.response_snapshot:
            mapping = row.as_persistable_mapping()
            assert set(mapping) == {"question_id", "response_kind", "value"}

    def test_i02_17_submit_transitions_in_progress_to_submitted(self) -> None:
        attempt = _start()
        submitted, _submission = transition_in_progress_attempt_to_submitted(
            attempt,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=2,
            due_at_at_submit=None,
        )
        assert submitted.lifecycle_state is AttemptLifecycleState.SUBMITTED
        assert int(submitted.aggregate_revision) == 1
        assert submitted.submitted_at == FIXED_NOW + timedelta(minutes=1)

    def test_i02_18_second_submit_domain_transition_rejected(self) -> None:
        attempt = _start()
        submitted, _submission = transition_in_progress_attempt_to_submitted(
            attempt,
            [],
            submitted_at=FIXED_NOW + timedelta(minutes=1),
            assignment_revision_at_submit=1,
            due_at_at_submit=None,
        )
        with pytest.raises(AttemptAlreadySubmittedError):
            transition_in_progress_attempt_to_submitted(
                submitted,
                [],
                submitted_at=FIXED_NOW + timedelta(minutes=2),
                assignment_revision_at_submit=1,
                due_at_at_submit=None,
            )

    def test_i02_19_abandoned_does_not_exist(self) -> None:
        assert "ABANDONED" not in AttemptLifecycleState.__members__

    def test_i02_20_graded_mastered_attempt_states_do_not_exist(self) -> None:
        assert "GRADED" not in AttemptLifecycleState.__members__
        assert "MASTERED" not in AttemptLifecycleState.__members__
        assert "CANCELLED" not in AttemptLifecycleState.__members__
        assert set(AttemptLifecycleState.__members__) == {
            "IN_PROGRESS",
            "SUBMITTED",
        }

    def test_pure_submit_is_not_an_authorized_student_command(self) -> None:
        import aieos.domains.learning.domain.submit as submit_mod

        doc = (
            (transition_in_progress_attempt_to_submitted.__doc__ or "")
            + " "
            + (submit_mod.__doc__ or "")
        )
        assert "Not an authorized Student" in doc
        assert "S01-I03" in doc
        assert int(AggregateRevision(0).next()) == 1
