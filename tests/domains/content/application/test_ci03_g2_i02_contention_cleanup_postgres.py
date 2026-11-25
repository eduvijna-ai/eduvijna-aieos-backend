"""Focused failure-path cleanup proofs for CI03-G2-I02 contention helpers."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy.engine import Engine

from tests.domains.content.application.helpers_ci03_g2_i02_contention import (
    passive_head_slot_blocker,
    run_two_party_contention,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import _NOW

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


def test_passive_blocker_exception_propagates_and_uow_released(
    ci03_g2_bootstrap_engine: Engine,
    ci03_g2_runtime_engine: Engine,
) -> None:
    (
        tenant_id,
        _owner,
        _reviewer,
        _provider,
        _revoke,
        _assignment_id,
        content_id,
        version_id,
        _revision,
    ) = _bound_hint_scenario(
        ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, clock=lambda: _NOW
    )
    locks_acquired = threading.Event()
    release = threading.Event()
    blocker_pid: list[int | None] = [None]

    blocker_errors: list[BaseException] = []

    def failing_blocker() -> None:
        try:
            passive_head_slot_blocker(
                ci03_g2_runtime_engine,
                tenant_id,
                content_id=content_id,
                version_id=version_id,
                locks_acquired=locks_acquired,
                release=release,
                blocker_pid=blocker_pid,
            )
            raise RuntimeError("blocker fault after locks")
        except BaseException as exc:  # noqa: BLE001
            blocker_errors.append(exc)

    blocker_thread = threading.Thread(target=failing_blocker, name="passive-fault")
    blocker_thread.start()
    assert locks_acquired.wait(timeout=30)
    release.set()
    blocker_thread.join(timeout=30)
    assert not blocker_thread.is_alive()
    assert len(blocker_errors) == 1
    assert isinstance(blocker_errors[0], RuntimeError)
    assert "blocker fault after locks" in str(blocker_errors[0])

