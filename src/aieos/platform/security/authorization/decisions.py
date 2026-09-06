"""ADR-AIEOS-031 authorization decision vocabulary and status constants.

Generic Authorization Kernel concepts only. Content capability strings are
owned by ``aieos.domains.content.application.ports`` and must not be redefined
here.
"""

from __future__ import annotations

from enum import StrEnum


class AuthorityDecision(StrEnum):
    """Binary Authorization Kernel decision. Default is DENY."""

    ALLOW = "ALLOW"
    DENY = "DENY"


class PrincipalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class PrincipalKind(StrEnum):
    """Authoritative principal classification (ADR-AIEOS-023R1 / A23R1-INV-03).

    Stored on ``security.principals.principal_kind``. JWT/headers never supply
    kind — current SoR revalidation is the only authority.
    """

    HUMAN = "HUMAN"
    WORKLOAD = "WORKLOAD"


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class MembershipStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class GrantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
