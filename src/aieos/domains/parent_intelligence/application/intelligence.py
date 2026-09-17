"""Derived-on-request Parent Intelligence application service.

Home authority order is binding:

1. trusted tenant_id + adult_principal_id
2. CurrentParentLearnerAccessService (I01) revalidates ACTIVE HUMAN adult,
   exact parent.intelligence.read, current adult→learner access, and every
   returned learner subject
3. sort / preserve deterministic authorized learner set
4. enforce implementation protection bounds without truncation
5. only then read learner current-fact inputs
6. build positive-allowlist Parent projection
7. return the read model

Selector authority order is binding:

1. trusted tenant_id + adult_principal_id
2. resolve and fully validate CURRENT authorized learner set through I01
3. compare requested learner UUID against that validated authorized set
4. if absent: ParentLearnerNotFound (concealment) — do not probe the guessed
   Principal, integrity, membership, or assignment sources
5. only if present: read current facts for that authorized learner
6. return a one-child ParentIntelligenceReadModel

Zero authorized learners is a successful empty home. The facts reader is
not invoked. Unconfigured Parent access remains unavailable.

Every GET revalidates. No cache. No durable Parent projection.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import UUID

from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapacityExceeded,
    ParentIntelligenceReadUnavailable,
    ParentLearnerNotFound,
)
from aieos.domains.parent_intelligence.application.learner_access import (
    CurrentParentLearnerAccessService,
)
from aieos.domains.parent_intelligence.application.models import (
    MAX_ASSIGNMENTS_PER_LEARNER,
    MAX_AUTHORIZED_LEARNER_COUNT,
    PARENT_ATTEMPT_STATUSES,
    PROJECTION_MODE_DERIVED_ON_REQUEST,
    TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST,
    ParentAssignmentFact,
    ParentAssignmentStatus,
    ParentChildCard,
    ParentIntelligenceFactsSnapshot,
    ParentIntelligenceReadModel,
    ParentIntelligenceTimeWindow,
    ParentLearnerFacts,
)
from aieos.domains.parent_intelligence.application.ports import (
    ParentIntelligenceFactsReader,
)

_UNAVAILABLE = "Parent Intelligence is temporarily unavailable"
_CONCEALED = "Parent learner was not found"

UtcClock = Callable[[], datetime]


def _unavailable() -> ParentIntelligenceReadUnavailable:
    return ParentIntelligenceReadUnavailable(_UNAVAILABLE)


def _capacity() -> ParentIntelligenceCapacityExceeded:
    return ParentIntelligenceCapacityExceeded(_UNAVAILABLE)


def _require_aware_utc(observed_at: datetime) -> datetime:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise _unavailable()
    return observed_at.astimezone(UTC)


def _validate_assignment_fact(fact: ParentAssignmentFact) -> None:
    if fact.attempt_status not in PARENT_ATTEMPT_STATUSES:
        raise _unavailable()
    if not fact.title.strip() or not fact.content_type.strip():
        raise _unavailable()
    if fact.available_from.tzinfo is None or fact.available_from.utcoffset() is None:
        raise _unavailable()
    if fact.due_at is not None and (
        fact.due_at.tzinfo is None or fact.due_at.utcoffset() is None
    ):
        raise _unavailable()
    if (
        fact.attempt_status == "IN_PROGRESS" or fact.attempt_status == "NOT_STARTED"
    ) and fact.submitted_at is not None:
        raise _unavailable()
    if fact.attempt_status == "SUBMITTED":
        if fact.submitted_at is None:
            raise _unavailable()
        if fact.submitted_at.tzinfo is None or fact.submitted_at.utcoffset() is None:
            raise _unavailable()


def _require_complete_facts_snapshot(
    requested_learner_ids: tuple[UUID, ...],
    snapshot: ParentIntelligenceFactsSnapshot,
    *,
    observed_at: datetime,
) -> dict[UUID, ParentLearnerFacts]:
    if snapshot.generated_at != observed_at:
        raise _unavailable()
    returned_ids = tuple(row.learner_principal_id for row in snapshot.learners)
    if requested_learner_ids == () and returned_ids == ():
        return {}
    if len(returned_ids) != len(requested_learner_ids):
        raise _unavailable()
    if len(set(returned_ids)) != len(returned_ids):
        raise _unavailable()
    if set(returned_ids) != set(requested_learner_ids):
        raise _unavailable()
    facts_by_id = {row.learner_principal_id: row for row in snapshot.learners}
    for learner_id in requested_learner_ids:
        row = facts_by_id[learner_id]
        assignment_ids = tuple(item.assignment_id for item in row.assignments)
        if len(set(assignment_ids)) != len(assignment_ids):
            raise _unavailable()
        if len(row.assignments) > MAX_ASSIGNMENTS_PER_LEARNER:
            raise _capacity()
        for item in row.assignments:
            _validate_assignment_fact(item)
    return facts_by_id


def _assignment_status(fact: ParentAssignmentFact) -> ParentAssignmentStatus:
    _validate_assignment_fact(fact)
    return ParentAssignmentStatus(
        assignment_id=fact.assignment_id,
        title=fact.title,
        content_type=fact.content_type,
        available_from=fact.available_from,
        due_at=fact.due_at,
        attempt_status=fact.attempt_status,
        submitted_at=fact.submitted_at,
    )


def _child_card(facts: ParentLearnerFacts) -> ParentChildCard:
    ordered = tuple(
        sorted(facts.assignments, key=lambda item: item.assignment_id.bytes)
    )
    return ParentChildCard(
        learner_principal_id=facts.learner_principal_id,
        assignments=tuple(_assignment_status(item) for item in ordered),
    )


def _read_model(
    *,
    observed_at: datetime,
    children: tuple[ParentChildCard, ...],
) -> ParentIntelligenceReadModel:
    return ParentIntelligenceReadModel(
        generated_at=observed_at,
        projection_mode=PROJECTION_MODE_DERIVED_ON_REQUEST,
        time_window=ParentIntelligenceTimeWindow(
            mode=TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST,
            start=None,
            end=observed_at,
        ),
        children=children,
    )


class GetParentIntelligenceService:
    """Side-effect-free derived Parent Intelligence projection."""

    def __init__(
        self,
        *,
        learner_access: CurrentParentLearnerAccessService,
        facts_reader: ParentIntelligenceFactsReader,
        clock: UtcClock | None = None,
    ) -> None:
        self._learner_access = learner_access
        self._facts_reader = facts_reader
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_home(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
    ) -> ParentIntelligenceReadModel:
        authorized_ids = self._current_authorized_ids(tenant_id, adult_principal_id)
        observed_at = _require_aware_utc(self._clock())
        if not authorized_ids:
            return _read_model(observed_at=observed_at, children=())
        return self._project(tenant_id, authorized_ids, observed_at)

    def get_child(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
        requested_learner_principal_id: UUID,
    ) -> ParentIntelligenceReadModel:
        authorized_ids = self._current_authorized_ids(tenant_id, adult_principal_id)
        if requested_learner_principal_id not in set(authorized_ids):
            raise ParentLearnerNotFound(_CONCEALED)
        observed_at = _require_aware_utc(self._clock())
        return self._project(
            tenant_id, (requested_learner_principal_id,), observed_at
        )

    def _current_authorized_ids(
        self,
        tenant_id: UUID,
        adult_principal_id: UUID,
    ) -> tuple[UUID, ...]:
        authorized = self._learner_access.current_authorized_learners(
            tenant_id, adult_principal_id
        )
        if len(authorized) > MAX_AUTHORIZED_LEARNER_COUNT:
            raise _capacity()
        return tuple(item.learner_principal_id for item in authorized)

    def _project(
        self,
        tenant_id: UUID,
        authorized_ids: Sequence[UUID],
        observed_at: datetime,
    ) -> ParentIntelligenceReadModel:
        requested = tuple(authorized_ids)
        snapshot = self._facts_reader.read_authorized_learner_facts(
            tenant_id=tenant_id,
            authorized_learner_principal_ids=requested,
            observed_at=observed_at,
        )
        facts_by_id = _require_complete_facts_snapshot(
            requested, snapshot, observed_at=observed_at
        )
        children = tuple(_child_card(facts_by_id[learner_id]) for learner_id in requested)
        return _read_model(observed_at=observed_at, children=children)
