"""Translate driver/ORM exceptions into Learning application persistence errors."""

from __future__ import annotations

from typing import NoReturn

from psycopg.errors import (
    InsufficientPrivilege,
    IntegrityError as PsycopgIntegrityError,
    TriggeredActionException,
    UniqueViolation,
)
from sqlalchemy.exc import IntegrityError

from aieos.domains.learning.application.errors import (
    AttemptAlreadySubmitted,
    InvalidAttemptState,
    InvalidResponse,
    LearningApplicationError,
    PersistenceInvariantViolation,
    PersistenceOperationFailed,
    SubmissionImmutable,
)
from aieos.domains.learning.domain.errors import (
    AttemptAlreadySubmittedError,
    InvalidAttemptResponseError,
    InvalidAttemptStateError,
    InvalidLearnerAttemptError,
    InvalidLearnerSubmissionError,
    LearningDomainError,
)


def _orig(exc: BaseException) -> BaseException:
    return getattr(exc, "orig", None) or exc


def _message(exc: BaseException) -> str:
    orig = _orig(exc)
    return f"{exc} {orig}".lower()


def translate_infrastructure_error(exc: BaseException) -> LearningApplicationError:
    if isinstance(exc, LearningApplicationError):
        return exc
    if isinstance(exc, AttemptAlreadySubmittedError):
        return AttemptAlreadySubmitted(str(exc))
    if isinstance(exc, InvalidAttemptResponseError):
        return InvalidResponse(str(exc))
    if isinstance(exc, InvalidLearnerSubmissionError):
        return PersistenceInvariantViolation(str(exc))
    if isinstance(exc, InvalidAttemptStateError | InvalidLearnerAttemptError):
        return InvalidAttemptState(str(exc))
    if isinstance(exc, LearningDomainError):
        return InvalidAttemptState(str(exc))
    orig = _orig(exc)
    message = _message(exc)
    is_triggered = isinstance(orig, TriggeredActionException) or "27000" in message
    if is_triggered:
        if "submissions is immutable" in message:
            return SubmissionImmutable("learning.submissions is immutable")
        if (
            "not in_progress" in message
            or "submitted row is terminal" in message
            or "cannot mutate unless parent attempt is in_progress" in message
        ):
            return AttemptAlreadySubmitted(
                "SUBMITTED LearnerAttempt rejects mutation"
            )
        return PersistenceInvariantViolation(
            "learning persistence trigger rejected the mutation"
        )
    is_unique = isinstance(exc, UniqueViolation) or isinstance(orig, UniqueViolation)
    if is_unique:
        return PersistenceInvariantViolation(
            "learning persistence invariant was violated"
        )
    if isinstance(exc, IntegrityError) or isinstance(orig, PsycopgIntegrityError):
        return PersistenceInvariantViolation(
            "learning persistence invariant was violated"
        )
    if isinstance(orig, InsufficientPrivilege):
        return PersistenceOperationFailed(
            "learning persistence operation is not permitted"
        )
    return PersistenceOperationFailed("learning persistence operation failed")


def reraise_as_application_error(exc: BaseException) -> NoReturn:
    if isinstance(exc, LearningApplicationError):
        raise exc
    raise translate_infrastructure_error(exc) from exc
