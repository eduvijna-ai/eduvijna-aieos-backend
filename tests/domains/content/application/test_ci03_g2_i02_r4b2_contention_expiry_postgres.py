"""CI03-G2-I02R4-B2 — dual-connection concurrency + integrated expiry crossing (PG18)."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.application.errors import ReviewAssignmentConflict
from aieos.platform.events.models import MutationEventContext
from aieos.domains.content.domain.identities import AggregateRevision, ContentId, ContentVersionId
from aieos.domains.teaching.application.school_context import SchoolContextClassAuthorityService
from tests.conftest import provision_runtime_grants
from tests.domains.content.application.helpers_ci03_g2_i01 import (
    integrated_attest_service,
    integrated_review_service,
)
from tests.domains.content.application.helpers_ci03_g2_i02_contention import (
    ContendingContentUowFactory,
    SlotHoldingContentUowFactory,
    join_bounded,
    passive_head_slot_blocker,
    revoke_service_with_factory,
    run_two_party_contention,
)
from tests.domains.content.application.helpers_ci03_g2_i02_snapshot import (
    DurableEffectSnapshot,
    assert_latest_denial_matches,
    assert_protected_effect_denied_after_revoke,
    assert_review_assignment_expiry_exception,
    assert_snapshot_unchanged_except_denial,
    assignment_valid_until,
    capture_durable_snapshot,
)
from tests.domains.learning.helpers_aieos_v1_hint_b_r2 import assert_genuine_lock_contention
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _event,
)
from tests.domains.content.application.test_ci03_g2_i01_fa01_foundation_proof import (
    _ExpiryAdvancingClassAuthority,
)
from tests.domains.content.application.test_ci03_g2_i02_r4b1_acceptance_postgres import (
    _bound_hint_scenario,
)
from tests.domains.education.test_aieos_v1_hint_t2 import (
    _hinted_ids_from_version,
    _positive_outcomes,
)

pytestmark = pytest.mark.ci03_g2_i02

def _correlated_event(principal_id: uuid.UUID) -> MutationEventContext:
    correlation_id = uuid.uuid7()
    return MutationEventContext(
        correlation_id=correlation_id,
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


@dataclass
class _Scenario:
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    reviewer_id: uuid.UUID
    provider: Any
    assignment_id: Any
    content_id: ContentId
    version_id: ContentVersionId
    revision: AggregateRevision


def _load_scenario(
    bootstrap: Engine,
    runtime: Engine,
    *,
    clock,
    attest_for_approve: bool,
) -> _Scenario:
    (
        tenant_id,
        owner_id,
        reviewer_id,
        provider,
        _revoke,
        assignment_id,
        content_id,
        version_id,
        revision,
    ) = _bound_hint_scenario(bootstrap, runtime, clock=clock)
    if attest_for_approve:
        attest = integrated_attest_service(
            runtime,
            tenant_id,
            clock=clock,
            school_context_provider=provider,
        )
        attest.record(
            tenant_id,
            reviewer_id,
            content_id=content_id,
            version_id=version_id,
            expected_aggregate_revision=revision,
            per_question_outcomes=_positive_outcomes(
                _hinted_ids_from_version(runtime, tenant_id, version_id)
            ),
            idempotency_key=f"b2-pre-approve-{uuid.uuid7()}",
            event_context=_event(reviewer_id),
            audit_provenance=api_mutation_audit_provenance(reviewer_id),
            now=clock(),
        )
    return _Scenario(
        tenant_id=tenant_id,
        owner_id=owner_id,
        reviewer_id=reviewer_id,
        provider=provider,
        assignment_id=assignment_id,
        content_id=content_id,
        version_id=version_id,
        revision=revision,
    )


def _invoke_attest(
    runtime: Engine,
    scenario: _Scenario,
    *,
    clock,
    uow_factory: Any,
    idem_key: str,
    now: datetime | None = None,
    event_context: MutationEventContext | None = None,
) -> None:
    service = integrated_attest_service(
        runtime,
        scenario.tenant_id,
        clock=clock,
        school_context_provider=scenario.provider,
        uow_factory=uow_factory,
    )
    service.record(
        scenario.tenant_id,
        scenario.reviewer_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        expected_aggregate_revision=scenario.revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(runtime, scenario.tenant_id, scenario.version_id)
        ),
        idempotency_key=idem_key,
        event_context=event_context or _event(scenario.reviewer_id),
        audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
        now=now or clock(),
    )


def _invoke_decision(
    runtime: Engine,
    scenario: _Scenario,
    *,
    effect: str,
    clock,
    uow_factory: Any,
    idem_key: str,
    now: datetime | None = None,
    event_context: MutationEventContext | None = None,
) -> None:
    review = integrated_review_service(
        runtime,
        scenario.tenant_id,
        clock=clock,
        school_context_provider=scenario.provider,
        uow_factory=uow_factory,
    )
    kwargs = dict(
        execution_tenant_id=scenario.tenant_id,
        principal_id=scenario.reviewer_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        expected_aggregate_revision=scenario.revision,
        idempotency_key=idem_key,
        event_context=event_context or _event(scenario.reviewer_id),
        audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
        now=now or clock(),
    )
    if effect == "approve":
        review.approve(**kwargs, reason_code=None, comment=None)
    elif effect == "reject":
        review.reject(**kwargs, reason_code="governed", comment="deny")
    elif effect == "request_changes":
        review.request_changes(**kwargs, reason_code="governed", comment="changes")
    else:
        raise ValueError(effect)


def _capture_snapshot(
    bootstrap: Engine,
    runtime: Engine,
    scenario: _Scenario,
) -> DurableEffectSnapshot:
    return capture_durable_snapshot(
        bootstrap_engine=bootstrap,
        runtime_engine=runtime,
        tenant_id=scenario.tenant_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        reviewer_principal_id=scenario.reviewer_id,
    )


def _pre_attest_for_approve(
    runtime: Engine,
    scenario: _Scenario,
    clock,
) -> None:
    attest = integrated_attest_service(
        runtime,
        scenario.tenant_id,
        clock=clock,
        school_context_provider=scenario.provider,
    )
    attest.record(
        scenario.tenant_id,
        scenario.reviewer_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        expected_aggregate_revision=scenario.revision,
        per_question_outcomes=_positive_outcomes(
            _hinted_ids_from_version(runtime, scenario.tenant_id, scenario.version_id)
        ),
        idempotency_key=f"b2-pre-{uuid.uuid7()}",
        event_context=_event(scenario.reviewer_id),
        audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
        now=clock(),
    )


def _invoke_revoke(
    runtime: Engine,
    scenario: _Scenario,
    *,
    clock,
    uow_factory: Any,
    idem_key: str,
) -> None:
    revoke = revoke_service_with_factory(
        runtime,
        scenario.tenant_id,
        uow_factory,
        clock=clock,
        provider=scenario.provider,
    )
    revoke.revoke(
        scenario.tenant_id,
        scenario.owner_id,
        content_id=scenario.content_id,
        version_id=scenario.version_id,
        assignment_id=scenario.assignment_id,
        idempotency_key=idem_key,
        event_context=_event(scenario.owner_id),
        audit_provenance=api_mutation_audit_provenance(scenario.owner_id),
    )


class TestCi03G2I02R4B2Concurrency:
    @pytest.mark.parametrize(
        ("effect",),
        (("attest",), ("approve",), ("reject",), ("request_changes",)),
    )
    def test_revoke_first_denies_protected_effect_under_head_lock(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
        effect: str,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        clock = lambda: _NOW  # noqa: E731
        scenario = _load_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=clock,
            attest_for_approve=effect == "approve",
        )
        snap_before = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )

        slot_held = threading.Event()
        contending = threading.Event()
        release = threading.Event()
        holder_pid: list[int | None] = [None]
        waiter_pid: list[int | None] = [None]
        holder_result: dict[str, Any] = {}
        waiter_result: dict[str, Any] = {}

        hold_factory = SlotHoldingContentUowFactory(
            ci03_g2_runtime_engine,
            slot_held=slot_held,
            release=release,
            holder_pid=holder_pid,
            hold_after_slot_lock=2,
        )
        contend_factory = ContendingContentUowFactory(
            ci03_g2_runtime_engine,
            contending=contending,
            waiter_pid=waiter_pid,
        )

        def holder() -> None:
            try:
                _invoke_revoke(
                    ci03_g2_runtime_engine,
                    scenario,
                    clock=clock,
                    uow_factory=hold_factory,
                    idem_key=f"b2-revoke-first-{effect}-{uuid.uuid7()}",
                )
                holder_result["ok"] = True
            except BaseException as exc:  # noqa: BLE001
                holder_result["error"] = exc

        waiter_event = _correlated_event(scenario.reviewer_id)

        def waiter() -> None:
            try:
                if effect == "attest":
                    _invoke_attest(
                        ci03_g2_runtime_engine,
                        scenario,
                        clock=clock,
                        uow_factory=contend_factory,
                        idem_key=f"b2-wait-attest-{uuid.uuid7()}",
                        event_context=waiter_event,
                    )
                else:
                    _invoke_decision(
                        ci03_g2_runtime_engine,
                        scenario,
                        effect=effect,
                        clock=clock,
                        uow_factory=contend_factory,
                        idem_key=f"b2-wait-{effect}-{uuid.uuid7()}",
                        event_context=waiter_event,
                    )
                waiter_result["ok"] = True
            except BaseException as exc:  # noqa: BLE001
                waiter_result["error"] = exc

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

        assert holder_result.get("ok") is True, holder_result.get("error")
        assert_review_assignment_expiry_exception(waiter_result["error"])

        snap_after = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )
        assert_protected_effect_denied_after_revoke(snap_before, snap_after)
        assert_latest_denial_matches(
            ci03_g2_runtime_engine,
            tenant_id=scenario.tenant_id,
            principal_id=scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            correlation_id=waiter_event.correlation_id,
        )

    @pytest.mark.parametrize(
        ("effect",),
        (("attest",), ("approve",), ("reject",), ("request_changes",)),
    )
    def test_effect_first_commits_then_revoke_under_head_lock(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
        effect: str,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        clock = lambda: _NOW  # noqa: E731
        scenario = _load_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=clock,
            attest_for_approve=False,
        )
        if effect == "approve":
            _pre_attest_for_approve(ci03_g2_runtime_engine, scenario, clock)
        snap_before = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )

        slot_held = threading.Event()
        contending = threading.Event()
        release = threading.Event()
        holder_pid: list[int | None] = [None]
        waiter_pid: list[int | None] = [None]
        holder_result: dict[str, Any] = {}
        waiter_result: dict[str, Any] = {}

        hold_factory = SlotHoldingContentUowFactory(
            ci03_g2_runtime_engine,
            slot_held=slot_held,
            release=release,
            holder_pid=holder_pid,
        )
        contend_factory = ContendingContentUowFactory(
            ci03_g2_runtime_engine,
            contending=contending,
            waiter_pid=waiter_pid,
        )

        def holder() -> None:
            try:
                if effect == "attest":
                    _invoke_attest(
                        ci03_g2_runtime_engine,
                        scenario,
                        clock=clock,
                        uow_factory=hold_factory,
                        idem_key=f"b2-eff-first-attest-{uuid.uuid7()}",
                    )
                elif effect == "approve":
                    _invoke_decision(
                        ci03_g2_runtime_engine,
                        scenario,
                        effect=effect,
                        clock=clock,
                        uow_factory=hold_factory,
                        idem_key=f"b2-eff-first-{effect}-{uuid.uuid7()}",
                    )
                else:
                    _invoke_decision(
                        ci03_g2_runtime_engine,
                        scenario,
                        effect=effect,
                        clock=clock,
                        uow_factory=hold_factory,
                        idem_key=f"b2-eff-first-{effect}-{uuid.uuid7()}",
                    )
                holder_result["ok"] = True
            except BaseException as exc:  # noqa: BLE001
                holder_result["error"] = exc

        def waiter() -> None:
            try:
                _invoke_revoke(
                    ci03_g2_runtime_engine,
                    scenario,
                    clock=clock,
                    uow_factory=contend_factory,
                    idem_key=f"b2-rev-after-{effect}-{uuid.uuid7()}",
                )
                waiter_result["ok"] = True
            except BaseException as exc:  # noqa: BLE001
                waiter_result["error"] = exc

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

        assert holder_result.get("ok") is True, holder_result.get("error")
        assert waiter_result.get("ok") is True, waiter_result.get("error")

        snap_after = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )
        assert snap_after.revocation_rows == snap_before.revocation_rows + 1
        assert snap_after.review_revoke_audits == snap_before.review_revoke_audits + 1
        assert snap_after.mutation_denials == snap_before.mutation_denials
        if effect == "attest":
            assert snap_after.attestations == snap_before.attestations + 1
            assert snap_after.worksheet_hint_attest_audits == (
                snap_before.worksheet_hint_attest_audits + 1
            )
            assert snap_after.outbox_messages >= snap_before.outbox_messages
            assert snap_after.reviewer_idempotency_success == (
                snap_before.reviewer_idempotency_success + 1
            )
        elif effect == "approve":
            assert snap_after.decisions == snap_before.decisions + 1
            assert snap_after.review_decision_audits == snap_before.review_decision_audits + 1
            assert snap_after.reviewer_idempotency_success == (
                snap_before.reviewer_idempotency_success + 1
            )
        elif effect in ("reject", "request_changes"):
            assert snap_after.decisions == snap_before.decisions + 1
            assert snap_after.review_decision_audits == snap_before.review_decision_audits + 1
            assert snap_after.reviewer_idempotency_success == (
                snap_before.reviewer_idempotency_success + 1
            )


class TestCi03G2I02R4B2IntegratedExpiry:
    @pytest.mark.parametrize(
        ("effect",),
        (("attest",), ("approve",), ("reject",), ("request_changes",)),
    )
    def test_expiry_advances_clock_while_same_txn_holds_slot_without_lock_wait(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
        effect: str,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        clock_holder = {"t": _NOW}

        def clock() -> datetime:
            return clock_holder["t"]

        scenario = _load_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=clock,
            attest_for_approve=effect == "approve",
        )
        valid_until = assignment_valid_until(
            ci03_g2_runtime_engine, scenario.tenant_id, scenario.assignment_id
        )
        snap_before = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )

        slot_held = threading.Event()
        release = threading.Event()
        holder_pid: list[int | None] = [None]
        clock_advanced = threading.Event()

        def advance_clock() -> None:
            clock_holder["t"] = valid_until + timedelta(seconds=1)
            clock_advanced.set()

        hold_factory = SlotHoldingContentUowFactory(
            ci03_g2_runtime_engine,
            slot_held=slot_held,
            release=release,
            holder_pid=holder_pid,
            while_held=advance_clock,
        )

        worker_errors: list[BaseException] = []
        worker_event = _correlated_event(scenario.reviewer_id)

        def worker() -> None:
            try:
                if effect == "attest":
                    _invoke_attest(
                        ci03_g2_runtime_engine,
                        scenario,
                        clock=clock,
                        uow_factory=hold_factory,
                        idem_key=f"b2-intra-hold-{effect}-{uuid.uuid7()}",
                        event_context=worker_event,
                    )
                else:
                    _invoke_decision(
                        ci03_g2_runtime_engine,
                        scenario,
                        effect=effect,
                        clock=clock,
                        uow_factory=hold_factory,
                        idem_key=f"b2-intra-hold-{effect}-{uuid.uuid7()}",
                        event_context=worker_event,
                    )
            except BaseException as exc:  # noqa: BLE001
                worker_errors.append(exc)

        thread = threading.Thread(target=worker, name=f"expiry-intra-{effect}")
        try:
            thread.start()
            assert slot_held.wait(timeout=30), "effect did not reach slot lock"
            assert clock_advanced.wait(timeout=5), "clock did not advance under hold callback"
            assert clock_holder["t"] >= valid_until
        finally:
            release.set()
            join_bounded(thread)

        assert len(worker_errors) == 1
        assert_review_assignment_expiry_exception(worker_errors[0])
        snap_after = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )
        assert_snapshot_unchanged_except_denial(snap_before, snap_after)
        assert_latest_denial_matches(
            ci03_g2_runtime_engine,
            tenant_id=scenario.tenant_id,
            principal_id=scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            correlation_id=worker_event.correlation_id,
        )

    @pytest.mark.parametrize("boundary", ("at_valid_until", "after_valid_until"))
    @pytest.mark.parametrize(
        ("effect",),
        (("attest",), ("approve",), ("reject",), ("request_changes",)),
    )
    def test_expiry_lock_wait_crosses_persisted_valid_until(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
        effect: str,
        boundary: str,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        clock_holder = {"t": _NOW}

        def clock() -> datetime:
            return clock_holder["t"]

        scenario = _load_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=clock,
            attest_for_approve=effect == "approve",
        )
        valid_until = assignment_valid_until(
            ci03_g2_runtime_engine, scenario.tenant_id, scenario.assignment_id
        )
        snap_before = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )

        blocker_ready = threading.Event()
        contending = threading.Event()
        release = threading.Event()
        blocker_pid: list[int | None] = [None]
        waiter_pid: list[int | None] = [None]
        worker_errors: list[BaseException] = []
        blocker_errors: list[BaseException] = []
        worker_event = _correlated_event(scenario.reviewer_id)

        contend_factory = ContendingContentUowFactory(
            ci03_g2_runtime_engine,
            contending=contending,
            waiter_pid=waiter_pid,
        )

        def blocker() -> None:
            try:
                passive_head_slot_blocker(
                    ci03_g2_runtime_engine,
                    scenario.tenant_id,
                    content_id=scenario.content_id,
                    version_id=scenario.version_id,
                    locks_acquired=blocker_ready,
                    release=release,
                    blocker_pid=blocker_pid,
                )
            except BaseException as exc:  # noqa: BLE001
                blocker_errors.append(exc)
                raise

        def worker() -> None:
            try:
                if effect == "attest":
                    _invoke_attest(
                        ci03_g2_runtime_engine,
                        scenario,
                        clock=clock,
                        uow_factory=contend_factory,
                        idem_key=f"b2-lockwait-{boundary}-{effect}-{uuid.uuid7()}",
                        event_context=worker_event,
                    )
                else:
                    _invoke_decision(
                        ci03_g2_runtime_engine,
                        scenario,
                        effect=effect,
                        clock=clock,
                        uow_factory=contend_factory,
                        idem_key=f"b2-lockwait-{boundary}-{effect}-{uuid.uuid7()}",
                        event_context=worker_event,
                    )
            except BaseException as exc:  # noqa: BLE001
                worker_errors.append(exc)

        blocker_thread = threading.Thread(target=blocker, name="passive-blocker")
        worker_thread: threading.Thread | None = None
        try:
            blocker_thread.start()
            assert blocker_ready.wait(timeout=30), "blocker did not acquire head/slot locks"
            worker_thread = threading.Thread(target=worker, name="expiry-waiter")
            worker_thread.start()
            assert contending.wait(timeout=30), "effect did not reach head lock wait"
            assert clock_holder["t"] < valid_until
            assert_genuine_lock_contention(
                ci03_g2_bootstrap_engine,
                waiter_thread=worker_thread,
                waiter_pid=waiter_pid[0],
                holder_pid=blocker_pid[0],
                locks_held=blocker_ready,
                release=release,
            )
            if boundary == "at_valid_until":
                clock_holder["t"] = valid_until
            else:
                clock_holder["t"] = valid_until + timedelta(seconds=1)
        finally:
            release.set()
            blocker_thread.join(timeout=30)
            if worker_thread is not None:
                worker_thread.join(timeout=30)
                assert not worker_thread.is_alive()
            assert not blocker_thread.is_alive()

        assert len(worker_errors) == 1
        assert_review_assignment_expiry_exception(worker_errors[0])
        assert not blocker_errors
        assert clock_holder["t"] >= valid_until
        snap_after = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )
        assert_snapshot_unchanged_except_denial(snap_before, snap_after)
        assert_latest_denial_matches(
            ci03_g2_runtime_engine,
            tenant_id=scenario.tenant_id,
            principal_id=scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            correlation_id=worker_event.correlation_id,
        )

    @pytest.mark.parametrize(
        ("effect",),
        (("attest",), ("approve",), ("reject",), ("request_changes",)),
    )
    def test_expiry_crosses_during_integrated_authority_lookup(
        self,
        ci03_g2_bootstrap_engine: Engine,
        ci03_g2_runtime_engine: Engine,
        effect: str,
    ) -> None:
        provision_runtime_grants(ci03_g2_bootstrap_engine)
        clock_holder = {"t": _NOW}

        def clock() -> datetime:
            return clock_holder["t"]

        scenario = _load_scenario(
            ci03_g2_bootstrap_engine,
            ci03_g2_runtime_engine,
            clock=clock,
            attest_for_approve=effect == "approve",
        )
        inner = SchoolContextClassAuthorityService(scenario.provider)
        slow = _ExpiryAdvancingClassAuthority(inner, clock_holder)
        valid_until = assignment_valid_until(
            ci03_g2_runtime_engine, scenario.tenant_id, scenario.assignment_id
        )
        snap_before = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )

        worker_event = _correlated_event(scenario.reviewer_id)
        if effect == "attest":
            service = integrated_attest_service(
                ci03_g2_runtime_engine,
                scenario.tenant_id,
                clock=clock,
                school_context_provider=scenario.provider,
                issuer_class_authority=slow,
            )
            with pytest.raises(ReviewAssignmentConflict, match="revoked or expired"):
                service.record(
                    scenario.tenant_id,
                    scenario.reviewer_id,
                    content_id=scenario.content_id,
                    version_id=scenario.version_id,
                    expected_aggregate_revision=scenario.revision,
                    per_question_outcomes=_positive_outcomes(
                        _hinted_ids_from_version(
                            ci03_g2_runtime_engine, scenario.tenant_id, scenario.version_id
                        )
                    ),
                    idempotency_key=f"b2-slow-{effect}-{uuid.uuid7()}",
                    event_context=worker_event,
                    audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
                    now=clock(),
                )
        else:
            review = integrated_review_service(
                ci03_g2_runtime_engine,
                scenario.tenant_id,
                clock=clock,
                school_context_provider=scenario.provider,
                issuer_class_authority=slow,
            )
            kwargs = dict(
                execution_tenant_id=scenario.tenant_id,
                principal_id=scenario.reviewer_id,
                content_id=scenario.content_id,
                version_id=scenario.version_id,
                expected_aggregate_revision=scenario.revision,
                idempotency_key=f"b2-slow-{effect}-{uuid.uuid7()}",
                event_context=worker_event,
                audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
                now=clock(),
            )
            with pytest.raises(ReviewAssignmentConflict, match="revoked or expired"):
                if effect == "approve":
                    review.approve(**kwargs, reason_code=None, comment=None)
                elif effect == "reject":
                    review.reject(**kwargs, reason_code="governed", comment="deny")
                else:
                    review.request_changes(**kwargs, reason_code="governed", comment="changes")

        assert clock_holder["t"] >= valid_until
        snap_after = _capture_snapshot(
            ci03_g2_bootstrap_engine, ci03_g2_runtime_engine, scenario
        )
        assert_snapshot_unchanged_except_denial(snap_before, snap_after)
        assert_latest_denial_matches(
            ci03_g2_runtime_engine,
            tenant_id=scenario.tenant_id,
            principal_id=scenario.reviewer_id,
            content_id=scenario.content_id,
            version_id=scenario.version_id,
            correlation_id=worker_event.correlation_id,
        )