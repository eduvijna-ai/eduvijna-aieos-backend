"""Technology-neutral Learning application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
Do not import Teaching application errors.
"""

from __future__ import annotations


class LearningApplicationError(Exception):
    """Base error for Learning application-boundary failures."""


class LearnerClassMembershipDenied(LearningApplicationError):
    """The learner is not a current member of the requested ClassRef."""


class SchoolContextUnavailable(LearningApplicationError):
    """School Context learner-membership provider is unavailable or not composed."""


class SchoolContextContractError(LearningApplicationError):
    """School Context learner-membership provider returned an invalid response."""


class PersistenceOperationFailed(LearningApplicationError):
    """Infrastructure/transaction/connection/driver failure, not a business conflict."""


class PersistenceInvariantViolation(LearningApplicationError):
    """A Learning persistence invariant failed (database check or visibility)."""


class AttemptNotFound(LearningApplicationError):
    """LearnerAttempt is not visible in the execution tenant."""


class AttemptConcurrencyConflict(LearningApplicationError):
    """expected_aggregate_revision did not match the stored LearnerAttempt head."""


class AttemptAlreadySubmitted(LearningApplicationError):
    """SUBMITTED LearnerAttempt rejects mutation, reopen, and a second submit."""


class InvalidAttemptState(LearningApplicationError):
    """The attempt lifecycle does not permit the requested persistence operation."""


class InvalidResponse(LearningApplicationError):
    """An AttemptResponseItem failed its typed exclusive-value contract."""


class SubmissionImmutable(LearningApplicationError):
    """LearnerSubmission evidence rejects UPDATE/DELETE, including privileged paths."""
