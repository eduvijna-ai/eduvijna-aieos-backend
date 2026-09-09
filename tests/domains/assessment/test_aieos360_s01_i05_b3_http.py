"""AIEOS360-S01-I05-B3 — Teacher Assessment Intelligence GET tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.engine import Engine

from aieos.development.learner_principals import CLASS_REF_5B
from tests.domains.assessment.helpers_dev08_i02 import MutableSchoolContextClassReader
from tests.domains.assessment.helpers_s01_i05_b3 import (
    INTELLIGENCE_ACTION,
    INTELLIGENCE_PATH,
    build_client,
    count_evaluations,
    ensure_assignment,
    ensure_submission,
    get_intelligence,
    insert_obsolete_policy_evaluation,
    insert_submitted,
    mc_correct,
    mc_incorrect,
    seed_world,
    short_answer,
    worksheet_payload,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import create_learner_assignment
from tests.fakes import AllowClassroomAssessmentAuthorization

pytestmark = pytest.mark.aieos360_s01_i05_b3


class TestIntelligenceAuthorization:
    def test_capability_required(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            assessment_authorization=AllowClassroomAssessmentAuthorization(allow=False),
        )
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 403
        assert response.json()["code"] == "assessment_capability_forbidden"

    def test_unauthenticated_denied(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            unauthenticated=True,
        )
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 401

    def test_class_ref_denied_and_school_context_unavailable(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        reader = MutableSchoolContextClassReader(
            tenant_id=world.tenant_id,
            teacher_principal_id=world.teacher_id,
            class_refs=(),
        )
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            school_context_reader=reader,
        )
        denied = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "class_ref_not_assignable"

        unavailable = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            with_school_context=False,
        )
        missing = get_intelligence(
            unavailable,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert missing.status_code == 503
        assert missing.json()["code"] == "school_context_unavailable"

    def test_cross_tenant_fail_closed(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        other_tenant = uuid.uuid7()
        client = build_client(runtime_engine, other_tenant, world.teacher_id)
        response = get_intelligence(
            client,
            tenant_id=other_tenant,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code in {403, 404}

    def test_historical_owner_without_current_class_authority_denied(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        other_teacher = uuid.uuid7()
        reader = MutableSchoolContextClassReader(
            tenant_id=world.tenant_id,
            teacher_principal_id=other_teacher,
            class_refs=(),
        )
        # Former owner (assignment teacher) no longer assignable for the class.
        owner_reader = MutableSchoolContextClassReader(
            tenant_id=world.tenant_id,
            teacher_principal_id=world.teacher_id,
            class_refs=(),
        )
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            school_context_reader=owner_reader,
        )
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 403
        assert response.json()["code"] == "class_ref_not_assignable"
        _ = reader  # keep local for clarity of scenario intent

    def test_current_authorized_non_owner_teacher_may_read(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(
            bootstrap_engine, runtime_engine, responses=[mc_correct()]
        )
        other_teacher = uuid.uuid7()
        reader = MutableSchoolContextClassReader(
            tenant_id=world.tenant_id,
            teacher_principal_id=other_teacher,
        )
        owner_client = build_client(
            runtime_engine, world.tenant_id, world.teacher_id
        )
        ensure_submission(
            owner_client,
            tenant_id=world.tenant_id,
            submission_id=world.submission_id,
            idempotency_key="b3-owner-eval",
        )
        client = build_client(
            runtime_engine,
            world.tenant_id,
            other_teacher,
            school_context_reader=reader,
        )
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["submitted_learner_count"] == 1
        assert body["evaluated_learner_count"] == 1


class TestIntelligenceProjection:
    def test_side_effect_free_get(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine, responses=[mc_correct()])
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        before = count_evaluations(bootstrap_engine, tenant_id=world.tenant_id)
        assert before == 0
        auth = AllowClassroomAssessmentAuthorization()
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            assessment_authorization=auth,
        )
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 200
        after = count_evaluations(bootstrap_engine, tenant_id=world.tenant_id)
        assert after == 0
        assert response.json()["evaluated_learner_count"] == 0
        assert response.json()["learners"][0]["evaluation_state"] == "NOT_EVALUATED"
        assert all(
            call[2] == INTELLIGENCE_ACTION for call in auth.calls
        )
        assert not any(
            call[2] == "assessment.learner_evaluation.ensure" for call in auth.calls
        )

    def test_not_submitted_count_omitted(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 200
        body = response.json()
        assert "not_submitted_count" not in body
        assert "mastery" not in body
        assert "class_score" not in body

    def test_obsolete_policy_is_not_current(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(
            bootstrap_engine, runtime_engine, responses=[mc_correct()]
        )
        insert_obsolete_policy_evaluation(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=world.learner_id,
            submission_id=world.submission_id,
            attempt_id=world.attempt_id,
            assignment_id=world.assignment.assignment_id.value,
            content_id=world.content_id,
            content_version_id=world.content_version_id,
            class_ref=world.assignment.class_ref,
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["submitted_learner_count"] == 1
        assert body["evaluated_learner_count"] == 0
        learner = body["learners"][0]
        assert learner["evaluation_state"] == "NOT_EVALUATED_UNDER_CURRENT_POLICY"
        assert learner["items"] == []
        assert body["frequently_missed_questions"] == []

    def test_development_scenario_multi_learner_projection(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        tenant_id = uuid.uuid7()
        teacher_id = uuid.uuid7()
        # Reuse seed_world path for assignment, then add additional learners.
        world = seed_world(
            bootstrap_engine,
            runtime_engine,
            payload=worksheet_payload(),
            responses=[mc_correct()],
        )
        tenant_id = world.tenant_id
        teacher_id = world.teacher_id
        assignment = world.assignment
        learner_b = uuid.uuid7()
        learner_c = uuid.uuid7()
        sub_b, _ = insert_submitted(
            runtime_engine,
            tenant_id=tenant_id,
            learner_id=learner_b,
            assignment=assignment,
            responses=[mc_incorrect()],
        )
        sub_c, _ = insert_submitted(
            runtime_engine,
            tenant_id=tenant_id,
            learner_id=learner_c,
            assignment=assignment,
            responses=[short_answer()],
        )
        client = build_client(runtime_engine, tenant_id, teacher_id)
        ensure = ensure_assignment(
            client,
            tenant_id=tenant_id,
            assignment_id=assignment.assignment_id.value,
            idempotency_key="b3-batch-1",
        )
        assert ensure.status_code == 204
        before = count_evaluations(bootstrap_engine, tenant_id=tenant_id)
        assert before == 3

        response = get_intelligence(
            client,
            tenant_id=tenant_id,
            assignment_id=assignment.assignment_id.value,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["submitted_learner_count"] == 3
        assert body["evaluated_learner_count"] == 3
        assert body["evaluation_policy_id"] == (
            "aieos.learner_assessment.deterministic"
        )
        assert body["evaluation_policy_version"] == 1
        assert "not_submitted_count" not in body
        assert "mastery" not in str(body).lower()

        by_submission = {row["submission_id"]: row for row in body["learners"]}
        assert by_submission[str(world.submission_id)]["evaluation_state"] == (
            "EVALUATED_UNDER_CURRENT_POLICY"
        )
        assert by_submission[str(sub_b)]["evaluation_state"] == (
            "EVALUATED_UNDER_CURRENT_POLICY"
        )
        assert by_submission[str(sub_c)]["evaluation_state"] == (
            "EVALUATED_UNDER_CURRENT_POLICY"
        )

        q1 = next(
            row
            for row in body["question_distributions"]
            if row["question_id"] == "q-1"
        )
        assert q1["correct"] == 1
        assert q1["incorrect"] == 1
        assert q1["unanswered"] == 1  # learner C left q-1 unanswered

        q2 = next(
            row
            for row in body["question_distributions"]
            if row["question_id"] == "q-2"
        )
        assert q2["open_response_unevaluated"] == 1
        assert q2["unanswered"] == 2

        missed = {
            row["question_id"]: row["incorrect_count"]
            for row in body["frequently_missed_questions"]
        }
        assert missed.get("q-1") == 1
        assert "q-2" not in missed  # OPEN_RESPONSE / UNANSWERED never "missed"

        # Incomplete evidence remains insufficient — never mastery-strengthened.
        for rollup in body["objective_evidence_rollups"]:
            assert rollup["insufficient_evidence"] >= 1

        after = count_evaluations(bootstrap_engine, tenant_id=tenant_id)
        assert after == before

    def test_assignment_not_found(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine, submit=False)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=uuid.uuid7(),
        )
        assert response.status_code == 404

    def test_historical_left_class_submission_still_visible(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(
            bootstrap_engine, runtime_engine, responses=[mc_correct()]
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        ensure_submission(
            client,
            tenant_id=world.tenant_id,
            submission_id=world.submission_id,
            idempotency_key="b3-left-class",
        )
        # Learner membership is not re-checked for historical submitted evidence.
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=world.assignment.assignment_id.value,
        )
        assert response.status_code == 200
        assert response.json()["submitted_learner_count"] == 1
        assert response.json()["evaluated_learner_count"] == 1


class TestIntelligencePathHygiene:
    def test_other_class_ref_assignment_not_readable_via_guess(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        other = create_learner_assignment(
            runtime_engine,
            tenant_id=world.tenant_id,
            principal_id=world.teacher_id,
            content_id=world.content_id,
            content_version_id=world.content_version_id,
            idempotency_key=str(uuid.uuid4()),
            class_ref=CLASS_REF_5B,
        )
        reader = MutableSchoolContextClassReader(
            tenant_id=world.tenant_id,
            teacher_principal_id=world.teacher_id,
            # Only current class 5A; 5B assignment must fail closed.
            class_refs=(world.assignment.class_ref,),
        )
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            school_context_reader=reader,
        )
        response = get_intelligence(
            client,
            tenant_id=world.tenant_id,
            assignment_id=other.assignment_id.value,
        )
        assert response.status_code == 403
        assert INTELLIGENCE_PATH.format(
            assignment_id=other.assignment_id.value
        ).endswith("/intelligence")
