"""Parent Intelligence capability vocabulary and technology-neutral ports.

Infrastructure types are not part of these contracts. Capability strings are
owned here and must not be redefined in AuthorizationKernel decisions.py.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

# Exact ADR-AIEOS-061 Parent Intelligence capability vocabulary.
# Protected reads compose: trusted principal + current tenant + ACTIVE HUMAN
# adult + exact capability ALLOW + distinct Parent Learner Access Current
# Authority. Capability ALLOW does not grant access to all learners.
PARENT_INTELLIGENCE_READ = "parent.intelligence.read"

AIEOS_PARENT_INTELLIGENCE_CAPABILITIES = frozenset({PARENT_INTELLIGENCE_READ})


class ParentIntelligenceAuthorization(Protocol):
    """Technology-neutral current Parent Intelligence capability authorization."""

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None: ...


class HumanPrincipalClassificationGate(Protocol):
    """Fail-closed current SoR HUMAN adult-actor check (no Parent SQL)."""

    def require_current_human_principal(self, principal_id: UUID) -> object: ...


class LearnerPrincipalIntegrityAuthority(Protocol):
    """Validate a learner data-subject Principal without actor ACTIVE rules.

    Adult actor lifecycle and learner data-subject integrity are distinct.
    SUSPENDED / DISABLED learner Principals must not fail merely because
    adult actors must be ACTIVE.
    """

    def validate_learner_subject(
        self,
        *,
        tenant_id: UUID,
        learner_principal_id: UUID,
    ) -> None: ...
