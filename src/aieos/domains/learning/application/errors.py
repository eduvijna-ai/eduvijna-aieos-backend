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
