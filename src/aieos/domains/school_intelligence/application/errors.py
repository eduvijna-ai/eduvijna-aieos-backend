"""Technology-neutral School Intelligence application errors.

No HTTP status codes, Problem Details, SQLAlchemy, or driver exceptions.
Do not import Teaching or Learning application errors.
"""

from __future__ import annotations


class SchoolIntelligenceApplicationError(Exception):
    """Base error for School Intelligence application-boundary failures."""


class SchoolIntelligenceCapabilityForbidden(SchoolIntelligenceApplicationError):
    """Exact current School Intelligence capability was DENY / not granted.

    Sanitized: does not reveal resource existence or kernel internals.
    """


class SchoolContextUnavailable(SchoolIntelligenceApplicationError):
    """School Context Principal-scope provider is unavailable or not composed."""


class SchoolContextContractError(SchoolIntelligenceApplicationError):
    """School Context Principal-scope provider returned an invalid response."""


class SchoolIntelligenceReadUnavailable(SchoolIntelligenceApplicationError):
    """Authoritative source-domain read failed. Do not translate into zero."""


class SchoolIntelligenceScopeCapacityExceeded(SchoolIntelligenceApplicationError):
    """Current authorized ClassRef set exceeds the bounded first-showcase limit."""
