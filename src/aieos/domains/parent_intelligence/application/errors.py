"""Technology-neutral Parent Intelligence application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
HTTP mapping is not authorized in AIEOS360-S03-I01.
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
