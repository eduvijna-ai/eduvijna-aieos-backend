"""Teacher Memory read queries. GET has no create side-effect."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.teaching.application.errors import TeacherMemoryNotFound
from aieos.domains.teaching.application.memory_models import (
    TeacherMemoryReadModel,
    teacher_memory_read_model,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory


class GetTeacherMemoryService:
    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
    ) -> TeacherMemoryReadModel:
        with self._uow_factory(execution_tenant_id) as uow:
            memory = uow.teacher_memories.get_for_teacher(principal_id)
            if memory is None:
                raise TeacherMemoryNotFound("Teacher Memory not found")
            return teacher_memory_read_model(memory)
