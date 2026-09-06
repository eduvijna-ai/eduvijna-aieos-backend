"""Update Teacher Memory application command."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from aieos.domains.teaching.application.audit import (
    MutationAuditProvenance,
    insert_required_teaching_execution_audit,
    memory_primary_ref,
)
from aieos.domains.teaching.application.errors import (
    AggregateRevisionConflict,
    IdempotencyKeyReused,
    InvalidTeacherMemoryRequest,
    PersistenceInvariantViolation,
    TeacherMemoryNotFound,
)
from aieos.domains.teaching.application.memory_models import (
    TeacherMemoryReadModel,
    UpdateTeacherMemoryCommand,
    teacher_memory_read_model,
)
from aieos.domains.teaching.application.owner_resolution import (
    resolve_represented_teacher_principal,
)
from aieos.domains.teaching.application.ports import TeachingUnitOfWorkFactory
from aieos.domains.teaching.domain.errors import InvalidTeacherMemoryError
from aieos.domains.teaching.domain.identities import AggregateRevision, MemoryId
from aieos.domains.teaching.domain.preferences import preferences_to_storage
from aieos.platform.events.models import MutationEventContext
from aieos.platform.idempotency.hashing import fingerprint_material, hash_idempotency_key
from aieos.platform.idempotency.models import (
    TEACHER_OS_MEMORY_UPDATE_V1,
    IdempotencyOutcome,
    IdempotencyScope,
)
from aieos.platform.security.audit import SecurityAuditAction


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def update_fingerprint(
    expected_revision: AggregateRevision,
    command: UpdateTeacherMemoryCommand,
) -> str:
    return fingerprint_material(
        {
            "expected_revision": int(expected_revision),
            "preferences": preferences_to_storage(command.preferences),
        }
    )


class UpdateTeacherMemoryService:
    def __init__(
        self,
        uow_factory: TeachingUnitOfWorkFactory,
        *,
        idempotency_retention: timedelta,
    ) -> None:
        if idempotency_retention.total_seconds() <= 0:
            raise ValueError("idempotency_retention must be a positive duration")
        self._uow_factory = uow_factory
        self._idempotency_retention = idempotency_retention

    def update(
        self,
        execution_tenant_id: UUID,
        principal_id: UUID,
        *,
        expected_aggregate_revision: AggregateRevision,
        command: UpdateTeacherMemoryCommand,
        idempotency_key: str,
        event_context: MutationEventContext,
        audit_provenance: MutationAuditProvenance,
        now: datetime | None = None,
    ) -> TeacherMemoryReadModel:
        teacher_principal_id = resolve_represented_teacher_principal(
            calling_principal_id=principal_id,
            effective_actor_id=event_context.effective_actor_id,
            execution_channel=audit_provenance.execution_channel,
        )
        updated_at = _now(now)
        fingerprint = update_fingerprint(expected_aggregate_revision, command)
        scope = IdempotencyScope(
            tenant_id=execution_tenant_id,
            principal_id=principal_id,
            operation=TEACHER_OS_MEMORY_UPDATE_V1,
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
                        "idempotent memory update outcome is not visible"
                    )
                if replayed.teacher_principal_id != teacher_principal_id:
                    raise PersistenceInvariantViolation(
                        "idempotent memory update outcome ownership mismatch"
                    )
                return teacher_memory_read_model(replayed)

            locked = uow.teacher_memories.get_for_teacher_for_update(
                teacher_principal_id
            )
            if locked is None:
                raise TeacherMemoryNotFound("Teacher Memory not found")
            if int(locked.aggregate_revision) != int(expected_aggregate_revision):
                raise AggregateRevisionConflict(
                    "Teacher Memory aggregate revision conflict"
                )
            try:
                updated = locked.replace_preferences(
                    preferences=command.preferences,
                    updated_at=updated_at,
                )
            except InvalidTeacherMemoryError as exc:
                raise InvalidTeacherMemoryRequest(str(exc)) from exc

            applied = uow.teacher_memories.update(
                updated, expected_revision=expected_aggregate_revision
            )
            if not applied:
                raise AggregateRevisionConflict(
                    "Teacher Memory aggregate revision conflict"
                )
            insert_required_teaching_execution_audit(
                uow,
                tenant_id=execution_tenant_id,
                action=SecurityAuditAction.TEACHING_MEMORY_UPDATE,
                primary_resource_ref=memory_primary_ref(
                    updated.memory_id.value, int(updated.aggregate_revision)
                ),
                resource_revision_before=int(expected_aggregate_revision),
                resource_revision_after=int(updated.aggregate_revision),
                related_resource_refs=(),
                mutation_event_context=event_context,
                audit_provenance=audit_provenance,
                occurred_at=updated_at,
            )
            uow.idempotency.insert(
                IdempotencyOutcome(
                    tenant_id=scope.tenant_id,
                    principal_id=scope.principal_id,
                    operation=scope.operation,
                    key_sha256=scope.key_sha256,
                    request_fingerprint_sha256=fingerprint,
                    result_content_id=updated.memory_id.value,
                    result_version_id=None,
                    result_review_decision_id=None,
                    result_publication_id=None,
                    result_aggregate_revision=int(updated.aggregate_revision),
                    created_at=updated_at,
                    expires_at=updated_at + self._idempotency_retention,
                )
            )
            uow.commit()
            return teacher_memory_read_model(updated)
