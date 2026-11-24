"""PG18 dual-connection lock contention fixtures for CI03-G2-I02 Stage B."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.review_assignment_revoke import (
    RevokeReviewAssignmentService,
)
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWork,
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.platform.security.authorization import (
    AuthorizationKernel,
    CurrentPrincipalClassificationAuthority,
    KernelReviewAssignmentAuthorization,
    KernelReviewAuthorization,
)
from aieos.platform.security.authorization.content_adapters import AIEOS_CONTENT_CAPABILITIES
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthorityService
from aieos.platform.security.audit.persistence.authorization_denial_records import (
    SqlAlchemyAuthorizationDenialRecords,
)
from tests.domains.learning.helpers_aieos_v1_hint_b_r2 import (
    assert_genuine_lock_contention,
    wait_pid_blocked_by,
)
from tests.fakes import IDEMPOTENCY_RETENTION

_DEFAULT_HOLD_TIMEOUT = 30.0


class SlotHoldingContentUow(SqlAlchemyContentUnitOfWork):
    """Holds row lock after first slot FOR UPDATE until release is set."""

    def __init__(
        self,
        engine: Engine,
        execution_tenant_id: uuid.UUID,
        *,
        slot_held: threading.Event,
        release: threading.Event,
        holder_pid: list[int | None],
        while_held: Callable[[], None] | None = None,
        hold_after_slot_lock: int = 1,
    ) -> None:
        super().__init__(engine, execution_tenant_id)
        self._slot_held = slot_held
        self._release = release
        self._holder_pid = holder_pid
        self._while_held = while_held
        self._hold_after_slot_lock = hold_after_slot_lock

    def __enter__(self) -> SlotHoldingContentUow:
        super().__enter__()
        repo = self.review_assignments
        original = repo.get_slot_for_update
        connection = self._connection
        assert connection is not None
        slot_lock_count = 0

        def get_slot_for_update(
            content_id: ContentId, version_id: ContentVersionId
        ) -> Any:
            nonlocal slot_lock_count
            row = original(content_id, version_id)
            slot_lock_count += 1
            if slot_lock_count < self._hold_after_slot_lock:
                return row
            self._holder_pid[0] = connection.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            self._slot_held.set()
            if self._while_held is not None:
                self._while_held()
            if not self._release.wait(timeout=_DEFAULT_HOLD_TIMEOUT):
                raise TimeoutError("slot hold release timed out")
            return row

        repo.get_slot_for_update = get_slot_for_update  # type: ignore[method-assign]
        return self


class SlotHoldingContentUowFactory:
    def __init__(
        self,
        engine: Engine,
        *,
        slot_held: threading.Event,
        release: threading.Event,
        holder_pid: list[int | None],
        while_held: Callable[[], None] | None = None,
        hold_after_slot_lock: int = 1,
    ) -> None:
        self._engine = engine
        self._slot_held = slot_held
        self._release = release
        self._holder_pid = holder_pid
        self._while_held = while_held
        self._hold_after_slot_lock = hold_after_slot_lock

    def __call__(self, execution_tenant_id: uuid.UUID) -> SlotHoldingContentUow:
        return SlotHoldingContentUow(
            self._engine,
            execution_tenant_id,
            slot_held=self._slot_held,
            release=self._release,
            holder_pid=self._holder_pid,
            while_held=self._while_held,
            hold_after_slot_lock=self._hold_after_slot_lock,
        )


class ContendingContentUow(SqlAlchemyContentUnitOfWork):
    """Records waiter backend PID on first head FOR UPDATE."""

    def __init__(
        self,
        engine: Engine,
        execution_tenant_id: uuid.UUID,
        *,
        contending: threading.Event,
        waiter_pid: list[int | None],
    ) -> None:
        super().__init__(engine, execution_tenant_id)
        self._contending = contending
        self._waiter_pid = waiter_pid

    def __enter__(self) -> ContendingContentUow:
        super().__enter__()
        connection = self._connection
        assert connection is not None
        original = self.contents.get_head_for_update

        def get_head_for_update(content_id: ContentId) -> Any:
            self._waiter_pid[0] = connection.execute(
                text("SELECT pg_backend_pid()")
            ).scalar_one()
            self._contending.set()
            return original(content_id)

        self.contents.get_head_for_update = get_head_for_update  # type: ignore[method-assign]
        return self


class ContendingContentUowFactory:
    def __init__(
        self,
        engine: Engine,
        *,
        contending: threading.Event,
        waiter_pid: list[int | None],
    ) -> None:
        self._engine = engine
        self._contending = contending
        self._waiter_pid = waiter_pid

    def __call__(self, execution_tenant_id: uuid.UUID) -> ContendingContentUow:
        return ContendingContentUow(
            self._engine,
            execution_tenant_id,
            contending=self._contending,
            waiter_pid=self._waiter_pid,
        )


def revoke_service_with_factory(
    runtime_engine: Engine,
    tenant_id: uuid.UUID,
    uow_factory: SqlAlchemyContentUnitOfWorkFactory | SlotHoldingContentUowFactory,
    *,
    clock: Callable[[], Any] | None,
    provider: Any,
) -> RevokeReviewAssignmentService:
    kernel = AuthorizationKernel(runtime_engine, known_capabilities=AIEOS_CONTENT_CAPABILITIES)
    classification = CurrentPrincipalClassificationAuthority(runtime_engine)
    class_authority = SchoolContextClassAuthorityService(provider)
    denial = SqlAlchemyAuthorizationDenialRecords(runtime_engine)
    effective_clock = clock or (lambda: None)
    return RevokeReviewAssignmentService(
        uow_factory,
        KernelReviewAssignmentAuthorization(kernel),
        KernelReviewAuthorization(kernel),
        classification,
        class_authority,
        denial,
        idempotency_retention=IDEMPOTENCY_RETENTION,
        clock=effective_clock,
    )


def run_two_party_contention(
    *,
    observer_engine: Engine,
    holder_worker: Callable[[], None],
    waiter_worker: Callable[[], None],
    slot_held: threading.Event,
    contending: threading.Event,
    holder_pid: list[int | None],
    waiter_pid: list[int | None],
    release: threading.Event,
    locks_held: threading.Event | None = None,
) -> tuple[threading.Thread, threading.Thread]:
    """Start holder, wait for slot lock, start waiter, verify PG lock wait, release."""
    held_event = locks_held or slot_held
    holder_errors: list[BaseException] = []
    waiter_errors: list[BaseException] = []

    def holder_wrapper() -> None:
        try:
            holder_worker()
        except BaseException as exc:  # noqa: BLE001
            holder_errors.append(exc)

    def waiter_wrapper() -> None:
        try:
            waiter_worker()
        except BaseException as exc:  # noqa: BLE001
            waiter_errors.append(exc)

    holder_thread = threading.Thread(target=holder_wrapper, name="ci03-slot-holder")
    waiter_thread = threading.Thread(target=waiter_wrapper, name="ci03-head-waiter")

    holder_thread.start()
    assert slot_held.wait(timeout=_DEFAULT_HOLD_TIMEOUT), "holder did not acquire slot lock"
    waiter_thread.start()
    assert contending.wait(timeout=_DEFAULT_HOLD_TIMEOUT), "waiter did not contend on head lock"
    assert_genuine_lock_contention(
        observer_engine,
        waiter_thread=waiter_thread,
        waiter_pid=waiter_pid[0],
        holder_pid=holder_pid[0],
        locks_held=held_event,
        release=release,
    )
    release.set()
    holder_thread.join(timeout=_DEFAULT_HOLD_TIMEOUT)
    waiter_thread.join(timeout=_DEFAULT_HOLD_TIMEOUT)
    assert not holder_thread.is_alive()
    assert not waiter_thread.is_alive()
    if holder_errors:
        raise holder_errors[0]
    return holder_thread, waiter_thread


def join_bounded(thread: threading.Thread, *, seconds: float = _DEFAULT_HOLD_TIMEOUT) -> None:
    thread.join(timeout=seconds)
    assert not thread.is_alive(), f"thread {thread.name} did not finish within {seconds}s"


__all__ = [
    "ContendingContentUowFactory",
    "SlotHoldingContentUowFactory",
    "join_bounded",
    "revoke_service_with_factory",
    "run_two_party_contention",
    "wait_pid_blocked_by",
]