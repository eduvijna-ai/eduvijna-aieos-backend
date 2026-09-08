"""Typed AttemptResponseItem working state.

Logical identity: attempt_id + question_id.
Exactly one value compatible with response_kind. No score, correctness, or
answer-key fields. Not an independent concurrency aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from aieos.domains.learning.domain.errors import InvalidAttemptResponseError
from aieos.domains.learning.domain.identities import AttemptId
from aieos.domains.learning.domain.response_kind import (
    AttemptResponseKind,
    parse_attempt_response_kind,
)

MAX_QUESTION_ID_LENGTH: Final = 128
MAX_CHOICE_VALUE_LENGTH: Final = 256
MAX_TEXT_VALUE_LENGTH: Final = 4096


def _require_question_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidAttemptResponseError("question_id must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > MAX_QUESTION_ID_LENGTH:
        raise InvalidAttemptResponseError(
            f"question_id must be at most {MAX_QUESTION_ID_LENGTH} characters"
        )
    return stripped


def _require_bounded_text(value: str, *, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidAttemptResponseError(f"{label} must be a non-empty string")
    stripped = value.strip()
    if len(stripped) > max_length:
        raise InvalidAttemptResponseError(
            f"{label} must be at most {max_length} characters"
        )
    return stripped


@dataclass(frozen=True, slots=True)
class AttemptResponseItem:
    """One typed working response for a question on a LearnerAttempt."""

    attempt_id: AttemptId
    question_id: str
    response_kind: AttemptResponseKind
    choice_value: str | None
    text_value: str | None
    boolean_value: bool | None

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.attempt_id, AttemptId):
            raise InvalidAttemptResponseError("attempt_id must be an AttemptId")
        set_(self, "question_id", _require_question_id(self.question_id))
        set_(
            self,
            "response_kind",
            parse_attempt_response_kind(self.response_kind),
        )
        kind = self.response_kind
        if kind is AttemptResponseKind.MULTIPLE_CHOICE:
            if self.text_value is not None or self.boolean_value is not None:
                raise InvalidAttemptResponseError(
                    "MULTIPLE_CHOICE requires only choice_value"
                )
            set_(
                self,
                "choice_value",
                _require_bounded_text(
                    self.choice_value or "",
                    label="choice_value",
                    max_length=MAX_CHOICE_VALUE_LENGTH,
                ),
            )
            return
        if kind is AttemptResponseKind.SHORT_ANSWER:
            if self.choice_value is not None or self.boolean_value is not None:
                raise InvalidAttemptResponseError(
                    "SHORT_ANSWER requires only text_value"
                )
            set_(
                self,
                "text_value",
                _require_bounded_text(
                    self.text_value or "",
                    label="text_value",
                    max_length=MAX_TEXT_VALUE_LENGTH,
                ),
            )
            return
        if kind is AttemptResponseKind.TRUE_FALSE:
            if self.choice_value is not None or self.text_value is not None:
                raise InvalidAttemptResponseError(
                    "TRUE_FALSE requires only boolean_value"
                )
            if type(self.boolean_value) is not bool:
                raise InvalidAttemptResponseError(
                    "boolean_value must be a bool for TRUE_FALSE"
                )

    def snapshot_value(self) -> str | bool:
        if self.response_kind is AttemptResponseKind.MULTIPLE_CHOICE:
            assert self.choice_value is not None
            return self.choice_value
        if self.response_kind is AttemptResponseKind.SHORT_ANSWER:
            assert self.text_value is not None
            return self.text_value
        assert self.boolean_value is not None
        return self.boolean_value

    @classmethod
    def multiple_choice(
        cls,
        *,
        attempt_id: AttemptId,
        question_id: str,
        choice_value: str,
    ) -> AttemptResponseItem:
        return cls(
            attempt_id=attempt_id,
            question_id=question_id,
            response_kind=AttemptResponseKind.MULTIPLE_CHOICE,
            choice_value=choice_value,
            text_value=None,
            boolean_value=None,
        )

    @classmethod
    def short_answer(
        cls,
        *,
        attempt_id: AttemptId,
        question_id: str,
        text_value: str,
    ) -> AttemptResponseItem:
        return cls(
            attempt_id=attempt_id,
            question_id=question_id,
            response_kind=AttemptResponseKind.SHORT_ANSWER,
            choice_value=None,
            text_value=text_value,
            boolean_value=None,
        )

    @classmethod
    def true_false(
        cls,
        *,
        attempt_id: AttemptId,
        question_id: str,
        boolean_value: bool,
    ) -> AttemptResponseItem:
        return cls(
            attempt_id=attempt_id,
            question_id=question_id,
            response_kind=AttemptResponseKind.TRUE_FALSE,
            choice_value=None,
            text_value=None,
            boolean_value=boolean_value,
        )
