"""Create Teacher Memory application command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    insert_required_teaching_execution_audit,
    memory_primary_ref,
)
from aieos.domains.teaching.application.errors import (
    IdempotencyKeyReused,
    InvalidTeacherMemoryRequest,
    PersistenceInvariantViolation,
)
from aieos.domains.teaching.application.memory_models import (
    CreateTeacherMemoryCommand,
    TeacherMemoryReadModel,
    teacher_memory_read_model,
)
from aieos.domains.teaching.application.owner_resolution import (
    HumanPrincipalClassificationGate,
    require_human_teacher_owner,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.errors import InvalidTeacherMemoryError
from aieos.domains.teaching.domain.identities import MemoryId
from aieos.domains.teaching.domain.preferences import preferences_to_storage
from aieos.domains.teaching.domain.teacher_memory import TeacherMemory
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHER_OS_MEMORY_CREATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def create_fingerprint(command: CreateTeacherMemoryCommand) -> str:
    return fingerprint_material(
        {"preferences": preferences_to_storage(command.preferences)}
    )


class CreateTeacherMemoryService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        *,
        idempotency_retention: timedelta,
        principal_classification: HumanPrincipalClassificationGate,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._idempotency_retention = idempotency_retention
        self._principal_classification = principal_classification

    def create(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        command: CreateTeacherMemoryCommand,
        *,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeacherMemoryReadModel:
        """Create initial Memory for the represented teacher Principal.

        Durable owner is require_human_teacher_owner(...): resolve then require
        current ACTIVE HUMAN from SoR. Never a client-supplied owner id.
        Calling principal remains audit/idempotency provenance and is not
        definitionally the Memory owner. Duplicate create for the same teacher
        is deterministic: return the existing profile without a second mutation.
        """
        teacher_principal_id = require_human_teacher_owner(
            calling_principal_id=principal_id,
            classification=self._principal_classification,
            effective_actor_id=event_context.effective_actor_id,
            execution_channel=audit_provenance.execution_channel,
        )
        created_at = _now(now)
        fingerprint = create_fingerprint(command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHER_OS_MEMORY_CREATE_V1,
            key_sha256=hash_idempotency_key(idempotency_key),
        )
        with self._uow_factory(execution_tenant_id) as uow:
            uow.idempotency.acquire_scope(scope)
            existing = uow.idempotency.get(scope)
            if existing is not None:
                if existing.request_fingerprint_sha256 != fingerprint:
                    raise IdempotencyKeyReused("idempotency key already bound")
                replayed = uow.teacher_memories.get_by_id(
                    MemoryId(existing.result_content_id)
                )
                if replayed is None:
                    raise PersistenceInvariantViolation(
                        "idempotent memory create outcome is not visible"
                    )
                if replayed.teacher_principal_id != teacher_principal_id:
                    raise PersistenceInvariantViolation(
                        "idempotent memory create outcome ownership mismatch"
                    )
                return teacher_memory_read_model(replayed)

            already = uow.teacher_memories.get_for_teacher(teacher_principal_id)
            if already is not None:
                # Deterministic duplicate: same teacher already has Memory.
                # Do not rewrite preferences; do not emit a second audit.
                return teacher_memory_read_model(already)

            try:
                memory = TeacherMemory.create(
                    tenant_id=execution_tenant_id,
                    teacher_principal_id=teacher_principal_id,
                    preferences=command.preferences,
                    created_at=created_at,
                )
            except InvalidTeacherMemoryError as exc:
                raise InvalidTeacherMemoryRequest(str(exc)) from exc

            uow.teacher_memories.insert(memory)
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_MEMORY_CREATE,
                primary_resource_ref=memory_primary_ref(
                    memory.memory_id.value, int(memory.aggregate_revision)
                ),
                resource_revision_before=None,
                resource_revision_after=int(memory.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=created_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=memory.memory_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(memory.aggregate_revision),
                    created_at=created_at,
                    expires_at=created_at + self._idempotency_retention,
                )
            )
            uow.commit()
            return teacher_memory_read_model(memory)
