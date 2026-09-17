"""Technology-neutral Parent Intelligence read models.

Derived-on-request projection only. Not a business SoR. No SQLAlchemy types.
Positive-allowlist DTOs only — no source-domain dump, no denylist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

PROJECTION_MODE_DERIVED_ON_REQUEST: Final = "DERIVED_ON_REQUEST"
TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST: Final = "CURRENT_FACTS_AS_OF_REQUEST"

ATTEMPT_STATUS_NOT_STARTED: Final = "NOT_STARTED"
ATTEMPT_STATUS_IN_PROGRESS: Final = "IN_PROGRESS"
ATTEMPT_STATUS_SUBMITTED: Final = "SUBMITTED"
PARENT_ATTEMPT_STATUSES: Final[frozenset[str]] = frozenset(
    {
        ATTEMPT_STATUS_NOT_STARTED,
        ATTEMPT_STATUS_IN_PROGRESS,
        ATTEMPT_STATUS_SUBMITTED,
    }
)

# Operational implementation-protection limits. Not educational/domain rules.
MAX_AUTHORIZED_LEARNER_COUNT: Final = 100
MAX_CLASS_REFS_PER_LEARNER: Final = 100
MAX_ASSIGNMENTS_PER_LEARNER: Final = 100


@dataclass(frozen=True, slots=True)
class ParentAssignmentFact:
    """Internal approved assignment facts sufficient to build the Parent DTO."""

    assignment_id: UUID
    title: str
    content_type: str
    available_from: datetime
    due_at: datetime | None
    attempt_status: str
    submitted_at: datetime | None


@dataclass(frozen=True, slots=True)
class ParentLearnerFacts:
    """Exactly one facts row for one already-authorized learner."""

    learner_principal_id: UUID
    assignments: tuple[ParentAssignmentFact, ...]


@dataclass(frozen=True, slots=True)
class ParentIntelligenceFactsSnapshot:
    """One coherent derived-fact snapshot for already-authorized learners."""

    generated_at: datetime
    learners: tuple[ParentLearnerFacts, ...]


@dataclass(frozen=True, slots=True)
class ParentIntelligenceTimeWindow:
    mode: str
    start: datetime | None
    end: datetime


@dataclass(frozen=True, slots=True)
class ParentAssignmentStatus:
    assignment_id: UUID
    title: str
    content_type: str
    available_from: datetime
    due_at: datetime | None
    attempt_status: str
    submitted_at: datetime | None


@dataclass(frozen=True, slots=True)
class ParentChildCard:
    learner_principal_id: UUID
    assignments: tuple[ParentAssignmentStatus, ...]


@dataclass(frozen=True, slots=True)
class ParentIntelligenceReadModel:
    generated_at: datetime
    projection_mode: str
    time_window: ParentIntelligenceTimeWindow
    children: tuple[ParentChildCard, ...]
