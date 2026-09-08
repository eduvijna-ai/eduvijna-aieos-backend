"""Helpers that assemble Learning attempt outbox rows.

Facts only. Payloads are identity/timestamp references. They must not carry
learner responses, answers, correctness, grade, or mastery.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from aieos.platform.events.cloudevents import build_learning_cloudevent
from aieos.platform.events.constants import (
    AGGREGATE_TYPE_LEARNING_ATTEMPT,
    EVENT_LEARNING_ATTEMPT_STARTED_V1,
    EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
    OUTBOX_PENDING,
    learning_attempt_subject,
)
from aieos.platform.events.identities import EventId
from aieos.platform.events.models import MutationEventContext, OutboxMessage


def _base(
    *,
    event_id: EventId,
    tenant_id: UUID,
    event_type: str,
    attempt_id: UUID,
    aggregate_revision: int,
    envelope: dict[str, object],
    created_at: datetime,
) -> OutboxMessage:
    return OutboxMessage(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=event_type,
        subject=learning_attempt_subject(str(attempt_id)),
        aggregate_type=AGGREGATE_TYPE_LEARNING_ATTEMPT,
        aggregate_id=attempt_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        status=OUTBOX_PENDING,
        attempt_count=0,
        available_at=created_at,
        claimed_by=None,
        claimed_until=None,
        published_at=None,
        broker_stream=None,
        broker_sequence=None,
        last_error_code=None,
        created_at=created_at,
    )


def attempt_started_outbox(
    *,
    tenant_id: UUID,
    attempt_id: UUID,
    teaching_assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    started_at: datetime,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_learning_cloudevent(
        event_id=event_id,
        event_type=EVENT_LEARNING_ATTEMPT_STARTED_V1,
        attempt_id=attempt_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "attempt_id": str(attempt_id),
            "teaching_assignment_id": str(teaching_assignment_id),
            "content_id": str(content_id),
            "content_version_id": str(content_version_id),
            "class_ref": class_ref,
            "started_at": started_at.isoformat(),
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_LEARNING_ATTEMPT_STARTED_V1,
        attempt_id=attempt_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )


def attempt_submitted_outbox(
    *,
    tenant_id: UUID,
    attempt_id: UUID,
    submission_id: UUID,
    teaching_assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
    class_ref: str,
    submitted_at: datetime,
    aggregate_revision: int,
    context: MutationEventContext,
    created_at: datetime,
) -> OutboxMessage:
    event_id = EventId.generate()
    envelope = build_learning_cloudevent(
        event_id=event_id,
        event_type=EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
        attempt_id=attempt_id,
        time=created_at,
        context=context,
        tenant_id=tenant_id,
        aggregate_revision=aggregate_revision,
        data={
            "attempt_id": str(attempt_id),
            "submission_id": str(submission_id),
            "teaching_assignment_id": str(teaching_assignment_id),
            "content_id": str(content_id),
            "content_version_id": str(content_version_id),
            "class_ref": class_ref,
            "submitted_at": submitted_at.isoformat(),
        },
    )
    return _base(
        event_id=event_id,
        tenant_id=tenant_id,
        event_type=EVENT_LEARNING_ATTEMPT_SUBMITTED_V1,
        attempt_id=attempt_id,
        aggregate_revision=aggregate_revision,
        envelope=envelope,
        created_at=created_at,
    )
