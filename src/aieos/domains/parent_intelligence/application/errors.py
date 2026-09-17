"""Technology-neutral Parent Intelligence application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
HTTP mapping lives in the platform Problem Details layer.
"""

from __future__ import annotations


class ParentIntelligenceApplicationError(Exception):
    """Base error for Parent Intelligence application-boundary failures."""


class ParentIntelligenceCapabilityForbidden(ParentIntelligenceApplicationError):
    """Exact current Parent Intelligence capability was DENY / not granted.

    Sanitized: does not reveal resource existence or kernel internals.
    """


class ParentLearnerAccessUnavailable(ParentIntelligenceApplicationError):
    """Parent Learner Access provider is unavailable, unconfigured, or failed."""


class ParentLearnerAccessContractError(ParentIntelligenceApplicationError):
    """Parent Learner Access provider returned an invalid current-access set."""


class ParentLearnerNotFound(ParentIntelligenceApplicationError):
    """Selector learner is absent from the current authorized set.

    Concealment only. Does not distinguish unknown, unauthorized, revoked,
    other-tenant, or other-school cases.
    """


class ParentIntelligenceReadUnavailable(ParentIntelligenceApplicationError):
    """Authoritative Parent fact source read failed. Do not translate into zero."""


class ParentIntelligenceCapacityExceeded(ParentIntelligenceApplicationError):
    """Authorized read exceeded an implementation protection limit."""
