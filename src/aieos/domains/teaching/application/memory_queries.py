"""Teacher Memory read queries. GET has no create side-effect."""

from __future__ import annotations

from uuid import UUID

from aieos.domains.teaching.application.errors import TeacherMemoryNotFound
from aieos.domains.teaching.application.memory_models import (
    TeacherMemoryReadModel,
    teacher_memory_read_model,
)
from aieos.domains.teaching.application.owner_resolution import (
    resolve_represented_teacher_principal,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.platform.security.audit import SecurityAuditExecutionChannel


class GetTeacherMemoryService:
    def __init__(self, uow_factory: TeachingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    def get(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
    ) -> TeacherMemoryReadModel:
        teacher_principal_id = resolve_represented_teacher_principal(
            calling_principal_id=principal_id,
            effective_actor_id=None,
            execution_channel=SecurityAuditExecutionChannel.API,
        )
        with self._uow_factory(execution_tenant_id) as uow:
            memory = uow.teacher_memories.get_for_teacher(teacher_principal_id)
            if memory is None:
                raise TeacherMemoryNotFound("Teacher Memory not found")
            return teacher_memory_read_model(memory)
