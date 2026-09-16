"""School Intelligence capability vocabulary and technology-neutral ports.

Infrastructure types are not part of these contracts. Capability strings are
owned here and must not be redefined in AuthorizationKernel decisions.py.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

# Exact ADR-AIEOS-060 School Intelligence capability vocabulary.
# Protected reads compose: trusted principal + current tenant + ACTIVE HUMAN
# + exact capability ALLOW + distinct current School Scope Current Authority.
# Capability ALLOW does not replace school/campus/class scope.
SCHOOL_INTELLIGENCE_READ = "school.intelligence.read"

AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES = frozenset({SCHOOL_INTELLIGENCE_READ})


class SchoolIntelligenceAuthorization(Protocol):
    """Technology-neutral current School Intelligence capability authorization."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None: ...


class HumanPrincipalClassificationGate(Protocol):
    """Fail-closed current SoR HUMAN check (no School Intelligence SQL)."""

    def require_current_human_principal(self, principal_id: UUID) -> object: ...
