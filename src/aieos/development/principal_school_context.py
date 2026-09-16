"""NON_PRODUCTION Principal School Context adapter (AIEOS360-S02-I01).

Deterministic synthetic HUMAN Principal school scope for development/tests
only. No network access. No real school/learner/teacher data. Not production
School Context. Not roster master.

Must never be imported by production runtime entrypoints.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from aieos.development.teacher_os_review_scenario import (
    SCENARIO_NAMESPACE,
    SYNTHETIC_TENANT_ID,
)
from aieos.domains.school_intelligence.application.errors import (
    SchoolIntelligenceCapabilityForbidden,
)
from aieos.domains.school_intelligence.application.ports import (
    SCHOOL_INTELLIGENCE_READ,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    CurrentPrincipalSchoolScopeService,
)

NON_PRODUCTION = True

PRINCIPAL_OS_HUMAN_PRINCIPAL_ID = uuid.uuid5(
    SCENARIO_NAMESPACE, "aieos.aieos360-s02.synthetic-principal-os-human"
)

CLASS_REF_6A = "class-6a"
CLASS_REF_6B = "class-6b"

_SYNTHETIC_CLASSES: tuple[AuthorizedSchoolClassRef, ...] = (
    AuthorizedSchoolClassRef(class_ref=CLASS_REF_6A, display_label="Grade 6A"),
    AuthorizedSchoolClassRef(class_ref=CLASS_REF_6B, display_label="Grade 6B"),
)

__all__ = [
    "NON_PRODUCTION",
    "CLASS_REF_6A",
    "CLASS_REF_6B",
    "PRINCIPAL_OS_HUMAN_PRINCIPAL_ID",
    "SYNTHETIC_TENANT_ID",
    "DevelopmentSchoolContextPrincipalScopeReader",
    "DevelopmentSchoolIntelligencePermit",
    "development_principal_school_scope_reader",
]


class DevelopmentSchoolContextPrincipalScopeReader:
    """Tenant- and principal-scoped NON_PRODUCTION Principal ClassRef reader.

    Synthetic authority is bound to the exact configured tenant + HUMAN
    Principal. Not ERP/SIS authority. Contains no learner identities.
    """

    def __init__(self, *, tenant_id: UUID, principal_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._principal_id = principal_id
        self.call_count = 0
        self.calls: list[tuple[UUID, UUID]] = []

    def list_current_authorized_classes(
        self,
        tenant_id: UUID,
        principal_id: UUID,
    ) -> tuple[AuthorizedSchoolClassRef, ...]:
        self.call_count += 1
        self.calls.append((tenant_id, principal_id))
        if tenant_id != self._tenant_id or principal_id != self._principal_id:
            return ()
        return _SYNTHETIC_CLASSES


def development_principal_school_scope_reader(
    *,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
    principal_id: UUID = PRINCIPAL_OS_HUMAN_PRINCIPAL_ID,
) -> DevelopmentSchoolContextPrincipalScopeReader:
    """NON_PRODUCTION Principal School Scope reader for tests/dev only."""
    return DevelopmentSchoolContextPrincipalScopeReader(
        tenant_id=tenant_id,
        principal_id=principal_id,
    )


def development_principal_school_scope_service(
    *,
    classification,
    authorization,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
    principal_id: UUID = PRINCIPAL_OS_HUMAN_PRINCIPAL_ID,
) -> CurrentPrincipalSchoolScopeService:
    """NON_PRODUCTION composed service. Callers inject classification/auth."""
    return CurrentPrincipalSchoolScopeService(
        classification=classification,
        authorization=authorization,
        reader=development_principal_school_scope_reader(
            tenant_id=tenant_id,
            principal_id=principal_id,
        ),
    )


class DevelopmentSchoolIntelligencePermit:
    """NON_PRODUCTION exact-capability permit for school.intelligence.read.

    Rejects wildcard and unknown School Intelligence capabilities. Must never
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
        if "*" in capability or capability != SCHOOL_INTELLIGENCE_READ:
            raise SchoolIntelligenceCapabilityForbidden(
                "school intelligence capability denied"
            )
