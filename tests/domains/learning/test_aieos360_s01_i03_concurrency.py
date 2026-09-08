"""AIEOS360-S01-I03 — TeachingAssignment lock races (I03-73..I03-76)."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest

from aieos.domains.learning.application.audit import api_mutation_audit_provenance
from aieos.domains.learning.application.errors import AssignmentClosedOrCancelled
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthorityService,
)
from aieos.domains.learning.application.submit_attempt import SubmitAttemptService
from aieos.domains.teaching.application.assignment_mutations import (
    CloseTeachingAssignmentService,
    UpdateTeachingAssignmentDueService,
)
from aieos.domains.teaching.application.audit import (
    api_mutation_audit_provenance as teaching_audit,
)
from aieos.domains.teaching.application.models import UpdateTeachingAssignmentDueCommand
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
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    IDEMPOTENCY_RETENTION,
    PAST_DUE,
    clear_i03_side_effects_after_test,
    fetch_submissions,
    prepare_class_5a_assignment,
    start_attempt,
    student_client,
)
from tests.domains.teaching.helpers_dev06_i03 import event_context

pytestmark = pytest.mark.aieos360_s01_i03

NEW_DUE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


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


class _HoldingTeachingProxy:
    def __init__(self, inner, locked: threading.Event, release: threading.Event):
        self._inner = inner
        self._locked = locked
        self._release = release

    def __enter__(self):
        uow = self._inner.__enter__()
        real = uow.assignments.get_for_update

        def wrapped(assignment_id):
            loaded = real(assignment_id)
            if loaded is not None:
                self._locked.set()
                if not self._release.wait(timeout=20):
                    raise TimeoutError("teacher assignment lock was not released")
            return loaded

        uow.assignments.get_for_update = wrapped  # type: ignore[method-assign]
        return uow

    def __exit__(self, exc_type, exc, tb):
        return self._inner.__exit__(exc_type, exc, tb)


class HoldingTeachingUowFactory:
    def __init__(self, engine, locked: threading.Event, release: threading.Event):
        self._inner = SqlAlchemyTeachingUnitOfWorkFactory(engine)
        self._locked = locked
        self._release = release

    def __call__(self, execution_tenant_id):
        return _HoldingTeachingProxy(
            self._inner(execution_tenant_id), self._locked, self._release
        )


def _learning_event(principal_id):
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


def _submit_service(factory, prepared, runtime_engine) -> SubmitAttemptService:
    return SubmitAttemptService(
        factory,
        SchoolContextLearnerMembershipAuthorityService(prepared.membership),
        CurrentPrincipalClassificationAuthority(runtime_engine),
        idempotency_retention=IDEMPOTENCY_RETENTION,
    )


def _start_in_progress(runtime_engine, bootstrap_engine, **kwargs):
    prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine, **kwargs)
    client = student_client(runtime_engine, prepared)
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    return prepared, uuid.UUID(started.json()["attempt_id"]), int(
        started.json()["aggregate_revision"]
    )


class TestAssignmentLockRaces:
    def test_i03_73_close_wins_assignment_lock_first_submit_fails(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, attempt_id, revision = _start_in_progress(
            runtime_engine, bootstrap_engine
        )
        locked = threading.Event()
        release = threading.Event()
        student_started = threading.Event()
        results: dict[str, object] = {}
        close_service = CloseTeachingAssignmentService(
            HoldingTeachingUowFactory(runtime_engine, locked, release),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        submit_service = _submit_service(
            SqlAlchemyStudentLearningCommandUnitOfWorkFactory(runtime_engine),
            prepared,
            runtime_engine,
        )

        def close() -> None:
            try:
                results["close"] = close_service.close(
                    prepared.tenant_id,
                    prepared.teacher_id,
                    assignment_id=prepared.assignment.assignment_id,
                    expected_aggregate_revision=prepared.assignment.aggregate_revision,
                    idempotency_key=f"close-{uuid.uuid7()}",
                    event_context=event_context(prepared.teacher_id),
                    audit_provenance=teaching_audit(prepared.teacher_id),
                )
            except BaseException as exc:  # noqa: BLE001
                results["close_error"] = exc

        def submit() -> None:
            student_started.set()
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

        teacher = threading.Thread(target=close)
        teacher.start()
        assert locked.wait(timeout=20)
        student = threading.Thread(target=submit)
        student.start()
        assert student_started.wait(timeout=20)
        release.set()
        teacher.join(timeout=20)
        student.join(timeout=20)
        assert "close" in results
        assert isinstance(results.get("submit_error"), AssignmentClosedOrCancelled)
        assert (
            fetch_submissions(
                bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
            )
            == []
        )

    def test_i03_74_submit_wins_assignment_lock_first_close_cannot_erase(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, attempt_id, revision = _start_in_progress(
            runtime_engine, bootstrap_engine
        )
        locked = threading.Event()
        release = threading.Event()
        teacher_started = threading.Event()
        results: dict[str, object] = {}
        submit_service = _submit_service(
            HoldingStudentUowFactory(runtime_engine, locked, release),
            prepared,
            runtime_engine,
        )
        close_service = CloseTeachingAssignmentService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            idempotency_retention=IDEMPOTENCY_RETENTION,
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

        def close() -> None:
            teacher_started.set()
            try:
                results["close"] = close_service.close(
                    prepared.tenant_id,
                    prepared.teacher_id,
                    assignment_id=prepared.assignment.assignment_id,
                    expected_aggregate_revision=prepared.assignment.aggregate_revision,
                    idempotency_key=f"close-{uuid.uuid7()}",
                    event_context=event_context(prepared.teacher_id),
                    audit_provenance=teaching_audit(prepared.teacher_id),
                )
            except BaseException as exc:  # noqa: BLE001
                results["close_error"] = exc

        student = threading.Thread(target=submit)
        student.start()
        assert locked.wait(timeout=20)
        teacher = threading.Thread(target=close)
        teacher.start()
        assert teacher_started.wait(timeout=20)
        release.set()
        student.join(timeout=20)
        teacher.join(timeout=20)
        assert "submit" in results
        assert "close" in results
        rows = fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )
        assert len(rows) == 1

    def test_i03_75_due_update_wins_first_submit_snapshots_new_due(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, attempt_id, revision = _start_in_progress(
            runtime_engine, bootstrap_engine, due_at=PAST_DUE
        )
        locked = threading.Event()
        release = threading.Event()
        student_started = threading.Event()
        results: dict[str, object] = {}
        due_service = UpdateTeachingAssignmentDueService(
            HoldingTeachingUowFactory(runtime_engine, locked, release),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        submit_service = _submit_service(
            SqlAlchemyStudentLearningCommandUnitOfWorkFactory(runtime_engine),
            prepared,
            runtime_engine,
        )

        def update_due() -> None:
            try:
                results["due"] = due_service.update_due(
                    prepared.tenant_id,
                    prepared.teacher_id,
                    assignment_id=prepared.assignment.assignment_id,
                    expected_aggregate_revision=prepared.assignment.aggregate_revision,
                    command=UpdateTeachingAssignmentDueCommand(due_at=NEW_DUE),
                    idempotency_key=f"due-{uuid.uuid7()}",
                    event_context=event_context(prepared.teacher_id),
                    audit_provenance=teaching_audit(prepared.teacher_id),
                )
            except BaseException as exc:  # noqa: BLE001
                results["due_error"] = exc

        def submit() -> None:
            student_started.set()
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

        teacher = threading.Thread(target=update_due)
        teacher.start()
        assert locked.wait(timeout=20)
        student = threading.Thread(target=submit)
        student.start()
        assert student_started.wait(timeout=20)
        release.set()
        teacher.join(timeout=20)
        student.join(timeout=20)
        assert "due" in results
        assert "submit" in results
        row = fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )[0]
        due_model = results["due"]
        assert int(row["assignment_revision_at_submit"]) == int(
            due_model.aggregate_revision
        )
        assert row["due_at_at_submit"] == due_model.due_at

    def test_i03_76_submit_wins_first_later_due_update_does_not_rewrite_snapshot(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        prepared, attempt_id, revision = _start_in_progress(
            runtime_engine, bootstrap_engine, due_at=PAST_DUE
        )
        locked = threading.Event()
        release = threading.Event()
        teacher_started = threading.Event()
        results: dict[str, object] = {}
        submit_service = _submit_service(
            HoldingStudentUowFactory(runtime_engine, locked, release),
            prepared,
            runtime_engine,
        )
        due_service = UpdateTeachingAssignmentDueService(
            SqlAlchemyTeachingUnitOfWorkFactory(runtime_engine),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        original_due = prepared.assignment.due_at
        original_revision = int(prepared.assignment.aggregate_revision)

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

        def update_due() -> None:
            teacher_started.set()
            try:
                results["due"] = due_service.update_due(
                    prepared.tenant_id,
                    prepared.teacher_id,
                    assignment_id=prepared.assignment.assignment_id,
                    expected_aggregate_revision=prepared.assignment.aggregate_revision,
                    command=UpdateTeachingAssignmentDueCommand(due_at=NEW_DUE),
                    idempotency_key=f"due-{uuid.uuid7()}",
                    event_context=event_context(prepared.teacher_id),
                    audit_provenance=teaching_audit(prepared.teacher_id),
                )
            except BaseException as exc:  # noqa: BLE001
                results["due_error"] = exc

        student = threading.Thread(target=submit)
        student.start()
        assert locked.wait(timeout=20)
        teacher = threading.Thread(target=update_due)
        teacher.start()
        assert teacher_started.wait(timeout=20)
        release.set()
        student.join(timeout=20)
        teacher.join(timeout=20)
        assert "submit" in results
        assert "due" in results
        row = fetch_submissions(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )[0]
        assert int(row["assignment_revision_at_submit"]) == original_revision
        assert row["due_at_at_submit"] == original_due
        assert results["due"].due_at == NEW_DUE
        assert int(results["due"].aggregate_revision) != original_revision
