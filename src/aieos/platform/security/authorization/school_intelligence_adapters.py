"""School Intelligence authorization port adapters backed by AuthorizationKernel.

School Intelligence capability vocabulary is owned by
``aieos.domains.school_intelligence.application.ports`` — this module composes
the injected catalog from those canonical constants and does not redefine them.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.school_intelligence.application.errors import (
    SchoolIntelligenceCapabilityForbidden,
)
from aieos.domains.school_intelligence.application.ports import (
    SCHOOL_INTELLIGENCE_READ,
    AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES as DOMAIN_SCHOOL_INTELLIGENCE_CAPABILITIES,
)
from aieos.platform.security.authorization.decisions import AuthorityDecision
from aieos.platform.security.authorization.kernel import (
    AuthorizationKernel,
    capability_contains_wildcard,
)
from aieos.platform.security.context import AuthorizationUnavailableError

# Code-governed School Intelligence capability catalog (composition only; not a DB catalog).
AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES: frozenset[str] = frozenset(
    DOMAIN_SCHOOL_INTELLIGENCE_CAPABILITIES
)

assert SCHOOL_INTELLIGENCE_READ in AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES


class KernelSchoolIntelligenceAuthorization:
    """AuthorizationKernel adapter for School Intelligence protected operations."""

    def __init__(self, kernel: AuthorizationKernel) -> None:
        self._kernel = kernel

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None:
        if capability_contains_wildcard(capability) or capability not in (
            AIEOS_SCHOOL_INTELLIGENCE_CAPABILITIES
        ):
            raise SchoolIntelligenceCapabilityForbidden(
                "school intelligence capability denied"
            )
        try:
            decision = self._kernel.decide_capability(
                principal_id=principal_id,
                tenant_id=tenant_id,
                capability=capability,
            )
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if decision is AuthorityDecision.ALLOW:
            return
        raise SchoolIntelligenceCapabilityForbidden(
            "school intelligence capability denied"
        )
