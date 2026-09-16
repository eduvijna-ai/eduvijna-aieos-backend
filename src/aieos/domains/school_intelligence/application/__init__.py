"""School Intelligence application layer. Orchestration only; no HTTP or SQL."""

from aieos.domains.school_intelligence.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
    SchoolIntelligenceApplicationError,
    SchoolIntelligenceCapabilityForbidden,
    SchoolIntelligenceReadUnavailable,
    SchoolIntelligenceScopeCapacityExceeded,
)
from aieos.domains.school_intelligence.application.intelligence import (
    GetPrincipalSchoolIntelligenceService,
)
from aieos.domains.school_intelligence.application.models import (
    MAX_AUTHORIZED_CLASS_COUNT,
    PROJECTION_MODE_DERIVED_ON_REQUEST,
    SCHOOL_INTELLIGENCE_SOURCE_AUTHORITIES,
    TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST,
    AssignmentLifecycleCounts,
    AuthorizedClassFacts,
    EvaluationCoverageAmongSubmitted,
    PrincipalSchoolIntelligenceClassCard,
    PrincipalSchoolIntelligenceReadModel,
    PrincipalSchoolIntelligenceSummary,
    SchoolIntelligenceEvaluationPolicy,
    SchoolIntelligenceFactsSnapshot,
    SchoolIntelligenceTimeWindow,
)
from aieos.domains.school_intelligence.application.ports import (
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES,
    SCHOOL_INTELLIGENCE_READ,
    HumanPrincipalClassificationGate,
    SchoolIntelligenceAuthorization,
    SchoolIntelligenceFactsReader,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    CurrentPrincipalSchoolScopeService,
    SchoolContextPrincipalScopeReader,
    UnconfiguredSchoolContextPrincipalScopeReader,
)

__all__ = [
    "AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES",
    "AssignmentLifecycleCounts",
    "AuthorizedClassFacts",
    "AuthorizedSchoolClassRef",
    "CurrentPrincipalSchoolScopeService",
    "EvaluationCoverageAmongSubmitted",
    "GetPrincipalSchoolIntelligenceService",
    "HumanPrincipalClassificationGate",
    "MAX_AUTHORIZED_CLASS_COUNT",
    "PROJECTION_MODE_DERIVED_ON_REQUEST",
    "PrincipalSchoolIntelligenceClassCard",
    "PrincipalSchoolIntelligenceReadModel",
    "PrincipalSchoolIntelligenceSummary",
    "SCHOOL_INTELLIGENCE_READ",
    "SCHOOL_INTELLIGENCE_SOURCE_AUTHORITIES",
    "SchoolContextContractError",
    "SchoolContextPrincipalScopeReader",
    "SchoolContextUnavailable",
    "SchoolIntelligenceApplicationError",
    "SchoolIntelligenceAuthorization",
    "SchoolIntelligenceCapabilityForbidden",
    "SchoolIntelligenceEvaluationPolicy",
    "SchoolIntelligenceFactsReader",
    "SchoolIntelligenceFactsSnapshot",
    "SchoolIntelligenceReadUnavailable",
    "SchoolIntelligenceScopeCapacityExceeded",
    "SchoolIntelligenceTimeWindow",
    "TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST",
    "UnconfiguredSchoolContextPrincipalScopeReader",
]
