"""Parent Intelligence application layer. Orchestration only; no HTTP or SQL."""

from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceApplicationError,
    ParentIntelligenceCapabilityForbidden,
    ParentIntelligenceCapacityExceeded,
    ParentIntelligenceReadUnavailable,
    ParentLearnerAccessContractError,
    ParentLearnerAccessUnavailable,
    ParentLearnerNotFound,
)
from aieos.domains.parent_intelligence.application.intelligence import (
    GetParentIntelligenceService,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
    CurrentParentLearnerAccessService,
    SchoolContextParentLearnerAccessReader,
    UnconfiguredSchoolContextParentLearnerAccessReader,
)
from aieos.domains.parent_intelligence.application.models import (
    MAX_ASSIGNMENTS_PER_LEARNER,
    MAX_AUTHORIZED_LEARNER_COUNT,
    MAX_CLASS_REFS_PER_LEARNER,
    PROJECTION_MODE_DERIVED_ON_REQUEST,
    TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST,
    ParentAssignmentStatus,
    ParentChildCard,
    ParentIntelligenceFactsSnapshot,
    ParentIntelligenceReadModel,
    ParentIntelligenceTimeWindow,
)
from aieos.domains.parent_intelligence.application.ports import (
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES,
    PARENT_INTELLIGENCE_READ,
    HumanPrincipalClassificationGate,
    LearnerPrincipalIntegrityAuthority,
    ParentIntelligenceAuthorization,
    ParentIntelligenceFactsReader,
)

__all__ = [
    "AIEOS_PARENT_INTELLIGENCE_CAPABILITIES",
    "AuthorizedLearnerAccess",
    "CurrentParentLearnerAccessService",
    "GetParentIntelligenceService",
    "HumanPrincipalClassificationGate",
    "LearnerPrincipalIntegrityAuthority",
    "MAX_ASSIGNMENTS_PER_LEARNER",
    "MAX_AUTHORIZED_LEARNER_COUNT",
    "MAX_CLASS_REFS_PER_LEARNER",
    "PARENT_INTELLIGENCE_READ",
    "PROJECTION_MODE_DERIVED_ON_REQUEST",
    "ParentAssignmentStatus",
    "ParentChildCard",
    "ParentIntelligenceApplicationError",
    "ParentIntelligenceAuthorization",
    "ParentIntelligenceCapabilityForbidden",
    "ParentIntelligenceCapacityExceeded",
    "ParentIntelligenceFactsReader",
    "ParentIntelligenceFactsSnapshot",
    "ParentIntelligenceReadModel",
    "ParentIntelligenceReadUnavailable",
    "ParentIntelligenceTimeWindow",
    "ParentLearnerAccessContractError",
    "ParentLearnerAccessUnavailable",
    "ParentLearnerNotFound",
    "SchoolContextParentLearnerAccessReader",
    "TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST",
    "UnconfiguredSchoolContextParentLearnerAccessReader",
]
