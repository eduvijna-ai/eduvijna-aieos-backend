"""Teacher Memory application read/command models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aieos.domains.teaching.domain.preferences import TeacherMemoryPreferences
from aieos.domains.teaching.domain.teacher_memory import TeacherMemory


@dataclass(frozen=True, slots=True)
class CreateTeacherMemoryCommand:
    preferences: TeacherMemoryPreferences


@dataclass(frozen=True, slots=True)
class UpdateTeacherMemoryCommand:
    preferences: TeacherMemoryPreferences


@dataclass(frozen=True, slots=True)
class TeacherMemoryReadModel:
    memory_id: UUID
    teacher_principal_id: UUID
    schema_version: int
    preferences: TeacherMemoryPreferences
    aggregate_revision: int
    created_at: datetime
    updated_at: datetime


def teacher_memory_read_model(memory: TeacherMemory) -> TeacherMemoryReadModel:
    return TeacherMemoryReadModel(
        memory_id=memory.memory_id.value,
        teacher_principal_id=memory.teacher_principal_id,
        schema_version=memory.schema_version,
        preferences=memory.preferences,
        aggregate_revision=int(memory.aggregate_revision),
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )
