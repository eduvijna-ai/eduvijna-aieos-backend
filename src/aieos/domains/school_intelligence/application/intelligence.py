"""Derived-on-request Principal School Intelligence application service.

Authority order is binding:

1. trusted tenant_id + principal_id
2. current ACTIVE HUMAN Principal
3. exact capability school.intelligence.read
4. current Principal School Scope enumeration
5. complete authorized ClassRef set
6. only then read/aggregate source-domain facts
7. return derived projection

Steps 2–5 are owned by CurrentPrincipalSchoolScopeService (I01). This service
does not recreate that authorization. Every GET revalidates. No cache.
"""

from __future__ import annotations

from uuid import UUID

from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
)
from aieos.domains.school_intelligence.application.errors import (
    SchoolIntelligenceReadUnavailable,
    SchoolIntelligenceScopeCapacityExceeded,
)
from aieos.domains.school_intelligence.application.models import (
    MAX_AUTHORIZED_CLASS_COUNT,
    PROJECTION_MODE_DERIVED_ON_REQUEST,
    SCHOOL_INTELLIGENCE_SOURCE_AUTHORITIES,
    TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST,
    AssignmentLifecycleCounts,
    AuthorizedClassFacts,
    EvaluationCoverageAmongSubmitted,
    PrincipalSchoolIntelligenceClassCard,
    PrincipalSchoolIntelligenceReadModel,
    PrincipalSchoolIntelligenceSummary,
    SchoolIntelligenceEvaluationPolicy,
    SchoolIntelligenceTimeWindow,
)
from aieos.domains.school_intelligence.application.ports import (
    SchoolIntelligenceFactsReader,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    CurrentPrincipalSchoolScopeService,
)


def _empty_lifecycle() -> AssignmentLifecycleCounts:
    return AssignmentLifecycleCounts(active=0, closed=0, cancelled=0)


def _empty_facts(class_ref: str) -> AuthorizedClassFacts:
    return AuthorizedClassFacts(
        class_ref=class_ref,
        teaching_assignment_count=0,
        assignment_lifecycle=_empty_lifecycle(),
        learner_submission_count=0,
        current_policy_evaluation_count=0,
        has_recorded_classroom_assessment=False,
        assignments_with_recorded_classroom_assessment_count=0,
        completed_teaching_execution_count=0,
        remediation_activity_count=0,
    )


def _submitted_but_not_evaluated(facts: AuthorizedClassFacts) -> int:
    return facts.learner_submission_count - facts.current_policy_evaluation_count


def _coverage(facts: AuthorizedClassFacts) -> EvaluationCoverageAmongSubmitted:
    return EvaluationCoverageAmongSubmitted(
        submitted_count=facts.learner_submission_count,
        current_policy_evaluated_count=facts.current_policy_evaluation_count,
    )


def _validate_class_facts(facts: AuthorizedClassFacts) -> None:
    lifecycle = facts.assignment_lifecycle
    if lifecycle.active + lifecycle.closed + lifecycle.cancelled != (
        facts.teaching_assignment_count
    ):
        raise SchoolIntelligenceReadUnavailable(
            "School Intelligence source is temporarily unavailable"
        )
    if facts.current_policy_evaluation_count < 0:
        raise SchoolIntelligenceReadUnavailable(
            "School Intelligence source is temporarily unavailable"
        )
    if facts.current_policy_evaluation_count > facts.learner_submission_count:
        raise SchoolIntelligenceReadUnavailable(
            "School Intelligence source is temporarily unavailable"
        )
    if facts.assignments_with_recorded_classroom_assessment_count < 0:
        raise SchoolIntelligenceReadUnavailable(
            "School Intelligence source is temporarily unavailable"
        )


def _class_card(
    scope_item: AuthorizedSchoolClassRef,
    facts: AuthorizedClassFacts,
) -> PrincipalSchoolIntelligenceClassCard:
    _validate_class_facts(facts)
    return PrincipalSchoolIntelligenceClassCard(
        class_ref=scope_item.class_ref,
        display_label=scope_item.display_label,
        has_assignment_activity=facts.teaching_assignment_count > 0,
        teaching_assignment_count=facts.teaching_assignment_count,
        assignment_lifecycle=facts.assignment_lifecycle,
        learner_submission_count=facts.learner_submission_count,
        current_policy_evaluation_count=facts.current_policy_evaluation_count,
        submitted_but_not_current_policy_evaluated_count=_submitted_but_not_evaluated(
            facts
        ),
        evaluation_coverage_among_submitted=_coverage(facts),
        has_recorded_classroom_assessment=facts.has_recorded_classroom_assessment,
        assignments_with_recorded_classroom_assessment_count=(
            facts.assignments_with_recorded_classroom_assessment_count
        ),
        completed_teaching_execution_count=facts.completed_teaching_execution_count,
        remediation_activity_count=facts.remediation_activity_count,
    )


def _summary(
    cards: tuple[PrincipalSchoolIntelligenceClassCard, ...]
) -> PrincipalSchoolIntelligenceSummary:
    assignment_count = sum(card.teaching_assignment_count for card in cards)
    active = sum(card.assignment_lifecycle.active for card in cards)
    closed = sum(card.assignment_lifecycle.closed for card in cards)
    cancelled = sum(card.assignment_lifecycle.cancelled for card in cards)
    submissions = sum(card.learner_submission_count for card in cards)
    evaluations = sum(card.current_policy_evaluation_count for card in cards)
    return PrincipalSchoolIntelligenceSummary(
        in_scope_class_count=len(cards),
        classes_with_assignment_activity_count=sum(
            1 for card in cards if card.has_assignment_activity
        ),
        teaching_assignment_count=assignment_count,
        assignment_lifecycle=AssignmentLifecycleCounts(
            active=active,
            closed=closed,
            cancelled=cancelled,
        ),
        learner_submission_count=submissions,
        current_policy_evaluation_count=evaluations,
        submitted_but_not_current_policy_evaluated_count=submissions - evaluations,
        evaluation_coverage_among_submitted=EvaluationCoverageAmongSubmitted(
            submitted_count=submissions,
            current_policy_evaluated_count=evaluations,
        ),
        classes_with_recorded_classroom_assessment_count=sum(
            1 for card in cards if card.has_recorded_classroom_assessment
        ),
        assignments_with_recorded_classroom_assessment_count=sum(
            card.assignments_with_recorded_classroom_assessment_count for card in cards
        ),
        completed_teaching_execution_count=sum(
            card.completed_teaching_execution_count for card in cards
        ),
        remediation_activity_count=sum(
            card.remediation_activity_count for card in cards
        ),
    )


class GetPrincipalSchoolIntelligenceService:
    """Side-effect-free derived Principal School Intelligence projection."""

    def __init__(
        self,
        *,
        school_scope: CurrentPrincipalSchoolScopeService,
        facts_reader: SchoolIntelligenceFactsReader,
    ) -> None:
        self._school_scope = school_scope
        self._facts_reader = facts_reader

    def get(
        self,
        tenant_id: UUID,
        principal_id: UUID,
    ) -> PrincipalSchoolIntelligenceReadModel:
        authorized = self._school_scope.current_authorized_classes(
            tenant_id, principal_id
        )
        if len(authorized) > MAX_AUTHORIZED_CLASS_COUNT:
            raise SchoolIntelligenceScopeCapacityExceeded(
                "School Intelligence is temporarily unavailable"
            )
        ordered = tuple(sorted(authorized, key=lambda item: item.class_ref))
        snapshot = self._facts_reader.read_authorized_class_facts(
            tenant_id=tenant_id,
            authorized_class_refs=tuple(item.class_ref for item in ordered),
            evaluation_policy_id=DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
            evaluation_policy_version=DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
        )
        facts_by_ref = {row.class_ref: row for row in snapshot.classes}
        cards = tuple(
            _class_card(
                item,
                facts_by_ref.get(item.class_ref, _empty_facts(item.class_ref)),
            )
            for item in ordered
        )
        return PrincipalSchoolIntelligenceReadModel(
            generated_at=snapshot.generated_at,
            projection_mode=PROJECTION_MODE_DERIVED_ON_REQUEST,
            time_window=SchoolIntelligenceTimeWindow(
                mode=TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST,
                start=None,
                end=snapshot.generated_at,
            ),
            evaluation_policy=SchoolIntelligenceEvaluationPolicy(
                policy_id=DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
                policy_version=DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
            ),
            sources=SCHOOL_INTELLIGENCE_SOURCE_AUTHORITIES,
            summary=_summary(cards),
            classes=cards,
        )
