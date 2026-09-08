"""S01 AttemptResponseItem kinds. Exactly one matching value per kind."""

from __future__ import annotations

from enum import StrEnum

from aieos.domains.learning.domain.errors import InvalidAttemptResponseError


class AttemptResponseKind(StrEnum):
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    SHORT_ANSWER = "SHORT_ANSWER"
    TRUE_FALSE = "TRUE_FALSE"


def parse_attempt_response_kind(
    value: AttemptResponseKind | str,
) -> AttemptResponseKind:
    if isinstance(value, AttemptResponseKind):
        return value
    if not isinstance(value, str):
        raise InvalidAttemptResponseError("response_kind must be a string")
    try:
        return AttemptResponseKind(value)
    except ValueError as exc:
        raise InvalidAttemptResponseError(
            f"response_kind is not a supported S01 kind: {value!r}"
        ) from exc
