"""Parent Intelligence capability vocabulary and technology-neutral ports.

Infrastructure types are not part of these contracts. Capability strings are
owned here and must not be redefined in AuthorizationKernel decisions.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from aieos.domains.parent_intelligence.application.models import (
    ParentIntelligenceFactsSnapshot,
)

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


class ParentIntelligenceFactsReader(Protocol):
    """Read-only Parent facts for learners that already passed I01.

    Receives ONLY learner IDs that have already passed Parent Learner Access
    Current Authority. Does not decide adult→learner entitlement. Does not
    replace Parent Learner Access Current Authority. Does not accept
    arbitrary client-selected learner IDs.

    Completeness contract for a successful snapshot:

    * empty authorized learner set → ``learners`` must be empty
    * non-empty authorized learner set → ``learners`` contains exactly one
      row for every requested learner, with exact set equality and exact
      multiplicity

    Missing, extra, or duplicate learner rows are a source-contract failure.
    Assignment IDs within each learner row must be unique.
    """

    def read_authorized_learner_facts(
        self,
        *,
        tenant_id: UUID,
        authorized_learner_principal_ids: Sequence[UUID],
        observed_at: datetime,
    ) -> ParentIntelligenceFactsSnapshot: ...
