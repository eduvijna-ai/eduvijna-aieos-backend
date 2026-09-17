"""Learner data-subject integrity backed by Security Authority SoR.

Adult actor ACTIVE checks are not reused. A HUMAN learner Principal that is
SUSPENDED or DISABLED remains a valid data subject when current tenant
membership facts are otherwise valid.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.domains.parent_intelligence.application.errors import (
    ParentLearnerAccessContractError,
    ParentLearnerAccessUnavailable,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.platform.security.authorization.repository import (
    SqlAlchemySecurityAuthorityRepository,
    membership_is_currently_valid,
)
from aieos.platform.security.context import AuthorizationUnavailableError


class SecurityAuthorityLearnerPrincipalIntegrity:
    """Fail-closed learner-subject validation without actor ACTIVE semantics."""

    def __init__(
        self,
        engine: Engine,
        *,
        repository: SqlAlchemySecurityAuthorityRepository | None = None,
    ) -> None:
        self._repo = repository or SqlAlchemySecurityAuthorityRepository(engine)

    def validate_learner_subject(
        self,
        *,
        tenant_id: UUID,
        learner_principal_id: UUID,
    ) -> None:
        try:
            bundle = self._repo.load_tenant_access_bundle(
                principal_id=learner_principal_id,
                tenant_id=tenant_id,
            )
        except AuthorizationUnavailableError as exc:
            raise ParentLearnerAccessUnavailable(
                "Parent Learner Access is temporarily unavailable"
            ) from exc
        except Exception as exc:
            raise ParentLearnerAccessUnavailable(
                "Parent Learner Access is temporarily unavailable"
            ) from exc

        principal = bundle.principal
        if principal is None:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned an unknown learner"
            )
        if principal.principal_kind is None:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned a learner without kind"
            )
        if principal.principal_kind is PrincipalKind.WORKLOAD:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned a WORKLOAD learner"
            )
        if principal.principal_kind is not PrincipalKind.HUMAN:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned a non-HUMAN learner"
            )
        membership = bundle.membership
        if membership is None:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned a tenant-incompatible learner"
            )
        if membership.tenant_id != tenant_id:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned a tenant-incompatible learner"
            )
        if not membership_is_currently_valid(membership, now=bundle.evaluated_at):
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned a tenant-incompatible learner"
            )
