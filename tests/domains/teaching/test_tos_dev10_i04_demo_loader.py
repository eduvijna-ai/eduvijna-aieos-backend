"""TOS-DEV10-I04 development demo loader proofs.

Runs against real PostgreSQL through the same HTTP contracts the CLI loader
uses. Synthetic tenant/principal only.
"""

from __future__ import annotations

import importlib
from datetime import date

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


class TestDemoLoaderIdempotency:
    def test_ensure_twice_reuses(
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
        assert first.work_id is not None
        assert first.content_id is not None
        assert first.assignment_id is not None
        assert first.execution_id is not None
        assert first.assessment_id is not None
        assert first.remediation_work_id is not None
        assert first.memory_id is not None

        second = ensure_teacher_os_demo(
            client,
            tenant_id=SYNTHETIC_TENANT_ID,
            principal_id=SYNTHETIC_PRINCIPAL_ID,
            scenario_date=SCENARIO_DATE,
        )
        assert second.reused_existing is True
        assert second.work_id == first.work_id
        assert second.content_id == first.content_id
        assert second.version_id == first.version_id
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
                "A.work",
                "A.generate",
                "B.publish",
                "C.assignment",
                "C.execution",
                "D.assessment",
                "D.remediation",
                "E.memory",
            }
        )


class TestDemoLoaderModulePurity:
    def test_loader_module_has_no_import_time_side_effects(self) -> None:
        module = importlib.import_module("aieos.development.teacher_os_demo")
        importlib.reload(module)
        assert module.SCENARIO_ID == SCENARIO_ID
        assert callable(module.ensure_teacher_os_demo)
        assert module.NON_PRODUCTION is True
        assert NON_PRODUCTION is True
