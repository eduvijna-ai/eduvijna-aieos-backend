"""Focused failure-path cleanup proofs for CI03-G2-I02 contention helpers."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy.engine import Engine

from tests.domains.content.application.helpers_ci03_g2_i02_contention import (
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