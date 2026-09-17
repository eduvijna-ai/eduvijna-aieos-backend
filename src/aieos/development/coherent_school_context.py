"""NON_PRODUCTION coherent School Context current-fact provider (AIEOS360-S04-I01).

One in-memory development fact universe projected through the FOUR EXISTING,
DISTINCT School Context reader contracts:

* Teaching ``SchoolContextClassReader.list_assignable_classes``
* Learning ``SchoolContextLearnerMembershipReader.list_current_memberships``
* School Intelligence ``SchoolContextPrincipalScopeReader.list_current_authorized_classes``
* Parent Intelligence ``SchoolContextParentLearnerAccessReader.list_current_authorized_learners``

Provider consolidation is not contract consolidation. This module does not
create ``SchoolContextService``, ``UniversalSchoolContextPort``, or
``AdminSchoolService``.

Classification: NON_PRODUCTION. Deterministic. In-memory. Replaceable.
No network dependency. Not an ERP/SIS master. Not an AIEOS School / Class /
Roster / Enrollment / Family SoR. Not Admin OS. Not a public mutation API.

Must never be imported by production runtime composition. Production remains
unconfigured / omitted / fail closed.

ADR-AIEOS-062 Frozen / Approved is architecture authority. This module is the
S04-I01 development substrate only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from aieos.development.learner_principals import (
    CLASS_REF_5A,
    CLASS_REF_5B,
    STUDENT_A_PRINCIPAL_ID,
    STUDENT_A_SCHOOL_LEARNER_REF,
    STUDENT_B_PRINCIPAL_ID,
)
from aieos.development.parent_learner_access import (
    PARENT_OS_HUMAN_ADULT_A_ID,
    PARENT_OS_HUMAN_ADULT_B_ID,
)
from aieos.development.principal_school_context import PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
from aieos.development.teacher_os_review_scenario import (
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
)
from aieos.domains.learning.application.learner_membership import (
    CurrentLearnerClassMembership,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    AuthorizedLearnerAccess,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
)
from aieos.domains.teaching.application.school_context import AssignableClassRef

NON_PRODUCTION = True

CLASS_LABEL_5A = "Grade 5A"
CLASS_LABEL_5B = "Grade 5B"

__all__ = [
    "NON_PRODUCTION",
    "CLASS_LABEL_5A",
    "CLASS_LABEL_5B",
    "CLASS_REF_5A",
    "CLASS_REF_5B",
    "STUDENT_A_PRINCIPAL_ID",
    "STUDENT_B_PRINCIPAL_ID",
    "STUDENT_A_SCHOOL_LEARNER_REF",
    "SYNTHETIC_TENANT_ID",
    "SYNTHETIC_PRINCIPAL_ID",
    "PRINCIPAL_OS_HUMAN_PRINCIPAL_ID",
    "PARENT_OS_HUMAN_ADULT_A_ID",
    "PARENT_OS_HUMAN_ADULT_B_ID",
    "CoherentSchoolContextFixtureError",
    "DevelopmentCoherentSchoolContextProvider",
    "development_coherent_school_context_provider",
]


class CoherentSchoolContextFixtureError(ValueError):
    """Malformed NON_PRODUCTION coherent School Context fixture state.

    Development/test control only. Not an Admin command, ERP mutation, or
    HTTP API error. Fail closed: do not return a partially valid authority
    result.
    """


@dataclass(frozen=True, slots=True)
class _Facts:
    tenant_id: UUID
    class_definitions: dict[str, str]
    class_order: tuple[str, ...]
    teacher_authority: dict[UUID, tuple[str, ...]]
    learner_memberships: dict[UUID, tuple[CurrentLearnerClassMembership, ...]]
    principal_scope: dict[UUID, tuple[str, ...]]
    adult_learner_access: dict[UUID, tuple[UUID, ...]]


def _require_uuid(value: object, *, what: str) -> UUID:
    if not isinstance(value, UUID):
        raise CoherentSchoolContextFixtureError(
            f"malformed required UUID identity: {what}"
        )
    return value


def _require_class_ref(value: object, *, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CoherentSchoolContextFixtureError(f"blank or malformed ClassRef: {what}")
    return value


def _require_display_label(value: object, *, class_ref: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoherentSchoolContextFixtureError(
            f"blank class display definition for {class_ref!r}"
        )
    return value


def _require_school_learner_ref(value: object, *, class_ref: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CoherentSchoolContextFixtureError(
            f"malformed optional external correlation for {class_ref!r}"
        )
    return value


def _unique_class_refs(
    values: Sequence[object],
    *,
    what: str,
    known_classes: Mapping[str, str],
) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        class_ref = _require_class_ref(raw, what=what)
        if class_ref not in known_classes:
            raise CoherentSchoolContextFixtureError(
                f"unknown referenced ClassRef {class_ref!r} in {what}"
            )
        if class_ref in seen:
            raise CoherentSchoolContextFixtureError(
                f"duplicate ClassRef {class_ref!r} in {what}"
            )
        seen.add(class_ref)
        ordered.append(class_ref)
    return tuple(ordered)


def _validated_facts(
    *,
    tenant_id: object,
    class_definitions: Sequence[tuple[object, object]],
    teacher_authority: Mapping[object, Sequence[object]],
    learner_memberships: Mapping[object, Sequence[object]],
    principal_scope: Mapping[object, Sequence[object]],
    adult_learner_access: Mapping[object, Sequence[object]],
) -> _Facts:
    bound_tenant = _require_uuid(tenant_id, what="tenant_id")

    definitions: dict[str, str] = {}
    class_order: list[str] = []
    for raw_ref, raw_label in class_definitions:
        class_ref = _require_class_ref(raw_ref, what="class definition")
        display_label = _require_display_label(raw_label, class_ref=class_ref)
        if class_ref in definitions:
            if definitions[class_ref] != display_label:
                raise CoherentSchoolContextFixtureError(
                    f"conflicting class display definition for {class_ref!r}"
                )
            raise CoherentSchoolContextFixtureError(
                f"duplicate ClassRef definition {class_ref!r}"
            )
        definitions[class_ref] = display_label
        class_order.append(class_ref)

    teachers: dict[UUID, tuple[str, ...]] = {}
    for raw_teacher, raw_refs in teacher_authority.items():
        teacher_id = _require_uuid(raw_teacher, what="teacher Principal")
        if teacher_id in teachers:
            raise CoherentSchoolContextFixtureError(
                "duplicate teacher Principal in ClassRef authority"
            )
        teachers[teacher_id] = _unique_class_refs(
            raw_refs,
            what="teacher ClassRef authority",
            known_classes=definitions,
        )

    memberships: dict[UUID, tuple[CurrentLearnerClassMembership, ...]] = {}
    for raw_learner, raw_items in learner_memberships.items():
        learner_id = _require_uuid(raw_learner, what="learner Principal")
        if learner_id in memberships:
            raise CoherentSchoolContextFixtureError(
                "duplicate learner Principal in membership"
            )
        seen_refs: set[str] = set()
        items: list[CurrentLearnerClassMembership] = []
        for raw_item in raw_items:
            if isinstance(raw_item, CurrentLearnerClassMembership):
                class_ref = raw_item.class_ref
                school_learner_ref = raw_item.school_learner_ref
            elif isinstance(raw_item, tuple) and len(raw_item) == 2:
                class_ref, school_learner_ref = raw_item
            else:
                raise CoherentSchoolContextFixtureError(
                    "malformed learner membership entry"
                )
            class_ref = _require_class_ref(class_ref, what="learner membership")
            if class_ref not in definitions:
                raise CoherentSchoolContextFixtureError(
                    f"unknown referenced ClassRef {class_ref!r} in learner membership"
                )
            if class_ref in seen_refs:
                raise CoherentSchoolContextFixtureError(
                    f"duplicate ClassRef {class_ref!r} in learner membership"
                )
            seen_refs.add(class_ref)
            items.append(
                CurrentLearnerClassMembership(
                    class_ref=class_ref,
                    school_learner_ref=_require_school_learner_ref(
                        school_learner_ref, class_ref=class_ref
                    ),
                )
            )
        memberships[learner_id] = tuple(items)

    principals: dict[UUID, tuple[str, ...]] = {}
    for raw_principal, raw_refs in principal_scope.items():
        principal_id = _require_uuid(raw_principal, what="school-leader Principal")
        if principal_id in principals:
            raise CoherentSchoolContextFixtureError(
                "duplicate Principal in school ClassRef scope"
            )
        principals[principal_id] = _unique_class_refs(
            raw_refs,
            what="Principal ClassRef scope",
            known_classes=definitions,
        )

    adult_access: dict[UUID, tuple[UUID, ...]] = {}
    for raw_adult, raw_learners in adult_learner_access.items():
        adult_id = _require_uuid(raw_adult, what="adult Principal")
        if adult_id in adult_access:
            raise CoherentSchoolContextFixtureError(
                "duplicate adult Principal in learner access"
            )
        seen_learners: set[UUID] = set()
        learners: list[UUID] = []
        for raw_learner in raw_learners:
            learner_id = _require_uuid(raw_learner, what="authorized learner Principal")
            if learner_id not in memberships:
                raise CoherentSchoolContextFixtureError(
                    "adult access to unknown learner fixture identity"
                )
            if learner_id in seen_learners:
                raise CoherentSchoolContextFixtureError(
                    "duplicate learner Principal in adult access"
                )
            seen_learners.add(learner_id)
            learners.append(learner_id)
        adult_access[adult_id] = tuple(learners)

    return _Facts(
        tenant_id=bound_tenant,
        class_definitions=definitions,
        class_order=tuple(class_order),
        teacher_authority=teachers,
        learner_memberships=memberships,
        principal_scope=principals,
        adult_learner_access=adult_access,
    )


def _default_class_definitions() -> tuple[tuple[str, str], ...]:
    return (
        (CLASS_REF_5A, CLASS_LABEL_5A),
        (CLASS_REF_5B, CLASS_LABEL_5B),
    )


def _default_teacher_authority() -> dict[UUID, tuple[str, ...]]:
    return {
        SYNTHETIC_PRINCIPAL_ID: (CLASS_REF_5A, CLASS_REF_5B),
    }


def _default_learner_memberships() -> dict[
    UUID, tuple[CurrentLearnerClassMembership, ...]
]:
    return {
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


def _default_principal_scope() -> dict[UUID, tuple[str, ...]]:
    return {
        PRINCIPAL_OS_HUMAN_PRINCIPAL_ID: (CLASS_REF_5A, CLASS_REF_5B),
    }


def _default_adult_learner_access() -> dict[UUID, tuple[UUID, ...]]:
    return {
        PARENT_OS_HUMAN_ADULT_A_ID: (STUDENT_A_PRINCIPAL_ID,),
        PARENT_OS_HUMAN_ADULT_B_ID: (STUDENT_B_PRINCIPAL_ID,),
    }


class DevelopmentCoherentSchoolContextProvider:
    """In-memory NON_PRODUCTION current-fact provider for one school story.

    Structurally implements the four existing reader Protocols. Current-fact
    mutation methods are DEVELOPMENT/TEST CONTROL ONLY — not AIEOS business
    commands, Admin commands, ERP/SIS mutation APIs, HTTP APIs, or production
    capabilities.
    """

    NON_PRODUCTION = True

    def __init__(
        self,
        *,
        tenant_id: UUID = SYNTHETIC_TENANT_ID,
        class_definitions: Sequence[tuple[object, object]] | None = None,
        teacher_authority: Mapping[object, Sequence[object]] | None = None,
        learner_memberships: Mapping[object, Sequence[object]] | None = None,
        principal_scope: Mapping[object, Sequence[object]] | None = None,
        adult_learner_access: Mapping[object, Sequence[object]] | None = None,
    ) -> None:
        self._facts = _validated_facts(
            tenant_id=tenant_id,
            class_definitions=(
                _default_class_definitions()
                if class_definitions is None
                else class_definitions
            ),
            teacher_authority=(
                _default_teacher_authority()
                if teacher_authority is None
                else teacher_authority
            ),
            learner_memberships=(
                _default_learner_memberships()
                if learner_memberships is None
                else learner_memberships
            ),
            principal_scope=(
                _default_principal_scope()
                if principal_scope is None
                else principal_scope
            ),
            adult_learner_access=(
                _default_adult_learner_access()
                if adult_learner_access is None
                else adult_learner_access
            ),
        )
        self.call_count = 0
        self.calls: list[tuple[str, UUID, UUID]] = []

    def _replace(self, **changes: object) -> None:
        current = self._facts
        payload = {
            "tenant_id": current.tenant_id,
            "class_definitions": tuple(
                (class_ref, current.class_definitions[class_ref])
                for class_ref in current.class_order
            ),
            "teacher_authority": dict(current.teacher_authority),
            "learner_memberships": dict(current.learner_memberships),
            "principal_scope": dict(current.principal_scope),
            "adult_learner_access": dict(current.adult_learner_access),
        }
        payload.update(changes)
        self._facts = _validated_facts(**payload)

    def _note(self, port: str, tenant_id: object, actor_id: object) -> tuple[UUID, UUID]:
        self.call_count += 1
        bound_tenant = _require_uuid(tenant_id, what="tenant_id")
        bound_actor = _require_uuid(actor_id, what=f"{port} actor")
        self.calls.append((port, bound_tenant, bound_actor))
        return bound_tenant, bound_actor

    def _in_tenant(self, tenant_id: UUID) -> bool:
        return tenant_id == self._facts.tenant_id

    def list_assignable_classes(
        self,
        tenant_id: UUID,
        teacher_principal_id: UUID,
    ) -> tuple[AssignableClassRef, ...]:
        bound_tenant, teacher_id = self._note(
            "teacher", tenant_id, teacher_principal_id
        )
        if not self._in_tenant(bound_tenant):
            return ()
        class_refs = self._facts.teacher_authority.get(teacher_id, ())
        return tuple(
            AssignableClassRef(
                class_ref=class_ref,
                display_label=self._facts.class_definitions[class_ref],
            )
            for class_ref in class_refs
        )

    def list_current_memberships(
        self,
        tenant_id: UUID,
        learner_principal_id: UUID,
    ) -> tuple[CurrentLearnerClassMembership, ...]:
        bound_tenant, learner_id = self._note(
            "learner", tenant_id, learner_principal_id
        )
        if not self._in_tenant(bound_tenant):
            return ()
        return self._facts.learner_memberships.get(learner_id, ())

    def list_current_authorized_classes(
        self,
        tenant_id: UUID,
        principal_id: UUID,
    ) -> tuple[AuthorizedSchoolClassRef, ...]:
        bound_tenant, leader_id = self._note("principal", tenant_id, principal_id)
        if not self._in_tenant(bound_tenant):
            return ()
        class_refs = self._facts.principal_scope.get(leader_id, ())
        return tuple(
            AuthorizedSchoolClassRef(
                class_ref=class_ref,
                display_label=self._facts.class_definitions[class_ref],
            )
            for class_ref in class_refs
        )

    def list_current_authorized_learners(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
    ) -> tuple[AuthorizedLearnerAccess, ...]:
        bound_tenant, adult_id = self._note("adult", tenant_id, adult_principal_id)
        if not self._in_tenant(bound_tenant):
            return ()
        learner_ids = self._facts.adult_learner_access.get(adult_id, ())
        return tuple(
            AuthorizedLearnerAccess(learner_principal_id=learner_id)
            for learner_id in learner_ids
        )

    def set_teacher_class_authority(
        self,
        teacher_principal_id: UUID,
        class_refs: Sequence[str],
    ) -> None:
        """DEVELOPMENT PROVIDER CONTROL ONLY. Not an Admin/ERP/HTTP command."""
        teacher_id = _require_uuid(teacher_principal_id, what="teacher Principal")
        updated = dict(self._facts.teacher_authority)
        updated[teacher_id] = tuple(class_refs)
        self._replace(teacher_authority=updated)

    def set_learner_membership(
        self,
        learner_principal_id: UUID,
        memberships: Sequence[CurrentLearnerClassMembership | tuple[str, str | None]],
    ) -> None:
        """DEVELOPMENT PROVIDER CONTROL ONLY. Not an Admin/ERP/HTTP command."""
        learner_id = _require_uuid(learner_principal_id, what="learner Principal")
        updated = dict(self._facts.learner_memberships)
        updated[learner_id] = tuple(memberships)
        self._replace(learner_memberships=updated)

    def set_principal_class_scope(
        self,
        principal_id: UUID,
        class_refs: Sequence[str],
    ) -> None:
        """DEVELOPMENT PROVIDER CONTROL ONLY. Not an Admin/ERP/HTTP command."""
        leader_id = _require_uuid(principal_id, what="school-leader Principal")
        updated = dict(self._facts.principal_scope)
        updated[leader_id] = tuple(class_refs)
        self._replace(principal_scope=updated)

    def set_adult_learner_access(
        self,
        adult_principal_id: UUID,
        learner_principal_ids: Sequence[UUID],
    ) -> None:
        """DEVELOPMENT PROVIDER CONTROL ONLY. Not an Admin/ERP/HTTP command."""
        adult_id = _require_uuid(adult_principal_id, what="adult Principal")
        updated = dict(self._facts.adult_learner_access)
        updated[adult_id] = tuple(learner_principal_ids)
        self._replace(adult_learner_access=updated)


def development_coherent_school_context_provider(
    *,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
) -> DevelopmentCoherentSchoolContextProvider:
    """NON_PRODUCTION coherent School Context provider for tests/dev only."""
    return DevelopmentCoherentSchoolContextProvider(tenant_id=tenant_id)
