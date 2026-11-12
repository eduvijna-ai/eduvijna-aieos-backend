"""PK-V1-HINT-T1c — Worksheet@2 exact-version Assessment evaluation (ADR-063 §3.1)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.domains.assessment.application.errors import EvaluationContentUnsupported
from aieos.domains.assessment.infrastructure.persistence.exact_content import (
    SqlAlchemyAssessmentExactContentAdapter,
)
from aieos.domains.content.domain.version import ContentPayload, canonical_payload_json
from aieos.domains.education.schema import WORKSHEET_SCHEMA_ID, WORKSHEET_SCHEMA_VERSION_V2
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.domains.education.worksheet_v2 import WorksheetV2
from tests.dbutil import set_tenant
from tests.domains.assessment.helpers_dev08_i02 import MutableSchoolContextClassReader
from tests.domains.assessment.helpers_s01_i05_b2 import (
    EVAL_ACTION,
    SINGLE_PATH,
    build_client,
    count_eval_audits,
    count_evaluations,
    fetch_eval_audit,
    headers,
    seed_world,
)
from tests.domains.education.test_aieos_v1_hint_t1 import (
    _hint_free_v2_payload,
    _worksheet_v2_payload,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    fetch_submissions,
    save_responses,
    start_attempt,
    student_client,
    submit_attempt,
    valid_mc_write,
    valid_short_write,
    valid_tf_write,
)
from tests.domains.learning.helpers_aieos_v1_hint_t1b import (
    FIXED_T1B_HINT,
    clear_t1b_side_effects,
    prepare_exact_pin_v1_then_v2_assignments,
    prepare_governed_worksheet_v2_assignment,
)
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_payload
from tests.fakes import AllowClassroomAssessmentAuthorization

pytestmark = pytest.mark.aieos_v1_hint_t1c

_FORBIDDEN = frozenset(
    {
        "learner_hint",
        "answer",
        "explanation",
        "teacher_notes",
        "teacher_summary",
        "payload",
        "score",
        "grade",
        "mastery",
    }
)


@pytest.fixture(autouse=True)
def _t1c_cleanup(bootstrap_engine: Engine) -> Iterator[None]:
    yield
    clear_t1b_side_effects(bootstrap_engine)


def _nested_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value.keys())
        for item in value.values():
            keys.update(_nested_keys(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.update(_nested_keys(item))
    return keys


def _assert_teacher_eval_safe(body: object, *, hint_text: str | None = None) -> None:
    assert _nested_keys(body).isdisjoint(_FORBIDDEN)
    dumped = json.dumps(body)
    if hint_text:
        assert hint_text not in dumped
    assert "Consider the denominator." not in dumped or hint_text is None


def _hinted_worksheet_v2_payload() -> dict:
    payload = _worksheet_v2_payload(hint=FIXED_T1B_HINT)
    payload["questions"][2]["learner_hint"] = "Compare to one half."
    return payload


def _evaluation_questions(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    content_id: UUID,
    content_version_id: UUID,
):
    with runtime_engine.connect() as conn:
        set_tenant(conn, tenant_id)
        adapter = SqlAlchemyAssessmentExactContentAdapter(conn, tenant_id)
        return adapter.load_evaluation_questions(
            content_id=content_id,
            content_version_id=content_version_id,
        )


def _submit_mixed_via_student_http(
    runtime_engine: Engine,
    bootstrap_engine: Engine,
    prepared,
) -> UUID:
    client = student_client(runtime_engine, prepared)
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    match = started.headers["ETag"]
    saved = save_responses(
        client,
        prepared,
        attempt_id,
        [valid_mc_write(), valid_short_write(), valid_tf_write()],
        if_match=match,
    )
    assert saved.status_code == 200, saved.text
    match = saved.headers["ETag"]
    submitted = submit_attempt(client, prepared, attempt_id, if_match=match)
    assert submitted.status_code == 200, submitted.text
    rows = fetch_submissions(
        bootstrap_engine,
        tenant_id=prepared.tenant_id,
        attempt_id=attempt_id,
    )
    assert len(rows) == 1
    sid = rows[0]["submission_id"]
    assert rows[0]["content_version_id"] == prepared.content_version_id
    return sid if isinstance(sid, UUID) else uuid.UUID(str(sid))


def _corrupt_version_payload(
    bootstrap_engine: Engine,
    *,
    version_id: UUID,
    payload: dict,
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


class TestWorksheetV2ExactEvaluationMapper:
    def test_t1c_v2_maps_same_private_facts_as_v1(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        pinned = prepare_exact_pin_v1_then_v2_assignments(
            runtime_engine,
            bootstrap_engine,
            v2_payload=_hinted_worksheet_v2_payload(),
        )
        v1_questions = _evaluation_questions(
            runtime_engine,
            tenant_id=pinned.v1_assignment.tenant_id,
            content_id=pinned.v1_assignment.content_id,
            content_version_id=pinned.v1_content_version_id,
        )
        v2_questions = _evaluation_questions(
            runtime_engine,
            tenant_id=pinned.v2_assignment.tenant_id,
            content_id=pinned.v2_assignment.content_id,
            content_version_id=pinned.v2_content_version_id,
        )
        assert len(v1_questions) == len(v2_questions) == 6
        for left, right in zip(v1_questions, v2_questions, strict=True):
            assert left.question_id == right.question_id
            assert left.question_type == right.question_type
            assert left.options == right.options
            assert left.answer == right.answer
            assert left.objective_ids == right.objective_ids
        assert all("learner_hint" not in asdict(q) for q in v2_questions)

    def test_t1c_hint_free_v2_supported(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hint_free_v2_payload(),
        )
        questions = _evaluation_questions(
            runtime_engine,
            tenant_id=prepared.tenant_id,
            content_id=prepared.content_id,
            content_version_id=prepared.content_version_id,
        )
        assert len(questions) == 6

    def test_t1c_schema_v3_unsupported(
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
                    SET schema_version = 3
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
        with pytest.raises(EvaluationContentUnsupported):
            _evaluation_questions(
                runtime_engine,
                tenant_id=world.tenant_id,
                content_id=world.content_id,
                content_version_id=world.content_version_id,
            )

    @pytest.mark.parametrize(
        "mutator",
        [
            lambda p: p["questions"][0].__setitem__("learner_hint", None),
            lambda p: p["questions"][0].__setitem__("learner_hint", " "),
            lambda p: p["questions"][0].__setitem__("learner_hint", "a" * 501),
            lambda p: p["questions"][0].__setitem__("learner_hint", 42),
        ],
    )
    def test_t1c_malformed_v2_fail_closed(
        self,
        bootstrap_engine: Engine,
        runtime_engine: Engine,
        mutator,
    ) -> None:
        world = seed_world(bootstrap_engine, runtime_engine)
        payload = _worksheet_v2_payload(hint=FIXED_T1B_HINT)
        mutator(payload)
        with pytest.raises((EvaluationContentUnsupported, ValueError)):
            WorksheetV2.model_validate(payload)
        _corrupt_version_payload(
            bootstrap_engine,
            version_id=world.content_version_id,
            payload=payload,
        )
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
                    SET schema_id = :schema_id, schema_version = :schema_version
                    WHERE version_id = :vid
                    """
                ),
                {
                    "vid": world.content_version_id,
                    "schema_id": WORKSHEET_SCHEMA_ID,
                    "schema_version": WORKSHEET_SCHEMA_VERSION_V2,
                },
            )
            conn.execute(
                text(
                    "ALTER TABLE content.content_versions ENABLE TRIGGER "
                    "content_versions_immutable_update"
                )
            )
        with pytest.raises(EvaluationContentUnsupported):
            _evaluation_questions(
                runtime_engine,
                tenant_id=world.tenant_id,
                content_id=world.content_id,
                content_version_id=world.content_version_id,
            )

    def test_t1c_lesson_plan_tuple_still_unsupported(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        """Preserves TestSingleEnsure.test_14_unsupported_schema invariant."""
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
            headers=headers(world.tenant_id, idempotency_key="t1c-lesson-plan"),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "evaluation_content_unsupported"


class TestGovernedWorksheetV2AssessmentHttp:
    def test_t1c_governed_submit_then_teacher_evaluate(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hinted_worksheet_v2_payload(),
        )
        submission_id = _submit_mixed_via_student_http(
            runtime_engine, bootstrap_engine, prepared
        )
        teacher = build_client(runtime_engine, prepared.tenant_id, prepared.teacher_id)
        response = teacher.post(
            SINGLE_PATH.format(submission_id=submission_id),
            headers=headers(prepared.tenant_id, idempotency_key="t1c-eval-1"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["content_version_id"] == str(prepared.content_version_id)
        assert body["evaluation_policy_id"] == "aieos.learner_assessment.deterministic"
        assert body["evaluation_policy_version"] == 1
        assert len(body["items"]) == 6
        outcomes = {item["question_id"]: item["outcome"] for item in body["items"]}
        assert outcomes["q-1"] == "CORRECT"
        assert outcomes["q-2"] == "OPEN_RESPONSE_UNEVALUATED"
        assert outcomes["q-3"] == "CORRECT"
        _assert_teacher_eval_safe(body, hint_text=FIXED_T1B_HINT)
        assert count_evaluations(bootstrap_engine, tenant_id=prepared.tenant_id) == 1
        assert count_eval_audits(bootstrap_engine, tenant_id=prepared.tenant_id) == 1
        audit = fetch_eval_audit(bootstrap_engine, tenant_id=prepared.tenant_id)
        assert audit["action"] == EVAL_ACTION

    def test_t1c_hint_does_not_affect_scoring(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        hinted = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hinted_worksheet_v2_payload(),
        )
        hint_free = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hint_free_v2_payload(),
        )
        for prepared in (hinted, hint_free):
            submission_id = _submit_mixed_via_student_http(
                runtime_engine, bootstrap_engine, prepared
            )
            teacher = build_client(
                runtime_engine, prepared.tenant_id, prepared.teacher_id
            )
            response = teacher.post(
                SINGLE_PATH.format(submission_id=submission_id),
                headers=headers(prepared.tenant_id, idempotency_key=f"t1c-hint-{uuid.uuid7()}"),
            )
            assert response.status_code == 200, response.text
            outcomes = {item["question_id"]: item["outcome"] for item in response.json()["items"]}
            assert outcomes["q-1"] == "CORRECT"
            assert outcomes["q-2"] == "OPEN_RESPONSE_UNEVALUATED"
            assert outcomes["q-3"] == "CORRECT"


class TestExactPinAssessmentEvaluation:
    def test_t1c_pinned_v1_answers_unchanged_after_v2_publish(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        v2_payload = _hinted_worksheet_v2_payload()
        v2_payload["questions"][0]["answer"] = "1/3"
        pinned = prepare_exact_pin_v1_then_v2_assignments(
            runtime_engine,
            bootstrap_engine,
            v2_payload=v2_payload,
        )
        v1_submission = _submit_mixed_via_student_http(
            runtime_engine, bootstrap_engine, pinned.v1_assignment
        )
        v2_submission = _submit_mixed_via_student_http(
            runtime_engine, bootstrap_engine, pinned.v2_assignment
        )
        teacher = build_client(
            runtime_engine,
            pinned.v1_assignment.tenant_id,
            pinned.v1_assignment.teacher_id,
        )
        v1_eval = teacher.post(
            SINGLE_PATH.format(submission_id=v1_submission),
            headers=headers(
                pinned.v1_assignment.tenant_id, idempotency_key="t1c-pin-v1"
            ),
        )
        assert v1_eval.status_code == 200, v1_eval.text
        v1_outcomes = {
            item["question_id"]: item["outcome"] for item in v1_eval.json()["items"]
        }
        assert v1_outcomes["q-1"] == "CORRECT"
        assert v1_eval.json()["content_version_id"] == str(
            pinned.v1_content_version_id
        )

        v2_eval = teacher.post(
            SINGLE_PATH.format(submission_id=v2_submission),
            headers=headers(
                pinned.v2_assignment.tenant_id, idempotency_key="t1c-pin-v2"
            ),
        )
        assert v2_eval.status_code == 200, v2_eval.text
        v2_outcomes = {
            item["question_id"]: item["outcome"] for item in v2_eval.json()["items"]
        }
        assert v2_outcomes["q-1"] == "INCORRECT"
        assert v2_eval.json()["content_version_id"] == str(
            pinned.v2_content_version_id
        )


class TestWorksheetV2AssessmentSecurity:
    def test_t1c_student_cannot_evaluate(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hinted_worksheet_v2_payload(),
        )
        submission_id = _submit_mixed_via_student_http(
            runtime_engine, bootstrap_engine, prepared
        )
        student = build_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.student_id,
            assessment_authorization=AllowClassroomAssessmentAuthorization(allow=False),
        )
        response = student.post(
            SINGLE_PATH.format(submission_id=submission_id),
            headers=headers(prepared.tenant_id, idempotency_key="t1c-student"),
        )
        assert response.status_code == 403

    def test_t1c_capability_and_school_context_denied(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        """Mirrors TestSingleEnsure.test_01 and test_03_04 boundaries."""
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hinted_worksheet_v2_payload(),
        )
        submission_id = _submit_mixed_via_student_http(
            runtime_engine, bootstrap_engine, prepared
        )
        denied_cap = build_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.teacher_id,
            assessment_authorization=AllowClassroomAssessmentAuthorization(allow=False),
        )
        assert (
            denied_cap.post(
                SINGLE_PATH.format(submission_id=submission_id),
                headers=headers(prepared.tenant_id, idempotency_key="t1c-cap"),
            ).status_code
            == 403
        )
        reader = MutableSchoolContextClassReader(
            tenant_id=prepared.tenant_id,
            teacher_principal_id=prepared.teacher_id,
            class_refs=(),
        )
        denied_class = build_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.teacher_id,
            school_context_reader=reader,
        )
        assert (
            denied_class.post(
                SINGLE_PATH.format(submission_id=submission_id),
                headers=headers(prepared.tenant_id, idempotency_key="t1c-class"),
            ).status_code
            == 403
        )

    def test_t1c_idempotent_replay_single_audit(
        self, runtime_engine: Engine, bootstrap_engine: Engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hinted_worksheet_v2_payload(),
        )
        submission_id = _submit_mixed_via_student_http(
            runtime_engine, bootstrap_engine, prepared
        )
        teacher = build_client(runtime_engine, prepared.tenant_id, prepared.teacher_id)
        key = "t1c-idem"
        first = teacher.post(
            SINGLE_PATH.format(submission_id=submission_id),
            headers=headers(prepared.tenant_id, idempotency_key=key),
        )
        second = teacher.post(
            SINGLE_PATH.format(submission_id=submission_id),
            headers=headers(prepared.tenant_id, idempotency_key=key),
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["evaluation_id"] == second.json()["evaluation_id"]
        assert count_evaluations(bootstrap_engine, tenant_id=prepared.tenant_id) == 1
        assert count_eval_audits(bootstrap_engine, tenant_id=prepared.tenant_id) == 1

    def test_t1c_v1_worksheet_regression_unchanged(
        self, bootstrap_engine: Engine, runtime_engine: Engine
    ) -> None:
        """Worksheet@1 path unchanged (TestSingleEnsure.test_17_22_23_24_25)."""
        world = seed_world(bootstrap_engine, runtime_engine)
        client = build_client(runtime_engine, world.tenant_id, world.teacher_id)
        response = client.post(
            SINGLE_PATH.format(submission_id=world.submission_id),
            headers=headers(world.tenant_id, idempotency_key="t1c-v1-reg"),
        )
        assert response.status_code == 200, response.text
        parsed_v1 = WorksheetV1.model_validate(valid_worksheet_payload())
        questions = _evaluation_questions(
            runtime_engine,
            tenant_id=world.tenant_id,
            content_id=world.content_id,
            content_version_id=world.content_version_id,
        )
        assert len(questions) == len(parsed_v1.questions)
