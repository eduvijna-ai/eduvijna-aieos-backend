"""AIEOS360-S01-I03R1 — strict TRUE_FALSE HTTP Boolean input."""

from __future__ import annotations

import uuid

import pytest

from tests.domains.learning.helpers_aieos360_s01_i03 import (
    clear_i03_side_effects_after_test,
    prepare_class_5a_assignment,
    save_responses,
    start_attempt,
    student_client,
    valid_tf_write,
)

pytestmark = [pytest.mark.aieos360_s01_i03, pytest.mark.aieos360_s01_i03r1]


def _started(runtime_engine, bootstrap_engine):
    prepared = prepare_class_5a_assignment(runtime_engine, bootstrap_engine)
    client = student_client(runtime_engine, prepared)
    started = start_attempt(client, prepared)
    assert started.status_code == 201, started.text
    attempt_id = uuid.UUID(started.json()["attempt_id"])
    return prepared, client, attempt_id, started.headers["ETag"]


class TestStrictTrueFalseHttpInput:
    def test_r1_06_json_true_accepted(self, runtime_engine, bootstrap_engine) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        write = valid_tf_write()
        write["boolean_value"] = True
        response = save_responses(
            client, prepared, attempt_id, [write], if_match=match
        )
        assert response.status_code == 200, response.text
        assert response.json()["responses"][0]["boolean_value"] is True

    def test_r1_07_json_false_accepted(self, runtime_engine, bootstrap_engine) -> None:
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        write = valid_tf_write()
        write["boolean_value"] = False
        response = save_responses(
            client, prepared, attempt_id, [write], if_match=match
        )
        assert response.status_code == 200, response.text
        assert response.json()["responses"][0]["boolean_value"] is False

    @pytest.mark.parametrize(
        ("value", "case_id"),
        [
            (1, "r1_08"),
            (0, "r1_09"),
            ("true", "r1_10"),
            ("false", "r1_11"),
        ],
    )
    def test_r1_08_through_11_coercion_rejected(
        self, runtime_engine, bootstrap_engine, value, case_id
    ) -> None:
        del case_id
        prepared, client, attempt_id, match = _started(
            runtime_engine, bootstrap_engine
        )
        write = valid_tf_write()
        write["boolean_value"] = value
        response = save_responses(
            client, prepared, attempt_id, [write], if_match=match
        )
        assert response.status_code == 422
