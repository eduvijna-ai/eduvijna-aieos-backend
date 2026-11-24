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
from aieos.domains.content.application.errors import (
    ReviewAssignmentConflict,
    ReviewAssignmentNotAllowed,
)
from aieos.domains.content.application.review_assignment_bound_mutations import (
    CONTENT_REVIEW_MUTATION_DENIED,
)
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
    revoke_service_with_factory,
    run_two_party_contention,
)
from tests.domains.content.application.test_ci03_g2_i01_assignment_postgres import (
    _NOW,
    _denial_count,
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

_EXPECTED_WAITER_DENIED = (ReviewAssignmentConflict, ReviewAssignmentNotAllowed)


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


def _attestation_count(runtime: Engine, tenant_id: uuid.UUID, scenario: _Scenario) -> int:
    with runtime.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.worksheet_learner_hint_version_attestations
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": scenario.content_id.value, "vid": scenario.version_id.value},
            ).scalar_one()
        )


def _decision_count(runtime: Engine, tenant_id: uuid.UUID, scenario: _Scenario) -> int:
    with runtime.connect() as conn:
        conn.execute(
            text("SELECT set_config('aieos.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        return int(
            conn.execute(
                text(
                    """
                    SELECT count(*) FROM content.review_decisions
                    WHERE content_id = :cid AND version_id = :vid
                    """
                ),
                {"cid": scenario.content_id.value, "vid": scenario.version_id.value},
            ).scalar_one()
        )


def _invoke_attest(
    runtime: Engine,
    scenario: _Scenario,
    *,
    clock,
    uow_factory: Any,
    idem_key: str,
    now: datetime | None = None,
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
        event_context=_event(scenario.reviewer_id),
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
        event_context=_event(scenario.reviewer_id),
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
        denials_before = _denial_count(
            ci03_g2_runtime_engine, scenario.tenant_id, CONTENT_REVIEW_MUTATION_DENIED
        )
        attest_before = _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)
        decisions_before = _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)

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

        def waiter() -> None:
            try:
                if effect == "attest":
                    _invoke_attest(
                        ci03_g2_runtime_engine,
                        scenario,
                        clock=clock,
                        uow_factory=contend_factory,
                        idem_key=f"b2-wait-attest-{uuid.uuid7()}",
                    )
                else:
                    _invoke_decision(
                        ci03_g2_runtime_engine,
                        scenario,
                        effect=effect,
                        clock=clock,
                        uow_factory=contend_factory,
                        idem_key=f"b2-wait-{effect}-{uuid.uuid7()}",
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
        assert isinstance(waiter_result.get("error"), _EXPECTED_WAITER_DENIED)

        assert _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
            attest_before + (1 if effect == "approve" else 0)
        )
        assert _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
            decisions_before
        )
        assert (
            _denial_count(
                ci03_g2_runtime_engine, scenario.tenant_id, CONTENT_REVIEW_MUTATION_DENIED
            )
            >= denials_before + 1
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
        attest_before = _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)
        decisions_before = _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)

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
                    pre = integrated_attest_service(
                        ci03_g2_runtime_engine,
                        scenario.tenant_id,
                        clock=clock,
                        school_context_provider=scenario.provider,
                    )
                    pre.record(
                        scenario.tenant_id,
                        scenario.reviewer_id,
                        content_id=scenario.content_id,
                        version_id=scenario.version_id,
                        expected_aggregate_revision=scenario.revision,
                        per_question_outcomes=_positive_outcomes(
                            _hinted_ids_from_version(
                                ci03_g2_runtime_engine,
                                scenario.tenant_id,
                                scenario.version_id,
                            )
                        ),
                        idempotency_key=f"b2-eff-pre-{uuid.uuid7()}",
                        event_context=_event(scenario.reviewer_id),
                        audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
                        now=clock(),
                    )
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

        if effect == "attest":
            assert _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
                attest_before + 1
            )
        elif effect == "approve":
            assert _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
                attest_before + 1
            )
            assert _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
                decisions_before + 1
            )
        elif effect in ("reject", "request_changes"):
            assert _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
                decisions_before + 1
            )


class TestCi03G2I02R4B2IntegratedExpiry:
    @pytest.mark.parametrize(
        ("effect",),
        (("attest",), ("approve",), ("reject",), ("request_changes",)),
    )
    def test_expiry_crosses_valid_until_while_slot_lock_held(
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
        denials_before = _denial_count(
            ci03_g2_runtime_engine, scenario.tenant_id, CONTENT_REVIEW_MUTATION_DENIED
        )
        attest_before = _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)
        decisions_before = _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)

        slot_held = threading.Event()
        release = threading.Event()
        holder_pid: list[int | None] = [None]

        def advance_clock() -> None:
            clock_holder["t"] = _NOW + timedelta(days=8)

        hold_factory = SlotHoldingContentUowFactory(
            ci03_g2_runtime_engine,
            slot_held=slot_held,
            release=release,
            holder_pid=holder_pid,
            while_held=advance_clock,
        )

        worker_errors: list[BaseException] = []

        def worker() -> None:
            try:
                if effect == "attest":
                    _invoke_attest(
                        ci03_g2_runtime_engine,
                        scenario,
                        clock=clock,
                        uow_factory=hold_factory,
                        idem_key=f"b2-lock-expire-{effect}-{uuid.uuid7()}",
                    )
                else:
                    _invoke_decision(
                        ci03_g2_runtime_engine,
                        scenario,
                        effect=effect,
                        clock=clock,
                        uow_factory=hold_factory,
                        idem_key=f"b2-lock-expire-{effect}-{uuid.uuid7()}",
                    )
            except BaseException as exc:  # noqa: BLE001
                worker_errors.append(exc)

        thread = threading.Thread(target=worker, name=f"expiry-{effect}")
        thread.start()
        assert slot_held.wait(timeout=30), "effect did not reach slot lock"
        assert clock_holder["t"] >= _NOW + timedelta(days=8)
        release.set()
        join_bounded(thread)

        assert len(worker_errors) == 1
        assert isinstance(worker_errors[0], _EXPECTED_WAITER_DENIED)
        assert _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
            attest_before
        )
        assert _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
            decisions_before
        )
        assert (
            _denial_count(
                ci03_g2_runtime_engine, scenario.tenant_id, CONTENT_REVIEW_MUTATION_DENIED
            )
            >= denials_before + 1
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

        denials_before = _denial_count(
            ci03_g2_runtime_engine, scenario.tenant_id, CONTENT_REVIEW_MUTATION_DENIED
        )
        attest_before = _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)
        decisions_before = _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario)

        if effect == "attest":
            service = integrated_attest_service(
                ci03_g2_runtime_engine,
                scenario.tenant_id,
                clock=clock,
                school_context_provider=scenario.provider,
                issuer_class_authority=slow,
            )
            with pytest.raises(ReviewAssignmentConflict, match="expired"):
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
                    event_context=_event(scenario.reviewer_id),
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
                event_context=_event(scenario.reviewer_id),
                audit_provenance=api_mutation_audit_provenance(scenario.reviewer_id),
                now=clock(),
            )
            with pytest.raises(ReviewAssignmentConflict, match="expired"):
                if effect == "approve":
                    review.approve(**kwargs, reason_code=None, comment=None)
                elif effect == "reject":
                    review.reject(**kwargs, reason_code="governed", comment="deny")
                else:
                    review.request_changes(**kwargs, reason_code="governed", comment="changes")

        assert clock_holder["t"] >= _NOW + timedelta(days=8)
        assert _attestation_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
            attest_before
        )
        assert _decision_count(ci03_g2_runtime_engine, scenario.tenant_id, scenario) == (
            decisions_before
        )
        assert (
            _denial_count(
                ci03_g2_runtime_engine, scenario.tenant_id, CONTENT_REVIEW_MUTATION_DENIED
            )
            >= denials_before + 1
        )