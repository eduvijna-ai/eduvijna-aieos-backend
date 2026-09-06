"""TeacherMemory aggregate contract.

Teacher Memory is the durable teacher-owned preference profile SoR.
Ownership is tenant_id + represented HUMAN teacher Principal
(teacher_principal_id). TrustedSecurityContext.principal_id is treated as
that represented teacher in DEV Teacher OS until principal_kind exists.

teacher_principal_id is NEVER taken from a client-supplied owner id.
Transport callers, service workloads, and audit provenance are not ownership.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.teaching.domain.errors import InvalidTeacherMemoryError
from aieos.domains.teaching.domain.identities import (
    AggregateRevision,
    MemoryId,
    require_foreign_uuid,
)
from aieos.domains.teaching.domain.preferences import (
    TEACHER_MEMORY_SCHEMA_VERSION,
    TeacherMemoryPreferences,
    default_preferences,
    parse_preferences,
)


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidTeacherMemoryError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTeacherMemoryError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class TeacherMemory:
    """Durable teacher preference profile (schema_version = 1)."""

    memory_id: MemoryId
    tenant_id: UUID
    teacher_principal_id: UUID
    schema_version: int
    preferences: TeacherMemoryPreferences
    aggregate_revision: AggregateRevision
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        if not isinstance(self.memory_id, MemoryId):
            raise InvalidTeacherMemoryError("memory_id must be a MemoryId")
        if not isinstance(self.aggregate_revision, AggregateRevision):
            raise InvalidTeacherMemoryError(
                "aggregate_revision must be an AggregateRevision"
            )
        require_foreign_uuid(self.tenant_id, label="tenant_id")
        require_foreign_uuid(
            self.teacher_principal_id, label="teacher_principal_id"
        )
        if self.schema_version != TEACHER_MEMORY_SCHEMA_VERSION:
            raise InvalidTeacherMemoryError(
                f"schema_version must be {TEACHER_MEMORY_SCHEMA_VERSION}"
            )
        if isinstance(self.preferences, TeacherMemoryPreferences):
            prefs = self.preferences
        else:
            prefs = parse_preferences(self.preferences)
        set_(self, "preferences", prefs)
        set_(self, "created_at", _require_aware(self.created_at, label="created_at"))
        set_(self, "updated_at", _require_aware(self.updated_at, label="updated_at"))
        if self.updated_at < self.created_at:
            raise InvalidTeacherMemoryError(
                "updated_at must be greater than or equal to created_at"
            )

    @classmethod
    def create(
        cls,
        *,
        tenant_id: UUID,
        teacher_principal_id: UUID,
        preferences: TeacherMemoryPreferences | None,
        created_at: datetime,
    ) -> TeacherMemory:
        prefs = preferences if preferences is not None else default_preferences()
        if not isinstance(prefs, TeacherMemoryPreferences):
            raise InvalidTeacherMemoryError(
                "preferences must be TeacherMemoryPreferences"
            )
        return cls(
            memory_id=MemoryId.generate(),
            tenant_id=tenant_id,
            teacher_principal_id=teacher_principal_id,
            schema_version=TEACHER_MEMORY_SCHEMA_VERSION,
            preferences=prefs,
            aggregate_revision=AggregateRevision(0),
            created_at=created_at,
            updated_at=created_at,
        )

    def replace_preferences(
        self,
        *,
        preferences: TeacherMemoryPreferences,
        updated_at: datetime,
    ) -> TeacherMemory:
        if not isinstance(preferences, TeacherMemoryPreferences):
            raise InvalidTeacherMemoryError(
                "preferences must be TeacherMemoryPreferences"
            )
        aware = _require_aware(updated_at, label="updated_at")
        if aware < self.created_at:
            raise InvalidTeacherMemoryError(
                "updated_at must be greater than or equal to created_at"
            )
        return dataclasses.replace(
            self,
            preferences=preferences,
            aggregate_revision=self.aggregate_revision.next(),
            updated_at=aware,
        )
