"""PK-V1-HINT-T1b — Worksheet@2 learner-safe projection and attempt validation (ADR-063 §3.1)."""

from __future__ import annotations

import json
import uuid

import pytest

from aieos.domains.education.schema import WORKSHEET_SCHEMA_VERSION_V2
from tests.domains.education.test_aieos_v1_hint_t1 import _worksheet_v2_payload
from tests.domains.learning.helpers_aieos360_s01_i03 import (
    ASSIGNMENTS_PATH,
    ATTEMPT_PATH,
    HOME_PATH,
    clear_i03_side_effects_after_test,
    fetch_responses,
    headers,
    prepare_class_5a_assignment,
    save_responses,
    start_attempt,
    student_client,
    submit_attempt,
    valid_mc_write,
    valid_short_write,
    valid_tf_write,
)

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


def _hinted_worksheet_v2_payload() -> dict:
    payload = _worksheet_v2_payload(hint="Consider the denominator.")
    payload["questions"][2]["learner_hint"] = "Compare to one half."
    return payload


class TestWorksheetV2StudentHttp:
    def test_t1b_assignment_get_list_home_no_hint_leak(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        payload = _hinted_worksheet_v2_payload()
        prepared = prepare_class_5a_assignment(
            runtime_engine,
            bootstrap_engine,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=payload,
        )
        client = student_client(runtime_engine, prepared)
        detail = client.get(
            f"{ASSIGNMENTS_PATH}/{prepared.assignment_id}",
            headers=headers(prepared.tenant_id),
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["resource"]["schema_version"] == WORKSHEET_SCHEMA_VERSION_V2
        keys = _nested_keys(body)
        assert keys.isdisjoint(_FORBIDDEN)
        assert "Consider the denominator." not in json.dumps(body)

        listed = client.get(
            ASSIGNMENTS_PATH, headers=headers(prepared.tenant_id)
        )
        assert listed.status_code == 200
        for item in listed.json()["items"]:
            assert _nested_keys(item).isdisjoint(_FORBIDDEN)

        home = client.get(HOME_PATH, headers=headers(prepared.tenant_id))
        assert home.status_code == 200
        assert _nested_keys(home.json()).isdisjoint(_FORBIDDEN)

    def test_t1b_start_save_submit_worksheet_v2(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        prepared = prepare_class_5a_assignment(
            runtime_engine,
            bootstrap_engine,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=_hinted_worksheet_v2_payload(),
        )
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        assert started.status_code == 201, started.text
        assert _nested_keys(started.json()).isdisjoint(_FORBIDDEN)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        match = started.headers["ETag"]

        for writer in (valid_mc_write, valid_short_write, valid_tf_write):
            saved = save_responses(
                client, prepared, attempt_id, [writer()], if_match=match
            )
            assert saved.status_code == 200, saved.text
            match = saved.headers["ETag"]
            assert _nested_keys(saved.json()).isdisjoint(_FORBIDDEN)

        submitted = submit_attempt(client, prepared, attempt_id, if_match=match)
        assert submitted.status_code == 200, submitted.text
        assert _nested_keys(submitted.json()).isdisjoint(_FORBIDDEN)

        attempt = client.get(
            ATTEMPT_PATH.format(attempt_id=attempt_id),
            headers=headers(prepared.tenant_id),
        )
        assert attempt.status_code == 200
        assert attempt.json()["lifecycle_state"] == "SUBMITTED"
        assert _nested_keys(attempt.json()).isdisjoint(_FORBIDDEN)

        responses = fetch_responses(
            bootstrap_engine, tenant_id=prepared.tenant_id, attempt_id=attempt_id
        )
        assert responses
        assert _nested_keys(responses).isdisjoint(_FORBIDDEN)

    def test_t1b_invalid_mc_option_rejected(
        self, runtime_engine, bootstrap_engine, postgres18
    ) -> None:
        assert postgres18["server_version"].startswith("18.")
        prepared = prepare_class_5a_assignment(
            runtime_engine,
            bootstrap_engine,
            schema_version=WORKSHEET_SCHEMA_VERSION_V2,
            payload=_hinted_worksheet_v2_payload(),
        )
        client = student_client(runtime_engine, prepared)
        started = start_attempt(client, prepared)
        attempt_id = uuid.UUID(started.json()["attempt_id"])
        bad = valid_mc_write()
        bad["choice_value"] = "not-an-option"
        response = save_responses(
            client,
            prepared,
            attempt_id,
            [bad],
            if_match=started.headers["ETag"],
        )
        assert response.status_code == 422
        assert "learner_hint" not in response.text.lower()
