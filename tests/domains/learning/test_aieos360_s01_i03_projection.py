"""AIEOS360-S01-I03 — learner-safe projection allowlist (I03-01..I03-13)."""

from __future__ import annotations

import inspect
import uuid
from dataclasses import asdict

import pytest

from aieos.domains.education.schema import (
    ANSWER_KEY_CONTENT_TYPE,
    ANSWER_KEY_SCHEMA_ID,
    HOMEWORK_CONTENT_TYPE,
    HOMEWORK_SCHEMA_ID,
    HOMEWORK_SCHEMA_VERSION,
    LESSON_PLAN_CONTENT_TYPE,
    LESSON_PLAN_SCHEMA_ID,
    QUIZ_CONTENT_TYPE,
    QUIZ_SCHEMA_ID,
    QUIZ_SCHEMA_VERSION,
    TEACHER_NOTES_CONTENT_TYPE,
    TEACHER_NOTES_SCHEMA_ID,
    WORKSHEET_CONTENT_TYPE,
    WORKSHEET_SCHEMA_ID,
    WORKSHEET_SCHEMA_VERSION,
)
from aieos.domains.learning.application.errors import ContentNotLearnerConsumable
from aieos.domains.learning.application.learner_projection import project_learner_resource
from aieos.domains.learning.application.models import ExactAssignedContent, LearnerResource
from tests.domains.education.test_tos_dev04_i03_content_payloads import (
    valid_homework_payload,
    valid_lesson_plan_payload,
    valid_quiz_payload,
    valid_teacher_notes_payload,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload

pytestmark = pytest.mark.aieos360_s01_i03

_FORBIDDEN = (
    "answer",
    "explanation",
    "teacher_summary",
    "teacher_notes",
    "difficulty",
    "bloom_level",
    "visual_description",
    "objective_ids",
    "payload",
)


def _assigned(
    *,
    content_type: str,
    schema_id: str,
    schema_version: int,
    payload: dict[str, object],
) -> ExactAssignedContent:
    return ExactAssignedContent(
        content_id=uuid.uuid7(),
        content_version_id=uuid.uuid7(),
        content_type=content_type,
        schema_id=schema_id,
        schema_version=schema_version,
        payload=payload,
    )


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value.keys())
        for item in value.values():
            keys.update(_nested_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.update(_nested_keys(item))
    return keys


def _assert_safe(resource: LearnerResource) -> None:
    dumped = asdict(resource)
    keys = _nested_keys(dumped)
    for forbidden in _FORBIDDEN:
        assert forbidden not in keys
    assert set(dumped) == {
        "content_id",
        "content_version_id",
        "content_type",
        "schema_id",
        "schema_version",
        "title",
        "learning_objectives",
        "instructions",
        "questions",
    }
    for question in resource.questions:
        assert question.question_type in {
            "MULTIPLE_CHOICE",
            "SHORT_ANSWER",
            "TRUE_FALSE",
        }


class TestLearnerProjectionAllowlist:
    def test_i03_01_worksheet_v1_projection_succeeds(self) -> None:
        resource = project_learner_resource(
            _assigned(
                content_type=WORKSHEET_CONTENT_TYPE,
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=valid_worksheet_payload(),
            )
        )
        _assert_safe(resource)
        assert resource.content_type == WORKSHEET_CONTENT_TYPE
        assert resource.schema_id == WORKSHEET_SCHEMA_ID
        assert resource.title == "Fractions Worksheet"
        types = {item.question_type for item in resource.questions}
        assert types == {"MULTIPLE_CHOICE", "SHORT_ANSWER", "TRUE_FALSE"}
        mc = next(
            item for item in resource.questions if item.question_type == "MULTIPLE_CHOICE"
        )
        assert mc.options == ("1/2", "1/3", "1/4", "2/3")

    def test_i03_02_quiz_v1_projection_succeeds(self) -> None:
        resource = project_learner_resource(
            _assigned(
                content_type=QUIZ_CONTENT_TYPE,
                schema_id=QUIZ_SCHEMA_ID,
                schema_version=QUIZ_SCHEMA_VERSION,
                payload=valid_quiz_payload(),
            )
        )
        _assert_safe(resource)
        assert resource.content_type == QUIZ_CONTENT_TYPE
        assert resource.instructions == "Answer independently."

    def test_i03_03_homework_v1_projection_succeeds(self) -> None:
        resource = project_learner_resource(
            _assigned(
                content_type=HOMEWORK_CONTENT_TYPE,
                schema_id=HOMEWORK_SCHEMA_ID,
                schema_version=HOMEWORK_SCHEMA_VERSION,
                payload=valid_homework_payload(),
            )
        )
        _assert_safe(resource)
        assert resource.content_type == HOMEWORK_CONTENT_TYPE
        assert resource.instructions == "Complete at home."

    def test_i03_04_answer_absent_recursively(self) -> None:
        resource = project_learner_resource(
            _assigned(
                content_type=WORKSHEET_CONTENT_TYPE,
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=valid_worksheet_payload(),
            )
        )
        assert "answer" not in _nested_keys(asdict(resource))

    def test_i03_05_explanation_absent_recursively(self) -> None:
        resource = project_learner_resource(
            _assigned(
                content_type=WORKSHEET_CONTENT_TYPE,
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=valid_worksheet_payload(),
            )
        )
        assert "explanation" not in _nested_keys(asdict(resource))

    def test_i03_06_teacher_summary_absent(self) -> None:
        resource = project_learner_resource(
            _assigned(
                content_type=WORKSHEET_CONTENT_TYPE,
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=valid_worksheet_payload(),
            )
        )
        assert "teacher_summary" not in _nested_keys(asdict(resource))

    def test_i03_07_teacher_notes_absent(self) -> None:
        resource = project_learner_resource(
            _assigned(
                content_type=WORKSHEET_CONTENT_TYPE,
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=valid_worksheet_payload(),
            )
        )
        assert "teacher_notes" not in _nested_keys(asdict(resource))

    def test_i03_08_lesson_plan_fails_closed(self) -> None:
        with pytest.raises(ContentNotLearnerConsumable):
            project_learner_resource(
                _assigned(
                    content_type=LESSON_PLAN_CONTENT_TYPE,
                    schema_id=LESSON_PLAN_SCHEMA_ID,
                    schema_version=1,
                    payload=valid_lesson_plan_payload(),
                )
            )

    def test_i03_09_answer_key_fails_closed(self) -> None:
        with pytest.raises(ContentNotLearnerConsumable):
            project_learner_resource(
                _assigned(
                    content_type=ANSWER_KEY_CONTENT_TYPE,
                    schema_id=ANSWER_KEY_SCHEMA_ID,
                    schema_version=1,
                    payload={"title": "Key", "entries": []},
                )
            )

    def test_i03_10_teacher_notes_fails_closed(self) -> None:
        with pytest.raises(ContentNotLearnerConsumable):
            project_learner_resource(
                _assigned(
                    content_type=TEACHER_NOTES_CONTENT_TYPE,
                    schema_id=TEACHER_NOTES_SCHEMA_ID,
                    schema_version=1,
                    payload=valid_teacher_notes_payload(),
                )
            )

    def test_i03_11_unknown_schema_version_fails_closed(self) -> None:
        with pytest.raises(ContentNotLearnerConsumable):
            project_learner_resource(
                _assigned(
                    content_type=WORKSHEET_CONTENT_TYPE,
                    schema_id=WORKSHEET_SCHEMA_ID,
                    schema_version=2,
                    payload=valid_worksheet_payload(),
                )
            )

    def test_i03_12_no_raw_payload_fallback(self) -> None:
        source = inspect.getsource(project_learner_resource)
        module = inspect.getsource(
            __import__(
                "aieos.domains.learning.application.learner_projection",
                fromlist=["project_learner_resource"],
            )
        )
        assert ".model_dump(" not in module
        assert "payload.model_dump" not in source
        resource = project_learner_resource(
            _assigned(
                content_type=WORKSHEET_CONTENT_TYPE,
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=valid_worksheet_payload(),
            )
        )
        assert not hasattr(resource, "payload")

    def test_i03_13_non_allowlisted_source_property_is_not_emitted(self) -> None:
        payload = valid_worksheet_payload()
        payload["future_secret"] = "do-not-emit"
        resource = project_learner_resource(
            _assigned(
                content_type=WORKSHEET_CONTENT_TYPE,
                schema_id=WORKSHEET_SCHEMA_ID,
                schema_version=WORKSHEET_SCHEMA_VERSION,
                payload=payload,
            )
        )
        keys = _nested_keys(asdict(resource))
        assert "future_secret" not in keys
        assert "do-not-emit" not in str(asdict(resource))
