"""School Context learner Class membership current-authority read (AIEOS360-S01-I01).

Learning consumes opaque ClassRef current-membership facts from an external
School Context port. Learning does not own Class / Roster / Enrollment master
data. This is CURRENT membership authority only — not an assignment snapshot,
historical enrollment, attendance, student profile, or roster SoR.

Teacher assignability ("this class is assignable for this teacher") is a
distinct Teaching contract and is not used here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from aieos.domains.learning.application.errors import (
    LearnerClassMembershipDenied,
    SchoolContextContractError,
    SchoolContextUnavailable,
)


@dataclass(frozen=True, slots=True)
class CurrentLearnerClassMembership:
    """Current Class membership fact for one learner.

    ``school_learner_ref`` is optional opaque correlation metadata only.
    It is not authentication identity, not a database foreign key, and not
    required for authorization.
    """

    class_ref: str
    school_learner_ref: str | None = None


class SchoolContextLearnerMembershipReader(Protocol):
    """Replaceable School Context port. Returns CURRENT learner Class memberships."""

    def list_current_memberships(
        self,
        tenant_id: UUID,
        learner_principal_id: UUID,
    ) -> Sequence[CurrentLearnerClassMembership]: ...


class ListCurrentLearnerMembershipsService:
    """Read-only application service. No UoW, events, or mutation audit."""

    def __init__(self, reader: SchoolContextLearnerMembershipReader) -> None:
        self._reader = reader

    def list(
        self,
        tenant_id: UUID,
        learner_principal_id: UUID,
    ) -> tuple[CurrentLearnerClassMembership, ...]:
        try:
            raw = self._reader.list_current_memberships(
                tenant_id, learner_principal_id
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


class SchoolContextLearnerMembershipAuthority(Protocol):
    """Current Class membership authority for learner assignment activity."""

    def require_current_membership(
        self,
        tenant_id: UUID,
        learner_principal_id: UUID,
        class_ref: str,
    ) -> CurrentLearnerClassMembership: ...


class SchoolContextLearnerMembershipAuthorityService:
    """Revalidates current learner Class membership via the School Context port."""

    def __init__(self, reader: SchoolContextLearnerMembershipReader) -> None:
        self._reader = reader

    def require_current_membership(
        self,
        tenant_id: UUID,
        learner_principal_id: UUID,
        class_ref: str,
    ) -> CurrentLearnerClassMembership:
        normalized = class_ref.strip()
        if not normalized:
            raise SchoolContextContractError(
                "requested ClassRef must not be blank"
            )
        items = ListCurrentLearnerMembershipsService(self._reader).list(
            tenant_id, learner_principal_id
        )
        for item in items:
            if item.class_ref == normalized:
                return item
        raise LearnerClassMembershipDenied(
            "learner is not currently a member of the requested class"
        )


class UnconfiguredSchoolContextLearnerMembershipReader:
    """Fail-closed reader when no real School Context provider is composed.

    Production S01-I01 default: real ERP/SIS learner membership is not
    configured. Current learner membership authority is unavailable.
    """

    def list_current_memberships(
        self,
        tenant_id: UUID,
        learner_principal_id: UUID,
    ) -> Sequence[CurrentLearnerClassMembership]:
        raise SchoolContextUnavailable(
            "School Context is temporarily unavailable"
        )


def _validate_provider_items(
    raw: object,
) -> tuple[CurrentLearnerClassMembership, ...]:
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

    validated: list[CurrentLearnerClassMembership] = []
    seen: set[str] = set()
    for item in items:
        class_ref, school_learner_ref = _extract_fields(item)
        if not class_ref.strip():
            raise SchoolContextContractError(
                "School Context provider returned a blank ClassRef"
            )
        if class_ref in seen:
            raise SchoolContextContractError(
                "School Context provider returned a duplicate ClassRef"
            )
        seen.add(class_ref)
        validated.append(
            CurrentLearnerClassMembership(
                class_ref=class_ref,
                school_learner_ref=school_learner_ref,
            )
        )
    return tuple(validated)


def _extract_fields(item: object) -> tuple[str, str | None]:
    if isinstance(item, CurrentLearnerClassMembership):
        return item.class_ref, item.school_learner_ref
    class_ref = getattr(item, "class_ref", None)
    if not isinstance(class_ref, str):
        raise SchoolContextContractError(
            "School Context provider returned a structurally invalid membership item"
        )
    school_learner_ref = getattr(item, "school_learner_ref", None)
    if school_learner_ref is not None and not isinstance(school_learner_ref, str):
        raise SchoolContextContractError(
            "School Context provider returned a structurally invalid membership item"
        )
    if isinstance(school_learner_ref, str) and not school_learner_ref.strip():
        school_learner_ref = None
    return class_ref, school_learner_ref
