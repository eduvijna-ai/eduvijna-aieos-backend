"""SQLAlchemy Teacher Memory repository. Never commits or rollbacks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from aieos.domains.teaching.application.errors import PersistenceInvariantViolation
from aieos.domains.teaching.domain.identities import AggregateRevision, MemoryId
from aieos.domains.teaching.domain.preferences import (
    preferences_to_storage,
    parse_preferences,
)
from aieos.domains.teaching.domain.teacher_memory import TeacherMemory
from aieos.domains.teaching.infrastructure.persistence.errors import (
    reraise_as_application_error,
)
from aieos.domains.teaching.infrastructure.persistence.models import (
    teacher_memories_table,
)


def teacher_memory_from_row(row) -> TeacherMemory:
    try:
        return TeacherMemory(
            memory_id=MemoryId(row["memory_id"]),
            tenant_id=row["tenant_id"],
            teacher_principal_id=row["teacher_principal_id"],
            schema_version=int(row["schema_version"]),
            preferences=parse_preferences(row["preferences"]),
            aggregate_revision=AggregateRevision(int(row["aggregate_revision"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
    except Exception as exc:
        raise PersistenceInvariantViolation(
            "stored TeacherMemory row violates the aggregate contract"
        ) from exc


class SqlAlchemyTeacherMemoryRepository:
    def __init__(self, connection: Connection, execution_tenant_id: UUID) -> None:
        self._connection = connection
        self._execution_tenant_id = execution_tenant_id

    def insert(self, memory: TeacherMemory) -> None:
        try:
            self._connection.execute(
                teacher_memories_table.insert().values(
                    memory_id=memory.memory_id.value,
                    tenant_id=memory.tenant_id,
                    teacher_principal_id=memory.teacher_principal_id,
                    schema_version=memory.schema_version,
                    preferences=preferences_to_storage(memory.preferences),
                    aggregate_revision=int(memory.aggregate_revision),
                    created_at=memory.created_at,
                    updated_at=memory.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)

    def get_for_teacher(self, teacher_principal_id: UUID) -> TeacherMemory | None:
        try:
            row = (
                self._connection.execute(
                    select(teacher_memories_table).where(
                        teacher_memories_table.c.tenant_id
                        == self._execution_tenant_id,
                        teacher_memories_table.c.teacher_principal_id
                        == teacher_principal_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return teacher_memory_from_row(row)

    def get_for_teacher_for_update(
        self, teacher_principal_id: UUID
    ) -> TeacherMemory | None:
        try:
            row = (
                self._connection.execute(
                    select(teacher_memories_table)
                    .where(
                        teacher_memories_table.c.tenant_id
                        == self._execution_tenant_id,
                        teacher_memories_table.c.teacher_principal_id
                        == teacher_principal_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return teacher_memory_from_row(row)

    def get_by_id(self, memory_id: MemoryId) -> TeacherMemory | None:
        try:
            row = (
                self._connection.execute(
                    select(teacher_memories_table).where(
                        teacher_memories_table.c.tenant_id
                        == self._execution_tenant_id,
                        teacher_memories_table.c.memory_id == memory_id.value,
                    )
                )
                .mappings()
                .one_or_none()
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        if row is None:
            return None
        return teacher_memory_from_row(row)

    def update(
        self,
        memory: TeacherMemory,
        *,
        expected_revision: AggregateRevision,
    ) -> bool:
        try:
            result = self._connection.execute(
                update(teacher_memories_table)
                .where(
                    teacher_memories_table.c.memory_id == memory.memory_id.value,
                    teacher_memories_table.c.tenant_id == self._execution_tenant_id,
                    teacher_memories_table.c.teacher_principal_id
                    == memory.teacher_principal_id,
                    teacher_memories_table.c.aggregate_revision
                    == int(expected_revision),
                )
                .values(
                    preferences=preferences_to_storage(memory.preferences),
                    schema_version=memory.schema_version,
                    aggregate_revision=int(memory.aggregate_revision),
                    updated_at=memory.updated_at,
                )
            )
        except Exception as exc:
            reraise_as_application_error(exc)
        return result.rowcount == 1
