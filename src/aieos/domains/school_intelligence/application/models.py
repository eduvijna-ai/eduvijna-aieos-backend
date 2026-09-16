"""Technology-neutral Principal School Intelligence read models.

Derived-on-request projection only. Not a business SoR. No SQLAlchemy types.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

PROJECTION_MODE_DERIVED_ON_REQUEST: Final = "DERIVED_ON_REQUEST"
TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST: Final = "CURRENT_FACTS_AS_OF_REQUEST"

MAX_AUTHORIZED_CLASS_COUNT: Final = 100

SCHOOL_INTELLIGENCE_SOURCE_AUTHORITIES: Final[tuple[str, ...]] = (
    "SCHOOL_CONTEXT",
    "TEACHING_ASSIGNMENT",
    "TEACHING_EXECUTION",
    "LEARNER_SUBMISSION",
    "LEARNER_ASSESSMENT_EVALUATION",
    "CLASSROOM_ASSESSMENT",
    "TEACHING_WORK_REMEDIATION_ORIGIN",
)


@dataclass(frozen=True, slots=True)
class AssignmentLifecycleCounts:
    active: int
    closed: int
    cancelled: int


@dataclass(frozen=True, slots=True)
class EvaluationCoverageAmongSubmitted:
    submitted_count: int
    current_policy_evaluated_count: int


@dataclass(frozen=True, slots=True)
class SchoolIntelligenceTimeWindow:
    mode: str
    start: datetime | None
    end: datetime


@dataclass(frozen=True, slots=True)
class SchoolIntelligenceEvaluationPolicy:
    policy_id: str
    policy_version: int


@dataclass(frozen=True, slots=True)
class AuthorizedClassFacts:
    """Privacy-safe aggregated counts for one authorized ClassRef."""

    class_ref: str
    teaching_assignment_count: int
    assignment_lifecycle: AssignmentLifecycleCounts
    learner_submission_count: int
    current_policy_evaluation_count: int
    has_recorded_classroom_assessment: bool
    assignments_with_recorded_classroom_assessment_count: int
    completed_teaching_execution_count: int
    remediation_activity_count: int


@dataclass(frozen=True, slots=True)
class SchoolIntelligenceFactsSnapshot:
    """One coherent derived-fact snapshot. No identity fields."""

    generated_at: datetime
    classes: tuple[AuthorizedClassFacts, ...]


@dataclass(frozen=True, slots=True)
class PrincipalSchoolIntelligenceSummary:
    in_scope_class_count: int
    classes_with_assignment_activity_count: int
    teaching_assignment_count: int
    assignment_lifecycle: AssignmentLifecycleCounts
    learner_submission_count: int
    current_policy_evaluation_count: int
    submitted_but_not_current_policy_evaluated_count: int
    evaluation_coverage_among_submitted: EvaluationCoverageAmongSubmitted
    classes_with_recorded_classroom_assessment_count: int
    assignments_with_recorded_classroom_assessment_count: int
    completed_teaching_execution_count: int
    remediation_activity_count: int


@dataclass(frozen=True, slots=True)
class PrincipalSchoolIntelligenceClassCard:
    class_ref: str
    display_label: str
    has_assignment_activity: bool
    teaching_assignment_count: int
    assignment_lifecycle: AssignmentLifecycleCounts
    learner_submission_count: int
    current_policy_evaluation_count: int
    submitted_but_not_current_policy_evaluated_count: int
    evaluation_coverage_among_submitted: EvaluationCoverageAmongSubmitted
    has_recorded_classroom_assessment: bool
    assignments_with_recorded_classroom_assessment_count: int
    completed_teaching_execution_count: int
    remediation_activity_count: int


@dataclass(frozen=True, slots=True)
class PrincipalSchoolIntelligenceReadModel:
    generated_at: datetime
    projection_mode: str
    time_window: SchoolIntelligenceTimeWindow
    evaluation_policy: SchoolIntelligenceEvaluationPolicy
    sources: tuple[str, ...]
    summary: PrincipalSchoolIntelligenceSummary
    classes: tuple[PrincipalSchoolIntelligenceClassCard, ...]
