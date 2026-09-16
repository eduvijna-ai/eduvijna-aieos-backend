"""AIEOS360-S02-I02 — application projection composition without SQL."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aieos.domains.assessment.domain.evaluation_policy_v1 import (
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID,
    DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION,
)
from aieos.domains.school_intelligence.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
    SchoolIntelligenceCapabilityForbidden,
    SchoolIntelligenceReadUnavailable,
    SchoolIntelligenceScopeCapacityExceeded,
)
from aieos.domains.school_intelligence.application.intelligence import (
    GetPrincipalSchoolIntelligenceService,
)
from aieos.domains.school_intelligence.application.models import (
    MAX_AUTHORIZED_CLASS_COUNT,
    PROJECTION_MODE_DERIVED_ON_REQUEST,
    TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST,
    AssignmentLifecycleCounts,
    AuthorizedClassFacts,
    SchoolIntelligenceFactsSnapshot,
)
from aieos.domains.school_intelligence.application.school_scope import (
    AuthorizedSchoolClassRef,
    CurrentPrincipalSchoolScopeService,
)
from aieos.platform.security.authorization.decisions import PrincipalKind
from aieos.platform.security.context import UnauthorizedError

pytestmark = pytest.mark.aieos360_s02_i02

GENERATED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


class _HumanGate:
    def __init__(self, *, kind: PrincipalKind = PrincipalKind.HUMAN) -> None:
        self.kind = kind
        self.calls: list[object] = []

    def require_current_human_principal(self, principal_id):
        self.calls.append(principal_id)
        if self.kind is not PrincipalKind.HUMAN:
            raise UnauthorizedError("principal not authorized")
        return PrincipalKind.HUMAN


class _Auth:
    def __init__(self, *, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[tuple[object, object, str]] = []

    def authorize(self, *, tenant_id, principal_id, capability):
        self.calls.append((tenant_id, principal_id, capability))
        if not self.allow:
            raise SchoolIntelligenceCapabilityForbidden(
                "school intelligence capability denied"
            )


class _Reader:
    def __init__(self, items=(), *, exc: BaseException | None = None) -> None:
        self.items = items
        self.exc = exc
        self.calls: list[tuple[object, object]] = []

    def list_current_authorized_classes(self, tenant_id, principal_id):
        self.calls.append((tenant_id, principal_id))
        if self.exc is not None:
            raise self.exc
        return self.items


class _Facts:
    def __init__(
        self,
        snapshot: SchoolIntelligenceFactsSnapshot | None = None,
        *,
        exc: BaseException | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.exc = exc
        self.calls: list[dict[str, object]] = []

    def read_authorized_class_facts(
        self,
        *,
        tenant_id,
        authorized_class_refs,
        evaluation_policy_id,
        evaluation_policy_version,
    ):
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "authorized_class_refs": tuple(authorized_class_refs),
                "evaluation_policy_id": evaluation_policy_id,
                "evaluation_policy_version": evaluation_policy_version,
            }
        )
        if self.exc is not None:
            raise self.exc
        assert self.snapshot is not None
        return self.snapshot


def _facts(
    class_ref: str,
    *,
    assignments: int = 0,
    active: int = 0,
    closed: int = 0,
    cancelled: int = 0,
    submissions: int = 0,
    evaluations: int = 0,
    recorded: bool = False,
    recorded_assignments: int = 0,
    executions: int = 0,
    remediation: int = 0,
) -> AuthorizedClassFacts:
    return AuthorizedClassFacts(
        class_ref=class_ref,
        teaching_assignment_count=assignments,
        assignment_lifecycle=AssignmentLifecycleCounts(
            active=active, closed=closed, cancelled=cancelled
        ),
        learner_submission_count=submissions,
        current_policy_evaluation_count=evaluations,
        has_recorded_classroom_assessment=recorded,
        assignments_with_recorded_classroom_assessment_count=recorded_assignments,
        completed_teaching_execution_count=executions,
        remediation_activity_count=remediation,
    )


def _service(*, classification=None, authorization=None, reader=None, facts=None):
    return GetPrincipalSchoolIntelligenceService(
        school_scope=CurrentPrincipalSchoolScopeService(
            classification=classification or _HumanGate(),
            authorization=authorization or _Auth(),
            reader=reader or _Reader(),
        ),
        facts_reader=facts
        or _Facts(
            SchoolIntelligenceFactsSnapshot(generated_at=GENERATED_AT, classes=())
        ),
    )


class TestAuthorityOrder:
    def test_success_revalidates_and_reads_facts_after_scope(self) -> None:
        tenant_id = uuid4()
        principal_id = uuid4()
        classification = _HumanGate()
        authorization = _Auth()
        reader = _Reader(
            (
                AuthorizedSchoolClassRef("class-6b", "Grade 6B"),
                AuthorizedSchoolClassRef("class-6a", "Grade 6A"),
            )
        )
        facts = _Facts(
            SchoolIntelligenceFactsSnapshot(
                generated_at=GENERATED_AT,
                classes=(
                    _facts(
                        "class-6a",
                        assignments=2,
                        active=1,
                        closed=1,
                        submissions=4,
                        evaluations=3,
                        recorded=True,
                        recorded_assignments=1,
                        executions=2,
                        remediation=1,
                    ),
                    _facts("class-6b"),
                ),
            )
        )
        result = _service(
            classification=classification,
            authorization=authorization,
            reader=reader,
            facts=facts,
        ).get(tenant_id, principal_id)
        assert classification.calls == [principal_id]
        assert authorization.calls == [
            (tenant_id, principal_id, "school.intelligence.read")
        ]
        assert reader.calls == [(tenant_id, principal_id)]
        assert facts.calls[0]["authorized_class_refs"] == ("class-6a", "class-6b")
        assert facts.calls[0]["evaluation_policy_id"] == (
            DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_ID
        )
        assert facts.calls[0]["evaluation_policy_version"] == (
            DETERMINISTIC_LEARNER_ASSESSMENT_POLICY_VERSION
        )
        assert result.projection_mode == PROJECTION_MODE_DERIVED_ON_REQUEST
        assert result.time_window.mode == TIME_WINDOW_MODE_CURRENT_FACTS_AS_OF_REQUEST
        assert result.time_window.start is None
        assert result.time_window.end == GENERATED_AT
        assert [card.class_ref for card in result.classes] == ["class-6a", "class-6b"]
        assert result.classes[0].display_label == "Grade 6A"
        assert result.classes[0].has_assignment_activity is True
        assert result.classes[0].submitted_but_not_current_policy_evaluated_count == 1
        assert result.classes[1].teaching_assignment_count == 0
        assert result.classes[1].evaluation_coverage_among_submitted.submitted_count == 0
        assert (
            result.classes[1].evaluation_coverage_among_submitted.current_policy_evaluated_count
            == 0
        )
        assert result.summary.in_scope_class_count == 2
        assert result.summary.classes_with_assignment_activity_count == 1
        assert result.summary.teaching_assignment_count == 2
        assert result.summary.assignment_lifecycle.active == 1
        assert result.summary.assignment_lifecycle.closed == 1
        assert result.summary.learner_submission_count == 4
        assert result.summary.current_policy_evaluation_count == 3
        assert result.summary.submitted_but_not_current_policy_evaluated_count == 1

    def test_workload_fails_before_scope_and_facts(self) -> None:
        reader = _Reader((AuthorizedSchoolClassRef("class-6a", "Grade 6A"),))
        facts = _Facts(
            SchoolIntelligenceFactsSnapshot(generated_at=GENERATED_AT, classes=())
        )
        with pytest.raises(UnauthorizedError):
            _service(
                classification=_HumanGate(kind=PrincipalKind.WORKLOAD),
                reader=reader,
                facts=facts,
            ).get(uuid4(), uuid4())
        assert reader.calls == []
        assert facts.calls == []

    def test_capability_denied_skips_facts(self) -> None:
        reader = _Reader()
        facts = _Facts(
            SchoolIntelligenceFactsSnapshot(generated_at=GENERATED_AT, classes=())
        )
        with pytest.raises(SchoolIntelligenceCapabilityForbidden):
            _service(
                authorization=_Auth(allow=False),
                reader=reader,
                facts=facts,
            ).get(uuid4(), uuid4())
        assert reader.calls == []
        assert facts.calls == []

    def test_scope_unavailable_skips_facts(self) -> None:
        facts = _Facts(
            SchoolIntelligenceFactsSnapshot(generated_at=GENERATED_AT, classes=())
        )
        with pytest.raises(SchoolContextUnavailable):
            _service(
                reader=_Reader(exc=SchoolContextUnavailable("unavailable")),
                facts=facts,
            ).get(uuid4(), uuid4())
        assert facts.calls == []

    def test_malformed_scope_skips_facts(self) -> None:
        facts = _Facts(
            SchoolIntelligenceFactsSnapshot(generated_at=GENERATED_AT, classes=())
        )
        with pytest.raises(SchoolContextContractError):
            _service(
                reader=_Reader(items=[object()]),
                facts=facts,
            ).get(uuid4(), uuid4())
        assert facts.calls == []

    def test_capacity_exceeded_skips_facts(self) -> None:
        items = tuple(
            AuthorizedSchoolClassRef(f"class-{index:03d}", f"Class {index}")
            for index in range(MAX_AUTHORIZED_CLASS_COUNT + 1)
        )
        facts = _Facts(
            SchoolIntelligenceFactsSnapshot(generated_at=GENERATED_AT, classes=())
        )
        with pytest.raises(SchoolIntelligenceScopeCapacityExceeded):
            _service(reader=_Reader(items), facts=facts).get(uuid4(), uuid4())
        assert facts.calls == []

    def test_source_failure_is_unavailable_not_zero(self) -> None:
        reader = _Reader((AuthorizedSchoolClassRef("class-6a", "Grade 6A"),))
        with pytest.raises(SchoolIntelligenceReadUnavailable):
            _service(
                reader=reader,
                facts=_Facts(exc=SchoolIntelligenceReadUnavailable("unavailable")),
            ).get(uuid4(), uuid4())

    def test_empty_authorized_scope_is_truthful_zero(self) -> None:
        result = _service().get(uuid4(), uuid4())
        assert result.summary.in_scope_class_count == 0
        assert result.summary.teaching_assignment_count == 0
        assert result.summary.evaluation_coverage_among_submitted.submitted_count == 0
        assert (
            result.summary.evaluation_coverage_among_submitted.current_policy_evaluated_count
            == 0
        )
        assert result.classes == ()


class TestFactsSnapshotCompleteness:
    def test_complete_explicit_rows_succeed(self) -> None:
        result = _service(
            reader=_Reader(
                (
                    AuthorizedSchoolClassRef("class-a", "A"),
                    AuthorizedSchoolClassRef("class-b", "B"),
                )
            ),
            facts=_Facts(
                SchoolIntelligenceFactsSnapshot(
                    generated_at=GENERATED_AT,
                    classes=(_facts("class-a", assignments=1, active=1), _facts("class-b")),
                )
            ),
        ).get(uuid4(), uuid4())
        assert [card.class_ref for card in result.classes] == ["class-a", "class-b"]
        assert result.classes[0].teaching_assignment_count == 1
        assert result.classes[1].teaching_assignment_count == 0

    def test_empty_snapshot_for_authorized_class_is_unavailable(self) -> None:
        with pytest.raises(SchoolIntelligenceReadUnavailable):
            _service(
                reader=_Reader((AuthorizedSchoolClassRef("class-a", "A"),)),
                facts=_Facts(
                    SchoolIntelligenceFactsSnapshot(generated_at=GENERATED_AT, classes=())
                ),
            ).get(uuid4(), uuid4())

    def test_missing_requested_class_is_unavailable(self) -> None:
        with pytest.raises(SchoolIntelligenceReadUnavailable):
            _service(
                reader=_Reader(
                    (
                        AuthorizedSchoolClassRef("class-a", "A"),
                        AuthorizedSchoolClassRef("class-b", "B"),
                    )
                ),
                facts=_Facts(
                    SchoolIntelligenceFactsSnapshot(
                        generated_at=GENERATED_AT,
                        classes=(_facts("class-a"),),
                    )
                ),
            ).get(uuid4(), uuid4())

    def test_unexpected_extra_class_is_unavailable(self) -> None:
        with pytest.raises(SchoolIntelligenceReadUnavailable):
            _service(
                reader=_Reader((AuthorizedSchoolClassRef("class-a", "A"),)),
                facts=_Facts(
                    SchoolIntelligenceFactsSnapshot(
                        generated_at=GENERATED_AT,
                        classes=(_facts("class-a"), _facts("class-extra")),
                    )
                ),
            ).get(uuid4(), uuid4())

    def test_duplicate_class_ref_is_unavailable(self) -> None:
        with pytest.raises(SchoolIntelligenceReadUnavailable):
            _service(
                reader=_Reader((AuthorizedSchoolClassRef("class-a", "A"),)),
                facts=_Facts(
                    SchoolIntelligenceFactsSnapshot(
                        generated_at=GENERATED_AT,
                        classes=(_facts("class-a"), _facts("class-a")),
                    )
                ),
            ).get(uuid4(), uuid4())
