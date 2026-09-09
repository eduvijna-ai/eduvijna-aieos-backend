"""AIEOS360-S01-I05-B2 — evaluation application/HTTP composition tests."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.learner_principals import CLASS_REF_5B
from aieos.domains.education.schema import (
    HOMEWORK_CONTENT_TYPE,
    QUIZ_CONTENT_TYPE,
)
from tests.domains.assessment.helpers_dev08_i02 import MutableSchoolContextClassReader
from tests.domains.assessment.helpers_s01_i05_b2 import (
    BATCH_PATH,
    SINGLE_PATH,
    build_client,
    close_assignment,
    count_eval_audits,
    count_evaluations,
    fetch_eval_audit,
    headers,
    insert_in_progress,
    insert_submitted,
    placeholder_mc,
    republish_with_payload,
    seed_content,
    seed_world,
    worksheet_payload,
    FIXED_NOW,
)
from tests.domains.education.test_tos_dev04_i03_content_payloads import (
    valid_homework_payload,
    valid_quiz_payload,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import create_learner_assignment
from tests.fakes import AllowClassroomAssessmentAuthorization
from aieos.platform.security.authorization.decisions import PrincipalKind
from tests.platform.security.authorization.helpers import seed_principal

pytestmark = pytest.mark.aieos360_s01_i05_b2


def _safe_dump(body: object) -> str:
    return json.dumps(body, default=str)


class TestSingleEnsure:
    def test_01_capability_required(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            assessment_authorization=AllowClassroomAssessmentAuthorization(allow=False),
        )
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="cap-1"),
        )
        assert response.status_code == 403
        assert response.json()["code"] == "assessment_capability_forbidden"

    def test_02_unauthenticated_denied(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            unauthenticated=True,
        )
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="unauth-1"),
        )
        assert response.status_code == 401

    def test_03_04_class_ref_required_and_school_context_unavailable(
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
        denied = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="class-1"),
        )
        assert denied.status_code == 403
        reader.raise_unavailable = True
        reader.class_refs = ["class-5a"]
        unavailable = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="class-2"),
        )
        assert unavailable.status_code == 503

    def test_05_06_current_authority_not_historical_owner(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        other = uuid.uuid7()
        former = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=world.tenant_id,
                teacher_principal_id=world.teacher_id,
                class_refs=(),
            ),
        )
        denied = former.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="owner-1"),
        )
        assert denied.status_code == 403
        allowed = build_client(
            runtime_engine,
            world.tenant_id,
            other,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=world.tenant_id,
                teacher_principal_id=other,
                class_refs=("class-5a",),
            ),
        )
        ok = allowed.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="owner-2"),
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["submission_id"] == str(world.submission_id)

    def test_07_historical_learner_membership_not_required(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="hist-member-1"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["learner_principal_id"] == str(world.learner_id)

    def test_08_09_cross_tenant_and_unknown_concealed(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        other_tenant = uuid.uuid7()
        other_teacher = uuid.uuid7()
        client = build_client(runtime_engine, other_tenant, other_teacher)
        hidden = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(other_tenant, idempotency_key="xt-1"),
        )
        assert hidden.status_code == 404
        owner = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        missing = owner.post(
            SINGLE_PATH.format(submission_id=uuid.uuid7()),
            headers=headers(world.tenant_id, idempotency_key="miss-1"),
        )
        assert missing.status_code == 404

    def test_10_11_12_lineage_mismatches_fail_closed(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine, submit=False)
        class_mismatch, _ = insert_submitted(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
            class_ref=CLASS_REF_5B,
        )
        content_mismatch, _ = insert_submitted(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
            content_id=uuid.uuid7(),
        )
        version_mismatch, _ = insert_submitted(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
            content_version_id=uuid.uuid7(),
        )
        client = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=world.tenant_id,
                teacher_principal_id=world.teacher_id,
                class_refs=("class-5a", CLASS_REF_5B),
            ),
        )
        for submission_id, key in (
            (class_mismatch, "lin-class"),
            (content_mismatch, "lin-content"),
            (version_mismatch, "lin-version"),
        ):
            response = client.post(
                SINGLE_PATH.format(submission_id=submission_id),
                headers=headers(world.tenant_id, idempotency_key=key),
            )
            assert response.status_code == 409, response.text
            assert response.json()["code"] == "evaluation_lineage_conflict"

    def test_13_exact_content_version_missing(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        with bootstrap_engine.begin() as conn:
            conn.execute(text("SET session_replication_role = replica"))
            conn.execute(
                text(
                    """
                    UPDATE content.contents
                    SET published_version_id = NULL, current_version_id = NULL
                    WHERE content_id = :cid
                    """
                ),
                {"cid": world.content_id},
            )
            conn.execute(
                text("DELETE FROM content.content_versions WHERE version_id = :vid"),
                {"vid": world.content_version_id},
            )
            conn.execute(text("SET session_replication_role = DEFAULT"))
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="missing-ver"),
        )
        assert response.status_code == 404, response.text

    def test_14_unsupported_schema(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
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
                    SET schema_id = 'education.lesson_plan'
                    WHERE version_id = :vid
                    """
                ),
                {"vid": world.content_version_id},
            )
            conn.execute(
                text(
                    "ALTER TABLE content.content_versions ENABLE TRIGGER "
                    "content_versions_immutable_update"
                )
            )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="unsup-1"),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "evaluation_content_unsupported"

    def test_15_16_exact_v1_not_published_v2(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        payload = worksheet_payload()
        world = seed_world(
            bootstrap_engine,
            runtime_engine,
            payload=payload,
            responses=[placeholder_mc("q-1", "1/2")],
        )
        v2_payload = worksheet_payload()
        v2_payload["questions"][0]["answer"] = "1/3"
        republish_with_payload(
            bootstrap_engine,
            tenant_id=world.tenant_id,
            content_id=world.content_id,
            parent_version_id=world.content_version_id,
            owner_id=world.teacher_id,
            payload=v2_payload,
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="v1-1"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["content_version_id"] == str(world.content_version_id)
        q1 = next(item for item in body["items"] if item["question_id"] == "q-1")
        assert q1["outcome"] == "CORRECT"
        dumped = _safe_dump(body)
        assert "1/3" not in dumped
        item_keys = {key for item in body["items"] for key in item}
        assert "answer" not in item_keys
        assert "options" not in item_keys

    def test_17_22_23_24_25_worksheet_evaluate_and_safe_response(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="eval-1"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["evaluation_policy_id"] == "aieos.learner_assessment.deterministic"
        assert body["evaluation_policy_version"] == 1
        assert "aggregate_revision" not in body
        assert "etag" not in {k.lower() for k in response.headers}
        assert "score" not in body
        assert "grade" not in body
        assert "mastery" not in body
        dumped = _safe_dump(body)
        item_keys = {key for item in body["items"] for key in item}
        assert "answer" not in item_keys
        assert "value" not in item_keys
        assert "options" not in item_keys
        assert "1/2" not in dumped
        assert {item["outcome"] for item in body["items"]} == {"UNANSWERED"}
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 1
        assert count_eval_audits(bootstrap_engine, tenant_id=world.tenant_id) == 1

    def test_18_19_quiz_and_homework(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        quiz = seed_world(
            bootstrap_engine,
            runtime_engine,
            content_type=QUIZ_CONTENT_TYPE,
            payload=valid_quiz_payload(),
        )
        homework = seed_world(
            bootstrap_engine,
            runtime_engine,
            content_type=HOMEWORK_CONTENT_TYPE,
            payload=valid_homework_payload(),
        )
        for world, key in ((quiz, "quiz-1"), (homework, "hw-1")):
            client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
            response = client.post(
                SINGLE_PATH.format(submission_id=world.submission_id),
                headers=headers(world.tenant_id, idempotency_key=key),
            )
            assert response.status_code == 200, response.text
            assert len(response.json()["items"]) >= 1

    def test_20_21_empty_and_partial_remain_evaluable(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        empty = seed_world(bootstrap_engine, runtime_engine, responses=[])
        client = build_client(runtime_engine, empty.tenant_id, empty.teacher_id)
        empty_resp = client.post(
            SINGLE_PATH.format(submission_id=empty.submission_id),
            headers=headers(empty.tenant_id, idempotency_key="empty-1"),
        )
        assert empty_resp.status_code == 200, empty_resp.text
        assert {item["outcome"] for item in empty_resp.json()["items"]} == {"UNANSWERED"}

        partial = seed_world(
            bootstrap_engine,
            runtime_engine,
            responses=[placeholder_mc("q-1", "1/2")],
        )
        client = build_client(runtime_engine, partial.tenant_id, partial.teacher_id)
        partial_resp = client.post(
            SINGLE_PATH.format(submission_id=partial.submission_id),
            headers=headers(partial.tenant_id, idempotency_key="partial-1"),
        )
        assert partial_resp.status_code == 200, partial_resp.text
        items = {item["question_id"]: item["outcome"] for item in partial_resp.json()["items"]}
        assert items["q-1"] == "CORRECT"
        assert "UNANSWERED" in items.values()

    def test_26_29_idempotency_replay_no_duplicate_audit(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        first = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="same-key"),
        )
        second = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="same-key"),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["evaluation_id"] == second.json()["evaluation_id"]
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 1
        assert count_eval_audits(bootstrap_engine, tenant_id=world.tenant_id) == 1

    def test_27_same_key_different_submission_409(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        other_sub, _ = insert_submitted(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        first = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="reuse-key"),
        )
        assert first.status_code == 200
        reused = client.post(
            SINGLE_PATH.format(submission_id=other_sub),
            headers=headers(world.tenant_id, idempotency_key="reuse-key"),
        )
        assert reused.status_code == 409
        assert reused.json()["code"] == "idempotency_key_reused"

    def test_28_new_key_same_submission_replays_row(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        first = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="k1"),
        )
        second = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="k2"),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["evaluation_id"] == second.json()["evaluation_id"]
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 1
        assert count_eval_audits(bootstrap_engine, tenant_id=world.tenant_id) == 1

    def test_55_56_57_58_idempotency_and_server_authority(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        missing = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers={"X-AIEOS-Tenant-ID": str(world.tenant_id)},
        )
        assert missing.status_code == 400
        assert missing.json()["code"] == "idempotency_key_required"
        ignored_body = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="body-ignored"),
            json={
                "learner_principal_id": str(uuid.uuid7()),
                "teacher_principal_id": str(uuid.uuid7()),
                "evaluation_policy_id": "caller.selected",
                "evaluation_policy_version": 99,
                "class_ref": "class-injected",
            },
        )
        assert ignored_body.status_code == 200, ignored_body.text
        body = ignored_body.json()
        assert body["learner_principal_id"] == str(world.learner_id)
        assert body["evaluation_policy_id"] == "aieos.learner_assessment.deterministic"
        assert body["evaluation_policy_version"] == 1
        assert body["class_ref"] == "class-5a"

    def test_61_62_63_audit_shape(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="audit-1"),
        )
        assert response.status_code == 200
        row = fetch_eval_audit(bootstrap_engine, tenant_id=world.tenant_id)
        assert row["action"] == "assessment.learner_evaluation.ensure"
        assert row["primary_resource_type"] == "assessment.learner_evaluation"
        assert row["resource_revision_before"] is None
        assert int(row["resource_revision_after"]) == 0
        related = row["related_resource_refs"]
        dumped = _safe_dump(related)
        assert "answer" not in dumped
        assert "1/2" not in dumped
        types = {item["resource_type"] for item in related}
        assert types == {
            "learning.submission",
            "learning.attempt",
            "teaching.assignment",
            "content.version",
        }


class TestBatchEnsure:
    def test_31_38_39_40_42_zero_and_multiple(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        empty = seed_world(bootstrap_engine, runtime_engine, submit=False)
        client = build_client(runtime_engine, empty.tenant_id, empty.teacher_id)
        zero = client.post(
            BATCH_PATH.format(assignment_id=empty.assignment.assignment_id.value),
            headers=headers(empty.tenant_id, idempotency_key="batch-zero"),
        )
        assert zero.status_code == 204
        assert count_evaluations(bootstrap_engine, tenant_id=empty.tenant_id) == 0
        assert count_eval_audits(bootstrap_engine, tenant_id=empty.tenant_id) == 0
        assert "not_submitted" not in (zero.text or "")

        world = seed_world(bootstrap_engine, runtime_engine)
        insert_submitted(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        ok = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-multi"),
        )
        assert ok.status_code == 204
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 2
        assert count_eval_audits(bootstrap_engine, tenant_id=world.tenant_id) == 2

    def test_32_33_batch_class_ref_and_historical_owner(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        denied = build_client(
            runtime_engine,
            world.tenant_id,
            world.teacher_id,
            school_context_reader=MutableSchoolContextClassReader(
                tenant_id=world.tenant_id,
                teacher_principal_id=world.teacher_id,
                class_refs=(),
            ),
        )
        response = denied.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-deny"),
        )
        assert response.status_code == 403

    def test_34_35_36_closed_and_in_progress_ignored(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        insert_in_progress(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
        )
        closed = close_assignment(
            runtime_engine,
            tenant_id=world.tenant_id,
            teacher_id=world.teacher_id,
            assignment=world.assignment,
            idempotency_key="close-1",
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            BATCH_PATH.format(assignment_id=closed.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-closed"),
        )
        assert response.status_code == 204, response.text
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 1

    def test_37_50_left_class_historical_evidence(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-left"),
        )
        assert response.status_code == 204
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 1

    def test_41_43_44_46_47_replay_and_later_submissions(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        first = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-replay"),
        )
        assert first.status_code == 204
        insert_submitted(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
        )
        replay = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-replay"),
        )
        assert replay.status_code == 204
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 1
        later = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-new"),
        )
        assert later.status_code == 204
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 2
        assert count_eval_audits(bootstrap_engine, tenant_id=world.tenant_id) == 2

    def test_45_same_batch_key_different_assignment(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine, submit=False)
        second_content, second_version = seed_content(
            bootstrap_engine,
            tenant_id=world.tenant_id,
            owner_id=world.teacher_id,
        )
        second = create_learner_assignment(
            runtime_engine,
            tenant_id=world.tenant_id,
            principal_id=world.teacher_id,
            content_id=second_content,
            content_version_id=second_version,
            idempotency_key=str(uuid.uuid4()),
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        ok = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-diff"),
        )
        assert ok.status_code == 204
        reused = client.post(
            BATCH_PATH.format(assignment_id=second.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-diff"),
        )
        assert reused.status_code == 409
        assert reused.json()["code"] == "idempotency_key_reused"

    def test_48_rollback_no_partial_mutations(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        insert_submitted(
            runtime_engine,
            tenant_id=world.tenant_id,
            learner_id=uuid.uuid7(),
            assignment=world.assignment,
            content_version_id=uuid.uuid7(),
            submitted_at=FIXED_NOW + timedelta(minutes=30),
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="batch-fail"),
        )
        assert response.status_code == 409, response.text
        assert count_evaluations(bootstrap_engine, tenant_id=world.tenant_id) == 0
        assert count_eval_audits(bootstrap_engine, tenant_id=world.tenant_id) == 0

    def test_55_batch_idempotency_key_required(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine, submit=False)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers={"X-AIEOS-Tenant-ID": str(world.tenant_id)},
        )
        assert response.status_code == 400


class TestGovernanceHttp:
    def test_51_52_53_54_routes(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        seed_principal(
            bootstrap_engine, world.teacher_id, principal_kind=PrincipalKind.HUMAN
        )
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        single = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="gov-1"),
        )
        batch = client.post(
            BATCH_PATH.format(assignment_id=world.assignment.assignment_id.value),
            headers=headers(world.tenant_id, idempotency_key="gov-2"),
        )
        intelligence = client.get(
            f"/api/v1/assessment/assignments/{world.assignment.assignment_id.value}/intelligence",
            headers={"X-AIEOS-Tenant-ID": str(world.tenant_id)},
        )
        evaluate_get = client.get(
            SINGLE_PATH.format(submission_id=world.submission_id)
        )
        assert single.status_code == 200
        assert batch.status_code == 204
        assert intelligence.status_code == 200
        assert evaluate_get.status_code == 405
        # B3 owns GET /intelligence; B2 only proves evaluate remains POST-only.
