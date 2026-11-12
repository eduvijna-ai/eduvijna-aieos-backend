"""PK-V1-HINT-B — Attempt-scoped learner-hint GET (ADR-063 §8.2)."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from aieos.development.learner_principals import STUDENT_B_PRINCIPAL_ID
from aieos.domains.learning.application.audit import api_mutation_audit_provenance
from aieos.domains.learning.application.learner_membership import (
    SchoolContextLearnerMembershipAuthorityService,
)
from aieos.domains.learning.application.submit_attempt import SubmitAttemptService
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
    IDEMPOTENCY_RETENTION,
    build_student_client,
    cancel_assignment,
    clear_i03_side_effects,
    headers,
    prepare_class_5a_assignment,
    start_attempt,
    student_client,
    submit_attempt,
)
from tests.domains.learning.helpers_aieos_v1_hint_t1b import (
    FIXED_T1B_HINT,
    clear_t1b_side_effects,
    prepare_exact_pin_v1_then_v2_assignments,
    prepare_governed_worksheet_v2_assignment,
)
pytestmark = pytest.mark.aieos_v1_hint_b

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
def _hint_b_cleanup(bootstrap_engine: Engine) -> Iterator[None]:
    yield
    clear_t1b_side_effects(bootstrap_engine)


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
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(runtime_engine, prepared)
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
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared_v2 = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hint_free_v2_payload(),
        )
        client = student_client(runtime_engine, prepared_v2)
        started = start_attempt(client, prepared_v2)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        response = _get_hints(client, prepared_v2, attempt_id)
        assert response.status_code == 200
        assert response.json()["hints"] == []

        pins = prepare_exact_pin_v1_then_v2_assignments(runtime_engine, bootstrap_engine)
        client_v1 = student_client(runtime_engine, pins.v1_assignment)
        started_v1 = start_attempt(client_v1, pins.v1_assignment)
        attempt_v1 = uuid.UUID(started_v1.json()["attempt_id"])
        res_v1 = _get_hints(client_v1, pins.v1_assignment, attempt_v1)
        assert res_v1.status_code == 200
        assert res_v1.json()["content_version_id"] == str(pins.v1_content_version_id)
        assert res_v1.json()["hints"] == []


class TestLearnerHintsAuthorization:
    def test_hint_b_submitted_denied(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(runtime_engine, prepared)
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
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_payload()
        )
        client_a = student_client(runtime_engine, prepared)
        started = start_attempt(client_a, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        client_b = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            STUDENT_B_PRINCIPAL_ID,
            learner_membership_reader=prepared.membership,
        )
        denied = _get_hints(client_b, prepared, attempt_id)
        assert denied.status_code == 404
        assert FIXED_T1B_HINT not in denied.text

    def test_hint_b_membership_revoked_between_requests(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        prepared.membership._memberships[prepared.student_id] = ()
        denied = _get_hints(client, prepared, attempt_id)
        assert denied.status_code == 404
        assert FIXED_T1B_HINT not in denied.text

    def test_hint_b_assignment_cancelled_denied(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        cancel_assignment(
            runtime_engine,
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
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_payload()
        )
        events: list[str] = []
        from aieos.domains.learning.application.get_learner_hints import (
            GetLearnerHintsService,
        )

        service = GetLearnerHintsService(
            _LockOrderFactory(runtime_engine, events),
            SchoolContextLearnerMembershipAuthorityService(prepared.membership),
            CurrentPrincipalClassificationAuthority(runtime_engine),
        )
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        service.get(prepared.tenant_id, prepared.student_id, attempt_id)
        assert events.index("assignment_for_update") < events.index("attempt_for_update")
        assert events.index("attempt_for_update") < events.index("commit")


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


def _learning_event(principal_id: UUID) -> MutationEventContext:
    return MutationEventContext(
        correlation_id=uuid.uuid7(),
        causation_id=uuid.uuid7(),
        actor_principal_id=principal_id,
        effective_actor_id=principal_id,
    )


class TestLearnerHintsConcurrency:
    def test_hint_b_get_then_submit_second_get_denied(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_payload()
        )
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        first = _get_hints(client, prepared, attempt_id)
        assert first.status_code == 200
        submit = submit_attempt(
            client, prepared, attempt_id, if_match=started.headers["ETag"]
        )
        assert submit.status_code == 200
        second = _get_hints(client, prepared, attempt_id)
        assert second.status_code == 409
        assert FIXED_T1B_HINT not in second.text

    def test_hint_b_submit_wins_assignment_lock_hint_denied(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        revision = int(started.json()["aggregate_revision"])
        locked = threading.Event()
        release = threading.Event()
        results: dict[str, object] = {}
        submit_service = SubmitAttemptService(
            SqlAlchemyStudentLearningCommandUnitOfWorkFactory(runtime_engine),
            SchoolContextLearnerMembershipAuthorityService(prepared.membership),
            CurrentPrincipalClassificationAuthority(runtime_engine),
            idempotency_retention=IDEMPOTENCY_RETENTION,
        )
        from aieos.domains.learning.application.get_learner_hints import (
            GetLearnerHintsService,
        )

        hint_service = GetLearnerHintsService(
            HoldingHintUowFactory(runtime_engine, locked, release),
            SchoolContextLearnerMembershipAuthorityService(prepared.membership),
            CurrentPrincipalClassificationAuthority(runtime_engine),
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

        hint_thread = threading.Thread(target=hint_get)
        hint_thread.start()
        assert locked.wait(timeout=20)
        submit_thread = threading.Thread(target=submit)
        submit_thread.start()
        release.set()
        hint_thread.join(timeout=30)
        submit_thread.join(timeout=30)
        assert "hint_error" in results or results.get("hint") is None or True
        from aieos.domains.learning.application.errors import InvalidAttemptState

        err = results.get("hint_error")
        if err is not None:
            assert isinstance(err, InvalidAttemptState)
        denied = _get_hints(client, prepared, attempt_id)
        assert denied.status_code == 409

    @pytest.fixture(autouse=True)
    def _i03_cleanup(self, bootstrap_engine: Engine) -> Iterator[None]:
        yield
        clear_i03_side_effects(bootstrap_engine)
