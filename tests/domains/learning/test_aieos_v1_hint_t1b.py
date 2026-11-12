"""PK-V1-HINT-T1b — Worksheet@2 learner-safe projection and attempt validation (ADR-063 §3.1)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from uuid import UUID

import pytest
from sqlalchemy.engine import Engine

from aieos.development.learner_principals import STUDENT_B_PRINCIPAL_ID
from aieos.domains.content.application.errors import WorksheetLearnerHintAttestationRequired
from aieos.domains.education.schema import WORKSHEET_SCHEMA_VERSION, WORKSHEET_SCHEMA_VERSION_V2
from aieos.domains.content.application.audit import api_mutation_audit_provenance
from tests.domains.education.test_aieos_v1_hint_t1 import _hint_free_v2_payload, _worksheet_v2_payload
from aieos.platform.security.authorization.decisions import PrincipalKind, PrincipalStatus
from tests.domains.education.test_aieos_v1_hint_t1_persistence import (
    FIXED_NOW,
    _append_v1,
    _event_context,
    _guarded_service,
    _seed_worksheet_content,
)
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ASSIGNMENTS_PATH,
    ATTEMPT_PATH,
    HOME_PATH,
    RaisingMembershipReader,
    build_student_client,
    fetch_responses,
    headers,
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
    publish_worksheet_v2_three_human,
)
from tests.platform.security.authorization.helpers import seed_principal

pytestmark = pytest.mark.aieos_v1_hint_t1b

_FORBIDDEN = frozenset(
    {
        "learner_hint",
        "answer",
        "explanation",
        "teacher_notes",
        "teacher_summary",
        "payload",
        "evaluation",
        "score",
        "grade",
    }
)


@pytest.fixture(autouse=True)
def _t1b_cleanup(bootstrap_engine: Engine) -> Iterator[None]:
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


def _assert_no_leak(body: object, *, hint_text: str | None = None) -> None:
    assert _nested_keys(body).isdisjoint(_FORBIDDEN)
    dumped = json.dumps(body)
    if hint_text:
        assert hint_text not in dumped
    for snippet in (hint_text, FIXED_T1B_HINT, "Compare to one half."):
        if snippet:
            assert snippet not in dumped


def _hinted_worksheet_v2_payload() -> dict:
    payload = _worksheet_v2_payload(hint=FIXED_T1B_HINT)
    payload["questions"][2]["learner_hint"] = "Compare to one half."
    return payload


def _assignment_reads_safe(client, prepared, *, hint_text: str) -> None:
    detail = client.get(
        f"{ASSIGNMENTS_PATH}/{prepared.assignment_id}",
        headers=headers(prepared.tenant_id),
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["resource"]["schema_version"] == WORKSHEET_SCHEMA_VERSION_V2
    _assert_no_leak(body, hint_text=hint_text)

    listed = client.get(ASSIGNMENTS_PATH, headers=headers(prepared.tenant_id))
    assert listed.status_code == 200
    for item in listed.json()["items"]:
        _assert_no_leak(item, hint_text=hint_text)

    home = client.get(HOME_PATH, headers=headers(prepared.tenant_id))
    assert home.status_code == 200
    _assert_no_leak(home.json(), hint_text=hint_text)


class TestGovernedWorksheetV2StudentHttp:
    def test_t1b_r1_governed_publish_path_evidence(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        publication = publish_worksheet_v2_three_human(
            runtime_engine,
            bootstrap_engine,
            payload=_hinted_worksheet_v2_payload(),
        )
        assert len({publication.author_id, publication.reviewer_id, publication.publisher_id}) == 3

    def test_t1b_assignment_states_no_hint_leak(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hinted_worksheet_v2_payload(),
        )
        client = student_client(runtime_engine, prepared)
        hint_snippets = ("Consider the denominator.", "Compare to one half.")

        _assignment_reads_safe(client, prepared, hint_text=hint_snippets[0])

        started = start_attempt(client, prepared)
        assert started.status_code == 201, started.text
        _assert_no_leak(started.json(), hint_text=hint_snippets[0])
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        match = started.headers["ETag"]

        _assignment_reads_safe(client, prepared, hint_text=hint_snippets[0])
        attempt_get = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers=headers(prepared.tenant_id),
        )
        assert attempt_get.status_code == 200
        _assert_no_leak(attempt_get.json(), hint_text=hint_snippets[0])

        saved = save_responses(
            client,
            prepared,
            attempt_id,
            [valid_mc_write(), valid_short_write(), valid_tf_write()],
            if_match=match,
        )
        assert saved.status_code == 200, saved.text
        match = saved.headers["ETag"]
        responses = saved.json()["responses"]
        assert len(responses) == 3
        _assert_no_leak(saved.json(), hint_text=hint_snippets[0])

        submitted = submit_attempt(client, prepared, attempt_id, if_match=match)
        assert submitted.status_code == 200, submitted.text
        _assert_no_leak(submitted.json(), hint_text=hint_snippets[0])

        _assignment_reads_safe(client, prepared, hint_text=hint_snippets[0])
        attempt_after = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers=headers(prepared.tenant_id),
        )
        assert attempt_after.status_code == 200
        assert attempt_after.json()["lifecycle_state"] == "SUBMITTED"
        _assert_no_leak(attempt_after.json(), hint_text=hint_snippets[0])

        persisted = fetch_responses(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )
        assert len(persisted) == 3
        _assert_no_leak(persisted, hint_text=hint_snippets[0])


class TestGovernedWorksheetV2Negatives:
    def test_t1b_wrong_learner_and_question_validation(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_worksheet_v2_payload()
        )
        other = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            STUDENT_B_PRINCIPAL_ID,
            learner_membership_reader=prepared.membership,
        )
        assert start_attempt(other, prepared).status_code == 404

        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        match = started.headers["ETag"]

        unknown = valid_mc_write()
        unknown["question_id"] = "q-missing"
        assert save_responses(client, prepared, attempt_id, [unknown], if_match=match).status_code == 422

        duplicate_kind = [valid_mc_write(), valid_mc_write()]
        assert (
            save_responses(client, prepared, attempt_id, duplicate_kind, if_match=match).status_code
            == 422
        )

        kind_mismatch = valid_short_write()
        kind_mismatch["question_id"] = "q-1"
        assert (
            save_responses(client, prepared, attempt_id, [kind_mismatch], if_match=match).status_code
            == 422
        )

        bad_mc = valid_mc_write()
        bad_mc["choice_value"] = "not-an-option"
        denied = save_responses(client, prepared, attempt_id, [bad_mc], if_match=match)
        assert denied.status_code == 422
        assert "learner_hint" not in denied.text.lower()

    def test_t1b_post_submit_and_membership_denials(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_worksheet_v2_payload()
        )
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        match = started.headers["ETag"]
        saved = save_responses(
            client, prepared, attempt_id, [valid_mc_write()], if_match=match
        )
        match = saved.headers["ETag"]
        submit_attempt(client, prepared, attempt_id, if_match=match)
        post_save = save_responses(
            client, prepared, attempt_id, [valid_short_write()], if_match=match
        )
        assert post_save.status_code == 409

        unavailable = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            prepared.student_id,
            learner_membership_reader=RaisingMembershipReader(RuntimeError("erp")),
        )
        assert start_attempt(unavailable, prepared).status_code == 503

    def test_t1b_disabled_human_denied(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine, bootstrap_engine, payload=_hinted_worksheet_v2_payload()
        )
        inactive = uuid.uuid7()
        seed_principal(
            bootstrap_engine, inactive, principal_kind=PrincipalKind.HUMAN, status=PrincipalStatus.DISABLED
        )
        client = build_student_client(
            runtime_engine,
            prepared.tenant_id,
            inactive,
            learner_membership_reader=prepared.membership,
        )
        assert start_attempt(client, prepared).status_code == 403


class TestGovernedExactVersionPin:
    def test_t1b_v1_assignment_unchanged_after_v2_publish(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        pinned = prepare_exact_pin_v1_then_v2_assignments(
            runtime_engine,
            bootstrap_engine,
            v2_payload=_hinted_worksheet_v2_payload(),
        )
        client = student_client(runtime_engine, pinned.v1_assignment)
        v1_detail = client.get(
            f"{ASSIGNMENTS_PATH}/{pinned.v1_assignment.assignment_id}",
            headers=headers(pinned.v1_assignment.tenant_id),
        )
        assert v1_detail.status_code == 200
        assert v1_detail.json()["resource"]["schema_version"] == WORKSHEET_SCHEMA_VERSION
        _assert_no_leak(v1_detail.json(), hint_text=FIXED_T1B_HINT)
        assert pinned.v1_assignment.content_version_id == pinned.v1_content_version_id

        client_v2 = student_client(runtime_engine, pinned.v2_assignment)
        v2_detail = client_v2.get(
            f"{ASSIGNMENTS_PATH}/{pinned.v2_assignment.assignment_id}",
            headers=headers(pinned.v2_assignment.tenant_id),
        )
        assert v2_detail.status_code == 200
        assert v2_detail.json()["resource"]["schema_version"] == WORKSHEET_SCHEMA_VERSION_V2
        assert pinned.v2_assignment.content_version_id == pinned.v2_content_version_id


class TestGovernedContentGate:
    def test_t1b_approve_without_attestation_denied(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        from tests.domains.learning.helpers_aieos_v1_hint_t1b import _review_service

        tenant_id = uuid.uuid7()
        author_id = uuid.uuid7()
        reviewer_id = uuid.uuid7()
        content_id = _seed_worksheet_content(
            bootstrap_engine, tenant_id=tenant_id, owner_principal_id=author_id
        )
        _, rev_after_v1 = _append_v1(
            runtime_engine,
            tenant_id=tenant_id,
            principal_id=author_id,
            content_id=content_id,
        )
        version_read, revision = _guarded_service(runtime_engine).append(
            tenant_id,
            author_id,
            content_id=content_id,
            expected_aggregate_revision=rev_after_v1,
            payload=_worksheet_v2_payload(hint=FIXED_T1B_HINT),
            authoring_class_ref="class-5a",
            idempotency_key=f"t1b-deny-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        submitted = _review_service(runtime_engine).submit(
            tenant_id,
            author_id,
            content_id=content_id,
            version_id=version_read.version_id,
            expected_aggregate_revision=revision,
            idempotency_key=f"submit-{uuid.uuid7()}",
            event_context=_event_context(author_id),
            audit_provenance=api_mutation_audit_provenance(author_id),
            now=FIXED_NOW,
        )
        with pytest.raises(WorksheetLearnerHintAttestationRequired):
            _review_service(runtime_engine).approve(
                tenant_id,
                reviewer_id,
                content_id=content_id,
                version_id=version_read.version_id,
                expected_aggregate_revision=submitted.aggregate_revision,
                reason_code=None,
                comment=None,
                idempotency_key=f"approve-{uuid.uuid7()}",
                event_context=_event_context(reviewer_id),
                audit_provenance=api_mutation_audit_provenance(reviewer_id),
                now=FIXED_NOW,
            )

    def test_t1b_hint_free_v2_governed_consumable(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        prepared = prepare_governed_worksheet_v2_assignment(
            runtime_engine,
            bootstrap_engine,
            payload=_hint_free_v2_payload(),
        )
        client = student_client(runtime_engine, prepared)
        detail = client.get(
            f"{ASSIGNMENTS_PATH}/{prepared.assignment_id}",
            headers=headers(prepared.tenant_id),
        )
        assert detail.status_code == 200
        assert detail.json()["resource"]["schema_version"] == WORKSHEET_SCHEMA_VERSION_V2
