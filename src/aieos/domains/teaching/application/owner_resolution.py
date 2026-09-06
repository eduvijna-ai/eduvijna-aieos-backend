"""Resolve Teacher Memory durable owner from trusted server-side identity.

teacher_principal_id means the represented/effective HUMAN teacher Principal.
It is NOT definitionally the transport/authenticated caller (principal_id).

TrustedSecurityContext today exposes only tenant_id + principal_id.
MutationEventContext.effective_actor_id and SecurityAuditExecutionChannel are
the only additional server-composed signals available without inventing
client-supplied owner headers or a new identity SoR.

Rules:
1. Non-API execution channel without a distinct represented/effective actor
   → fail closed (service/workload/system cannot become Memory owner).
2. Distinct effective_actor_id != calling principal_id
   → fail closed until governed delegation exists (do not invent ownership).
3. Direct Teacher OS execution (API channel, effective equals caller or absent)
   → teacher_principal_id = calling_principal_id as explicit direct-execution
   fallback — not as a durable definition that caller == owner.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.teaching.application.errors import InvalidTeacherMemoryRequest
from aieos.platform.security.audit import SecurityAuditExecutionChannel

_NON_API_CHANNELS = frozenset(
    {
        SecurityAuditExecutionChannel.WORKFLOW_ACTIVITY,
        SecurityAuditExecutionChannel.AI_MATERIALIZATION,
        SecurityAuditExecutionChannel.MIGRATION,
        SecurityAuditExecutionChannel.SYSTEM,
    }
)


def resolve_represented_teacher_principal(
    *,
    calling_principal_id: UUID,
    effective_actor_id: UUID | None = None,
    execution_channel: SecurityAuditExecutionChannel | None = None,
) -> UUID:
    """Return trusted teacher_principal_id for Teacher Memory ownership.

    Never reads client body/query/header owner fields.
    """
    if not isinstance(calling_principal_id, UUID):
        raise InvalidTeacherMemoryRequest(
            "calling_principal_id must be a trusted UUID"
        )

    channel = (
        SecurityAuditExecutionChannel.API
        if execution_channel is None
        else execution_channel
    )
    if not isinstance(channel, SecurityAuditExecutionChannel):
        raise InvalidTeacherMemoryRequest(
            "execution_channel must be a SecurityAuditExecutionChannel"
        )

    if effective_actor_id is not None and not isinstance(effective_actor_id, UUID):
        raise InvalidTeacherMemoryRequest(
            "effective_actor_id must be a trusted UUID when provided"
        )

    distinct_effective = (
        effective_actor_id is not None
        and effective_actor_id != calling_principal_id
    )

    if channel in _NON_API_CHANNELS and not distinct_effective:
        raise InvalidTeacherMemoryRequest(
            "Teacher Memory owner cannot be resolved: non-API execution "
            "without a distinct represented HUMAN teacher Principal"
        )

    if distinct_effective:
        # Delegation / represented-actor separation is not yet a governed
        # runtime ownership path for Memory. Fail closed rather than assign
        # either caller or untrusted effective as durable owner by default.
        raise InvalidTeacherMemoryRequest(
            "Teacher Memory owner cannot be resolved: distinct effective "
            "actor without governed represented-teacher binding"
        )

    # Direct Teacher OS execution: principal_id == effective_actor_id (or
    # effective omitted on read). Explicit compatibility fallback only.
    return calling_principal_id
