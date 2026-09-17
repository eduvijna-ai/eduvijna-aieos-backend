"""Parent Learner Access Current Authority (AIEOS360-S03-I01).

Parent Intelligence consumes current adult→learner ACCESS from an external
School Context / ERP / SIS / Admin port. AIEOS does not own family, guardian,
custody, household, enrollment, or roster master data.

This contract is distinct from teacher assignability, learner membership,
and Principal School Scope. Those ports answer different questions and are
not used here.

Current-authority composition, every call:

1. require adult current ACTIVE HUMAN Principal
2. require exact capability ``parent.intelligence.read``
3. invoke current Parent Learner Access provider
4. validate provider response structure
5. validate every returned learner subject
6. reject any contract-invalid set
7. return the complete validated current set

No JWT / header / query / body / frontend cache / teacher classes /
learner membership enumeration / Principal School Scope / historical
grant snapshot may supply learner IDs.
No cached authorization.
No presentation metadata.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aieos.domains.parent_intelligence.application.errors import (
    ParentLearnerAccessContractError,
    ParentLearnerAccessUnavailable,
)
from aieos.domains.parent_intelligence.application.ports import (
    PARENT_INTELLIGENCE_READ,
    HumanPrincipalClassificationGate,
    LearnerPrincipalIntegrityAuthority,
    ParentIntelligenceAuthorization,
)


@dataclass(frozen=True, slots=True)
class AuthorizedLearnerAccess:
    """Currently authorized learner Principal for Parent Intelligence.

    Authorization result only. Presentation labels, names, emails, and
    family-law categories are not part of this contract.
    """

    learner_principal_id: UUID


class SchoolContextParentLearnerAccessReader(Protocol):
    """Replaceable School Context port for current adult→learner access.

    Returns learner Principals CURRENTLY accessible to this HUMAN adult
    Principal in this tenant.
    """

    def list_current_authorized_learners(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
    ) -> Sequence[AuthorizedLearnerAccess]: ...


class UnconfiguredSchoolContextParentLearnerAccessReader:
    """Fail-closed reader when no real School Context provider is composed.

    Production I01 default: real ERP/SIS Parent learner access is not
    configured. Current adult→learner authority is unavailable.
    Empty successful access is not returned.
    """

    def list_current_authorized_learners(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
    ) -> Sequence[AuthorizedLearnerAccess]:
        raise ParentLearnerAccessUnavailable(
            "Parent Learner Access is temporarily unavailable"
        )


class CurrentParentLearnerAccessService:
    """Read-only current-authority composition. No UoW, events, or persistence."""

    def __init__(
        self,
        *,
        classification: HumanPrincipalClassificationGate,
        authorization: ParentIntelligenceAuthorization,
        reader: SchoolContextParentLearnerAccessReader,
        integrity: LearnerPrincipalIntegrityAuthority,
    ) -> None:
        self._classification = classification
        self._authorization = authorization
        self._reader = reader
        self._integrity = integrity

    def current_authorized_learners(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
    ) -> tuple[AuthorizedLearnerAccess, ...]:
        self._classification.require_current_human_principal(adult_principal_id)
        self._authorization.authorize(
            tenant_id=tenant_id,
            principal_id=adult_principal_id,
            capability=PARENT_INTELLIGENCE_READ,
        )
        try:
            raw = self._reader.list_current_authorized_learners(
                tenant_id, adult_principal_id
            )
        except ParentLearnerAccessUnavailable:
            raise
        except ParentLearnerAccessContractError:
            raise
        except Exception as exc:
            raise ParentLearnerAccessUnavailable(
                "Parent Learner Access is temporarily unavailable"
            ) from exc

        items = _validate_provider_structure(raw)
        for item in items:
            try:
                self._integrity.validate_learner_subject(
                    tenant_id=tenant_id,
                    learner_principal_id=item.learner_principal_id,
                )
            except ParentLearnerAccessContractError:
                raise
            except ParentLearnerAccessUnavailable:
                raise
            except Exception as exc:
                raise ParentLearnerAccessUnavailable(
                    "Parent Learner Access is temporarily unavailable"
                ) from exc
        return tuple(
            sorted(items, key=lambda item: item.learner_principal_id.bytes)
        )


def _validate_provider_structure(
    raw: object,
) -> tuple[AuthorizedLearnerAccess, ...]:
    if raw is None:
        raise ParentLearnerAccessContractError(
            "Parent Learner Access provider returned an invalid response"
        )
    try:
        items = list(raw)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ParentLearnerAccessContractError(
            "Parent Learner Access provider returned an invalid response"
        ) from exc

    validated: list[AuthorizedLearnerAccess] = []
    seen: set[UUID] = set()
    for item in items:
        learner_principal_id = _extract_learner_principal_id(item)
        if learner_principal_id in seen:
            raise ParentLearnerAccessContractError(
                "Parent Learner Access provider returned a duplicate learner"
            )
        seen.add(learner_principal_id)
        validated.append(
            AuthorizedLearnerAccess(learner_principal_id=learner_principal_id)
        )
    return tuple(validated)


def _extract_learner_principal_id(item: object) -> UUID:
    if isinstance(item, AuthorizedLearnerAccess):
        learner_principal_id = item.learner_principal_id
    else:
        learner_principal_id = getattr(item, "learner_principal_id", None)
    if not isinstance(learner_principal_id, UUID):
        raise ParentLearnerAccessContractError(
            "Parent Learner Access provider returned a malformed learner UUID"
        )
    return learner_principal_id
