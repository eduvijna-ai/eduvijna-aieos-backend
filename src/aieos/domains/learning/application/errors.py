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


class AssignmentNotFound(LearningApplicationError):
    """TeachingAssignment is not visible as current learner activity."""


class AssignmentNotCurrentlyConsumable(LearningApplicationError):
    """Assignment is not currently consumable for the learner."""


class AssignmentNotYetAvailable(LearningApplicationError):
    """Assignment is ACTIVE but available_from is still in the future."""


class SecondAttemptNotAuthorized(LearningApplicationError):
    """S01 one-attempt policy rejects a fresh start after SUBMITTED."""


class AttemptInProgressConflict(LearningApplicationError):
    """A fresh start is rejected because an IN_PROGRESS attempt already exists."""


class LearnerAttemptForbidden(LearningApplicationError):
    """The attempt is owned by a different learner. Conceal as not found at HTTP."""


class ContentNotLearnerConsumable(LearningApplicationError):
    """Exact assigned ContentVersion is not a governed learner-facing contract."""


class ExactContentVersionNotFound(LearningApplicationError):
    """Exact assigned ContentVersion is not visible in the execution tenant."""


class IdempotencyKeyReused(LearningApplicationError):
    """The Idempotency-Key was already bound to a different material request."""


class InvalidLearnerRequest(LearningApplicationError):
    """Learner request failed application validation."""


class ResponseValidationFailed(LearningApplicationError):
    """A response write failed validation against the exact learner projection."""


class AssignmentClosedOrCancelled(LearningApplicationError):
    """CLOSED or CANCELLED assignments reject start, save, and submit."""


class HumanPrincipalRequired(LearningApplicationError):
    """Current activity requires an ACTIVE HUMAN Principal."""
