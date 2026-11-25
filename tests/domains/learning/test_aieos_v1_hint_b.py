"""PK-V1-HINT-B — Attempt-scoped learner-hint GET (ADR-063 §8.2)."""

from __future__ import annotations

import dataclasses
import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.parent_learner_access import PARENT_OS_HUMAN_ADULT_A_ID
from aieos.development.learner_principals import STUDENT_B_PRINCIPAL_ID
from aieos.development.principal_school_context import PRINCIPAL_OS_HUMAN_PRINCIPAL_ID
from aieos.domains.learning.application.audit import api_mutation_audit_provenance
from aieos.domains.learning.application.errors import (
    AssignmentClosedOrCancelled,
    AssignmentNotYetAvailable,
    AttemptNotFound,
    ExactContentVersionNotFound,
    InvalidAttemptState,
)
from aieos.domains.learning.application.learner_membership import (
    SchoolContextContractError,
    SchoolContextLearnerMembershipAuthorityService,
    SchoolContextUnavailable,
)
from aieos.domains.learning.application.submit_attempt import SubmitAttemptService
from aieos.domains.teaching.application.assignment_mutations import (
    CloseTeachingAssignmentService,
)
from aieos.domains.teaching.application.audit import (
    api_mutation_audit_provenance as teaching_audit,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.events.models import MutationEventContext
from aieos.platform.runtime.student_learning_command import (
    SqlAlchemyStudentLearningCommandUnitOfWork,
    SqlAlchemyStudentLearningCommandUnitOfWorkFactory,
)
from aieos.platform.security.authorization.principal_classification import (
    CurrentPrincipalClassificationAuthority,
)
from tests.domains.education.test_aieos_v1_hint_t1 import _hint_free_v2_payload, _worksheet_v2_payload
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ATTEMPT_PATH,
    FUTURE_AVAILABLE,
    IDEMPOTENCY_RETENTION,
    PAST_DUE,
    RaisingMembershipReader,
    build_student_client,
    cancel_assignment,
    clear_i03_side_effects,
    close_assignment,
    headers,
    prepare_class_5a_assignment,
    start_attempt,
    student_client,
    submit_attempt,
)
from tests.domains.teaching.helpers_dev06_i03 import event_context as teaching_event_context
from tests.platform.security.authorization.helpers import seed_principal
from aieos.platform.security.authorization.decisions import PrincipalKind, PrincipalStatus
from tests.domains.learning.helpers_aieos_v1_hint_t1b import (
    FIXED_T1B_HINT,
    clear_t1b_side_effects,
    prepare_exact_pin_v1_then_v2_assignments,
    prepare_governed_worksheet_v2_assignment,
)
from aieos.platform.api.problems import _LEARNING_PROBLEMS
from aieos.platform.runtime.worksheet_learner_hints import (
    load_governed_worksheet_learner_hints,
)
from tests.domains.learning.helpers_aieos_v1_hint_b_r2 import (
    B8_EVENT_ORDER,
    ContentionHintUowFactory,
    ContentionSubmitUowFactory,
    EventOrderStudentUowFactory,
    HintHoldingStudentUowFactory,
    SubmitHoldingStudentUowFactory,
    assert_assignment_content_version_fk,
    assert_fk_rejects_coherent_assignment_version_pin,
    assert_problem,
    begin_successive_v2_same_content,
    complete_successive_v2_second_publication,
    corrupt_content_type,
    corrupt_version_payload,
    corrupt_version_schema,
    assert_genuine_lock_contention,
    delete_hint_attestation,
    guarded_hint_b_schema_integrity,
    require_disposable_hint_b_postgres,
    snapshot_assignment_content_version_fk,
)
from aieos.domains.learning.application.start_attempt import StartAttemptService
pytestmark = [
    pytest.mark.aieos_v1_hint_b,
    pytest.mark.usefixtures("ci03_hint_governed_substrate"),
]

HINTS_PATH = "/api/v1/learning/attempts/{attempt_id}/learner-hints"
_NO_STORE = "private, no-store"

_FORBIDDEN = frozenset(
    {
        "answer",
        "explanation",
        "teacher_notes",
        "teacher_summary",
        "payload",
        "evaluation",
        "score",
        "grade",
    }
)


@pytest.fixture(autouse=True)
def _hint_b_cleanup(ci03_g2_bootstrap_engine: Engine) -> Iterator[None]:
    yield
    clear_t1b_side_effects(ci03_g2_bootstrap_engine)


def _hinted_payload() -> dict[str, Any]:
    payload = _worksheet_v2_payload(hint=FIXED_T1B_HINT)
    payload["questions"][2]["learner_hint"] = "Compare to one half."
    return payload


def _hints_url(attempt_id: UUID) -> str:
    return HINTS_PATH.format(attempt_id=attempt_id)


def _get_hints(client: TestClient, prepared, attempt_id: UUID):
    return client.get(_hints_url(attempt_id), headers=headers(prepared.tenant_id))


def _assert_no_leak(body: object, *, allowed_hints: tuple[str, ...] = ()) -> None:
    dumped = json.dumps(body)

    def keys(value: object) -> set[str]:
        found: set[str] = set()
        if isinstance(value, dict):
            found.update(value.keys())
            for item in value.values():
                found.update(keys(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                found.update(keys(item))
        return found

    assert keys(body).isdisjoint(_FORBIDDEN)
    for snippet in ("Consider the denominator.", "Compare to one half.", "answer", "explanation"):
        if snippet in allowed_hints or snippet in dumped and snippet in allowed_hints:
            continue
        if snippet not in allowed_hints and snippet in ("answer", "explanation"):
            assert snippet not in dumped.lower()


def _start_and_hints(client, prepared) -> tuple[UUID, dict]:
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    hints = _get_hints(client, prepared, attempt_id)
    return attempt_id, hints.json() if hints.status_code == 200 else {}


class TestLearnerHintsPositive:
    def test_hint_b_200_governed_three_human_path(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(client, prepared)
        assert started.status_code == 201
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        response = _get_hints(client, prepared, attempt_id)
        assert response.status_code == 200, response.text
        assert response.headers.get("Cache-Control") == _NO_STORE
        body = response.json()
        assert body["attempt_id"] == str(attempt_id)
        assert body["content_version_id"] == str(prepared.content_version_id)
        qids = {item["question_id"] for item in body["hints"]}
        texts = {item["hint_text"] for item in body["hints"]}
        assert qids == {"q-1", "q-3"}
        assert FIXED_T1B_HINT in texts
        assert "Compare to one half." in texts
        allowed = tuple(texts)
        _assert_no_leak(body, allowed_hints=allowed)

    def test_hint_b_empty_hints_v1_pin_and_hint_free_v2(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared_v2 = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine,
            ci03_g2_bootstrap_engine,
            payload=_hint_free_v2_payload(),
        )
        client = student_client(ci03_g2_runtime_engine, prepared_v2)
        started = start_attempt(client, prepared_v2)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        response = _get_hints(client, prepared_v2, attempt_id)
        assert response.status_code == 200
        assert response.json()["hints"] == []

        pins = prepare_exact_pin_v1_then_v2_assignments(ci03_g2_runtime_engine, ci03_g2_bootstrap_engine)
        client_v1 = student_client(ci03_g2_runtime_engine, pins.v1_assignment)
        started_v1 = start_attempt(client_v1, pins.v1_assignment)
        attempt_v1 = uuid.UUID(started_v1.json()["attempt_id"])
        res_v1 = _get_hints(client_v1, pins.v1_assignment, attempt_v1)
        assert res_v1.status_code == 200
        assert res_v1.json()["content_version_id"] == str(pins.v1_content_version_id)
        assert res_v1.json()["hints"] == []


class TestLearnerHintsAuthorization:
    def test_hint_b_submitted_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        match = started.headers["ETag"]
        submitted = submit_attempt(client, prepared, attempt_id, if_match=match)
        assert submitted.status_code == 200
        denied = _get_hints(client, prepared, attempt_id)
        assert denied.status_code == 409
        assert denied.headers.get("Cache-Control") == _NO_STORE
        assert "learner_hint" not in denied.text.lower()
        assert FIXED_T1B_HINT not in denied.text

    def test_hint_b_wrong_student_and_foreign_attempt(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        client_a = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(client_a, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        client_b = build_student_client(
            ci03_g2_runtime_engine,
            prepared.tenant_id,
            STUDENT_B_PRINCIPAL_ID,
            learner_membership_reader=prepared.membership,
        )
        denied = _get_hints(client_b, prepared, attempt_id)
        assert denied.status_code == 404
        assert FIXED_T1B_HINT not in denied.text

    def test_hint_b_membership_revoked_between_requests(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        first = _get_hints(client, prepared, attempt_id)
        assert first.status_code == 200
        assert first.headers.get("Cache-Control") == _NO_STORE
        prepared.membership._memberships[prepared.student_id] = ()
        denied = _get_hints(client, prepared, attempt_id)
        assert denied.status_code == 404
        assert denied.headers.get("Cache-Control") == _NO_STORE
        assert FIXED_T1B_HINT not in denied.text

    def test_hint_b_assignment_cancelled_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        cancel_assignment(
            ci03_g2_runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=prepared.assignment,
            idempotency_key=f"cancel-{uuid.uuid7()}",
        )
        denied = _get_hints(client, prepared, attempt_id)
        assert denied.status_code == 409
        assert FIXED_T1B_HINT not in denied.text


class _LockOrderUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    def __init__(self, engine, tenant_id, events: list[str]):
        super().__init__(engine, tenant_id)
        self._events = events

    def __enter__(self):
        super().__enter__()
        real = self.attempts.get_for_update

        def wrapped(attempt_id):
            self._events.append("attempt_for_update")
            return real(attempt_id)

        self.attempts.get_for_update = wrapped  # type: ignore[method-assign]
        return self

    def get_assignment_for_update(self, assignment_id):
        self._events.append("assignment_for_update")
        return super().get_assignment_for_update(assignment_id)

    def commit(self) -> None:
        self._events.append("commit")
        super().commit()


class _LockOrderFactory:
    def __init__(self, engine, events: list[str]):
        self._engine = engine
        self._events = events

    def __call__(self, execution_tenant_id):
        return _LockOrderUow(self._engine, execution_tenant_id, self._events)


class TestLearnerHintsLockOrder:
    def test_hint_b_assignment_before_attempt_and_commit_before_response(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        events: list[str] = []
        from aieos.domains.learning.application.get_learner_hints import (
            GetLearnerHintsService,
        )

        service = GetLearnerHintsService(
            _LockOrderFactory(ci03_g2_runtime_engine, events),
            SchoolContextLearnerMembershipAuthorityService(prepared.membership),
            CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine),
        )
        client = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        service.get(prepared.tenant_id, prepared.student_id, attempt_id)
        assert events.index("assignment_for_update") < events.index("attempt_for_update")
        assert events.index("attempt_for_update") < events.index("commit")


class _DriftLockedAttemptUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    """Test-only: drift locked attempt class_ref away from Phase A preview (B5)."""

    def __enter__(self):
        super().__enter__()
        real = self.attempts.get_for_update

        def wrapped(attempt_id):
            loaded = real(attempt_id)
            if loaded is not None:
                return dataclasses.replace(loaded, class_ref="class-drifted")
            return loaded

        self.attempts.get_for_update = wrapped  # type: ignore[method-assign]
        return self


class HoldingStudentUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    def __init__(self, engine, tenant_id, locked: threading.Event, release: threading.Event):
        super().__init__(engine, tenant_id)
        self._hold_locked = locked
        self._hold_release = release

    def get_assignment_for_update(self, assignment_id):
        loaded = super().get_assignment_for_update(assignment_id)
        if loaded is not None:
            self._hold_locked.set()
            if not self._hold_release.wait(timeout=20):
                raise TimeoutError("student assignment lock was not released")
        return loaded


class HoldingStudentUowFactory:
    def __init__(self, engine, locked: threading.Event, release: threading.Event):
        self._engine = engine
        self._locked = locked
        self._release = release

    def __call__(self, execution_tenant_id):
        return HoldingStudentUow(
            self._engine, execution_tenant_id, self._locked, self._release
        )


class HoldingHintUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    def __init__(self, engine, tenant_id, locked: threading.Event, release: threading.Event):
        super().__init__(engine, tenant_id)
        self._hold_locked = locked
        self._hold_release = release

    def get_assignment_for_update(self, assignment_id):
        loaded = super().get_assignment_for_update(assignment_id)
        if loaded is not None:
            self._hold_locked.set()
            if not self._hold_release.wait(timeout=20):
                raise TimeoutError("hint assignment lock was not released")
        return loaded


class HoldingHintUowFactory:
    def __init__(self, engine, locked: threading.Event, release: threading.Event):
        self._engine = engine
        self._locked = locked
        self._release = release

    def __call__(self, execution_tenant_id):
        return HoldingHintUow(
            self._engine, execution_tenant_id, self._locked, self._release
        )


class _HoldingTeachingProxy:
    def __init__(
        self,
        inner,
        locked: threading.Event,
        release: threading.Event,
        holder_pid: list[int | None] | None = None,
    ):
        self._inner = inner
        self._locked = locked
        self._release = release
        self._holder_pid = holder_pid

    def __enter__(self):
        uow = self._inner.__enter__()
        real = uow.assignments.get_for_update

        def wrapped(assignment_id):
            loaded = real(assignment_id)
            if loaded is not None:
                if self._holder_pid is not None:
                    self._holder_pid[0] = uow._connection.execute(  # noqa: SLF001
                        text("SELECT pg_backend_pid()")
                    ).scalar_one()
                self._locked.set()
                if not self._release.wait(timeout=25):
                    raise TimeoutError("teacher assignment lock was not released")
            return loaded

        uow.assignments.get_for_update = wrapped  # type: ignore[method-assign]
        return uow

    def __exit__(self, exc_type, exc, tb):
        return self._inner.__exit__(exc_type, exc, tb)


class HoldingTeachingUowFactory:
    def __init__(
        self,
        engine,
        locked: threading.Event,
        release: threading.Event,
        holder_pid: list[int | None] | None = None,
    ):
        self._inner = SqlAlchemyTeachingUnitOfWorkFactory(engine)
        self._locked = locked
        self._release = release
        self._holder_pid = holder_pid

    def __call__(self, execution_tenant_id):
        return _HoldingTeachingProxy(
            self._inner(execution_tenant_id),
            self._locked,
            self._release,
            self._holder_pid,
        )


def _learning_event(principal_id: UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def _hints_service(
    ci03_g2_runtime_engine: Engine, prepared, factory
) -> "GetLearnerHintsService":
    from aieos.domains.learning.application.get_learner_hints import (
        GetLearnerHintsService,
    )

    return GetLearnerHintsService(
        factory,
        SchoolContextLearnerMembershipAuthorityService(prepared.membership),
        CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine),
    )


def _submit_service(ci03_g2_runtime_engine: Engine, prepared, factory) -> SubmitAttemptService:
    return SubmitAttemptService(
        factory,
        SchoolContextLearnerMembershipAuthorityService(prepared.membership),
        CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _start_governed_v2(ci03_g2_runtime_engine, ci03_g2_bootstrap_engine):
    prepared = prepare_governed_worksheet_v2_assignment(
        ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
    )
    client = student_client(ci03_g2_runtime_engine, prepared)
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    revision = int(started.json()["aggregate_revision"])
    return prepared, client, attempt_id, revision, started.headers["ETag"]


class TestLearnerHintsB5B8:
    def test_hint_b_post_lock_preview_drift_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, _client, attempt_id, _revision, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        factory = lambda tid: _DriftLockedAttemptUow(ci03_g2_runtime_engine, tid)  # noqa: E731
        service = _hints_service(ci03_g2_runtime_engine, prepared, factory)
        with pytest.raises(AttemptNotFound):
            service.get(prepared.tenant_id, prepared.student_id, attempt_id)

    def test_hint_b_b8_event_order_after_hint_extraction_before_commit(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, _client, attempt_id, _revision, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        events: list[str] = []
        service = _hints_service(
            ci03_g2_runtime_engine,
            prepared,
            EventOrderStudentUowFactory(ci03_g2_runtime_engine, events),
        )
        model = service.get(prepared.tenant_id, prepared.student_id, attempt_id)
        events.append("HTTP-body-after-commit")
        assert model.hints
        assert events == [
            *B8_EVENT_ORDER,
            "HTTP-body-after-commit",
        ]


class TestLearnerHintsConcurrency:
    def test_hint_b_get_then_submit_second_get_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _revision, etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        first = _get_hints(client, prepared, attempt_id)
        assert first.status_code == 200
        assert first.headers.get("Cache-Control") == _NO_STORE
        submit = submit_attempt(client, prepared, attempt_id, if_match=etag)
        assert submit.status_code == 200
        second = _get_hints(client, prepared, attempt_id)
        assert second.status_code == 409
        assert second.headers.get("Cache-Control") == _NO_STORE
        assert FIXED_T1B_HINT not in second.text

    def test_hint_b_submit_wins_assignment_lock_hint_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, revision, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        both_locks_held = threading.Event()
        holder_pid: list[int | None] = [None]
        hint_contending = threading.Event()
        waiter_pid: list[int | None] = [None]
        release = threading.Event()
        results: dict[str, object] = {}
        submit_service = _submit_service(
            ci03_g2_runtime_engine,
            prepared,
            SubmitHoldingStudentUowFactory(
                ci03_g2_runtime_engine,
                assignment_locked=threading.Event(),
                both_locks_held=both_locks_held,
                holder_pid=holder_pid,
                release=release,
            ),
        )
        hint_service = _hints_service(
            ci03_g2_runtime_engine,
            prepared,
            ContentionHintUowFactory(
                ci03_g2_runtime_engine,
                contending_on_assignment=hint_contending,
                waiter_pid=waiter_pid,
            ),
        )

        def submit() -> None:
            try:
                results["submit"] = submit_service.submit(
                    prepared.tenant_id,
                    prepared.student_id,
                    attempt_id,
                    expected_aggregate_revision=revision,
                    idempotency_key=f"submit-{uuid.uuid7()}",
                    event_context=_learning_event(prepared.student_id),
                    audit_provenance=api_mutation_audit_provenance(prepared.student_id),
                )
            except BaseException as exc:  # noqa: BLE001
                results["submit_error"] = exc

        def hint_get() -> None:
            try:
                results["hint"] = hint_service.get(
                    prepared.tenant_id, prepared.student_id, attempt_id
                )
            except BaseException as exc:  # noqa: BLE001
                results["hint_error"] = exc

        submit_thread = threading.Thread(target=submit, name="submit-first")
        hint_thread: threading.Thread | None = None
        try:
            submit_thread.start()
            assert both_locks_held.wait(timeout=25)
            hint_thread = threading.Thread(target=hint_get, name="hint-blocked")
            hint_thread.start()
            assert hint_contending.wait(timeout=25)
            assert holder_pid[0] is not None and waiter_pid[0] is not None
            assert_genuine_lock_contention(
                ci03_g2_bootstrap_engine,
                waiter_thread=hint_thread,
                waiter_pid=waiter_pid[0],
                holder_pid=holder_pid[0],
                locks_held=both_locks_held,
                release=release,
            )
        finally:
            release.set()
            submit_thread.join(timeout=30)
            if hint_thread is not None:
                hint_thread.join(timeout=30)
        assert not submit_thread.is_alive()
        assert hint_thread is not None and not hint_thread.is_alive()
        assert "submit" in results
        assert results.get("submit_error") is None
        assert isinstance(results.get("hint_error"), InvalidAttemptState)
        assert "hint" not in results
        denied = client.get(
            _hints_url(attempt_id), headers=headers(prepared.tenant_id)
        )
        assert_problem(
            denied,
            status=409,
            code="invalid_attempt_state",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    def test_hint_b_hint_wins_lock_commits_before_submit(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, revision, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        both_locks_held = threading.Event()
        holder_pid: list[int | None] = [None]
        submit_contending = threading.Event()
        submit_waiter_pid: list[int | None] = [None]
        release = threading.Event()
        results: dict[str, object] = {}
        hint_service = _hints_service(
            ci03_g2_runtime_engine,
            prepared,
            HintHoldingStudentUowFactory(
                ci03_g2_runtime_engine,
                both_locks_held=both_locks_held,
                holder_pid=holder_pid,
                release=release,
            ),
        )
        submit_service = _submit_service(
            ci03_g2_runtime_engine,
            prepared,
            ContentionSubmitUowFactory(
                ci03_g2_runtime_engine,
                contending_on_assignment=submit_contending,
                waiter_pid=submit_waiter_pid,
            ),
        )

        def hint_get() -> None:
            try:
                results["hint"] = hint_service.get(
                    prepared.tenant_id, prepared.student_id, attempt_id
                )
            except BaseException as exc:  # noqa: BLE001
                results["hint_error"] = exc

        def submit() -> None:
            try:
                results["submit"] = submit_service.submit(
                    prepared.tenant_id,
                    prepared.student_id,
                    attempt_id,
                    expected_aggregate_revision=revision,
                    idempotency_key=f"submit-{uuid.uuid7()}",
                    event_context=_learning_event(prepared.student_id),
                    audit_provenance=api_mutation_audit_provenance(prepared.student_id),
                )
            except BaseException as exc:  # noqa: BLE001
                results["submit_error"] = exc

        hint_thread = threading.Thread(target=hint_get, name="hint-first")
        submit_thread: threading.Thread | None = None
        try:
            hint_thread.start()
            assert both_locks_held.wait(timeout=25)
            submit_thread = threading.Thread(target=submit, name="submit-waiting")
            submit_thread.start()
            assert submit_contending.wait(timeout=25)
            assert holder_pid[0] is not None and submit_waiter_pid[0] is not None
            assert_genuine_lock_contention(
                ci03_g2_bootstrap_engine,
                waiter_thread=submit_thread,
                waiter_pid=submit_waiter_pid[0],
                holder_pid=holder_pid[0],
                locks_held=both_locks_held,
                release=release,
            )
        finally:
            release.set()
            hint_thread.join(timeout=30)
            if submit_thread is not None:
                submit_thread.join(timeout=30)
        assert not hint_thread.is_alive()
        assert submit_thread is not None and not submit_thread.is_alive()
        assert "hint" in results
        assert results.get("hint_error") is None
        hint_model = results["hint"]
        assert hint_model.hints
        assert any(FIXED_T1B_HINT in item.hint_text for item in hint_model.hints)
        assert "submit" in results
        assert results.get("submit_error") is None
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=409,
            code="invalid_attempt_state",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    def test_hint_b_close_wins_assignment_lock_hint_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _revision, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        locked = threading.Event()
        release = threading.Event()
        holder_pid: list[int | None] = [None]
        hint_contending = threading.Event()
        waiter_pid: list[int | None] = [None]
        results: dict[str, object] = {}
        close_service = CloseTeachingAssignmentService(
            HoldingTeachingUowFactory(
                ci03_g2_runtime_engine, locked, release, holder_pid=holder_pid
            ),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        hint_service = _hints_service(
            ci03_g2_runtime_engine,
            prepared,
            ContentionHintUowFactory(
                ci03_g2_runtime_engine,
                contending_on_assignment=hint_contending,
                waiter_pid=waiter_pid,
            ),
        )

        def close() -> None:
            try:
                results["close"] = close_service.close(
                    prepared.tenant_id,
                    prepared.teacher_id,
                    assignment_id=prepared.assignment.assignment_id,
                    expected_aggregate_revision=prepared.assignment.aggregate_revision,
                    idempotency_key=f"close-{uuid.uuid7()}",
                    event_context=teaching_event_context(prepared.teacher_id),
                    audit_provenance=teaching_audit(prepared.teacher_id),
                )
            except BaseException as exc:  # noqa: BLE001
                results["close_error"] = exc

        def hint_get() -> None:
            try:
                results["hint"] = hint_service.get(
                    prepared.tenant_id, prepared.student_id, attempt_id
                )
            except BaseException as exc:  # noqa: BLE001
                results["hint_error"] = exc

        try:
            teacher = threading.Thread(target=close, name="close-first")
            teacher.start()
            assert locked.wait(timeout=25)
            hint_thread = threading.Thread(target=hint_get, name="hint-close-race")
            hint_thread.start()
            assert hint_contending.wait(timeout=25)
            assert_genuine_lock_contention(
                ci03_g2_bootstrap_engine,
                waiter_thread=hint_thread,
                waiter_pid=waiter_pid[0],
                holder_pid=holder_pid[0],
                locks_held=locked,
                release=release,
            )
            release.set()
            teacher.join(timeout=30)
            hint_thread.join(timeout=30)
        finally:
            release.set()
        assert not teacher.is_alive()
        assert not hint_thread.is_alive()
        assert "close" in results
        assert results.get("close_error") is None
        assert isinstance(results.get("hint_error"), AssignmentClosedOrCancelled)
        assert "hint" not in results
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=409,
            code="assignment_closed_or_cancelled",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    @pytest.fixture(autouse=True)
    def _i03_cleanup(self, ci03_g2_bootstrap_engine: Engine) -> Iterator[None]:
        yield
        clear_i03_side_effects(ci03_g2_bootstrap_engine)


class TestLearnerHintsSecurityNegatives:
    def test_hint_b_wrong_tenant_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        wrong_tenant = uuid.uuid7()
        response = client.get(
            _hints_url(attempt_id),
            headers={"X-AIEOS-Tenant-ID": str(wrong_tenant)},
        )
        assert_problem(response, status=403, code="forbidden")

    def test_hint_b_workload_and_disabled_human_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        started_client = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(started_client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        workload = uuid.uuid7()
        seed_principal(ci03_g2_bootstrap_engine, workload, principal_kind=PrincipalKind.WORKLOAD)
        workload_client = build_student_client(
            ci03_g2_runtime_engine,
            prepared.tenant_id,
            workload,
            learner_membership_reader=prepared.membership,
        )
        denied_workload = _get_hints(workload_client, prepared, attempt_id)
        assert denied_workload.status_code == 403
        assert denied_workload.headers.get("Cache-Control") == _NO_STORE

        inactive = uuid.uuid7()
        seed_principal(
            ci03_g2_bootstrap_engine,
            inactive,
            principal_kind=PrincipalKind.HUMAN,
            status=PrincipalStatus.DISABLED,
        )
        inactive_client = build_student_client(
            ci03_g2_runtime_engine,
            prepared.tenant_id,
            inactive,
            learner_membership_reader=prepared.membership,
        )
        denied_inactive = _get_hints(inactive_client, prepared, attempt_id)
        assert denied_inactive.status_code == 403
        assert FIXED_T1B_HINT not in denied_inactive.text

    def test_hint_b_teacher_parent_principal_cannot_read_hints(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, _client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        for principal_id in (
            prepared.teacher_id,
            PARENT_OS_HUMAN_ADULT_A_ID,
            PRINCIPAL_OS_HUMAN_PRINCIPAL_ID,
        ):
            seed_principal(ci03_g2_bootstrap_engine, principal_id, principal_kind=PrincipalKind.HUMAN)
            alt = build_student_client(
                ci03_g2_runtime_engine,
                prepared.tenant_id,
                principal_id,
                learner_membership_reader=prepared.membership,
            )
            denied = _get_hints(alt, prepared, attempt_id)
            assert denied.status_code in (403, 404)
            assert denied.headers.get("Cache-Control") == _NO_STORE
            assert denied.json()["code"] in ("attempt_not_found", "forbidden")
            assert FIXED_T1B_HINT not in denied.text

    def test_hint_b_school_context_unavailable_and_contract_errors(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(ci03_g2_runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        for exc in (
            SchoolContextUnavailable("unavailable"),
            SchoolContextContractError("invalid contract"),
        ):
            broken = build_student_client(
                ci03_g2_runtime_engine,
                prepared.tenant_id,
                prepared.student_id,
                learner_membership_reader=RaisingMembershipReader(exc),
            )
            denied = _get_hints(broken, prepared, attempt_id)
            assert denied.status_code == 503
            assert denied.headers.get("Cache-Control") == _NO_STORE
            assert FIXED_T1B_HINT not in denied.text

    def test_hint_b_closed_not_yet_available_and_past_due_active(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        close_assignment(
            ci03_g2_runtime_engine,
            tenant_id=prepared.tenant_id,
            teacher_id=prepared.teacher_id,
            assignment=prepared.assignment,
            idempotency_key=f"close-{uuid.uuid7()}",
        )
        denied_closed = _get_hints(client, prepared, attempt_id)
        assert denied_closed.status_code in (409, 422)
        assert denied_closed.headers.get("Cache-Control") == _NO_STORE

        future_prep = prepare_class_5a_assignment(
            ci03_g2_runtime_engine,
            ci03_g2_bootstrap_engine,
            available_from=FUTURE_AVAILABLE,
        )
        future_client = student_client(ci03_g2_runtime_engine, future_prep)
        future_started = start_attempt(future_client, future_prep)
        assert future_started.status_code == 409
        assert future_started.json()["code"] == "assignment_not_yet_available"
        start_service = StartAttemptService(
            SqlAlchemyStudentLearningCommandUnitOfWorkFactory(ci03_g2_runtime_engine),
            SchoolContextLearnerMembershipAuthorityService(future_prep.membership),
            CurrentPrincipalClassificationAuthority(ci03_g2_runtime_engine),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        with pytest.raises(AssignmentNotYetAvailable):
            start_service.start(
                future_prep.tenant_id,
                future_prep.student_id,
                future_prep.assignment_id,
                idempotency_key=f"future-blocked-{uuid.uuid7()}",
                event_context=_learning_event(future_prep.student_id),
                audit_provenance=api_mutation_audit_provenance(future_prep.student_id),
            )

        past_due_prepared = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine,
            ci03_g2_bootstrap_engine,
            payload=_hinted_payload(),
        )
        from tests.domains.learning.helpers_aieos360_s01_i03 import update_assignment_due

        update_assignment_due(
            ci03_g2_runtime_engine,
            tenant_id=past_due_prepared.tenant_id,
            teacher_id=past_due_prepared.teacher_id,
            assignment=past_due_prepared.assignment,
            due_at=PAST_DUE,
            idempotency_key=f"due-{uuid.uuid7()}",
        )
        past_client = student_client(ci03_g2_runtime_engine, past_due_prepared)
        past_started = start_attempt(past_client, past_due_prepared)
        assert past_started.status_code == 201
        past_attempt = uuid.UUID(past_started.json()["attempt_id"])
        still_ok = _get_hints(past_client, past_due_prepared, past_attempt)
        assert still_ok.status_code == 200

    def test_hint_b_pinned_v2_does_not_float_to_newer_publication(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        pins = prepare_exact_pin_v1_then_v2_assignments(
            ci03_g2_runtime_engine,
            ci03_g2_bootstrap_engine,
            v2_payload=_hinted_payload(),
        )
        client_v1 = student_client(ci03_g2_runtime_engine, pins.v1_assignment)
        started_v1 = start_attempt(client_v1, pins.v1_assignment)
        attempt_v1 = uuid.UUID(started_v1.json()["attempt_id"])
        res_v1 = _get_hints(client_v1, pins.v1_assignment, attempt_v1)
        assert res_v1.status_code == 200
        assert res_v1.json()["hints"] == []

        client_v2 = student_client(ci03_g2_runtime_engine, pins.v2_assignment)
        started_v2 = start_attempt(client_v2, pins.v2_assignment)
        attempt_v2 = uuid.UUID(started_v2.json()["attempt_id"])
        res_v2 = _get_hints(client_v2, pins.v2_assignment, attempt_v2)
        assert res_v2.status_code == 200
        assert res_v2.json()["content_version_id"] == str(pins.v2_content_version_id)
        assert any(FIXED_T1B_HINT in item["hint_text"] for item in res_v2.json()["hints"])

    def test_hint_b_successive_published_v2_pins_keep_exact_hints(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        ctx = begin_successive_v2_same_content(ci03_g2_runtime_engine, ci03_g2_bootstrap_engine)
        client_old = student_client(ci03_g2_runtime_engine, ctx.first_assignment)
        started_old = start_attempt(client_old, ctx.first_assignment)
        assert started_old.status_code == 201
        attempt_old = uuid.UUID(started_old.json()["attempt_id"])
        before_pub = _get_hints(client_old, ctx.first_assignment, attempt_old)
        assert before_pub.status_code == 200
        assert before_pub.headers.get("Cache-Control") == _NO_STORE
        assert before_pub.json()["content_version_id"] == str(
            ctx.first_content_version_id
        )
        assert any(
            ctx.first_hint_text in item["hint_text"]
            for item in before_pub.json()["hints"]
        )
        assert ctx.second_hint_text not in before_pub.text

        pins = complete_successive_v2_second_publication(
            ctx, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        old_hints = _get_hints(client_old, pins.first_assignment, attempt_old)
        assert old_hints.status_code == 200
        assert old_hints.json()["content_version_id"] == str(
            pins.first_content_version_id
        )
        assert any(
            pins.first_hint_text in item["hint_text"] for item in old_hints.json()["hints"]
        )
        assert pins.second_hint_text not in old_hints.text

        client_new = student_client(ci03_g2_runtime_engine, pins.second_assignment)
        started_new = start_attempt(client_new, pins.second_assignment)
        attempt_new = uuid.UUID(started_new.json()["attempt_id"])
        new_hints = _get_hints(client_new, pins.second_assignment, attempt_new)
        assert new_hints.status_code == 200
        assert new_hints.json()["content_version_id"] == str(
            pins.second_content_version_id
        )
        assert any(
            pins.second_hint_text in item["hint_text"] for item in new_hints.json()["hints"]
        )
        assert pins.first_content_version_id != pins.second_content_version_id

    def test_hint_b_tampered_hint_attestation_digest_fails_closed(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        with ci03_g2_bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                    "DISABLE TRIGGER worksheet_hint_attestations_immutable_update"
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE content.worksheet_learner_hint_version_attestations
                    SET payload_digest = repeat('f', 64)
                    WHERE tenant_id = :tid AND version_id = :vid
                    """
                ),
                {
                    "tid": prepared.tenant_id,
                    "vid": prepared.content_version_id,
                },
            )
            conn.execute(
                text(
                    "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                    "ENABLE TRIGGER worksheet_hint_attestations_immutable_update"
                )
            )
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=422,
            code="content_not_learner_consumable",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    def test_hint_b_submitted_still_allows_historical_attempt_get(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        submit = submit_attempt(client, prepared, attempt_id, if_match=etag)
        assert submit.status_code == 200
        historical = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers=headers(prepared.tenant_id),
        )
        assert historical.status_code == 200
        assert "learner_hint" not in json.dumps(historical.json()).lower()
        denied_hints = _get_hints(client, prepared, attempt_id)
        assert denied_hints.status_code == 409


class TestLearnerHintsR2ContentNegatives:
    def test_hint_b_missing_attestation_row(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        delete_hint_attestation(
            ci03_g2_bootstrap_engine,
            tenant_id=prepared.tenant_id,
            version_id=prepared.content_version_id,
        )
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=422,
            code="content_not_learner_consumable",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    def test_hint_b_binding_drift_attempt_version_only_denies(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        """Attempt pin drift without assignment alignment → B5/consumable, not B7 loader."""
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        ghost_version = uuid.uuid7()
        with ci03_g2_bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE learning.attempts
                    SET content_version_id = :cvid
                    WHERE tenant_id = :tid AND attempt_id = :aid
                    """
                ),
                {
                    "cvid": ghost_version,
                    "tid": prepared.tenant_id,
                    "aid": attempt_id,
                },
            )
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=409,
            code="assignment_not_currently_consumable",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    def test_hint_b_frozen_fk_blocks_missing_version_coherent_pin(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        """Coherent missing ContentVersion pin is unreachable: FK rejects before B7 HTTP."""
        require_disposable_hint_b_postgres(postgres18)
        baseline = assert_assignment_content_version_fk(ci03_g2_bootstrap_engine)
        prepared, _client, _attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        ghost_version = uuid.uuid7()
        assert_fk_rejects_coherent_assignment_version_pin(
            ci03_g2_bootstrap_engine,
            tenant_id=prepared.tenant_id,
            assignment_id=prepared.assignment_id,
            content_id=prepared.content_id,
            content_version_id=ghost_version,
        )
        assert_assignment_content_version_fk(ci03_g2_bootstrap_engine, baseline=baseline)

    def test_hint_b_b7_loader_missing_exact_content_version_fail_closed(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        """B7 governed loader fail-closed for missing exact version (HTTP 404 contract)."""
        require_disposable_hint_b_postgres(postgres18)
        baseline = assert_assignment_content_version_fk(ci03_g2_bootstrap_engine)
        prepared, _client, _attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        ghost_version = uuid.uuid7()
        with SqlAlchemyStudentLearningCommandUnitOfWork(
            ci03_g2_runtime_engine, prepared.tenant_id
        ) as uow:
            with pytest.raises(ExactContentVersionNotFound, match="not visible"):
                load_governed_worksheet_learner_hints(
                    uow,
                    execution_tenant_id=prepared.tenant_id,
                    content_id=prepared.content_id,
                    content_version_id=ghost_version,
                )
        status, code, _, _ = _LEARNING_PROBLEMS[ExactContentVersionNotFound]
        assert status == 404
        assert code == "content_version_not_found"
        assert_assignment_content_version_fk(ci03_g2_bootstrap_engine, baseline=baseline)

    def test_hint_b_frozen_fk_blocks_foreign_content_version_coherent_pin(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        """Foreign aggregate version pin is unreachable under frozen tenant/content/version FK."""
        require_disposable_hint_b_postgres(postgres18)
        baseline = assert_assignment_content_version_fk(ci03_g2_bootstrap_engine)
        first, _client_a, _attempt_a, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        second = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        assert_fk_rejects_coherent_assignment_version_pin(
            ci03_g2_bootstrap_engine,
            tenant_id=first.tenant_id,
            assignment_id=first.assignment_id,
            content_id=first.content_id,
            content_version_id=second.content_version_id,
        )
        assert_assignment_content_version_fk(ci03_g2_bootstrap_engine, baseline=baseline)

    def test_hint_b_b7_loader_foreign_content_version_fail_closed(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        """B7 loader denies version from a different Content aggregate (mismatched binding)."""
        require_disposable_hint_b_postgres(postgres18)
        baseline = assert_assignment_content_version_fk(ci03_g2_bootstrap_engine)
        first, _client_a, _attempt_a, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        second = prepare_governed_worksheet_v2_assignment(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, payload=_hinted_payload()
        )
        with SqlAlchemyStudentLearningCommandUnitOfWork(
            ci03_g2_runtime_engine, first.tenant_id
        ) as uow:
            with pytest.raises(ExactContentVersionNotFound, match="not visible"):
                load_governed_worksheet_learner_hints(
                    uow,
                    execution_tenant_id=first.tenant_id,
                    content_id=first.content_id,
                    content_version_id=second.content_version_id,
                )
        assert_assignment_content_version_fk(ci03_g2_bootstrap_engine, baseline=baseline)

    def test_hint_b_assignment_fk_survives_failure_path_probe(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        """Schema integrity restoration runs even when the negative-case body fails."""
        require_disposable_hint_b_postgres(postgres18)
        baseline = snapshot_assignment_content_version_fk(ci03_g2_bootstrap_engine)
        prepared, _client, _attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        ghost_version = uuid.uuid7()
        try:
            with guarded_hint_b_schema_integrity(ci03_g2_bootstrap_engine, baseline=baseline):
                assert_fk_rejects_coherent_assignment_version_pin(
                    ci03_g2_bootstrap_engine,
                    tenant_id=prepared.tenant_id,
                    assignment_id=prepared.assignment_id,
                    content_id=prepared.content_id,
                    content_version_id=ghost_version,
                )
                raise RuntimeError("PK-V1-HINT-B-I01R4 deliberate failure-path probe")
        except RuntimeError as exc:
            assert "deliberate failure-path probe" in str(exc)
        else:
            pytest.fail("expected deliberate failure-path probe")
        assert_assignment_content_version_fk(ci03_g2_bootstrap_engine, baseline=baseline)
        assert_fk_rejects_coherent_assignment_version_pin(
            ci03_g2_bootstrap_engine,
            tenant_id=prepared.tenant_id,
            assignment_id=prepared.assignment_id,
            content_id=prepared.content_id,
            content_version_id=ghost_version,
        )

    def test_hint_b_malformed_v2_payload_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        payload = _hinted_payload()
        payload["questions"][0]["learner_hint"] = 42
        corrupt_version_payload(
            ci03_g2_bootstrap_engine,
            version_id=prepared.content_version_id,
            payload=payload,
        )
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=422,
            code="content_not_learner_consumable",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    def test_hint_b_worksheet_schema_v3_unsupported(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        corrupt_version_schema(
            ci03_g2_bootstrap_engine,
            version_id=prepared.content_version_id,
            schema_version=3,
        )
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=422,
            code="content_not_learner_consumable",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )

    def test_hint_b_non_worksheet_content_type_denied(
        self, ci03_g2_runtime_engine, ci03_g2_bootstrap_engine, postgres18
    ) -> None:
        prepared, client, attempt_id, _rev, _etag = _start_governed_v2(
            ci03_g2_runtime_engine, ci03_g2_bootstrap_engine
        )
        corrupt_content_type(
            ci03_g2_bootstrap_engine,
            tenant_id=prepared.tenant_id,
            content_id=prepared.content_id,
            content_type="quiz",
        )
        denied = _get_hints(client, prepared, attempt_id)
        assert_problem(
            denied,
            status=422,
            code="content_not_learner_consumable",
            forbidden_substrings=(FIXED_T1B_HINT,),
        )
