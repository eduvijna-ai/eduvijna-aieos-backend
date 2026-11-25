"""Focused failure-path cleanup proofs for CI03-G2-I02 contention helpers."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from tests.domains.content.application.helpers_ci03_g2_i02_contention import (
    passive_head_slot_blocker,
    run_two_party_contention,
)

pytestmark = pytest.mark.ci03_g2_i02


def test_run_two_party_contention_does_not_start_waiter_when_holder_never_locks(
    ci03_g2_bootstrap_engine: Engine,
) -> None:
    slot_held = threading.Event()
    contending = threading.Event()
    release = threading.Event()
    holder_pid: list[int | None] = [None]
    waiter_pid: list[int | None] = [None]

    def holder() -> None:
        raise RuntimeError("holder failed before slot lock")

    def waiter() -> None:
        raise AssertionError("waiter must not run")

    with pytest.raises(RuntimeError, match="holder failed before slot lock"):
        run_two_party_contention(
            observer_engine=ci03_g2_bootstrap_engine,
            holder_worker=holder,
            waiter_worker=waiter,
            slot_held=slot_held,
            contending=contending,
            holder_pid=holder_pid,
            waiter_pid=waiter_pid,
            release=release,
        )

    assert not contending.is_set()


def test_passive_blocker_failure_leaves_locks_unset_and_thread_terminates(
    ci03_g2_runtime_engine: Engine,
) -> None:
    tenant_id = uuid.uuid7()
    release = threading.Event()
    locks_acquired = threading.Event()
    blocker_pid: list[int | None] = [None]
    thread_errors: list[BaseException] = []

    def run_blocker() -> None:
        try:
            passive_head_slot_blocker(
                ci03_g2_runtime_engine,
                tenant_id,
                content_id=ContentId.generate(),
                version_id=ContentVersionId.generate(),
                locks_acquired=locks_acquired,
                release=release,
                blocker_pid=blocker_pid,
            )
        except BaseException as exc:  # noqa: BLE001
            thread_errors.append(exc)

    thread = threading.Thread(target=run_blocker, name="ci03-passive-blocker-fail")
    thread.start()
    thread.join(timeout=35.0)
    assert not thread.is_alive()
    assert thread_errors
    assert not locks_acquired.is_set()
    release.set()