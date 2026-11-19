"""PK-V1-HINT-B-I01R2 test-only instrumentation and fixtures."""

from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from aieos.development.learner_principals import CLASS_REF_5A, STUDENT_A_PRINCIPAL_ID
from aieos.domains.content.application.audit import api_mutation_audit_provenance
from aieos.domains.content.domain.identities import ContentId, ContentVersionId
from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.education.schema import WORKSHEET_SCHEMA_VERSION_V2
from aieos.platform.runtime.student_learning_command import (
    SqlAlchemyStudentLearningCommandUnitOfWork,
)
from tests.domains.education.test_aieos_v1_hint_t1 import _worksheet_v2_payload
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    FIXED_NOW,
    _event_context,
    _guarded_service,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    PreparedAssignment,
    create_learner_assignment,
    default_membership_reader,
)
from tests.domains.learning.helpers_aieos_v1_hint_t1b import (
    FIXED_T1B_HINT,
    _complete_v2_publish,
    _current_content_revision,
    assert_publication_evidence,
    publish_worksheet_v2_three_human,
)

B8_EVENT_ORDER = (
    "phase-A-preview",
    "phase-B-B1-read",
    "assignment-FOR-UPDATE",
    "attempt-FOR-UPDATE",
    "B7-governed-hint-extraction",
    "B8-new-attempt-get",
    "B9-commit",
)


class EventOrderStudentUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    def __init__(self, engine, tenant_id: UUID, events: list[str], txn_index: int) -> None:
        super().__init__(engine, tenant_id)
        self._events = events
        self._txn_index = txn_index
        self._b7_recorded = False
        self._attempt_get_count = 0

    def __enter__(self):
        uow = super().__enter__()
        real_get = uow.attempts.get
        real_get_for_update = uow.attempts.get_for_update
        real_load = uow.load_governed_worksheet_learner_hints

        def wrapped_get(attempt_id):
            self._attempt_get_count += 1
            if self._txn_index == 1 and self._attempt_get_count == 1:
                self._events.append("phase-A-preview")
            elif self._txn_index == 2:
                if self._attempt_get_count == 1:
                    self._events.append("phase-B-B1-read")
                elif self._b7_recorded:
                    self._events.append("B8-new-attempt-get")
            return real_get(attempt_id)

        def wrapped_get_for_update(attempt_id):
            self._events.append("attempt-FOR-UPDATE")
            return real_get_for_update(attempt_id)

        def wrapped_load(content_id, content_version_id):
            self._events.append("B7-governed-hint-extraction")
            self._b7_recorded = True
            return real_load(content_id, content_version_id)

        uow.attempts.get = wrapped_get  # type: ignore[method-assign]
        uow.attempts.get_for_update = wrapped_get_for_update  # type: ignore[method-assign]
        uow.load_governed_worksheet_learner_hints = wrapped_load  # type: ignore[method-assign]
        return uow

    def get_assignment_for_update(self, assignment_id):
        if self._txn_index == 2:
            self._events.append("assignment-FOR-UPDATE")
        return super().get_assignment_for_update(assignment_id)

    def commit(self) -> None:
        if self._txn_index == 2:
            self._events.append("B9-commit")
        super().commit()


class EventOrderStudentUowFactory:
    def __init__(self, engine: Engine, events: list[str]) -> None:
        self._engine = engine
        self._events = events
        self._txn = 0

    def __call__(self, execution_tenant_id: UUID):
        self._txn += 1
        return EventOrderStudentUow(
            self._engine, execution_tenant_id, self._events, self._txn
        )


class SubmitHoldingStudentUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    """Hold assignment+attempt FOR UPDATE until release (submit-first races)."""

    def __init__(
        self,
        engine,
        tenant_id: UUID,
        *,
        assignment_locked: threading.Event,
        both_locks_held: threading.Event,
        holder_pid: list[int | None],
        release: threading.Event,
    ) -> None:
        super().__init__(engine, tenant_id)
        self._assignment_locked = assignment_locked
        self._both_locks_held = both_locks_held
        self._holder_pid = holder_pid
        self._release = release
        self._assignment_done = False

    def __enter__(self):
        uow = super().__enter__()
        real_attempt_update = uow.attempts.get_for_update

        def wrapped_attempt_update(attempt_id):
            loaded = real_attempt_update(attempt_id)
            if loaded is not None and self._assignment_done:
                self._holder_pid[0] = self.connection.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                self._assignment_locked.set()
                self._both_locks_held.set()
                if not self._release.wait(timeout=25):
                    raise TimeoutError("submit locks were not released")
            return loaded

        uow.attempts.get_for_update = wrapped_attempt_update  # type: ignore[method-assign]
        return uow

    def get_assignment_for_update(self, assignment_id):
        loaded = super().get_assignment_for_update(assignment_id)
        if loaded is not None:
            self._assignment_done = True
        return loaded


class SubmitHoldingStudentUowFactory:
    def __init__(
        self,
        engine: Engine,
        *,
        assignment_locked: threading.Event,
        both_locks_held: threading.Event,
        holder_pid: list[int | None],
        release: threading.Event,
    ) -> None:
        self._engine = engine
        self._assignment_locked = assignment_locked
        self._both_locks_held = both_locks_held
        self._holder_pid = holder_pid
        self._release = release

    def __call__(self, execution_tenant_id: UUID):
        return SubmitHoldingStudentUow(
            self._engine,
            execution_tenant_id,
            assignment_locked=self._assignment_locked,
            both_locks_held=self._both_locks_held,
            holder_pid=self._holder_pid,
            release=self._release,
        )


class ContentionHintUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    def __init__(
        self,
        engine,
        tenant_id: UUID,
        *,
        contending_on_assignment: threading.Event,
        waiter_pid: list[int | None],
    ) -> None:
        super().__init__(engine, tenant_id)
        self._contending = contending_on_assignment
        self._waiter_pid = waiter_pid

    def get_assignment_for_update(self, assignment_id):
        self._waiter_pid[0] = self.connection.execute(
            text("SELECT pg_backend_pid()")
        ).scalar_one()
        self._contending.set()
        return super().get_assignment_for_update(assignment_id)


class ContentionHintUowFactory:
    def __init__(
        self,
        engine: Engine,
        *,
        contending_on_assignment: threading.Event,
        waiter_pid: list[int | None],
    ) -> None:
        self._engine = engine
        self._contending = contending_on_assignment
        self._waiter_pid = waiter_pid

    def __call__(self, execution_tenant_id: UUID):
        return ContentionHintUow(
            self._engine,
            execution_tenant_id,
            contending_on_assignment=self._contending,
            waiter_pid=self._waiter_pid,
        )


class HintHoldingStudentUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    """Hold assignment + attempt locks during hint Phase B (hint-first races)."""

    def __init__(
        self,
        engine,
        tenant_id: UUID,
        *,
        both_locks_held: threading.Event,
        holder_pid: list[int | None],
        release: threading.Event,
    ) -> None:
        super().__init__(engine, tenant_id)
        self._both_locks_held = both_locks_held
        self._holder_pid = holder_pid
        self._release = release
        self._assignment_done = False

    def __enter__(self):
        uow = super().__enter__()
        real_attempt_update = uow.attempts.get_for_update

        def wrapped_attempt_update(attempt_id):
            loaded = real_attempt_update(attempt_id)
            if loaded is not None and self._assignment_done:
                self._holder_pid[0] = self.connection.execute(
                    text("SELECT pg_backend_pid()")
                ).scalar_one()
                self._both_locks_held.set()
                if not self._release.wait(timeout=25):
                    raise TimeoutError("hint lock holder was not released")
            return loaded

        uow.attempts.get_for_update = wrapped_attempt_update  # type: ignore[method-assign]
        return uow

    def get_assignment_for_update(self, assignment_id):
        loaded = super().get_assignment_for_update(assignment_id)
        if loaded is not None:
            self._assignment_done = True
        return loaded


class HintHoldingStudentUowFactory:
    def __init__(
        self,
        engine: Engine,
        *,
        both_locks_held: threading.Event,
        holder_pid: list[int | None],
        release: threading.Event,
    ) -> None:
        self._engine = engine
        self._both_locks_held = both_locks_held
        self._holder_pid = holder_pid
        self._release = release

    def __call__(self, execution_tenant_id: UUID):
        return HintHoldingStudentUow(
            self._engine,
            execution_tenant_id,
            both_locks_held=self._both_locks_held,
            holder_pid=self._holder_pid,
            release=self._release,
        )


class ContentionSubmitUow(SqlAlchemyStudentLearningCommandUnitOfWork):
    def __init__(
        self,
        engine,
        tenant_id: UUID,
        *,
        contending_on_assignment: threading.Event,
        waiter_pid: list[int | None],
    ) -> None:
        super().__init__(engine, tenant_id)
        self._contending = contending_on_assignment
        self._waiter_pid = waiter_pid

    def get_assignment_for_update(self, assignment_id):
        self._waiter_pid[0] = self.connection.execute(
            text("SELECT pg_backend_pid()")
        ).scalar_one()
        self._contending.set()
        return super().get_assignment_for_update(assignment_id)


class ContentionSubmitUowFactory:
    def __init__(
        self,
        engine: Engine,
        *,
        contending_on_assignment: threading.Event,
        waiter_pid: list[int | None],
    ) -> None:
        self._engine = engine
        self._contending = contending_on_assignment
        self._waiter_pid = waiter_pid

    def __call__(self, execution_tenant_id: UUID):
        return ContentionSubmitUow(
            self._engine,
            execution_tenant_id,
            contending_on_assignment=self._contending,
            waiter_pid=self._waiter_pid,
        )


def wait_pid_blocked_by(
    observer_engine: Engine,
    *,
    waiter_pid: int,
    holder_pid: int,
    deadline_s: float = 12.0,
) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        with observer_engine.connect() as conn:
            blockers = conn.execute(
                text("SELECT pg_blocking_pids(:pid)"),
                {"pid": waiter_pid},
            ).scalar_one()
        if blockers and holder_pid in list(blockers):
            return True
        time.sleep(0.02)
    return False


def assert_genuine_lock_contention(
    observer_engine: Engine,
    *,
    waiter_thread: threading.Thread,
    waiter_pid: int | None,
    holder_pid: int | None,
    locks_held: threading.Event,
    release: threading.Event,
) -> None:
    assert holder_pid is not None, "holder backend PID was not recorded"
    assert waiter_pid is not None, "waiter backend PID was not recorded"
    assert waiter_pid != holder_pid, "holder and waiter must use distinct DB connections"
    assert locks_held.is_set(), "holder did not acquire expected locks"
    assert not release.is_set(), "holder released before contention check"
    assert waiter_thread.is_alive(), "waiter finished before contention observation"
    blocked = wait_pid_blocked_by(
        observer_engine,
        waiter_pid=waiter_pid,
        holder_pid=holder_pid,
        deadline_s=12.0,
    )
    assert blocked, (
        f"pg_blocking_pids({waiter_pid}) did not list holder {holder_pid} "
        "while holder still holds the row lock"
    )


_DEFAULT_FORBIDDEN = (
    "answer",
    "explanation",
    "teacher_notes",
    "teacher_summary",
    '"payload"',
    "evaluation",
    "Consider the denominator.",
    "Compare to one half.",
)


def assert_problem(
    response,
    *,
    status: int,
    code: str,
    no_store: str = "private, no-store",
    forbidden_substrings: tuple[str, ...] = (),
) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers.get("Cache-Control") == no_store
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith(
        "application/problem+json"
    ), f"unexpected content-type: {content_type!r}"
    body = response.json()
    assert isinstance(body, dict)
    assert body.get("status") == status
    assert body.get("code") == code
    assert isinstance(body.get("type"), str) and body["type"]
    assert isinstance(body.get("title"), str) and body["title"]
    lowered = response.text.lower()
    assert "hint_text" not in lowered
    assert "learner_hint" not in lowered
    for snippet in (*_DEFAULT_FORBIDDEN, *forbidden_substrings):
        assert snippet not in response.text, (
            f"problem response leaked forbidden fragment: {snippet!r}"
        )
    return body


@dataclass(frozen=True, slots=True)
class SuccessiveV2Pins:
    first_assignment: PreparedAssignment
    second_assignment: PreparedAssignment
    first_content_version_id: UUID
    second_content_version_id: UUID
    first_hint_text: str
    second_hint_text: str
    content_id: UUID


@dataclass(frozen=True, slots=True)
class SuccessiveV2Context:
    """First published @2 assignment before a successor publication on same Content."""

    first_assignment: PreparedAssignment
    first_content_version_id: UUID
    first_hint_text: str
    second_hint_text: str
    content_id: UUID
    tenant_id: UUID
    author_id: UUID
    reviewer_id: UUID
    publisher_id: UUID


def begin_successive_v2_same_content(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    first_hint: str = FIXED_T1B_HINT,
    second_hint: str = "Use the least common denominator carefully.",
) -> SuccessiveV2Context:
    first_payload = _worksheet_v2_payload(hint=first_hint)
    publication = publish_worksheet_v2_three_human(
        runtime_engine, bootstrap_engine, payload=first_payload
    )
    tenant_id = publication.tenant_id
    author_id = publication.author_id
    reviewer_id = publication.reviewer_id
    publisher_id = publication.publisher_id
    first_version_id = ContentVersionId(publication.content_version_id)

    first_assignment = create_learner_assignment(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=author_id,
        content_id=publication.content_id,
        content_version_id=publication.content_version_id,
        idempotency_key=f"r2-v2a-assign-{uuid.uuid7()}",
        class_ref=CLASS_REF_5A,
    )
    reader = default_membership_reader(tenant_id)
    prepared_first = PreparedAssignment(
        tenant_id=tenant_id,
        teacher_id=author_id,
        student_id=STUDENT_A_PRINCIPAL_ID,
        content_id=publication.content_id,
        content_version_id=publication.content_version_id,
        assignment=first_assignment,
        membership=reader,
    )
    assert_publication_evidence(
        bootstrap_engine,
        tenant_id=tenant_id,
        content_id=publication.content_id,
        version_id=first_version_id.value,
        author_id=author_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
        expect_attestation=True,
    )
    return SuccessiveV2Context(
        first_assignment=prepared_first,
        first_content_version_id=first_version_id.value,
        first_hint_text=first_hint,
        second_hint_text=second_hint,
        content_id=publication.content_id,
        tenant_id=tenant_id,
        author_id=author_id,
        reviewer_id=reviewer_id,
        publisher_id=publisher_id,
    )


def complete_successive_v2_second_publication(
    ctx: SuccessiveV2Context,
    runtime_engine: Engine,
    bootstrap_engine: Engine,
) -> SuccessiveV2Pins:
    content_id = ContentId(ctx.content_id)
    second_payload = _worksheet_v2_payload(hint=ctx.second_hint_text)
    expected_rev = _current_content_revision(
        bootstrap_engine, tenant_id=ctx.tenant_id, content_id=ctx.content_id
    )
    version_read, revision = _guarded_service(runtime_engine).append(
        ctx.tenant_id,
        ctx.author_id,
        content_id=content_id,
        expected_aggregate_revision=expected_rev,
        payload=second_payload,
        authoring_class_ref=CLASS_REF_5A,
        idempotency_key=f"r2-v2b-append-{uuid.uuid7()}",
        event_context=_event_context(ctx.author_id),
        audit_provenance=api_mutation_audit_provenance(ctx.author_id),
        now=FIXED_NOW,
    )
    assert int(version_read.version_number) >= 3
    with bootstrap_engine.connect() as conn:
        schema_v = conn.execute(
            text(
                """
                SELECT schema_version, version_number
                FROM content.content_versions
                WHERE tenant_id = :tid AND version_id = :vid
                """
            ),
            {"tid": ctx.tenant_id, "vid": version_read.version_id.value},
        ).one()
    assert int(schema_v.version_number) >= 3
    assert int(schema_v.schema_version) == WORKSHEET_SCHEMA_VERSION_V2
    second_version_id = _complete_v2_publish(
        runtime_engine,
        bootstrap_engine,
        tenant_id=ctx.tenant_id,
        author_id=ctx.author_id,
        reviewer_id=ctx.reviewer_id,
        publisher_id=ctx.publisher_id,
        content_id=content_id,
        version_read=version_read,
        revision=revision,
        class_ref=CLASS_REF_5A,
        require_attestation=True,
    )
    assert_publication_evidence(
        bootstrap_engine,
        tenant_id=ctx.tenant_id,
        content_id=ctx.content_id,
        version_id=ctx.first_content_version_id,
        author_id=ctx.author_id,
        reviewer_id=ctx.reviewer_id,
        publisher_id=ctx.publisher_id,
        expect_attestation=True,
    )
    assert_publication_evidence(
        bootstrap_engine,
        tenant_id=ctx.tenant_id,
        content_id=ctx.content_id,
        version_id=second_version_id.value,
        author_id=ctx.author_id,
        reviewer_id=ctx.reviewer_id,
        publisher_id=ctx.publisher_id,
        expect_attestation=True,
    )

    reader = default_membership_reader(ctx.tenant_id)
    second_assignment = create_learner_assignment(
        runtime_engine,
        tenant_id=ctx.tenant_id,
        principal_id=ctx.author_id,
        content_id=ctx.content_id,
        content_version_id=second_version_id.value,
        idempotency_key=f"r2-v2b-assign-{uuid.uuid7()}",
        class_ref=CLASS_REF_5A,
    )
    prepared_second = PreparedAssignment(
        tenant_id=ctx.tenant_id,
        teacher_id=ctx.author_id,
        student_id=STUDENT_A_PRINCIPAL_ID,
        content_id=ctx.content_id,
        content_version_id=second_version_id.value,
        assignment=second_assignment,
        membership=reader,
    )
    return SuccessiveV2Pins(
        first_assignment=ctx.first_assignment,
        second_assignment=prepared_second,
        first_content_version_id=ctx.first_content_version_id,
        second_content_version_id=second_version_id.value,
        first_hint_text=ctx.first_hint_text,
        second_hint_text=ctx.second_hint_text,
        content_id=ctx.content_id,
    )


def prepare_successive_published_v2_on_same_content(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    *,
    first_hint: str = FIXED_T1B_HINT,
    second_hint: str = "Use the least common denominator carefully.",
) -> SuccessiveV2Pins:
    ctx = begin_successive_v2_same_content(
        runtime_engine,
        bootstrap_engine,
        first_hint=first_hint,
        second_hint=second_hint,
    )
    return complete_successive_v2_second_publication(
        ctx, runtime_engine, bootstrap_engine
    )


def delete_hint_attestation(
    bootstrap_engine: Engine, *, tenant_id: UUID, version_id: UUID
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                "DISABLE TRIGGER worksheet_hint_attestations_immutable_update"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                "DISABLE TRIGGER worksheet_hint_attestations_immutable_delete"
            )
        )
        conn.execute(
            text(
                """
                DELETE FROM content.worksheet_learner_hint_version_attestations
                WHERE tenant_id = :tid AND version_id = :vid
                """
            ),
            {"tid": tenant_id, "vid": version_id},
        )
        conn.execute(
            text(
                "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                "ENABLE TRIGGER worksheet_hint_attestations_immutable_update"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE content.worksheet_learner_hint_version_attestations "
                "ENABLE TRIGGER worksheet_hint_attestations_immutable_delete"
            )
        )


_ASSIGNMENT_CONTENT_VERSION_FK = "fk_teaching_assignments_content_version"
def _compact_sql(fragment: str) -> str:
    return "".join(fragment.lower().split())


_CANONICAL_FK_COMPACT_MARKERS = (
    _compact_sql(
        "FOREIGN KEY (tenant_id, content_id, content_version_id) "
        "REFERENCES content.content_versions (tenant_id, content_id, version_id)"
    ),
    _compact_sql("ON DELETE RESTRICT"),
)


@dataclass(frozen=True, slots=True)
class AssignmentContentVersionFkSnapshot:
    conname: str
    convalidated: bool
    confdeltype: str
    definition: str


def require_disposable_hint_b_postgres(postgres18: dict[str, str]) -> None:
    """Refuse schema-sensitive B7 probes against user-supplied external databases."""
    if os.environ.get("AIEOS_TEST_DATABASE_URL"):
        raise RuntimeError(
            "Hint-B schema integrity probes require agent-owned disposable PostgreSQL 18 "
            "(unset AIEOS_TEST_DATABASE_URL)."
        )
    if not str(postgres18.get("server_version", "")).startswith("18."):
        raise RuntimeError("PostgreSQL 18 required for Hint-B acceptance")


def snapshot_assignment_content_version_fk(engine: Engine) -> AssignmentContentVersionFkSnapshot:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT c.conname,
                       c.convalidated,
                       c.confdeltype,
                       pg_get_constraintdef(c.oid) AS definition
                  FROM pg_constraint c
                  JOIN pg_class cl ON cl.oid = c.conrelid
                  JOIN pg_namespace n ON n.oid = cl.relnamespace
                 WHERE n.nspname = 'teaching'
                   AND cl.relname = 'assignments'
                   AND c.conname = :cname
                   AND c.contype = 'f'
                """
            ),
            {"cname": _ASSIGNMENT_CONTENT_VERSION_FK},
        ).one()
    return AssignmentContentVersionFkSnapshot(
        conname=str(row.conname),
        convalidated=bool(row.convalidated),
        confdeltype=str(row.confdeltype),
        definition=str(row.definition),
    )


def assert_assignment_content_version_fk(
    engine: Engine,
    *,
    baseline: AssignmentContentVersionFkSnapshot | None = None,
) -> AssignmentContentVersionFkSnapshot:
    snap = snapshot_assignment_content_version_fk(engine)
    if snap.conname != _ASSIGNMENT_CONTENT_VERSION_FK:
        raise AssertionError(f"unexpected FK name: {snap.conname}")
    if not snap.convalidated:
        raise AssertionError(f"{_ASSIGNMENT_CONTENT_VERSION_FK} is not validated")
    if snap.confdeltype != "r":
        raise AssertionError(
            f"{_ASSIGNMENT_CONTENT_VERSION_FK} ON DELETE must be RESTRICT"
        )
    compact = _compact_sql(snap.definition)
    for marker in _CANONICAL_FK_COMPACT_MARKERS:
        if marker not in compact:
            raise AssertionError(
                f"{_ASSIGNMENT_CONTENT_VERSION_FK} definition missing canonical "
                f"fragment {marker!r}: {snap.definition}"
            )
    if baseline is not None:
        if snap.definition != baseline.definition:
            raise AssertionError(
                "assignment content-version FK definition drifted: "
                f"expected {baseline.definition!r}, got {snap.definition!r}"
            )
        if snap.convalidated != baseline.convalidated:
            raise AssertionError("assignment content-version FK convalidated drifted")
    return snap


def assert_fk_rejects_coherent_assignment_version_pin(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    assignment_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
) -> None:
    try:
        with bootstrap_engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE teaching.assignments
                       SET content_version_id = :cvid
                     WHERE tenant_id = :tid
                       AND assignment_id = :asgid
                       AND content_id = :cid
                    """
                ),
                {
                    "cvid": content_version_id,
                    "tid": tenant_id,
                    "asgid": assignment_id,
                    "cid": content_id,
                },
            )
    except IntegrityError:
        return
    raise AssertionError(
        "fk_teaching_assignments_content_version must reject invalid "
        f"(tenant_id, content_id, content_version_id)=({tenant_id}, {content_id}, "
        f"{content_version_id})"
    )


@contextmanager
def guarded_hint_b_schema_integrity(
    bootstrap_engine: Engine,
    *,
    baseline: AssignmentContentVersionFkSnapshot,
) -> Iterator[None]:
    try:
        yield
    finally:
        assert_assignment_content_version_fk(bootstrap_engine, baseline=baseline)


def corrupt_version_schema(
    bootstrap_engine: Engine,
    *,
    version_id: UUID,
    schema_version: int,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE content.content_versions DISABLE TRIGGER "
                "content_versions_immutable_update"
            )
        )
        conn.execute(
            text(
                """
                UPDATE content.content_versions
                SET schema_version = :sv
                WHERE version_id = :vid
                """
            ),
            {"sv": schema_version, "vid": version_id},
        )
        conn.execute(
            text(
                "ALTER TABLE content.content_versions ENABLE TRIGGER "
                "content_versions_immutable_update"
            )
        )


def corrupt_version_payload(
    bootstrap_engine: Engine,
    *,
    version_id: UUID,
    payload: dict[str, Any],
) -> None:
    typed = ContentPayload.from_mapping(payload)
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE content.content_versions DISABLE TRIGGER "
                "content_versions_immutable_update"
            )
        )
        conn.execute(
            text(
                """
                UPDATE content.content_versions
                SET payload = CAST(:payload AS jsonb),
                    payload_sha256 = :sha
                WHERE version_id = :vid
                """
            ),
            {
                "vid": version_id,
                "payload": canonical_payload_json(typed.body),
                "sha": typed.sha256.value,
            },
        )
        conn.execute(
            text(
                "ALTER TABLE content.content_versions ENABLE TRIGGER "
                "content_versions_immutable_update"
            )
        )


def corrupt_content_type(
    bootstrap_engine: Engine,
    *,
    tenant_id: UUID,
    content_id: UUID,
    content_type: str,
) -> None:
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE content.contents
                SET content_type = :ctype
                WHERE tenant_id = :tid AND content_id = :cid
                """
            ),
            {"ctype": content_type, "tid": tenant_id, "cid": content_id},
        )