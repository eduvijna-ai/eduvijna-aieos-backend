"""TOS-DEV10-I03R1 Teacher Memory owner resolution unit tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aieos.domains.teaching.application.errors import InvalidTeacherMemoryRequest
from aieos.domains.teaching.application.owner_resolution import (
    require_human_teacher_owner,
    resolve_represented_teacher_principal,
)
from aieos.platform.security.audit import SecurityAuditExecutionChannel


def test_direct_human_teacher_owner_is_calling_principal() -> None:
    teacher = uuid4()
    assert (
        resolve_represented_teacher_principal(
            calling_principal_id=teacher,
            effective_actor_id=teacher,
            execution_channel=SecurityAuditExecutionChannel.API,
        )
        == teacher
    )
    assert (
        resolve_represented_teacher_principal(
            calling_principal_id=teacher,
            effective_actor_id=None,
            execution_channel=SecurityAuditExecutionChannel.API,
        )
        == teacher
    )


def test_non_api_without_represented_teacher_fails_closed() -> None:
    caller = uuid4()
    for channel in (
        SecurityAuditExecutionChannel.WORKFLOW_ACTIVITY,
        SecurityAuditExecutionChannel.SYSTEM,
        SecurityAuditExecutionChannel.AI_MATERIALIZATION,
        SecurityAuditExecutionChannel.MIGRATION,
    ):
        with pytest.raises(InvalidTeacherMemoryRequest, match="non-API"):
            resolve_represented_teacher_principal(
                calling_principal_id=caller,
                effective_actor_id=caller,
                execution_channel=channel,
            )
        with pytest.raises(InvalidTeacherMemoryRequest, match="non-API"):
            resolve_represented_teacher_principal(
                calling_principal_id=caller,
                effective_actor_id=None,
                execution_channel=channel,
            )


def test_distinct_effective_actor_fails_closed_without_governed_binding() -> None:
    caller = uuid4()
    represented = uuid4()
    with pytest.raises(InvalidTeacherMemoryRequest, match="distinct effective"):
        resolve_represented_teacher_principal(
            calling_principal_id=caller,
            effective_actor_id=represented,
            execution_channel=SecurityAuditExecutionChannel.API,
        )


def test_client_cannot_supply_owner_through_resolver_signature() -> None:
    """Resolver accepts only trusted UUID arguments — no header/body parsing."""
    teacher = uuid4()
    owner = resolve_represented_teacher_principal(calling_principal_id=teacher)
    assert owner == teacher
    assert owner is not str(teacher)  # type: ignore[comparison-overlap]


def test_require_human_teacher_owner_calls_classification_after_resolve() -> None:
    teacher = uuid4()
    seen: list[object] = []

    class _Gate:
        def require_current_human_principal(self, principal_id):
            seen.append(principal_id)
            return "HUMAN"

    assert (
        require_human_teacher_owner(
            calling_principal_id=teacher,
            classification=_Gate(),
            effective_actor_id=teacher,
            execution_channel=SecurityAuditExecutionChannel.API,
        )
        == teacher
    )
    assert seen == [teacher]


def test_require_human_teacher_owner_fails_closed_for_non_api() -> None:
    class _Gate:
        def require_current_human_principal(self, principal_id):
            raise AssertionError("classification must not run when resolve fails")

    with pytest.raises(InvalidTeacherMemoryRequest, match="non-API"):
        require_human_teacher_owner(
            calling_principal_id=uuid4(),
            classification=_Gate(),
            execution_channel=SecurityAuditExecutionChannel.SYSTEM,
        )


def test_require_human_teacher_owner_fails_closed_for_distinct_effective() -> None:
    class _Gate:
        def require_current_human_principal(self, principal_id):
            raise AssertionError("classification must not run when resolve fails")

    with pytest.raises(InvalidTeacherMemoryRequest, match="distinct effective"):
        require_human_teacher_owner(
            calling_principal_id=uuid4(),
            classification=_Gate(),
            effective_actor_id=uuid4(),
            execution_channel=SecurityAuditExecutionChannel.API,
        )
