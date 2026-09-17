"""Parent Intelligence authorization port adapters backed by AuthorizationKernel.

Parent Intelligence capability vocabulary is owned by
``aieos.domains.parent_intelligence.application.ports`` — this module composes
the injected catalog from those canonical constants and does not redefine them.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapabilityForbidden,
)
from aieos.domains.parent_intelligence.application.ports import (
    PARENT_INTELLIGENCE_READ,
    AIEOS_PARENT_INTELLIGENCE_CAPABILITIES as DOMAIN_PARENT_INTELLIGENCE_CAPABILITIES,
)
from aieos.platform.security.authorization.decisions import AuthorityDecision
from aieos.platform.security.authorization.kernel import (
    AuthorizationKernel,
    capability_contains_wildcard,
)
from aieos.platform.security.context import AuthorizationUnavailableError

# Code-governed Parent Intelligence capability catalog (composition only; not a DB catalog).
AIEOS_PARENT_INTELLIGENCE_CAPABILITIES: frozenset[str] = frozenset(
    DOMAIN_PARENT_INTELLIGENCE_CAPABILITIES
)

assert PARENT_INTELLIGENCE_READ in AIEOS_PARENT_INTELLIGENCE_CAPABILITIES


class KernelParentIntelligenceAuthorization:
    """AuthorizationKernel adapter for Parent Intelligence protected operations."""

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
            AIEOS_PARENT_INTELLIGENCE_CAPABILITIES
        ):
            raise ParentIntelligenceCapabilityForbidden(
                "parent intelligence capability denied"
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
        raise ParentIntelligenceCapabilityForbidden(
            "parent intelligence capability denied"
        )
