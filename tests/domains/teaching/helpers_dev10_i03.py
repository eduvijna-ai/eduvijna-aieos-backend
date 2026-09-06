"""Shared fixtures for TOS-DEV10-I03 Teacher Memory HTTP/Postgres tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from aieos.domains.teaching.infrastructure.persistence.uow import (
    SqlAlchemyTeachingUnitOfWorkFactory,
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
from tests.fakes import (
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
from tests.platform.security.authorization.helpers import seed_principal

FIXED_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
IDEMPOTENCY_RETENTION = timedelta(hours=24)
CURSOR_KEY = b"tos-dev10-i03-test-cursor-key"
MEMORY_PATH = "/api/v1/teacher-os/memory"

DEFAULT_PREFERENCES = {
    "teaching_style": "balanced",
    "preferred_difficulty": "standard",
    "preparation_detail": "balanced",
    "output_format": "structured",
    "include_differentiation": False,
}


class _AllowTeachingWorkAuthorization:
    def authorize(self, *, tenant_id, principal_id, capability) -> None:
        return None


def headers(
    tenant_id: UUID,
    *,
    idempotency_key: str | None = None,
    if_match: str | None = None,
) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id)}
    if idempotency_key is not None:
        out["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        out["If-Match"] = if_match
    return out


def seed_teacher_principal(
    bootstrap_engine: Engine,
    principal_id: UUID,
    *,
    principal_kind: PrincipalKind | None = PrincipalKind.HUMAN,
    status: str = PrincipalStatus.ACTIVE,
) -> None:
    """Seed teacher fixture principals as HUMAN by default for Memory tests."""
    seed_principal(
        bootstrap_engine,
        principal_id,
        principal_kind=principal_kind,
        status=status,
    )


def build_memory_client(
    runtime_engine: Engine,
    tenant_id: UUID,
    principal_id: UUID,
    *,
    bootstrap_engine: Engine | None = None,
    seed_kind: PrincipalKind | None = PrincipalKind.HUMAN,
    seed_status: str = PrincipalStatus.ACTIVE,
    seed: bool = True,
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
        principal_classification_authority=CurrentPrincipalClassificationAuthority(
            runtime_engine
        ),
    )
    return TestClient(app, raise_server_exceptions=False)


def create_memory(
    client: TestClient,
    tenant_id: UUID,
    *,
    key: str,
    preferences: dict | None = None,
):
    return client.post(
        MEMORY_PATH,
        headers=headers(tenant_id, idempotency_key=key),
        json={"preferences": preferences or DEFAULT_PREFERENCES},
    )
