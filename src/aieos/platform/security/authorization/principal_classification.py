"""Current Principal classification authority (HUMAN | WORKLOAD).

Kind is current server-side SoR authority on ``security.principals``.
JWT claims, request headers, and transport never supply ``principal_kind``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.platform.security.authorization.decisions import (
    PrincipalKind,
    PrincipalStatus,
)
from aieos.platform.security.authorization.repository import (
    SqlAlchemySecurityAuthorityRepository,
)
from aieos.platform.security.context import (
    AuthorizationUnavailableError,
    UnauthorizedError,
)


class CurrentPrincipalClassificationAuthority:
    """Fail-closed current Principal kind checks against security SoR."""

    def __init__(
        self,
        engine: Engine,
        *,
        repository: SqlAlchemySecurityAuthorityRepository | None = None,
    ) -> None:
        self._repo = repository or SqlAlchemySecurityAuthorityRepository(engine)

    def resolve_current_principal_kind(
        self, principal_id: UUID
    ) -> PrincipalKind:
        """Return current HUMAN/WORKLOAD kind; fail closed otherwise.

        Missing/inactive/NULL kind → ``UnauthorizedError``.
        Corrupt/unavailable SoR → ``AuthorizationUnavailableError``.
        """
        try:
            row = self._repo.load_principal(principal_id)
        except AuthorizationUnavailableError:
            raise
        except Exception as exc:
            raise AuthorizationUnavailableError(
                "authorization unavailable"
            ) from exc
        if row is None:
            raise UnauthorizedError("principal not authorized")
        if row.status != PrincipalStatus.ACTIVE:
            raise UnauthorizedError("principal not authorized")
        if row.principal_kind is None:
            raise UnauthorizedError("principal not authorized")
        return row.principal_kind

    def require_current_human_principal(
        self, principal_id: UUID
    ) -> PrincipalKind:
        """Require current ACTIVE HUMAN principal; fail closed otherwise."""
        kind = self.resolve_current_principal_kind(principal_id)
        if kind is not PrincipalKind.HUMAN:
            raise UnauthorizedError("principal not authorized")
        return PrincipalKind.HUMAN

    def require_current_workload_principal(
        self, principal_id: UUID
    ) -> PrincipalKind:
        """Require current ACTIVE WORKLOAD principal; fail closed otherwise."""
        kind = self.resolve_current_principal_kind(principal_id)
        if kind is not PrincipalKind.WORKLOAD:
            raise UnauthorizedError("principal not authorized")
        return PrincipalKind.WORKLOAD
