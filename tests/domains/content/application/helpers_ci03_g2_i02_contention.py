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
            if self._while_held is not None:
                self._while_held()
            self._slot_held.set()
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


def passive_head_slot_blocker(
    engine: Engine,
    tenant_id: uuid.UUID,
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    locks_acquired: threading.Event,
    release: threading.Event,
    blocker_pid: list[int | None],
    while_holding: Callable[[], None] | None = None,
) -> None:
    """Independent transaction: content head then slot FOR UPDATE, no business mutation."""
    uow = SqlAlchemyContentUnitOfWork(engine, tenant_id)
    uow.__enter__()
    try:
        uow.contents.get_head_for_update(content_id)
        uow.review_assignments.get_slot_for_update(content_id, version_id)
        connection = uow._connection
        assert connection is not None
        blocker_pid[0] = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        locks_acquired.set()
        if while_holding is not None:
            while_holding()
        if not release.wait(timeout=_DEFAULT_HOLD_TIMEOUT):
            raise TimeoutError("passive blocker release timed out")
    finally:
        uow.__exit__(None, None, None)


def reacquire_head_slot_lock_or_timeout(
    engine: Engine,
    tenant_id: uuid.UUID,
    *,
    content_id: ContentId,
    version_id: ContentVersionId,
    timeout_seconds: float = 3.0,
) -> None:
    """Prove head+slot locks are released after passive blocker teardown."""
    uow = SqlAlchemyContentUnitOfWork(engine, tenant_id)
    uow.__enter__()
    try:
        connection = uow._connection
        assert connection is not None
        connection.execute(text(f"SET lock_timeout = '{int(timeout_seconds * 1000)}ms'"))
        uow.contents.get_head_for_update(content_id)
        uow.review_assignments.get_slot_for_update(content_id, version_id)
    finally:
        uow.__exit__(None, None, None)


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
) -> tuple[list[BaseException], list[BaseException]]:
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
    waiter_thread: threading.Thread | None = None
    waiter_started = False
    primary_error: BaseException | None = None

    try:
        holder_thread.start()
        if not slot_held.wait(timeout=_DEFAULT_HOLD_TIMEOUT):
            raise AssertionError("holder did not acquire slot lock")
        waiter_thread = threading.Thread(target=waiter_wrapper, name="ci03-head-waiter")
        waiter_thread.start()
        waiter_started = True
        if not contending.wait(timeout=_DEFAULT_HOLD_TIMEOUT):
            raise AssertionError("waiter did not contend on head lock")
        assert_genuine_lock_contention(
            observer_engine,
            waiter_thread=waiter_thread,
            waiter_pid=waiter_pid[0],
            holder_pid=holder_pid[0],
            locks_held=held_event,
            release=release,
        )
    except BaseException as exc:  # noqa: BLE001
        primary_error = exc
    finally:
        release.set()
        holder_thread.join(timeout=_DEFAULT_HOLD_TIMEOUT)
        if waiter_thread is not None and waiter_started:
            waiter_thread.join(timeout=_DEFAULT_HOLD_TIMEOUT)
        assert not holder_thread.is_alive()
        if waiter_thread is not None and waiter_started:
            assert not waiter_thread.is_alive()

    if holder_errors:
        raise holder_errors[0]
    if waiter_errors:
        raise waiter_errors[0]
    if primary_error is not None:
        raise primary_error
    return holder_errors, waiter_errors


def join_bounded(thread: threading.Thread, *, seconds: float = _DEFAULT_HOLD_TIMEOUT) -> None:
    thread.join(timeout=seconds)
    assert not thread.is_alive(), f"thread {thread.name} did not finish within {seconds}s"


__all__ = [
    "ContendingContentUowFactory",
    "SlotHoldingContentUowFactory",
    "join_bounded",
    "passive_head_slot_blocker",
    "revoke_service_with_factory",
    "run_two_party_contention",
    "wait_pid_blocked_by",
]