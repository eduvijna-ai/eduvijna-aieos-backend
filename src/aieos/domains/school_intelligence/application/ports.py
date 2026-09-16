"""School Intelligence capability vocabulary and technology-neutral ports.

Infrastructure types are not part of these contracts. Capability strings are
owned here and must not be redefined in AuthorizationKernel decisions.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from aieos.domains.school_intelligence.application.models import (
    SchoolIntelligenceFactsSnapshot,
)

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


class SchoolIntelligenceFactsReader(Protocol):
    """Read-only privacy-safe aggregated facts for currently authorized ClassRefs.

    Receives the complete current authorized ClassRef set and current
    evaluation-policy identity. Must filter to those ClassRefs before any
    aggregation. Must not return teacher or learner identity fields.

    Completeness contract for a successful snapshot:

    * empty authorized ClassRef set → ``classes`` must be empty
    * non-empty authorized ClassRef set → ``classes`` contains exactly one
      ``AuthorizedClassFacts`` row for every requested ClassRef, with exact
      set equality and exact multiplicity, including explicit zero-activity
      rows when no source facts exist

    Missing, extra, duplicate, or blank ClassRefs are a source-contract
    failure, not truthful zero.
    """

    def read_authorized_class_facts(
        self,
        *,
        tenant_id: UUID,
        authorized_class_refs: Sequence[str],
        evaluation_policy_id: str,
        evaluation_policy_version: int,
    ) -> SchoolIntelligenceFactsSnapshot: ...
