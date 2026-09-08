"""Immutable LearnerSubmission evidence snapshot.

Distinct from LearnerAttempt lifecycle. Captures learner RESPONSES only —
never raw ContentVersion payload, answer keys, scores, grades, mastery, or AI.

Snapshot items are deeply immutable value objects. Nested dicts are never
retained as aggregate state. Repository JSONB serialization is explicit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from aieos.domains.learning.domain.errors import InvalidLearnerSubmissionError
from aieos.domains.learning.domain.identities import (
    AttemptId,
    SubmissionId,
    require_foreign_uuid,
)
from aieos.domains.learning.domain.response_item import (
    MAX_CHOICE_VALUE_LENGTH,
    MAX_QUESTION_ID_LENGTH,
    MAX_TEXT_VALUE_LENGTH,
    AttemptResponseItem,
)
from aieos.domains.learning.domain.response_kind import (
    AttemptResponseKind,
    parse_attempt_response_kind,
)

MAX_CLASS_REF_LENGTH: Final = 512
_SNAPSHOT_KEYS: Final = frozenset({"question_id", "response_kind", "value"})
_FORBIDDEN_SNAPSHOT_KEYS: Final = frozenset(
    {
        "score",
        "grade",
        "mastery",
        "misconception",
        "recommendation",
        "answer",
        "explanation",
        "teacher_notes",
        "teacher_summary",
        "ai",
        "correct",
        "correctness",
        "payload",
    }
)


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidLearnerSubmissionError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidLearnerSubmissionError(f"{label} must be timezone-aware")
    return value


def _require_class_ref(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidLearnerSubmissionError("class_ref must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > MAX_CLASS_REF_LENGTH:
        raise InvalidLearnerSubmissionError(
            f"class_ref must be at most {MAX_CLASS_REF_LENGTH} characters"
        )
    return stripped


def _require_snapshot_question_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidLearnerSubmissionError(
            "response_snapshot question_id must be a non-empty string"
        )
    stripped = value.strip()
    if len(stripped) > MAX_QUESTION_ID_LENGTH:
        raise InvalidLearnerSubmissionError(
            f"response_snapshot question_id must be at most "
            f"{MAX_QUESTION_ID_LENGTH} characters"
        )
    return stripped


def _require_snapshot_value(
    kind: AttemptResponseKind, raw_value: object
) -> str | bool:
    if kind is AttemptResponseKind.TRUE_FALSE:
        if type(raw_value) is not bool:
            raise InvalidLearnerSubmissionError(
                "TRUE_FALSE snapshot value must be a boolean"
            )
        return raw_value
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise InvalidLearnerSubmissionError(
            f"{kind.value} snapshot value must be a non-empty string"
        )
    stripped = raw_value.strip()
    max_length = (
        MAX_CHOICE_VALUE_LENGTH
        if kind is AttemptResponseKind.MULTIPLE_CHOICE
        else MAX_TEXT_VALUE_LENGTH
    )
    if len(stripped) > max_length:
        raise InvalidLearnerSubmissionError(
            f"{kind.value} snapshot value must be at most {max_length} characters"
        )
    return stripped


@dataclass(frozen=True, slots=True)
class SubmissionResponseItem:
    """One deeply immutable learner-response evidence row.

    JSONB persistence must call ``as_persistable_mapping``; callers never
    receive a shared mutable alias of construction input.
    """

    question_id: str
    response_kind: AttemptResponseKind
    value: str | bool

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "question_id", _require_snapshot_question_id(self.question_id))
        set_(
            self,
            "response_kind",
            parse_attempt_response_kind(self.response_kind),
        )
        set_(
            self,
            "value",
            _require_snapshot_value(self.response_kind, self.value),
        )

    def as_persistable_mapping(self) -> dict[str, str | bool]:
        """Explicit JSON object for PostgreSQL JSONB. Always a fresh dict."""
        return {
            "question_id": self.question_id,
            "response_kind": self.response_kind.value,
            "value": self.value,
        }


def canonical_response_snapshot(
    items: Sequence[AttemptResponseItem],
) -> tuple[SubmissionResponseItem, ...]:
    """Deterministic learner-response-only snapshot, sorted by question_id."""
    seen: set[str] = set()
    rows: list[SubmissionResponseItem] = []
    for item in items:
        if not isinstance(item, AttemptResponseItem):
            raise InvalidLearnerSubmissionError(
                "response snapshot items must be AttemptResponseItem"
            )
        if item.question_id in seen:
            raise InvalidLearnerSubmissionError(
                "response snapshot cannot contain duplicate question_id"
            )
        seen.add(item.question_id)
        rows.append(
            SubmissionResponseItem(
                question_id=item.question_id,
                response_kind=item.response_kind,
                value=item.snapshot_value(),
            )
        )
    rows.sort(key=lambda row: row.question_id)
    return tuple(rows)


def _snapshot_entry_mapping(entry: object) -> Mapping[str, Any]:
    if isinstance(entry, SubmissionResponseItem):
        return entry.as_persistable_mapping()
    if isinstance(entry, Mapping):
        return dict(entry)
    raise InvalidLearnerSubmissionError("response_snapshot entries must be objects")


def validate_response_snapshot(value: object) -> tuple[SubmissionResponseItem, ...]:
    """Revalidate snapshot bounds on construction and reconstruction.

    Does not depend on already-valid AttemptResponseItem objects. Copies every
    field so caller-owned mappings/lists cannot alias into evidence.
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise InvalidLearnerSubmissionError(
            "response_snapshot must be a JSON array of response objects"
        )
    rows: list[SubmissionResponseItem] = []
    seen: set[str] = set()
    for entry in value:
        mapping = _snapshot_entry_mapping(entry)
        keys = set(mapping.keys())
        forbidden = keys & _FORBIDDEN_SNAPSHOT_KEYS
        if forbidden:
            raise InvalidLearnerSubmissionError(
                "response_snapshot must not contain score/grade/mastery/AI fields"
            )
        extra = keys - _SNAPSHOT_KEYS
        if extra or keys != _SNAPSHOT_KEYS:
            raise InvalidLearnerSubmissionError(
                "response_snapshot entries must contain exactly "
                "question_id, response_kind, and value"
            )
        item = SubmissionResponseItem(
            question_id=mapping["question_id"],
            response_kind=mapping["response_kind"],
            value=mapping["value"],
        )
        if item.question_id in seen:
            raise InvalidLearnerSubmissionError(
                "response_snapshot cannot contain duplicate question_id"
            )
        seen.add(item.question_id)
        rows.append(item)
    expected_order = sorted(row.question_id for row in rows)
    actual_order = [row.question_id for row in rows]
    if actual_order != expected_order:
        raise InvalidLearnerSubmissionError(
            "response_snapshot must be sorted by question_id"
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class LearnerSubmission:
    """Authoritative immutable learner-response evidence for one attempt."""

    submission_id: SubmissionId
    tenant_id: UUID
    attempt_id: AttemptId
    learner_principal_id: UUID
    teaching_assignment_id: UUID
    content_id: UUID
    content_version_id: UUID
    class_ref: str
    response_snapshot: tuple[SubmissionResponseItem, ...]
    submitted_at: datetime
    assignment_revision_at_submit: int
    due_at_at_submit: datetime | None
    created_at: datetime

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.submission_id, SubmissionId):
            raise InvalidLearnerSubmissionError(
                "submission_id must be a SubmissionId"
            )
        if not isinstance(self.attempt_id, AttemptId):
            raise InvalidLearnerSubmissionError("attempt_id must be an AttemptId")
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.learner_principal_id, label="learner_principal_id"
        )
        require_foreign_uuid(
            self.teaching_assignment_id, label="teaching_assignment_id"
        )
        require_foreign_uuid(self.content_id, label="content_id")
        require_foreign_uuid(self.content_version_id, label="content_version_id")
        set_(self, "class_ref", _require_class_ref(self.class_ref))
        if (
            isinstance(self.assignment_revision_at_submit, bool)
            or not isinstance(self.assignment_revision_at_submit, int)
            or self.assignment_revision_at_submit < 0
        ):
            raise InvalidLearnerSubmissionError(
                "assignment_revision_at_submit must be a non-negative integer"
            )
        set_(
            self,
            "submitted_at",
            _require_aware(self.submitted_at, label="submitted_at"),
        )
        set_(self, "created_at", _require_aware(self.created_at, label="created_at"))
        if self.due_at_at_submit is not None:
            set_(
                self,
                "due_at_at_submit",
                _require_aware(self.due_at_at_submit, label="due_at_at_submit"),
            )
        set_(
            self,
            "response_snapshot",
            validate_response_snapshot(self.response_snapshot),
        )
