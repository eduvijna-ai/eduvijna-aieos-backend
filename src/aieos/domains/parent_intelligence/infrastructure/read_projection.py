"""Read-only SQL adapter for Parent Intelligence derived facts.

Queries existing authoritative schemas. Never mutates. Never commits.
Receives only learner IDs that already passed Parent Learner Access Current
Authority. Current Class membership is a fact source after that authority,
not Parent entitlement.

Assignment SELECTs are request-bounded with sentinel detection. Dependent
LearnerAttempt, LearnerSubmission, Content, and ContentVersion reads run only
after the request-wide assignment bound and every per-learner assignment
bound have passed. Over-capacity fails closed; rows are never truncated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from aieos.domains.learning.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.learning.application.learner_membership import (
    ListCurrentLearnerMembershipsService,
    SchoolContextLearnerMembershipReader,
)
from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapacityExceeded,
    ParentIntelligenceReadUnavailable,
)
from aieos.domains.parent_intelligence.application.models import (
    ATTEMPT_STATUS_IN_PROGRESS,
    ATTEMPT_STATUS_NOT_STARTED,
    ATTEMPT_STATUS_SUBMITTED,
    MAX_ASSIGNMENTS_PER_LEARNER,
    MAX_AUTHORIZED_LEARNER_COUNT,
    MAX_CLASS_REFS_PER_LEARNER,
    ParentAssignmentFact,
    ParentIntelligenceFactsSnapshot,
    ParentLearnerFacts,
)

_UNAVAILABLE = "Parent Intelligence source is temporarily unavailable"

# Operational I02 SELECT protection for independent Learning reads.
# Learning unique (tenant, learner, assignment, attempt_number) does not cap
# attempt_number. This is not a Learning-domain max_attempts rule.
_MAX_ATTEMPT_ROWS_PER_QUERY_PAIR: Final = 8

_ASSIGNMENT_SQL = text(
    """
    SELECT
        assignment_id,
        class_ref,
        content_id,
        content_version_id,
        available_from,
        due_at
    FROM teaching.assignments
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND class_ref IN :class_refs
      AND lifecycle_state = 'ACTIVE'
      AND available_from <= :observed_at
    ORDER BY assignment_id ASC
    LIMIT :assignment_row_limit
    """
).bindparams(bindparam("class_refs", expanding=True))

_ATTEMPT_SQL = text(
    """
    SELECT
        attempt_id,
        learner_principal_id,
        teaching_assignment_id,
        lifecycle_state,
        submitted_at,
        submission_id
    FROM learning.attempts
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND learner_principal_id IN :learner_ids
      AND teaching_assignment_id IN :assignment_ids
    LIMIT :attempt_row_limit
    """
).bindparams(
    bindparam("learner_ids", expanding=True),
    bindparam("assignment_ids", expanding=True),
)

_SUBMISSION_SQL = text(
    """
    SELECT
        submission_id,
        attempt_id,
        learner_principal_id,
        teaching_assignment_id,
        submitted_at
    FROM learning.submissions
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND learner_principal_id IN :learner_ids
      AND teaching_assignment_id IN :assignment_ids
    LIMIT :submission_row_limit
    """
).bindparams(
    bindparam("learner_ids", expanding=True),
    bindparam("assignment_ids", expanding=True),
)

_CONTENT_SQL = text(
    """
    SELECT content_id, title, content_type
    FROM content.contents
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND content_id IN :content_ids
    """
).bindparams(bindparam("content_ids", expanding=True))

_CONTENT_VERSION_SQL = text(
    """
    SELECT version_id, content_id
    FROM content.content_versions
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND version_id IN :version_ids
    """
).bindparams(bindparam("version_ids", expanding=True))


def _unavailable() -> ParentIntelligenceReadUnavailable:
    return ParentIntelligenceReadUnavailable(_UNAVAILABLE)


def _capacity() -> ParentIntelligenceCapacityExceeded:
    return ParentIntelligenceCapacityExceeded(
        "Parent Intelligence is temporarily unavailable"
    )


def _request_assignment_row_limit(authorized_learner_count: int) -> int:
    derived = max(authorized_learner_count, 0) * MAX_ASSIGNMENTS_PER_LEARNER
    ceiling = MAX_AUTHORIZED_LEARNER_COUNT * MAX_ASSIGNMENTS_PER_LEARNER
    return min(derived, ceiling)


def _request_dependent_row_limit(learner_count: int, assignment_count: int) -> int:
    return learner_count * assignment_count * _MAX_ATTEMPT_ROWS_PER_QUERY_PAIR


def _require_bounded_rows(
    rows: Iterable[Mapping[str, Any]], *, limit: int
) -> list[Mapping[str, Any]]:
    materialized = list(rows)
    if len(materialized) > limit:
        raise _capacity()
    return materialized


class SqlAlchemyParentIntelligenceFactsReader:
    """Short-lived read-only PostgreSQL adapter. Rollback/close only."""

    def __init__(
        self,
        engine: Engine,
        *,
        membership_reader: SchoolContextLearnerMembershipReader,
    ) -> None:
        self._engine = engine
        self._memberships = ListCurrentLearnerMembershipsService(membership_reader)

    def read_authorized_learner_facts(
        self,
        *,
        tenant_id: UUID,
        authorized_learner_principal_ids: Sequence[UUID],
        observed_at: datetime,
    ) -> ParentIntelligenceFactsSnapshot:
        learner_ids = list(authorized_learner_principal_ids)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise _unavailable()
        memberships_by_learner = self._read_memberships(tenant_id, learner_ids)
        class_refs = sorted(
            {
                class_ref
                for refs in memberships_by_learner.values()
                for class_ref in refs
            }
        )
        connection: Connection | None = None
        transaction = None
        assignments_by_class: dict[str, list[Mapping[str, Any]]] = {}
        visible_by_learner: dict[UUID, list[Mapping[str, Any]]] = {}
        attempts: list[Mapping[str, Any]] = []
        submissions: list[Mapping[str, Any]] = []
        contents: dict[UUID, Mapping[str, Any]] = {}
        versions: dict[UUID, UUID] = {}
        try:
            connection = self._engine.connect()
            transaction = connection.begin()
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            )
            connection.execute(
                text("SELECT set_config('aieos.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            if class_refs:
                request_assignment_limit = _request_assignment_row_limit(
                    len(learner_ids)
                )
                assignment_rows = _require_bounded_rows(
                    connection.execute(
                        _ASSIGNMENT_SQL,
                        {
                            "tenant_id": tenant_id,
                            "class_refs": class_refs,
                            "observed_at": observed_at,
                            "assignment_row_limit": request_assignment_limit + 1,
                        },
                    ).mappings(),
                    limit=request_assignment_limit,
                )
                for row in assignment_rows:
                    class_ref = str(row["class_ref"])
                    assignments_by_class.setdefault(class_ref, []).append(row)
                for learner_id in learner_ids:
                    visible = _visible_assignment_rows(
                        class_refs=memberships_by_learner[learner_id],
                        assignments_by_class=assignments_by_class,
                    )
                    if len(visible) > MAX_ASSIGNMENTS_PER_LEARNER:
                        raise _capacity()
                    visible_by_learner[learner_id] = visible
                assignment_ids = [
                    row["assignment_id"]
                    for rows in assignments_by_class.values()
                    for row in rows
                ]
                if assignment_ids:
                    dependent_limit = _request_dependent_row_limit(
                        len(learner_ids), len(assignment_ids)
                    )
                    attempts = _require_bounded_rows(
                        connection.execute(
                            _ATTEMPT_SQL,
                            {
                                "tenant_id": tenant_id,
                                "learner_ids": learner_ids,
                                "assignment_ids": assignment_ids,
                                "attempt_row_limit": dependent_limit + 1,
                            },
                        ).mappings(),
                        limit=dependent_limit,
                    )
                    submissions = _require_bounded_rows(
                        connection.execute(
                            _SUBMISSION_SQL,
                            {
                                "tenant_id": tenant_id,
                                "learner_ids": learner_ids,
                                "assignment_ids": assignment_ids,
                                "submission_row_limit": dependent_limit + 1,
                            },
                        ).mappings(),
                        limit=dependent_limit,
                    )
                    content_ids = list(
                        {
                            row["content_id"]
                            for rows in assignments_by_class.values()
                            for row in rows
                        }
                    )
                    version_ids = list(
                        {
                            row["content_version_id"]
                            for rows in assignments_by_class.values()
                            for row in rows
                        }
                    )
                    contents = {
                        row["content_id"]: row
                        for row in connection.execute(
                            _CONTENT_SQL,
                            {"tenant_id": tenant_id, "content_ids": content_ids},
                        ).mappings()
                    }
                    versions = {
                        row["version_id"]: row["content_id"]
                        for row in connection.execute(
                            _CONTENT_VERSION_SQL,
                            {"tenant_id": tenant_id, "version_ids": version_ids},
                        ).mappings()
                    }
        except ParentIntelligenceReadUnavailable:
            raise
        except ParentIntelligenceCapacityExceeded:
            raise
        except Exception as exc:
            raise _unavailable() from exc
        finally:
            if transaction is not None and transaction.is_active:
                transaction.rollback()
            if connection is not None:
                connection.close()

        learners = tuple(
            self._learner_facts(
                learner_id=learner_id,
                visible=visible_by_learner.get(learner_id, []),
                attempts=attempts,
                submissions=submissions,
                contents=contents,
                versions=versions,
            )
            for learner_id in learner_ids
        )
        return ParentIntelligenceFactsSnapshot(
            generated_at=observed_at,
            learners=learners,
        )

    def _read_memberships(
        self,
        tenant_id: UUID,
        learner_ids: Sequence[UUID],
    ) -> dict[UUID, tuple[str, ...]]:
        memberships: dict[UUID, tuple[str, ...]] = {}
        for learner_id in learner_ids:
            try:
                items = self._memberships.list(tenant_id, learner_id)
            except SchoolContextUnavailable as exc:
                raise _unavailable() from exc
            except SchoolContextContractError as exc:
                raise _unavailable() from exc
            except Exception as exc:
                raise _unavailable() from exc
            if len(items) > MAX_CLASS_REFS_PER_LEARNER:
                raise _capacity()
            memberships[learner_id] = tuple(item.class_ref for item in items)
        return memberships

    def _learner_facts(
        self,
        *,
        learner_id: UUID,
        visible: Sequence[Mapping[str, Any]],
        attempts: Sequence[Mapping[str, Any]],
        submissions: Sequence[Mapping[str, Any]],
        contents: Mapping[UUID, Mapping[str, Any]],
        versions: Mapping[UUID, UUID],
    ) -> ParentLearnerFacts:
        if len(visible) > MAX_ASSIGNMENTS_PER_LEARNER:
            raise _capacity()
        learner_attempts = [
            row for row in attempts if row["learner_principal_id"] == learner_id
        ]
        learner_submissions = [
            row for row in submissions if row["learner_principal_id"] == learner_id
        ]
        assignments = tuple(
            sorted(
                (
                    self._assignment_fact(
                        row,
                        attempts=learner_attempts,
                        submissions=learner_submissions,
                        contents=contents,
                        versions=versions,
                    )
                    for row in visible
                ),
                key=lambda item: item.assignment_id.bytes,
            )
        )
        return ParentLearnerFacts(
            learner_principal_id=learner_id,
            assignments=assignments,
        )

    def _assignment_fact(
        self,
        row: Mapping[str, Any],
        *,
        attempts: Sequence[Mapping[str, Any]],
        submissions: Sequence[Mapping[str, Any]],
        contents: Mapping[UUID, Mapping[str, Any]],
        versions: Mapping[UUID, UUID],
    ) -> ParentAssignmentFact:
        content_id = row["content_id"]
        content_version_id = row["content_version_id"]
        catalog = contents.get(content_id)
        if catalog is None:
            raise _unavailable()
        if versions.get(content_version_id) != content_id:
            raise _unavailable()
        title = catalog["title"]
        content_type = catalog["content_type"]
        if not isinstance(title, str) or not title.strip():
            raise _unavailable()
        if not isinstance(content_type, str) or not content_type.strip():
            raise _unavailable()
        attempt_status, submitted_at = _attempt_status(
            assignment_id=row["assignment_id"],
            attempts=attempts,
            submissions=submissions,
        )
        available_from = row["available_from"]
        due_at = row["due_at"]
        if not isinstance(available_from, datetime):
            raise _unavailable()
        if due_at is not None and not isinstance(due_at, datetime):
            raise _unavailable()
        return ParentAssignmentFact(
            assignment_id=row["assignment_id"],
            title=title,
            content_type=content_type,
            available_from=available_from,
            due_at=due_at,
            attempt_status=attempt_status,
            submitted_at=submitted_at,
        )


def _visible_assignment_rows(
    *,
    class_refs: Sequence[str],
    assignments_by_class: Mapping[str, list[Mapping[str, Any]]],
) -> list[Mapping[str, Any]]:
    visible: list[Mapping[str, Any]] = []
    seen_assignment_ids: set[UUID] = set()
    class_ref_set = set(class_refs)
    for class_ref in class_refs:
        for row in assignments_by_class.get(class_ref, ()):
            assignment_id = row["assignment_id"]
            if assignment_id in seen_assignment_ids:
                raise _unavailable()
            seen_assignment_ids.add(assignment_id)
            if str(row["class_ref"]) not in class_ref_set:
                raise _unavailable()
            visible.append(row)
    return visible


def _attempt_status(
    *,
    assignment_id: UUID,
    attempts: Sequence[Mapping[str, Any]],
    submissions: Sequence[Mapping[str, Any]],
) -> tuple[str, datetime | None]:
    matching = [
        row for row in attempts if row["teaching_assignment_id"] == assignment_id
    ]
    submissions_by_id = {
        row["submission_id"]: row
        for row in submissions
        if row["teaching_assignment_id"] == assignment_id
    }
    in_progress = [
        row for row in matching if row["lifecycle_state"] == ATTEMPT_STATUS_IN_PROGRESS
    ]
    submitted = [
        row for row in matching if row["lifecycle_state"] == ATTEMPT_STATUS_SUBMITTED
    ]
    unexpected = [
        row
        for row in matching
        if row["lifecycle_state"]
        not in {ATTEMPT_STATUS_IN_PROGRESS, ATTEMPT_STATUS_SUBMITTED}
    ]
    if unexpected:
        raise _unavailable()
    submitted_times: list[datetime] = []
    for row in submitted:
        evidence = submissions_by_id.get(row["submission_id"])
        if evidence is None:
            raise _unavailable()
        if evidence["attempt_id"] != row["attempt_id"]:
            raise _unavailable()
        if evidence["learner_principal_id"] != row["learner_principal_id"]:
            raise _unavailable()
        if evidence["teaching_assignment_id"] != assignment_id:
            raise _unavailable()
        submitted_at = evidence["submitted_at"]
        if not isinstance(submitted_at, datetime):
            raise _unavailable()
        submitted_times.append(submitted_at)
    if in_progress:
        return ATTEMPT_STATUS_IN_PROGRESS, None
    if submitted_times:
        latest = max(submitted_times)
        return ATTEMPT_STATUS_SUBMITTED, latest
    return ATTEMPT_STATUS_NOT_STARTED, None
