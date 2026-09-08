"""Learning domain errors. No HTTP, SQL, or driver concepts."""

from __future__ import annotations


class LearningDomainError(Exception):
    """Base error for Learning domain invariant violations."""


class InvalidLearningIdentityError(LearningDomainError):
    """A Learning-owned identity value failed its identity contract."""


class InvalidAggregateRevisionError(LearningDomainError):
    """aggregate_revision is not a non-negative integer."""


class InvalidLearnerAttemptError(LearningDomainError):
    """A LearnerAttempt aggregate field or construction invariant failed."""


class InvalidAttemptStateError(InvalidLearnerAttemptError):
    """The attempt lifecycle does not permit the requested transition."""


class AttemptAlreadySubmittedError(InvalidAttemptStateError):
    """SUBMITTED is terminal; mutation and reopen are rejected."""


class InvalidAttemptResponseError(LearningDomainError):
    """An AttemptResponseItem field or exclusive-value shape failed."""


class InvalidLearnerSubmissionError(LearningDomainError):
    """A LearnerSubmission field or snapshot invariant failed."""
