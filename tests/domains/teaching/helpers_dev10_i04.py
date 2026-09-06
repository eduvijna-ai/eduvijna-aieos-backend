"""Shared fixtures for TOS-DEV10-I04 Teacher OS Assistant HTTP/Postgres tests."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from aieos.development.schemas import (
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.domains.teaching.application.assistant_answer_v1 import (
    TeacherAssistantAnswerV1,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import ModelGenerationFailed, StructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.runtime.remediation_assessment_source import (
    SqlAlchemyRemediationAssessmentSource,
)
from aieos.platform.security.authorization import (
    CurrentPrincipalClassificationAuthority,
    PrincipalKind,
)
from aieos.platform.security.authorization.decisions import PrincipalStatus
from aieos.platform.security.context import AuthorizationUnavailableError
from tests.domains.teaching.helpers_dev04_i06 import pass_preparation_kit
from tests.domains.teaching.helpers_dev10_i03 import seed_teacher_principal
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model
from tests.fakes import (
    AllowAIGenerationAuthorization,
    AllowAssetCurrentGovernance,
    AllowAssetReferenceValidation,
    AllowClassroomAssessmentAuthorization,
    AllowPublicationAuthorization,
    AllowPublicationGovernance,
    AllowReviewAuthorization,
    AllowReviewCommentPolicy,
    FixedPrincipalAuthenticator,
    StubSecurityContextResolver,
)

IDEMPOTENCY_RETENTION = timedelta(hours=24)
CURSOR_KEY = b"tos-dev10-i04-test-cursor-key"
ASSISTANT_PATH = "/api/v1/teacher-os/assistant"


class _AllowTeachingWorkAuthorization:
    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        return None


class UnavailablePrincipalClassificationAuthority:
    """Test stub: classification SoR unavailable → fail closed."""

    def resolve_current_principal_kind(self, principal_id: UUID) -> PrincipalKind:
        raise AuthorizationUnavailableError("authorization unavailable")

    def require_current_human_principal(self, principal_id: UUID) -> PrincipalKind:
        raise AuthorizationUnavailableError("authorization unavailable")

    def require_current_workload_principal(self, principal_id: UUID) -> PrincipalKind:
        raise AuthorizationUnavailableError("authorization unavailable")


def headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-AIEOS-Tenant-ID": str(tenant_id)}


def assistant_result_factory(request):
    output_type = request.output_type
    if output_type is WorksheetV1:
        return valid_worksheet_model()
    if output_type is PreparationKitV1:
        return pass_preparation_kit()
    if output_type is TeacherAssistantAnswerV1:
        return TeacherAssistantAnswerV1.development_fake(request.input_text)
    raise ModelGenerationFailed(
        f"no fixture for {getattr(output_type, '__name__', output_type)}"
    )


def build_fake_assistant_gateway(
    *,
    error: Exception | None = None,
) -> FakeStructuredModelGateway:
    gateway = FakeStructuredModelGateway(
        result_factory=assistant_result_factory,
        provider_id="fake",
        model_id="fake-model",
    )
    if error is not None:
        gateway.error = error  # type: ignore[assignment]
    return gateway


def build_assistant_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    bootstrap_engine: Engine | None = None,
    seed_kind: PrincipalKind | None = PrincipalKind.HUMAN,
    seed_status: str = PrincipalStatus.ACTIVE,
    seed: bool = True,
    model_gateway: StructuredModelGateway | None = None,
    principal_classification_authority=None,
) -> TestClient:
    if seed:
        if bootstrap_engine is None:
            raise TypeError(
                "bootstrap_engine is required when seeding teacher principals"
            )
        seed_teacher_principal(
            bootstrap_engine,
            principal_id,
            principal_kind=seed_kind,
            status=seed_status,
        )
    gateway = (
        model_gateway
        if model_gateway is not None
        else build_fake_assistant_gateway()
    )
    classification = principal_classification_authority
    if classification is None:
        classification = CurrentPrincipalClassificationAuthority(runtime_engine)
    app = create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(
            runtime_engine,
            remediation_assessment_source_factory=SqlAlchemyRemediationAssessmentSource,
        ),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=AllowClassroomAssessmentAuthorization(),
        request_identity_authenticator=FixedPrincipalAuthenticator(principal_id),
        security_resolver=StubSecurityContextResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog(development_content_type_names()),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=AllowReviewAuthorization(),
        review_comment_policy=AllowReviewCommentPolicy(),
        publication_authorization=AllowPublicationAuthorization(),
        publication_governance=AllowPublicationGovernance(),
        asset_reference_validation=AllowAssetReferenceValidation(),
        asset_current_governance=AllowAssetCurrentGovernance(),
        school_context_class_reader=DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        ),
        teaching_authorization=_AllowTeachingWorkAuthorization(),  # type: ignore[arg-type]
        principal_classification_authority=classification,
        ai_uow_factory=SqlAlchemyAIUnitOfWorkFactory(runtime_engine),
        model_gateway=gateway,
        ai_generation_authorization=AllowAIGenerationAuthorization(),
        ai_provider_id="fake",
        ai_model_id="fake-model",
    )
    return TestClient(app, raise_server_exceptions=False)


def post_assistant(
    client: TestClient,
    tenant_id: UUID,
    *,
    message: str,
    history: list[dict] | None = None,
    teaching_work_id: UUID | None = None,
    mission_date: str | None = "2026-09-06",
):
    body: dict = {"message": message, "history": history or []}
    if teaching_work_id is not None:
        body["teaching_work_id"] = str(teaching_work_id)
    if mission_date is not None:
        body["mission_date"] = mission_date
    return client.post(ASSISTANT_PATH, headers=headers(tenant_id), json=body)
