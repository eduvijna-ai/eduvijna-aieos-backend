"""AIEOS360-S03-I02 — Parent Intelligence application service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from aieos.domains.learning.application.errors import (
    SchoolContextContractError,
    SchoolContextUnavailable,
)
from aieos.domains.parent_intelligence.application.errors import (
    ParentIntelligenceCapacityExceeded,
    ParentIntelligenceReadUnavailable,
    ParentLearnerAccessContractError,
    ParentLearnerAccessUnavailable,
    ParentLearnerNotFound,
)
from aieos.domains.parent_intelligence.application.intelligence import (
    GetParentIntelligenceService,
)
from aieos.domains.parent_intelligence.application.models import (
    MAX_AUTHORIZED_LEARNER_COUNT,
    ParentAssignmentFact,
    ParentIntelligenceFactsSnapshot,
    ParentLearnerFacts,
)
from tests.domains.parent_intelligence.helpers_s03_i02 import (
    FIXED_NOW,
    AllowParentIntelligenceAuthorization,
    AlwaysHumanGate,
    MutableParentAccessReader,
    RecordingFactsReader,
    RecordingIntegrity,
    empty_facts,
    parent_access_service,
)

pytestmark = pytest.mark.aieos360_s03_i02


def _service(
    *,
    learner_ids: tuple[UUID, ...] = (),
    facts: RecordingFactsReader | None = None,
    integrity: RecordingIntegrity | None = None,
    reader=None,
) -> tuple[GetParentIntelligenceService, RecordingFactsReader, RecordingIntegrity]:
    recorded_integrity = integrity or RecordingIntegrity()
    recorded_facts = facts or RecordingFactsReader()
    access = parent_access_service(
        reader=reader or MutableParentAccessReader(learner_ids),
        integrity=recorded_integrity,
        authorization=AllowParentIntelligenceAuthorization(),
        classification=AlwaysHumanGate(),
    )
    service = GetParentIntelligenceService(
        learner_access=access,
        facts_reader=recorded_facts,
        clock=lambda: FIXED_NOW,
    )
    return service, recorded_facts, recorded_integrity


class TestHomeAuthorityOrder:
    def test_authorized_adult_one_child_no_assignments(self) -> None:
        learner_id = uuid4()
        service, facts, _ = _service(learner_ids=(learner_id,))
        result = service.get_home(uuid4(), uuid4())
        assert result.projection_mode == "DERIVED_ON_REQUEST"
        assert result.time_window.mode == "CURRENT_FACTS_AS_OF_REQUEST"
        assert result.time_window.start is None
        assert result.time_window.end == FIXED_NOW
        assert result.generated_at == FIXED_NOW
        assert len(result.children) == 1
        assert result.children[0].learner_principal_id == learner_id
        assert result.children[0].assignments == ()
        assert len(facts.calls) == 1

    def test_zero_authorized_children_skips_facts_reader(self) -> None:
        service, facts, integrity = _service(learner_ids=())
        result = service.get_home(uuid4(), uuid4())
        assert result.children == ()
        assert facts.calls == []
        assert integrity.calls == []

    def test_multiple_children_are_deterministic(self) -> None:
        first, second = uuid4(), uuid4()
        low, high = sorted((first, second), key=lambda item: item.bytes)
        service, _, _ = _service(learner_ids=(high, low))
        result = service.get_home(uuid4(), uuid4())
        assert [child.learner_principal_id for child in result.children] == [low, high]

    def test_access_unavailable_does_not_read_facts(self) -> None:
        from tests.domains.parent_intelligence.helpers_s03_i02 import (
            UnavailableParentAccessReader,
        )

        service, facts, _ = _service(reader=UnavailableParentAccessReader())
        with pytest.raises(ParentLearnerAccessUnavailable):
            service.get_home(uuid4(), uuid4())
        assert facts.calls == []

    def test_access_contract_invalid_does_not_read_facts(self) -> None:
        from tests.domains.parent_intelligence.helpers_s03_i02 import (
            InvalidParentAccessReader,
        )

        service, facts, _ = _service(reader=InvalidParentAccessReader())
        with pytest.raises(ParentLearnerAccessContractError):
            service.get_home(uuid4(), uuid4())
        assert facts.calls == []

    def test_capacity_exceeded_does_not_read_facts(self) -> None:
        learner_ids = tuple(uuid4() for _ in range(MAX_AUTHORIZED_LEARNER_COUNT + 1))
        service, facts, _ = _service(learner_ids=learner_ids)
        with pytest.raises(ParentIntelligenceCapacityExceeded):
            service.get_home(uuid4(), uuid4())
        assert facts.calls == []


class TestSelectorAuthorityOrder:
    def test_selected_authorized_child_returns_exactly_one(self) -> None:
        first, second = uuid4(), uuid4()
        service, facts, _ = _service(learner_ids=(first, second))
        result = service.get_child(uuid4(), uuid4(), second)
        assert [child.learner_principal_id for child in result.children] == [second]
        assert facts.calls[0][1] == (second,)

    def test_selector_miss_is_concealment_and_does_not_probe_facts_or_integrity(
        self,
    ) -> None:
        authorized = uuid4()
        guessed = uuid4()
        service, facts, integrity = _service(learner_ids=(authorized,))
        with pytest.raises(ParentLearnerNotFound) as exc:
            service.get_child(uuid4(), uuid4(), guessed)
        assert str(exc.value) == "Parent learner was not found"
        assert facts.calls == []
        assert integrity.calls == [authorized]
        assert guessed not in integrity.calls


class TestFactsCompleteness:
    def test_missing_learner_row_is_unavailable(self) -> None:
        learner_id = uuid4()
        extra_missing = RecordingFactsReader(
            ParentIntelligenceFactsSnapshot(generated_at=FIXED_NOW, learners=())
        )
        service, _, _ = _service(learner_ids=(learner_id,), facts=extra_missing)
        with pytest.raises(ParentIntelligenceReadUnavailable):
            service.get_home(uuid4(), uuid4())

    def test_extra_learner_row_is_unavailable(self) -> None:
        learner_id = uuid4()
        extra = uuid4()
        facts = RecordingFactsReader(
            ParentIntelligenceFactsSnapshot(
                generated_at=FIXED_NOW,
                learners=(
                    ParentLearnerFacts(learner_principal_id=learner_id, assignments=()),
                    ParentLearnerFacts(learner_principal_id=extra, assignments=()),
                ),
            )
        )
        service, _, _ = _service(learner_ids=(learner_id,), facts=facts)
        with pytest.raises(ParentIntelligenceReadUnavailable):
            service.get_home(uuid4(), uuid4())

    def test_duplicate_learner_row_is_unavailable(self) -> None:
        learner_id = uuid4()
        facts = RecordingFactsReader(
            ParentIntelligenceFactsSnapshot(
                generated_at=FIXED_NOW,
                learners=(
                    ParentLearnerFacts(learner_principal_id=learner_id, assignments=()),
                    ParentLearnerFacts(learner_principal_id=learner_id, assignments=()),
                ),
            )
        )
        service, _, _ = _service(learner_ids=(learner_id,), facts=facts)
        with pytest.raises(ParentIntelligenceReadUnavailable):
            service.get_home(uuid4(), uuid4())

    def test_duplicate_assignment_is_unavailable(self) -> None:
        learner_id = uuid4()
        assignment_id = uuid4()
        assignment = ParentAssignmentFact(
            assignment_id=assignment_id,
            title="Worksheet",
            content_type="worksheet",
            available_from=FIXED_NOW,
            due_at=None,
            attempt_status="NOT_STARTED",
            submitted_at=None,
        )
        facts = RecordingFactsReader(
            ParentIntelligenceFactsSnapshot(
                generated_at=FIXED_NOW,
                learners=(
                    ParentLearnerFacts(
                        learner_principal_id=learner_id,
                        assignments=(assignment, assignment),
                    ),
                ),
            )
        )
        service, _, _ = _service(learner_ids=(learner_id,), facts=facts)
        with pytest.raises(ParentIntelligenceReadUnavailable):
            service.get_home(uuid4(), uuid4())

    def test_positive_allowlist_construction_copies_only_approved_fields(self) -> None:
        learner_id = uuid4()
        assignment_id = uuid4()
        facts = RecordingFactsReader(
            ParentIntelligenceFactsSnapshot(
                generated_at=FIXED_NOW,
                learners=(
                    ParentLearnerFacts(
                        learner_principal_id=learner_id,
                        assignments=(
                            ParentAssignmentFact(
                                assignment_id=assignment_id,
                                title="Quiz 1",
                                content_type="quiz",
                                available_from=FIXED_NOW,
                                due_at=None,
                                attempt_status="NOT_STARTED",
                                submitted_at=None,
                            ),
                        ),
                    ),
                ),
            )
        )
        service, _, _ = _service(learner_ids=(learner_id,), facts=facts)
        result = service.get_home(uuid4(), uuid4())
        card = result.children[0]
        assert card.learner_principal_id == learner_id
        status = card.assignments[0]
        assert status.assignment_id == assignment_id
        assert status.title == "Quiz 1"
        assert status.content_type == "quiz"
        assert status.attempt_status == "NOT_STARTED"
        assert status.submitted_at is None
        assert not hasattr(status, "class_ref")
        assert not hasattr(status, "content_id")
        assert not hasattr(result, "sources")
        assert not hasattr(result, "summary")
