"""Build a NON_PRODUCTION FastAPI app for Teacher OS development scenarios."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy.engine import Engine

from aieos.development.auth_adapters import (
    DevelopmentAIGenerationPermit,
    DevelopmentAssetCurrentUsePermit,
    DevelopmentAssetReferencePermit,
    DevelopmentClassroomAssessmentPermit,
    DevelopmentPrincipalAuthenticator,
    DevelopmentPublicationAuthorizationPermit,
    DevelopmentPublicationGovernancePermit,
    DevelopmentReviewAuthorizationPermit,
    DevelopmentReviewCommentPermit,
    DevelopmentTeachingWorkPermit,
    DevelopmentTenantSecurityResolver,
)
from aieos.development.schemas import (
    build_development_schema_registry,
    development_content_type_names,
)
from aieos.development.school_context import DevelopmentSchoolContextClassReader
from aieos.domains.content.application.catalog import StaticContentTypeCatalog
from aieos.domains.content.infrastructure.persistence.uow import (
    SqlAlchemyContentUnitOfWorkFactory,
)
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
)
from aieos.domains.assessment.infrastructure.persistence.uow import (
    SqlAlchemyAssessmentUnitOfWorkFactory,
)
from aieos.platform.ai.composition import compose_configured_model_provider
from aieos.platform.ai.config import (
    DEFAULT_AI_MODEL,
    DEFAULT_AI_PROVIDER,
    load_generation_lease_seconds,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.platform.ai.gateway import StructuredModelGateway
from aieos.platform.ai.infrastructure.persistence.uow import (
    SqlAlchemyAIUnitOfWorkFactory,
)
from aieos.platform.api.app import create_app
from aieos.platform.runtime.remediation_assessment_source import (
    SqlAlchemyRemediationAssessmentSource,
)

CURSOR_KEY = b"tos-dev01-development-cursor-signing-key"
IDEMPOTENCY_RETENTION = timedelta(hours=24)


def build_development_teacher_os_app(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
    model_gateway: StructuredModelGateway | None = None,
    ai_provider_id: str = DEFAULT_AI_PROVIDER,
    ai_model_id: str = DEFAULT_AI_MODEL,
):
    """Compose create_app with development adapters, schemas, and optional gateway.

    Must not be called from production runtime entrypoints.
    Explicit openai/groq without a valid key fails closed. Unset provider uses Fake.
    """
    configured = compose_configured_model_provider(
        injected_gateway=model_gateway,
        provider_id=ai_provider_id if model_gateway is not None else None,
        model_id=ai_model_id if model_gateway is not None else None,
    )

    return create_app(
        uow_factory=SqlAlchemyContentUnitOfWorkFactory(runtime_engine),
        teaching_uow_factory=SqlAlchemyTeachingUnitOfWorkFactory(
            runtime_engine,
            remediation_assessment_source_factory=(
                SqlAlchemyRemediationAssessmentSource
            ),
        ),
        assessment_uow_factory=SqlAlchemyAssessmentUnitOfWorkFactory(runtime_engine),
        assessment_authorization=DevelopmentClassroomAssessmentPermit(),
        request_identity_authenticator=DevelopmentPrincipalAuthenticator(principal_id),
        security_resolver=DevelopmentTenantSecurityResolver(tenant_id, principal_id),
        content_types=StaticContentTypeCatalog(development_content_type_names()),
        cursor_signing_key=CURSOR_KEY,
        schema_registry=build_development_schema_registry(),
        idempotency_retention=IDEMPOTENCY_RETENTION,
        review_authorization=DevelopmentReviewAuthorizationPermit(),
        review_comment_policy=DevelopmentReviewCommentPermit(),
        publication_authorization=DevelopmentPublicationAuthorizationPermit(),
        publication_governance=DevelopmentPublicationGovernancePermit(),
        asset_reference_validation=DevelopmentAssetReferencePermit(),
        asset_current_governance=DevelopmentAssetCurrentUsePermit(),
        ai_uow_factory=SqlAlchemyAIUnitOfWorkFactory(runtime_engine),
        model_gateway=configured.gateway,
        ai_generation_authorization=DevelopmentAIGenerationPermit(),
        ai_provider_id=configured.provider_id,
        ai_model_id=configured.model_id,
        provider_runtime=configured.projection,
        generation_lease_seconds=load_generation_lease_seconds(),
        school_context_class_reader=DevelopmentSchoolContextClassReader(
            tenant_id=tenant_id,
            teacher_principal_id=principal_id,
        ),
        teaching_authorization=DevelopmentTeachingWorkPermit(),
    )


def build_development_review_scenario_app(
    runtime_engine: Engine,
    *,
    tenant_id: UUID,
    principal_id: UUID,
):
    """Back-compat DEV01/DEV02 helper. Delegates to Teacher OS development app."""
    return build_development_teacher_os_app(
        runtime_engine,
        tenant_id=tenant_id,
        principal_id=principal_id,
        model_gateway=FakeStructuredModelGateway(),
        ai_provider_id="fake",
        ai_model_id="fake-model",
    )
