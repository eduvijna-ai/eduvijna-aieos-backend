"""NON_PRODUCTION Parent Learner Access adapter (AIEOS360-S03-I01).

Deterministic synthetic HUMAN adult→learner current access for
development/tests only. No network access. No real family/guardian/custody
data. Not production School Context. Not ERP/SIS integration.

Must never be imported by production runtime entrypoints.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from aieos.development.teacher_os_review_scenario import (
    SCENARIO_NAMESPACE,
    SYNTHETIC_TENANT_ID,
)
from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapabilityForbidden,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
    CurrentParentLearnerAccessService,
)
from aieos.domains.parent_intelligence.application.ports import (
    PARENT_INTELLIGENCE_READ,
)

NON_PRODUCTION = True

PARENT_OS_HUMAN_ADULT_A_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s03.synthetic-parent-os-adult-a"
)
PARENT_OS_HUMAN_ADULT_B_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s03.synthetic-parent-os-adult-b"
)
LEARNER_CHILD_1_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s03.synthetic-learner-child-1"
)
LEARNER_CHILD_2_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s03.synthetic-learner-child-2"
)
LEARNER_CHILD_3_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s03.synthetic-learner-child-3"
)

_DEFAULT_ACCESS: dict[UUID, tuple[UUID, ...]] = {
    PARENT_OS_HUMAN_ADULT_A_ID: (LEARNER_CHILD_1_ID, LEARNER_CHILD_2_ID),
    PARENT_OS_HUMAN_ADULT_B_ID: (LEARNER_CHILD_3_ID,),
}

__all__ = [
    "NON_PRODUCTION",
    "PARENT_OS_HUMAN_ADULT_A_ID",
    "PARENT_OS_HUMAN_ADULT_B_ID",
    "LEARNER_CHILD_1_ID",
    "LEARNER_CHILD_2_ID",
    "LEARNER_CHILD_3_ID",
    "SYNTHETIC_TENANT_ID",
    "DevelopmentSchoolContextParentLearnerAccessReader",
    "DevelopmentParentIntelligencePermit",
    "development_parent_learner_access_reader",
]


class DevelopmentSchoolContextParentLearnerAccessReader:
    """Tenant- and adult-principal-scoped NON_PRODUCTION learner-access reader.

    Synthetic authority is bound to the exact configured tenant. Current
    adult→learner grants are mutable so tests can prove revocation without
    cache. Not ERP/SIS authority. Contains no presentation labels.
    """

    def __init__(
        self,
        *,
        tenant_id: UUID,
        access: dict[UUID, tuple[UUID, ...]] | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._access = {
            adult_id: tuple(learner_ids)
            for adult_id, learner_ids in (access or _DEFAULT_ACCESS).items()
        }
        self.call_count = 0
        self.calls: list[tuple[UUID, UUID]] = []

    def set_current_authorized_learners(
        self,
        adult_principal_id: UUID,
        learner_principal_ids: tuple[UUID, ...],
    ) -> None:
        self._access[adult_principal_id] = tuple(learner_principal_ids)

    def list_current_authorized_learners(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
    ) -> tuple[AuthorizedLearnerAccess, ...]:
        self.call_count += 1
        self.calls.append((tenant_id, adult_principal_id))
        if tenant_id != self._tenant_id:
            return ()
        learner_ids = self._access.get(adult_principal_id, ())
        return tuple(
            AuthorizedLearnerAccess(learner_principal_id=learner_id)
            for learner_id in learner_ids
        )


def development_parent_learner_access_reader(
    *,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
    access: dict[UUID, tuple[UUID, ...]] | None = None,
) -> DevelopmentSchoolContextParentLearnerAccessReader:
    """NON_PRODUCTION Parent Learner Access reader for tests/dev only."""
    return DevelopmentSchoolContextParentLearnerAccessReader(
        tenant_id=tenant_id,
        access=access,
    )


def development_parent_learner_access_service(
    *,
    classification,
    authorization,
    integrity,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
    access: dict[UUID, tuple[UUID, ...]] | None = None,
) -> CurrentParentLearnerAccessService:
    """NON_PRODUCTION composed service. Callers inject classification/auth."""
    return CurrentParentLearnerAccessService(
        classification=classification,
        authorization=authorization,
        reader=development_parent_learner_access_reader(
            tenant_id=tenant_id,
            access=access,
        ),
        integrity=integrity,
    )


class DevelopmentParentIntelligencePermit:
    """NON_PRODUCTION exact-capability permit for parent.intelligence.read.

    Rejects wildcard and unknown Parent Intelligence capabilities. Must never
    be imported by production runtime composition.
    """

    NON_PRODUCTION = True

    def authorize(
        self,
        *,
        tenant_id: UUID,
        principal_id: UUID,
        capability: str,
    ) -> None:
        del tenant_id, principal_id
        if "*" in capability or capability != PARENT_INTELLIGENCE_READ:
            raise ParentIntelligenceCapabilityForbidden(
                "parent intelligence capability denied"
            )
