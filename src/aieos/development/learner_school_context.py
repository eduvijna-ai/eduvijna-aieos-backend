"""NON_PRODUCTION School Context learner-membership adapter (AIEOS360-S01-I01).

Deterministic synthetic current Class membership for Student OS development
only. No network access. No real student/teacher data. Not production
authority. Must never be imported by production runtime entrypoints.

Teacher assignability is a distinct contract and is not used here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from aieos.development.learner_principals import (
    CLASS_REF_5A,
    CLASS_REF_5B,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_A_SCHOOL_LEARNER_REF,
    STUDENT_B_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
    SchoolContextLearnerMembershipAuthorityService,
)

NON_PRODUCTION = True

_DEFAULT_MEMBERSHIPS: dict[UUID, tuple[CurrentLearnerClassMembership, ...]] = {
    STUDENT_A_PRINCIPAL_ID: (
        CurrentLearnerClassMembership(
            class_ref=CLASS_REF_5A,
            school_learner_ref=STUDENT_A_SCHOOL_LEARNER_REF,
        ),
    ),
    STUDENT_B_PRINCIPAL_ID: (
        CurrentLearnerClassMembership(class_ref=CLASS_REF_5B),
    ),
}


class DevelopmentSchoolContextLearnerMembershipReader:
    """Tenant- and learner-principal-scoped NON_PRODUCTION membership reader.

    Synthetic authority is bound to the exact configured tenant + explicit
    learner Principal mapping. Not ERP/SIS authority. Unknown learners and
    the Teacher OS development principal have no membership unless mapped.
    """

    def __init__(
        self,
        *,
        tenant_id: UUID,
        memberships: Mapping[UUID, Sequence[CurrentLearnerClassMembership]]
        | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._memberships: dict[UUID, tuple[CurrentLearnerClassMembership, ...]] = {
            principal_id: tuple(items)
            for principal_id, items in (
                memberships if memberships is not None else _DEFAULT_MEMBERSHIPS
            ).items()
        }
        self.call_count = 0
        self.calls: list[tuple[UUID, UUID]] = []

    def list_current_memberships(
        self,
        tenant_id: UUID,
        learner_principal_id: UUID,
    ) -> tuple[CurrentLearnerClassMembership, ...]:
        self.call_count += 1
        self.calls.append((tenant_id, learner_principal_id))
        if tenant_id != self._tenant_id:
            return ()
        return self._memberships.get(learner_principal_id, ())


def development_learner_membership_reader(
    *,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
) -> DevelopmentSchoolContextLearnerMembershipReader:
    """NON_PRODUCTION current-membership reader for the synthetic Student set."""
    return DevelopmentSchoolContextLearnerMembershipReader(tenant_id=tenant_id)


def development_learner_membership_authority(
    *,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
) -> SchoolContextLearnerMembershipAuthorityService:
    """NON_PRODUCTION current-membership authority for the synthetic Student set."""
    return SchoolContextLearnerMembershipAuthorityService(
        development_learner_membership_reader(tenant_id=tenant_id)
    )
