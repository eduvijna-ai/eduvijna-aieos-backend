"""TOS-DEV10-I04 / I04R1 development demo loader proofs.

Runs against real PostgreSQL through the same HTTP contracts the CLI loader
uses. Synthetic tenant/principal only. Proves distinct Review vs Library
lifecycles and second-run idempotency.
"""

from __future__ import annotations

import ast
import importlib
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aieos.development.app_factory import build_development_teacher_os_app
from aieos.development.teacher_os_demo import (
    NON_PRODUCTION,
    SCENARIO_ID,
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
    ensure_synthetic_human_principal,
    ensure_teacher_os_demo,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.domains.teaching.application.assistant_answer_v1 import (
    TeacherAssistantAnswerV1,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelGenerationFailed
from tests.domains.teaching.helpers_dev04_i06 import pass_preparation_kit
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model

pytestmark = pytest.mark.tos_dev10_i04

SCENARIO_DATE = date(2026, 9, 6)
DEMO_MODULE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "aieos"
    / "development"
    / "teacher_os_demo.py"
)


def _demo_result_factory(request):
    output_type = request.output_type
    if output_type is WorksheetV1:
        return valid_worksheet_model()
    if output_type is PreparationKitV1:
        return pass_preparation_kit()
    if output_type is TeacherAssistantAnswerV1:
        return TeacherAssistantAnswerV1.development_fake(request.input_text)
    raise ModelGenerationFailed("unexpected output type")


def _client(runtime_engine) -> TestClient:
    gateway = FakeStructuredModelGateway(
        result_factory=_demo_result_factory,
        provider_id="fake",
        model_id="fake-model",
    )
    app = build_development_teacher_os_app(
        runtime_engine,
        tenant_id=SYNTHETIC_TENANT_ID,
        principal_id=SYNTHETIC_PRINCIPAL_ID,
        model_gateway=gateway,
        ai_provider_id="fake",
        ai_model_id="fake-model",
    )
    return TestClient(app, raise_server_exceptions=False)


def _headers(tenant_id) -> dict[str, str]:
    return {"X-AIEOS-Tenant-ID": str(tenant_id)}


class TestDemoLoaderProductionRefusal:
    def test_production_environment_refused(self, monkeypatch) -> None:
        module = importlib.import_module("tools.development.load_teacher_os_demo")
        monkeypatch.setenv("AIEOS_ENVIRONMENT", "production")
        with pytest.raises(SystemExit) as exc:
            module._refuse_production_environment()
        assert "production" in str(exc.value).lower()

        monkeypatch.setenv("AIEOS_ENVIRONMENT", "staging")
        monkeypatch.setenv("ENVIRONMENT", "prod")
        with pytest.raises(SystemExit):
            module._refuse_production_environment()


class TestDemoLoaderLifecycleAndIdempotency:
    def test_ensure_twice_keeps_distinct_review_and_library(
        self, runtime_engine, bootstrap_engine
    ) -> None:
        ensure_synthetic_human_principal(
            bootstrap_engine, principal_id=SYNTHETIC_PRINCIPAL_ID
        )
        client = _client(runtime_engine)
        first = ensure_teacher_os_demo(
            client,
            tenant_id=SYNTHETIC_TENANT_ID,
            principal_id=SYNTHETIC_PRINCIPAL_ID,
            scenario_date=SCENARIO_DATE,
        )
        assert first.scenario_id == SCENARIO_ID
        assert first.reused_existing is False

        assert first.review_work_id is not None
        assert first.review_content_id is not None
        assert first.review_version_id is not None
        assert first.published_work_id is not None
        assert first.published_content_id is not None
        assert first.published_version_id is not None
        assert first.assignment_id is not None
        assert first.execution_id is not None
        assert first.assessment_id is not None
        assert first.remediation_work_id is not None
        assert first.memory_id is not None

        assert first.review_work_id != first.published_work_id
        assert first.review_content_id != first.published_content_id
        assert first.remediation_work_id not in {
            first.review_work_id,
            first.published_work_id,
        }

        review = client.get(
            f"/api/v1/contents/{first.review_content_id}",
            headers=_headers(SYNTHETIC_TENANT_ID),
        )
        assert review.status_code == 200
        assert review.json()["stewardship_state"] == "IN_REVIEW"
        assert review.json().get("published_version_id") is None

        queue = client.get(
            "/api/v1/teacher-os/review-queue",
            params={"limit": 100},
            headers=_headers(SYNTHETIC_TENANT_ID),
        )
        assert queue.status_code == 200
        assert any(
            item["content_id"] == first.review_content_id
            and item["version_id"] == first.review_version_id
            for item in queue.json()["items"]
        )

        published = client.get(
            f"/api/v1/contents/{first.published_content_id}",
            headers=_headers(SYNTHETIC_TENANT_ID),
        )
        assert published.status_code == 200
        assert published.json()["published_version_id"] == first.published_version_id

        library = client.get(
            "/api/v1/teacher-os/library",
            params={"limit": 100, "published_only": True},
            headers=_headers(SYNTHETIC_TENANT_ID),
        )
        assert library.status_code == 200
        assert any(
            item["content_id"] == first.published_content_id
            and item["published_version_id"] == first.published_version_id
            for item in library.json()["items"]
        )
        assert all(
            item["content_id"] != first.review_content_id
            for item in library.json()["items"]
        ), "pending Review artifact must not appear in published_only Library"

        second = ensure_teacher_os_demo(
            client,
            tenant_id=SYNTHETIC_TENANT_ID,
            principal_id=SYNTHETIC_PRINCIPAL_ID,
            scenario_date=SCENARIO_DATE,
        )
        assert second.reused_existing is True
        assert second.review_work_id == first.review_work_id
        assert second.review_content_id == first.review_content_id
        assert second.review_version_id == first.review_version_id
        assert second.published_work_id == first.published_work_id
        assert second.published_content_id == first.published_content_id
        assert second.published_version_id == first.published_version_id
        assert second.assignment_id == first.assignment_id
        assert second.execution_id == first.execution_id
        assert second.assessment_id == first.assessment_id
        assert second.remediation_work_id == first.remediation_work_id
        assert second.memory_id == first.memory_id

        assert {step.status for step in second.steps} <= {"reused", "updated"}
        assert all(
            step.status == "reused"
            for step in second.steps
            if step.key
            in {
                "A.review_work",
                "A.review_generate",
                "A.review_pending",
                "B.published_work",
                "B.published_generate",
                "B.approve",
                "B.publish",
                "B.library_visible",
                "C.assignment",
                "C.execution",
                "D.assessment",
                "D.remediation",
                "E.memory",
                "F.assistant_context",
            }
        )

        review_after = client.get(
            f"/api/v1/contents/{second.review_content_id}",
            headers=_headers(SYNTHETIC_TENANT_ID),
        )
        assert review_after.status_code == 200
        assert review_after.json()["stewardship_state"] == "IN_REVIEW"


class TestDemoLoaderModulePurity:
    def test_loader_module_has_no_import_time_side_effects(self) -> None:
        module = importlib.import_module("aieos.development.teacher_os_demo")
        importlib.reload(module)
        assert module.SCENARIO_ID == SCENARIO_ID
        assert callable(module.ensure_teacher_os_demo)
        assert module.NON_PRODUCTION is True
        assert NON_PRODUCTION is True

    def test_no_business_table_direct_sql_seeding(self) -> None:
        source = DEMO_MODULE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        sql_literals: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "text":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        sql_literals.append(str(node.args[0].value))
        assert len(sql_literals) == 1
        only = sql_literals[0].lower()
        assert "insert into security.principals" in only
        assert "teaching." not in only
        assert "content." not in only
        assert "assessment." not in only
        assert "classroom" not in only
        assert "teacher_memory" not in only
        assert "memories" not in only
