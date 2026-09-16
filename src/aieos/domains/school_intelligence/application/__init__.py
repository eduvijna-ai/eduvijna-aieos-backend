"""School Intelligence application layer. Orchestration only; no HTTP or SQL."""

from aieos.domains.school_intelligence.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
    SchoolIntelligenceApplicationError,
    SchoolIntelligenceCapabilityForbidden,
)
from aieos.domains.school_intelligence.application.ports import (
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
    SCHOOL_INTELLIGENCE_READ,
    HumanPrincipalClassificationGate,
    SchoolIntelligenceAuthorization,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    CurrentPrincipalSchoolScopeService,
    SchoolContextPrincipalScopeReader,
    UnconfiguredSchoolContextPrincipalScopeReader,
)

__all__ = [
    "AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES",
    "AuthorizedSchoolClassRef",
    "CurrentPrincipalSchoolScopeService",
    "HumanPrincipalClassificationGate",
    "SCHOOL_INTELLIGENCE_READ",
    "SchoolContextContractError",
    "SchoolContextPrincipalScopeReader",
    "SchoolContextUnavailable",
    "SchoolIntelligenceApplicationError",
    "SchoolIntelligenceAuthorization",
    "SchoolIntelligenceCapabilityForbidden",
    "UnconfiguredSchoolContextPrincipalScopeReader",
]
