"""Distinct Principal School Scope Current Authority (AIEOS360-S02-I01).

School Intelligence consumes opaque ClassRef values from an external School
Context / ERP / SIS / Admin port. AIEOS does not own Class / Roster /
Enrollment master data.

This contract is distinct from teacher assignability and from learner
membership. Those ports answer different questions and are not used here.

Current-authority composition, every call:

1. trusted tenant_id + principal_id from the server-side caller
2. require current ACTIVE HUMAN Principal
3. require exact capability ``school.intelligence.read``
4. query current School Scope provider
5. validate provider contract
6. return only currently authorized ClassRefs

No JWT / header / query / body / frontend cache / teacher assignability /
learner membership / historical grant snapshot may supply ClassRefs.
No cached authorization.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aieos.domains.school_intelligence.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.school_intelligence.application.ports import (
    SCHOOL_INTELLIGENCE_READ,
    HumanPrincipalClassificationGate,
    SchoolIntelligenceAuthorization,
)


@dataclass(frozen=True, slots=True)
class AuthorizedSchoolClassRef:
    """Opaque currently authorized class target for Principal School Intelligence.

    ClassRef remains external School Context identity. This is not a canonical
    Class SoR, roster copy, learner list, teacher score, or learner-model truth.
    """

    class_ref: str
    display_label: str


class SchoolContextPrincipalScopeReader(Protocol):
    """Replaceable School Context port for current Principal school scope.

    Returns ClassRefs CURRENTLY within this HUMAN Principal's authorized
    School Intelligence scope for this tenant.
    """

    def list_current_authorized_classes(
        self,
        tenant_id: UUID,
        principal_id: UUID,
    ) -> Sequence[AuthorizedSchoolClassRef]: ...


class UnconfiguredSchoolContextPrincipalScopeReader:
    """Fail-closed reader when no real School Context provider is composed.

    Production I01 default: real ERP/SIS Principal school scope is not
    configured. Current Principal school-scope authority is unavailable.
    Empty successful scope is not returned.
    """

    def list_current_authorized_classes(
        self,
        tenant_id: UUID,
        principal_id: UUID,
    ) -> Sequence[AuthorizedSchoolClassRef]:
        raise SchoolContextUnavailable(
            "School Context is temporarily unavailable"
        )


class CurrentPrincipalSchoolScopeService:
    """Read-only current-authority composition. No UoW, events, or persistence."""

    def __init__(
        self,
        *,
        classification: HumanPrincipalClassificationGate,
        authorization: SchoolIntelligenceAuthorization,
        reader: SchoolContextPrincipalScopeReader,
    ) -> None:
        self._classification = classification
        self._authorization = authorization
        self._reader = reader

    def current_authorized_classes(
        self,
        tenant_id: UUID,
        principal_id: UUID,
    ) -> tuple[AuthorizedSchoolClassRef, ...]:
        self._classification.require_current_human_principal(principal_id)
        self._authorization.authorize(
            tenant_id=tenant_id,
            principal_id=principal_id,
            capability=SCHOOL_INTELLIGENCE_READ,
        )
        try:
            raw = self._reader.list_current_authorized_classes(
                tenant_id, principal_id
            )
        except SchoolContextUnavailable:
            raise
        except SchoolContextContractError:
            raise
        except Exception as exc:
            raise SchoolContextUnavailable(
                "School Context is temporarily unavailable"
            ) from exc

        return _validate_provider_items(raw)


def _validate_provider_items(
    raw: object,
) -> tuple[AuthorizedSchoolClassRef, ...]:
    if raw is None:
        raise SchoolContextContractError(
            "School Context provider returned an invalid response"
        )
    try:
        items = list(raw)  # type: ignore[arg-type]
    except TypeError as exc:
        raise SchoolContextContractError(
            "School Context provider returned an invalid response"
        ) from exc

    validated: list[AuthorizedSchoolClassRef] = []
    seen: set[str] = set()
    for item in items:
        class_ref, display_label = _extract_fields(item)
        if not class_ref.strip():
            raise SchoolContextContractError(
                "School Context provider returned a blank ClassRef"
            )
        if not display_label.strip():
            raise SchoolContextContractError(
                "School Context provider returned a blank display label"
            )
        if class_ref in seen:
            raise SchoolContextContractError(
                "School Context provider returned a duplicate ClassRef"
            )
        seen.add(class_ref)
        validated.append(
            AuthorizedSchoolClassRef(
                class_ref=class_ref,
                display_label=display_label,
            )
        )
    return tuple(validated)


def _extract_fields(item: object) -> tuple[str, str]:
    if isinstance(item, AuthorizedSchoolClassRef):
        return item.class_ref, item.display_label
    class_ref = getattr(item, "class_ref", None)
    display_label = getattr(item, "display_label", None)
    if not isinstance(class_ref, str) or not isinstance(display_label, str):
        raise SchoolContextContractError(
            "School Context provider returned a structurally invalid class item"
        )
    return class_ref, display_label
