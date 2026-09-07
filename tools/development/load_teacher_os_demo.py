"""CLI: load TOS-DEV10-I04 Teacher OS development demo (scenarios A–F).

NON_PRODUCTION. Explicit invocation only. Never runs on application startup,
migration, worker startup, or production composition.

Example:

  uv run python tools/development/load_teacher_os_demo.py `
    --database-url postgresql+psycopg://aieos_runtime:...@127.0.0.1:55432/aieos `
    --bootstrap-database-url postgresql+psycopg://aieos_bootstrap:...@127.0.0.1:55432/aieos

Evidence JSON is written under tmp/teacher-os-demo.json (gitignored) unless
--report-path is set.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from aieos.development.app_factory import build_development_teacher_os_app  # noqa: E402
from aieos.development.teacher_os_demo import (  # noqa: E402
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
    ensure_synthetic_human_principal,
    ensure_teacher_os_demo,
    write_demo_report,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1  # noqa: E402
from aieos.domains.education.worksheet_v1 import WorksheetV1  # noqa: E402
from aieos.domains.teaching.application.assistant_answer_v1 import (  # noqa: E402
    TeacherAssistantAnswerV1,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway  # noqa: E402
from aieos.platform.ai.gateway import ModelGenerationFailed  # noqa: E402
from tests.domains.teaching.helpers_dev04_i06 import pass_preparation_kit  # noqa: E402
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model  # noqa: E402
from tools.dev.constants import ALLOWED_DB_HOSTS, FORBIDDEN_DB_HOST_FRAGMENTS  # noqa: E402

NON_PRODUCTION = True


def _parse_scenario_date(raw: str) -> date:
    return date.fromisoformat(raw)


def _refuse_production_environment() -> None:
    for key in ("AIEOS_ENVIRONMENT", "ENVIRONMENT"):
        value = os.environ.get(key, "").strip().lower()
        if value in {"production", "prod"}:
            raise SystemExit(
                f"NON_PRODUCTION refusal: {key}={value!r} looks like production"
            )


def _host_from_database_url(database_url: str) -> str:
    try:
        return (make_url(database_url).host or "").strip().lower()
    except Exception:
        parsed = urlparse(database_url)
        return (parsed.hostname or "").strip().lower()


def _refuse_remote_database_url(database_url: str, *, allow_remote: bool) -> None:
    host = _host_from_database_url(database_url)
    for fragment in FORBIDDEN_DB_HOST_FRAGMENTS:
        if fragment in host:
            raise SystemExit(
                f"NON_PRODUCTION refusal: database host looks managed/production "
                f"({host!r})"
            )
    if allow_remote:
        return
    if host not in ALLOWED_DB_HOSTS:
        raise SystemExit(
            "NON_PRODUCTION refusal: default allows only localhost / 127.0.0.1. "
            "Pass --allow-remote-non-production for other non-production hosts."
        )


def _demo_result_factory(request):
    output_type = request.output_type
    if output_type is WorksheetV1:
        return valid_worksheet_model()
    if output_type is PreparationKitV1:
        return pass_preparation_kit()
    if output_type is TeacherAssistantAnswerV1:
        return TeacherAssistantAnswerV1.development_fake(request.input_text)
    raise ModelGenerationFailed(
        f"demo fake gateway has no fixture for {getattr(output_type, '__name__', output_type)}"
    )


def main() -> int:
    assert NON_PRODUCTION is True
    _refuse_production_environment()

    parser = argparse.ArgumentParser(
        description=(
            "NON_PRODUCTION: seed Teacher OS demo scenarios A–F via existing "
            "application HTTP contracts for Assistant context."
        )
    )
    parser.add_argument(
        "--database-url",
        required=True,
        help="SQLAlchemy URL for the runtime PostgreSQL role (NON_PRODUCTION).",
    )
    parser.add_argument(
        "--bootstrap-database-url",
        required=True,
        help=(
            "SQLAlchemy URL for the bootstrap PostgreSQL role used to upsert "
            "the synthetic ACTIVE HUMAN principal."
        ),
    )
    parser.add_argument(
        "--scenario-date",
        type=_parse_scenario_date,
        default=None,
        help=(
            "Local educational day as YYYY-MM-DD. Work target_date is the next "
            "day. Defaults to the current UTC date."
        ),
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=REPO_ROOT / "tmp" / "teacher-os-demo.json",
        help="Where to write the non-secret demo evidence report.",
    )
    parser.add_argument(
        "--allow-remote-non-production",
        action="store_true",
        help=(
            "Allow non-localhost database hosts. Still refuses production env "
            "and known managed-service host fragments."
        ),
    )
    args = parser.parse_args()

    _refuse_remote_database_url(
        args.database_url, allow_remote=args.allow_remote_non_production
    )
    _refuse_remote_database_url(
        args.bootstrap_database_url, allow_remote=args.allow_remote_non_production
    )

    scenario_date = (
        args.scenario_date
        if args.scenario_date is not None
        else datetime.now(UTC).date()
    )

    runtime_engine = create_engine(args.database_url)
    bootstrap_engine = create_engine(args.bootstrap_database_url)
    try:
        ensure_synthetic_human_principal(
            bootstrap_engine, principal_id=SYNTHETIC_PRINCIPAL_ID
        )
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
        with TestClient(app, raise_server_exceptions=False) as client:
            report = ensure_teacher_os_demo(
                client,
                tenant_id=SYNTHETIC_TENANT_ID,
                principal_id=SYNTHETIC_PRINCIPAL_ID,
                scenario_date=scenario_date,
            )
    finally:
        bootstrap_engine.dispose()
        runtime_engine.dispose()

    write_demo_report(report, args.report_path)
    print(f"scenario_id={report.scenario_id}")
    print(f"tenant_id={report.tenant_id}")
    print(f"principal_id={report.principal_id}")
    print(f"scenario_date={report.scenario_date}")
    print(f"target_date={report.target_date}")
    print(f"reused_existing={report.reused_existing}")
    print(f"review_work_id={report.review_work_id}")
    print(f"review_content_id={report.review_content_id}")
    print(f"review_version_id={report.review_version_id}")
    print(f"published_work_id={report.published_work_id}")
    print(f"published_content_id={report.published_content_id}")
    print(f"published_version_id={report.published_version_id}")
    print(f"assignment_id={report.assignment_id}")
    print(f"execution_id={report.execution_id}")
    print(f"assessment_id={report.assessment_id}")
    print(f"remediation_work_id={report.remediation_work_id}")
    print(f"memory_id={report.memory_id}")
    print(f"report={args.report_path}")
    for step in report.steps:
        print(f"  {step.key}: {step.status} {step.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
